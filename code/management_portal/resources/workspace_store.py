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

from .errors import ResourceVersionConflict


# Accept native Windows separators as well as the forward slashes used by
# installation exports. A drive or UNC share root is not a workspace.
ABSOLUTE_PATH_CHECK = "replace(path, chr(92), '/') ~ '^(/[^/].*|[A-Za-z]:/.+|//[^/]+/[^/]+/.+)$'"


def canonical_path(value: object) -> str:
    raw = str(value or "").strip()
    if not raw or not Path(raw).expanduser().is_absolute():
        raise ValueError("Workspace path must be absolute")
    resolved = Path(raw).expanduser().resolve(strict=False)
    if resolved.parent == resolved:
        raise ValueError("Workspace cannot use the filesystem root")
    # Match paths materialized from release SQL and the '/' prefix queries.
    return resolved.as_posix()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class WorkspaceStore:
    def __init__(self):
        self.database = portal_database(schema="workspace")
        self.table = f'"{self.database.schema}".workspaces'
        self.database.write(self._initialize)

    def _initialize(self, connection):
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                           (self.table + ":init",))
        connection.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
            workspace_id TEXT PRIMARY KEY CHECK (workspace_id ~ '^workspace_[0-9a-f]{{12}}$'),
            path TEXT NOT NULL CONSTRAINT workspaces_absolute_path_check CHECK ({ABSOLUTE_PATH_CHECK}),
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            deleted_at TIMESTAMPTZ,
            deleted_by TEXT
        )""")
        constraints = {row[0] for row in connection.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass "
            "AND conname IN ('workspaces_path_check','workspaces_absolute_path_check')",
            (self.table,),
        ).fetchall()}
        if "workspaces_absolute_path_check" not in constraints:
            connection.execute(f"ALTER TABLE {self.table} ADD CONSTRAINT "
                               f"workspaces_absolute_path_check CHECK ({ABSOLUTE_PATH_CHECK})")
        if "workspaces_path_check" in constraints:
            connection.execute(f"ALTER TABLE {self.table} DROP CONSTRAINT workspaces_path_check")
        # Avoid an ACCESS EXCLUSIVE ALTER lock on every service startup.
        if connection.execute(
            "SELECT 1 FROM pg_attribute WHERE attrelid=%s::regclass AND attname=%s AND NOT attisdropped",
            (self.table, "summary"),
        ).fetchone() is None:
            connection.execute(f"ALTER TABLE {self.table} ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
        # CREATE INDEX IF NOT EXISTS takes a ShareLock on the table even when
        # the index exists, so starts queued writers behind open transactions.
        if connection.execute("SELECT to_regclass(%s)", (f'"{self.database.schema}".workspaces_live_path_idx',)).fetchone()[0] is None:
            connection.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS workspaces_live_path_idx "
                               f"ON {self.table}(path) WHERE deleted_at IS NULL")

    def rows(self, ids: Iterable[str], *, connection=None, include_deleted=False):
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        with nullcontext(connection) if connection is not None else self.database.read() as conn:
            rows = conn.execute(f"SELECT * FROM {self.table} WHERE workspace_id=ANY(%s)"
                                + ("" if include_deleted else " AND deleted_at IS NULL"), (ids,)).fetchall()
        return {row["workspace_id"]: decode_times(row, ('created_at', 'updated_at', 'deleted_at')) for row in rows}

    def paths(self, ids, *, connection=None):
        ids = list(dict.fromkeys(ids))
        rows = self.rows(ids, connection=connection)
        missing = set(ids) - rows.keys()
        if missing:
            raise KeyError("Missing or deleted Workspace: " + ", ".join(sorted(missing)))
        return {wid: row["path"] for wid, row in rows.items()}

    def ensure(self, workspace_id: str, path: str, *, title="", connection=None, move=False):
        get_resource_type_registry().validate(workspace_id, "workspace")
        path = canonical_path(path)
        def write(conn):
            # The partial path index arbitrates concurrent registration, while
            # the row lock prevents a stale save from reviving a deletion.
            row = conn.execute(f"SELECT * FROM {self.table} WHERE workspace_id=%s FOR UPDATE",
                               (workspace_id,)).fetchone()
            if row:
                if row["deleted_at"] is not None:
                    raise ValueError("Deleted Workspace cannot be restored")
                if row["path"] != path:
                    if not move:
                        raise ValueError("Workspace path differs from its registered location")
                    conn.execute(f"UPDATE {self.table} SET path=%s,"
                                 "updated_at=GREATEST(%s::timestamptz, updated_at + interval '1 microsecond') "
                                 "WHERE workspace_id=%s",
                                 (path, now(), workspace_id))
                return workspace_id
            timestamp = now()
            conn.execute(f"INSERT INTO {self.table} "
                         "(workspace_id,path,title,created_at,updated_at) VALUES (%s,%s,%s,%s,%s)",
                         (workspace_id, path, title or Path(path).name, timestamp, timestamp))
            return workspace_id
        return write(connection) if connection is not None else self.database.write(write)

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

    def for_path(self, path: str, *, new_id, preferred_id="") -> str:
        path = canonical_path(path)
        def write(conn):
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (self.table + path,))
            row = conn.execute(f"SELECT workspace_id FROM {self.table} WHERE path=%s AND deleted_at IS NULL",
                               (path,)).fetchone()
            if row:
                if preferred_id and row["workspace_id"] != preferred_id:
                    raise ValueError("Workspace path already has a different identity")
                return row["workspace_id"]
            return self.ensure(preferred_id or new_id(), path, connection=conn)
        return self.database.write(write)

    def deleted_under(self, paths, *, connection=None) -> list[str]:
        """Deleted Workspaces registered at or below these directories."""
        roots = sorted({canonical_path(path) for path in paths if path})
        if not roots:
            return []
        with nullcontext(connection) if connection is not None else self.database.read() as conn:
            rows = conn.execute(
                f"SELECT w.workspace_id FROM {self.table} w JOIN unnest(%s::text[]) AS r(path) "
                "ON w.path=r.path OR starts_with(w.path, rtrim(r.path,'/') || '/') "
                "WHERE w.deleted_at IS NOT NULL ORDER BY w.workspace_id", (roots,)).fetchall()
        return list(dict.fromkeys(row["workspace_id"] for row in rows))

    def mark_deleted(self, workspace_id, *, deleted_at, deleted_by, connection):
        connection.execute(f"UPDATE {self.table} SET deleted_at=%s,deleted_by=%s,"
                           "updated_at=GREATEST(%s::timestamptz, updated_at + interval '1 microsecond') "
                           "WHERE workspace_id=%s AND deleted_at IS NULL",
                           (deleted_at, deleted_by, deleted_at, workspace_id))

    @staticmethod
    def descriptor(row):
        return {
            "schema": "tfi.collaboration-descriptor.v2",
            "resource_id": row["workspace_id"], "title": row["title"], "summary": row["summary"],
            "status": "deleted" if row["deleted_at"] is not None else "active",
            "attributes": {"path": row["path"]}, "relations": [], "project_ids": [],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "deleted_at": row["deleted_at"] or "",
        }

    def descriptors(self, ids=None, *, connection=None):
        if ids is None:
            with nullcontext(connection) if connection is not None else self.database.read() as conn:
                rows = conn.execute(f"SELECT * FROM {self.table} ORDER BY workspace_id").fetchall()
            return {row["workspace_id"]: self.descriptor(decode_times(row, ('created_at', 'updated_at', 'deleted_at')))
                    for row in rows}
        return {wid: self.descriptor(row) for wid, row in
                self.rows(ids, connection=connection, include_deleted=True).items()}


@lru_cache(maxsize=16)
def _store(dsn, schema):
    return WorkspaceStore()


def get_workspace_store():
    return _store(portal_database_url(), _database_schema("workspace"))
