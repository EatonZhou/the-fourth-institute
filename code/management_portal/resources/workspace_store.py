"""Workspace identity, location and lifecycle. No Resident or descriptor copies.

Callers may supply their transaction so identity, ownership and bindings commit
together. Resource descriptors are disposable projections of these typed rows.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from management_common.portal_database import portal_database, _database_schema, portal_database_url
from management_common.resource_identity import get_resource_type_registry
from management_common.database_time import decode_times
from management_common.instance_locations import located_path, stored_location
from management_common.instance_manifest import AREAS
from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_common.directory_paths import location_key

from .errors import ResourceVersionConflict


# A location is an area reference (``projects:AI/agents/...``) or a host
# directory's absolute path. Accept native Windows separators as well as the
# forward slashes used by installation exports. A drive or UNC share root is
# not a workspace.
LOCATION_CHECK = (
    "replace(path, chr(92), '/') ~ '^(/[^/].*|[A-Za-z]:/.+|//[^/]+/[^/]+/.+|("
    + "|".join(AREAS) + "):([^/].*)?)$'"
)


def canonical_path(value: object) -> str:
    raw = str(value or "").strip()
    if not raw or not Path(raw).expanduser().is_absolute():
        raise ValueError("Workspace path must be absolute")
    resolved = Path(raw).expanduser().resolve(strict=False)
    if resolved.parent == resolved:
        raise ValueError("Workspace cannot use the filesystem root")
    # Match paths materialized from release SQL and the '/' prefix queries.
    return resolved.as_posix()


def _located(row):
    return {**row, "path": located_path(row["path"])}


def registrant_fields(actor: object) -> tuple[str, str]:
    """Who registered a Workspace: the User or Resident ``actor`` names, and the owner a personal AMA's Resident stands for.

    Portal services and scripts are not registrants: a Workspace Portal creates
    for a Resident, Lineage or Project gives no one @admin by being created.
    """
    from management_portal.permission_system.facts import principal_id

    import re

    creator = principal_id(actor)
    # A resource ID other than a Resident's (a Lineage running a handoff) names
    # no person, though it parses as a user name; it would give its members @admin.
    if not creator or (re.fullmatch(r"[a-z]+_[0-9a-f]{12}", creator) and not creator.startswith("resident_")):
        return "", ""
    owner = ""
    if creator.startswith("resident_"):
        from management_portal.permission_system.personal_ama import owner_of
        owner = owner_of(creator) or ""
    return creator, owner if owner != creator else ""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def refresh_directory_dependency(connection, workspace_id):
    from management_common import directory_dependencies as deps
    if not deps.installed(connection):
        return
    table = '"' + _database_schema('workspace') + '".workspaces'
    row = connection.execute(f'SELECT path,owner_id,deleted_at FROM {table} WHERE workspace_id=%s', (workspace_id,)).fetchone()
    entries = [] if row is None or row['deleted_at'] else [('', row['owner_id'], located_path(row['path']), 'owned')]
    deps.replace(connection, 'workspace', workspace_id, entries)


def initialize_directory_owner(connection):
    store = WorkspaceStore.__new__(WorkspaceStore)
    store.database = portal_database(schema='workspace')
    store.table = '"' + store.database.schema + '".workspaces'
    connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{store.database.schema}"')
    store._initialize(connection)


class WorkspaceStore:
    def __init__(self):
        self.database = portal_database(schema="workspace")
        self.table = f'"{self.database.schema}".workspaces'
        self.cleanup = get_directory_cleanup_store()
        self.database.write(self._initialize)

    @property
    def registrants(self):
        # Who registered each Workspace. A table of its own: the registry table
        # is read with SELECT * by running services and is never altered online.
        return f'"{self.database.schema}".workspace_registrants'

    def _initialize_registrants(self, connection):
        if connection.execute("SELECT to_regclass(%s)", (self.registrants,)).fetchone()[0] is not None:
            return
        connection.execute(f"""CREATE TABLE {self.registrants} (
            workspace_id TEXT PRIMARY KEY CHECK (workspace_id ~ '^workspace_[0-9a-f]{{12}}$'),
            created_by TEXT NOT NULL CHECK (created_by <> ''),
            created_for TEXT NOT NULL DEFAULT ''
        )""")
        self._backfill_registrants(connection)

    def _backfill_registrants(self, connection):
        """One time, with the table: the actor of each completed CreateWorkspace/RegisterWorkspace operation.

        BindWorkspace registrations left no record that names their actor
        reliably; they stay without a registrant (and give no permission).
        """
        creations = '"' + _database_schema('directory_lifecycle') + '".resource_creations'
        if connection.execute("SELECT to_regclass(%s)", (creations,)).fetchone()[0] is None:
            return
        rows = connection.execute(f"""
            SELECT c.resource_id, c.actor FROM {creations} c
            JOIN {self.table} w ON w.workspace_id=c.resource_id
            WHERE c.completed AND NOT c.aborted AND w.deleted_at IS NULL AND c.resource_id LIKE 'workspace\\_%%'
            ORDER BY c.resource_id
        """).fetchall()
        count = 0
        for row in rows:
            if self._record_registrant(connection, row["resource_id"], row["actor"]):
                count += 1
        if count:
            import logging
            logging.getLogger(__name__).warning("Workspace registrants backfilled for %d Workspaces", count)

    def _record_registrant(self, connection, workspace_id, actor) -> bool:
        creator, created_for = registrant_fields(actor)
        if not creator:
            return False
        return connection.execute(
            f"INSERT INTO {self.registrants} (workspace_id,created_by,created_for) VALUES (%s,%s,%s) "
            "ON CONFLICT (workspace_id) DO NOTHING", (workspace_id, creator, created_for)).rowcount == 1

    def _initialize(self, connection):
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                           (self.table + ":init",))
        if connection.execute("SELECT to_regclass(%s)", (self.table,)).fetchone()[0] is not None:
            columns = {row[0] for row in connection.execute(
                "SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND NOT attisdropped",
                (self.table,),
            ).fetchall()}
            if not {'owner_id', 'operation_id'} <= columns:
                raise RuntimeError('Workspace directory protocol migration requires the offline maintenance window')
            if 'origin' in columns:
                connection.execute(f'ALTER TABLE {self.table} DROP COLUMN origin')
            self._initialize_registrants(connection)
            return
        connection.execute(f"""CREATE TABLE {self.table} (
            workspace_id TEXT PRIMARY KEY CHECK (workspace_id ~ '^workspace_[0-9a-f]{{12}}$'),
            path TEXT NOT NULL CONSTRAINT workspaces_location_check CHECK ({LOCATION_CHECK}),
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            owner_id TEXT NOT NULL CHECK (owner_id <> ''),
            operation_id TEXT UNIQUE,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            deleted_at TIMESTAMPTZ,
            deleted_by TEXT
        )""")
        connection.execute(f"CREATE UNIQUE INDEX workspaces_live_path_idx "
                           f"ON {self.table}(path) WHERE deleted_at IS NULL")
        from management_common.directory_dependencies import guard_new_table
        guard_new_table(connection, table=self.table, kind='workspace', key='workspace_id',
                        watched=['path', 'owner_id', 'deleted_at'], condition='NEW.deleted_at IS NULL')
        self._initialize_registrants(connection)

    def lifecycle_facts(self, ids: Iterable[str]):
        """Current deletion facts without decoding or locating workspace paths."""
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        with self.database.read() as connection:
            rows = connection.execute(
                f"SELECT workspace_id AS resource_id,deleted_at FROM {self.table} WHERE workspace_id=ANY(%s)",
                (ids,),
            ).fetchall()
        return {row['resource_id']: dict(row) for row in rows}

    def rows(self, ids: Iterable[str], *, connection=None, include_deleted=False):
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        with nullcontext(connection) if connection is not None else self.database.read() as conn:
            rows = conn.execute(f"SELECT w.*,r.created_by,r.created_for FROM {self.table} w "
                                f"LEFT JOIN {self.registrants} r ON r.workspace_id=w.workspace_id "
                                "WHERE w.workspace_id=ANY(%s)"
                                + ("" if include_deleted else " AND w.deleted_at IS NULL"), (ids,)).fetchall()
        return {row["workspace_id"]: _located(decode_times(row, ('created_at', 'updated_at', 'deleted_at')))
                for row in rows}

    def paths(self, ids, *, connection=None):
        ids = list(dict.fromkeys(ids))
        rows = self.rows(ids, connection=connection)
        missing = set(ids) - rows.keys()
        if missing:
            raise KeyError("Missing or deleted Workspace: " + ", ".join(sorted(missing)))
        return {wid: row["path"] for wid, row in rows.items()}

    def ensure(self, workspace_id: str, path: str, *, title="", connection=None, move=False,
               owner_id=None, operation_id=None, child_of_operation=False, registrant=""):
        get_resource_type_registry().validate(workspace_id, "workspace")
        absolute = canonical_path(path)
        path = stored_location(absolute)
        def write(conn):
            # An existing, unchanged registration is an observation, not a
            # path allocation. A concurrent deletion may follow this read;
            # this branch never writes and cannot revive it.
            observed = conn.execute(f"SELECT path,deleted_at FROM {self.table} WHERE workspace_id=%s",
                                    (workspace_id,)).fetchone()
            if observed:
                if observed['deleted_at'] is not None:
                    raise ValueError("Deleted Workspace cannot be restored")
                if observed['path'] == path:
                    return workspace_id
            self.cleanup.lock(conn)
            from management_common.directory_dependencies import prepare_write
            prepare_write(conn)
            # The partial path index arbitrates concurrent registration, while
            # the row lock prevents a stale save from reviving a deletion.
            row = conn.execute(f"SELECT * FROM {self.table} WHERE workspace_id=%s FOR UPDATE",
                               (workspace_id,)).fetchone()
            if row:
                if row["deleted_at"] is not None:
                    raise ValueError("Deleted Workspace cannot be restored")
                if row["path"] != path:
                    self._require_distinct_location(conn, absolute, workspace_id)
                    self.cleanup.assert_available(absolute, connection=conn, operation_id=operation_id)
                    if not move:
                        raise ValueError("Workspace path differs from its registered location")
                    conn.execute(f"UPDATE {self.table} SET path=%s,"
                                 "updated_at=GREATEST(%s::timestamptz, updated_at + interval '1 microsecond') "
                                 "WHERE workspace_id=%s",
                                 (path, now(), workspace_id))
                    self.refresh_directory_dependency(workspace_id, connection=conn)
                return workspace_id
            self._require_distinct_location(conn, absolute, workspace_id)
            self._insert(conn, workspace_id, absolute, path, title=title,
                         owner_id=owner_id, operation_id=operation_id, child_of_operation=child_of_operation,
                         registrant=registrant)
            return workspace_id
        return write(connection) if connection is not None else self.database.write(write)

    def _insert(self, connection, workspace_id, absolute, stored, *, title="", owner_id=None,
                operation_id=None, child_of_operation=False, registrant=""):
        """Insert after the caller's locked identity and location decision."""
        from management_common.directory_dependencies import prepare_write
        prepare_write(connection)
        self.cleanup.assert_available(absolute, connection=connection, operation_id=operation_id)
        timestamp = now()
        connection.execute(f"INSERT INTO {self.table} "
                           "(workspace_id,path,title,owner_id,operation_id,created_at,updated_at) "
                           "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                           (workspace_id, stored, title or Path(absolute).name,
                            owner_id or workspace_id, None if child_of_operation else operation_id, timestamp, timestamp))
        # The real actor that asked for this registration; Portal's own registrations pass none.
        registered = self._record_registrant(connection, workspace_id, registrant)
        self.refresh_directory_dependency(workspace_id, connection=connection)
        if registered:
            # The Workspace and its registrant's @admin enter the permission
            # instance with the registration (relation rule "Workspace 登记人").
            # Portal-made Workspaces keep their place through their owner.
            from management_portal import permission_system
            descriptor = self.descriptor(self.rows([workspace_id], connection=connection)[workspace_id])
            permission_system.sync_resource(descriptor, resource_type="workspace", connection=connection)

    def _location(self, connection, absolute):
        # One target key for this locked decision. Never retain native-case
        # evidence across calls or trust it as a reservation.
        key = location_key(Path(absolute))
        return next((row for row in connection.execute(
            f'SELECT workspace_id,path FROM {self.table} WHERE deleted_at IS NULL').fetchall()
            if location_key(Path(located_path(row['path']))) == key), None)

    def _require_distinct_location(self, connection, path, workspace_id):
        key = location_key(Path(path))
        for row in connection.execute(f'SELECT workspace_id,path FROM {self.table} WHERE deleted_at IS NULL').fetchall():
            if row['workspace_id'] != workspace_id and location_key(Path(located_path(row['path']))) == key:
                raise ValueError('Workspace location already has a different identity: ' + row['workspace_id'])

    def require_owner(self, workspace_id, *, expected_version, expected_owner,
                              expected_path, connection):
        """Validate a relocation snapshot under the identity owner's row lock."""
        row = connection.execute(f'SELECT * FROM {self.table} WHERE workspace_id=%s FOR UPDATE',
                                 (workspace_id,)).fetchone()
        if row is None or row['deleted_at'] is not None:
            raise KeyError(workspace_id)
        row = _located(decode_times(row, ('created_at', 'updated_at', 'deleted_at')))
        if (row['updated_at'] != expected_version or row['owner_id'] != expected_owner
                or row['path'] != str(expected_path)):
            raise ResourceVersionConflict('Workspace relocation snapshot changed',
                expected_version=expected_version, current_version=row['updated_at'])
        return row

    def restore_self_owner(self, workspace_id, *, expected_version, expected_owner,
                           expected_path, connection):
        """Retain the explicit standalone-Workspace ownership operation."""
        self.restore_owner(workspace_id, owner_id=workspace_id,
            expected_version=expected_version, expected_owner=expected_owner,
            expected_path=expected_path, connection=connection)

    def restore_owner(self, workspace_id, *, owner_id, expected_version, expected_owner,
                      expected_path, connection):
        """Apply the lifecycle owner's declaration against a locked snapshot."""
        if not owner_id:
            raise ValueError('Workspace directory owner is required')
        self.cleanup.lock(connection)
        from management_common.directory_dependencies import prepare_write
        prepare_write(connection)
        self.require_owner(workspace_id, expected_version=expected_version,
            expected_owner=expected_owner, expected_path=expected_path, connection=connection)
        connection.execute(f'UPDATE {self.table} SET owner_id=%s,'
                           "updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond') "
                           'WHERE workspace_id=%s', (owner_id, workspace_id))
        self.refresh_directory_dependency(workspace_id, connection=connection)

    def refresh_directory_dependency(self, workspace_id, *, connection):
        refresh_directory_dependency(connection, workspace_id)

    def update_metadata(self, workspace_id: str, *, title=None, summary=None, expected_version: str, authorize):
        """Compare and change metadata while holding the lifecycle row lock.

        The resource provider authorizes the locked row, so a concurrent move
        cannot change its personal-AMA boundary between the check and write.
        """
        get_resource_type_registry().validate(workspace_id, "workspace")
        if title is None and summary is None:
            raise ValueError("Workspace update requires title or summary")
        if title is not None and (not isinstance(title, str) or not title.strip() or "\x00" in title):
            raise ValueError("Workspace title must be a nonempty string without NUL")
        if summary is not None and (not isinstance(summary, str) or "\x00" in summary or len(summary) > 2000):
            raise ValueError("Workspace summary must be text of at most 2000 characters without NUL")
        if not isinstance(expected_version, str) or not expected_version.strip():
            raise ValueError("Workspace expected_version must be a nonempty string")

        def write(conn):
            row = conn.execute(f"SELECT * FROM {self.table} WHERE workspace_id=%s FOR UPDATE",
                               (workspace_id,)).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise KeyError(workspace_id)
            current = decode_times(row, ('created_at', 'updated_at', 'deleted_at'))
            authorize(self.descriptor(current))
            if expected_version != current["updated_at"]:
                raise ResourceVersionConflict(
                    "Workspace changed since it was read",
                    expected_version=expected_version, current_version=current["updated_at"],
                )
            next_title = title.strip() if title is not None else row["title"]
            next_summary = summary.strip() if summary is not None else row["summary"]
            if next_title == row["title"] and next_summary == row["summary"]:
                return self.descriptor(current)
            row = conn.execute(
                f"UPDATE {self.table} SET title=%s,summary=%s,"
                "updated_at=GREATEST(%s::timestamptz, updated_at + interval '1 microsecond') "
                "WHERE workspace_id=%s RETURNING *",
                (next_title, next_summary, now(), workspace_id),
            ).fetchone()
            return self.descriptor(decode_times(row, ('created_at', 'updated_at', 'deleted_at')))

        return self.database.write(write)

    def rename(self, workspace_id: str, title: str, *, expected_version: str, authorize):
        return self.update_metadata(workspace_id, title=title, expected_version=expected_version, authorize=authorize)

    def owned_binding(self, owner_id, path, *, connection):
        row = connection.execute(
            f"SELECT workspace_id FROM {self.table} WHERE path=%s AND owner_id=%s AND deleted_at IS NULL",
            (stored_location(canonical_path(path)), owner_id),
        ).fetchone()
        if row:
            return {"workspace_id": row["workspace_id"], "path": canonical_path(path), "purpose": ""}
        return None

    def for_path(self, path: str, *, new_id, preferred_id="", registrant="") -> str:
        absolute = canonical_path(path)
        stored = stored_location(absolute)
        def identity(row):
            if preferred_id and row['workspace_id'] != preferred_id:
                raise ValueError("Workspace path already has a different identity")
            return row['workspace_id']

        # Observation only: the partial unique index supplies the live exact
        # spelling. Like ensure's unchanged read, this may become stale after
        # the statement and cannot reserve a path for a later mutation.
        with self.database.read() as conn:
            row = conn.execute(f"SELECT workspace_id FROM {self.table} "
                               "WHERE path=%s AND deleted_at IS NULL", (stored,)).fetchone()
        if row:
            return identity(row)

        def write(conn):
            self.cleanup.lock(conn)
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (self.table + stored,))
            row = self._location(conn, absolute)
            if row:
                return identity(row)
            if not Path(absolute).is_dir():
                raise ValueError('Workspace registration requires an existing directory')
            workspace_id = preferred_id or new_id()
            get_resource_type_registry().validate(workspace_id, "workspace")
            # The location decision above already covered all live identities.
            # Check a supplied ID as well, including tombstones, without making
            # ensure scan the same registry for a second time.
            existing = conn.execute(f"SELECT path,deleted_at FROM {self.table} "
                                    "WHERE workspace_id=%s FOR UPDATE", (workspace_id,)).fetchone()
            if existing:
                if existing['deleted_at'] is not None:
                    raise ValueError("Deleted Workspace cannot be restored")
                raise ValueError("Workspace path differs from its registered location")
            self._insert(conn, workspace_id, absolute, stored, registrant=registrant)
            return workspace_id
        return self.database.write(write)

    def mark_deleted(self, workspace_id, *, deleted_at, deleted_by, connection):
        from management_common.directory_dependencies import prepare_write
        prepare_write(connection)
        connection.execute(f"UPDATE {self.table} SET deleted_at=%s,deleted_by=%s,"
                           "updated_at=GREATEST(%s::timestamptz, updated_at + interval '1 microsecond') "
                           "WHERE workspace_id=%s AND deleted_at IS NULL",
                           (deleted_at, deleted_by, deleted_at, workspace_id))
        self.refresh_directory_dependency(workspace_id, connection=connection)

    @staticmethod
    def descriptor(row):
        return {
            "schema": "tfi.collaboration-descriptor.v2",
            "resource_id": row["workspace_id"], "title": row["title"], "summary": row["summary"],
            "status": "deleted" if row["deleted_at"] is not None else "active",
            "attributes": {"path": located_path(row["path"]),
                           "directory": {"owner_id": row['owner_id']},
                           # The registrant (relation 创建者); Portal-created Workspaces have none.
                           **({"created_by": row["created_by"]} if row.get("created_by") else {}),
                           **({"created_for": row["created_for"]} if row.get("created_for") else {})},
            "relations": [], "project_ids": [],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "deleted_at": row["deleted_at"] or "",
        }

    def descriptors(self, ids=None, *, connection=None):
        if ids is None:
            with nullcontext(connection) if connection is not None else self.database.read() as conn:
                rows = conn.execute(f"SELECT w.*,r.created_by,r.created_for FROM {self.table} w "
                                    f"LEFT JOIN {self.registrants} r ON r.workspace_id=w.workspace_id "
                                    "ORDER BY w.workspace_id").fetchall()
            return {row["workspace_id"]: self.descriptor(decode_times(row, ('created_at', 'updated_at', 'deleted_at')))
                    for row in rows}
        return {wid: self.descriptor(row) for wid, row in
                self.rows(ids, connection=connection, include_deleted=True).items()}


@lru_cache(maxsize=16)
def _store(dsn, schema):
    return WorkspaceStore()


def get_workspace_store():
    return _store(portal_database_url(), _database_schema("workspace"))
