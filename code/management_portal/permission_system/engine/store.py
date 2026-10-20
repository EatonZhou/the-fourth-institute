"""PostgreSQL storage of permission schemes and their instances (schema ``permission``).

A permission scheme is its rules plus the instance saved with it.  Custom
rules are documents in ``schemes`` (built-in rules ship with the code);
``active_scheme`` names the scheme the permission system uses.  Each scheme
keeps its own instance, every row keyed by ``scheme_id``:

- ``scheme_nodes`` places elements in the P and R trees: one row per
  element, with its parent and its root-to-element ``path``.  "Element e is
  in node X" is "X is on e's path", answered by a GIN index in both
  directions: one row gives all ancestors; ``path && nodes`` lists whole
  subtrees.  Elements without a row sit under their tree's default parent.
- ``scheme_entries`` is the permission table: ``(p, a, r)``, an effect, a
  ``disabled`` flag and an edit ``version``.  A row does not record where it
  came from.  Lasting rows are unique by content, so generating the same
  row twice keeps one.  Temporary credentials are rows with an expiry, an
  issuer and, when they travel with a message, that message's ID.
- ``state`` holds one version number.  Every change a decision cache could
  hold — rows added, changed, disabled or deleted, credentials issued or
  revoked, principals moved in the P tree, another scheme activated, rules
  replaced — adds one in the same transaction.  Resource paths are read at
  each decision and never cached, so the R tree changes without it.
- ``scheme_objects`` lists the Portal objects whose rules the instance has
  applied, with the relations (a Family's steward, a chatroom's members) it
  applied them with.  An object missing there is new to the instance; a
  change of steward or members replaces exactly the rows the old ones gave.
- ``scheme_instances`` records which rules the instance was generated from
  (``rules_digest``, and the whole ``rules`` as the basis the next release of
  a built-in scheme compares against) and when.
- ``scheme_sources`` records where a copy came from; ``scheme_merges`` the
  last merge of its source's changes (automatic, or saved with
  ``source_digest``) or, for a built-in scheme, the last time a release's
  rules were applied to its instance: the conflicts it kept, the edits it
  could not write back, the generated rows it left deleted and its
  authorization difference (``authdiff``).

Statements name their tables with the schema, so a caller's transaction on
another schema of the same database can carry instance changes: a new object
and its place in the trees commit together.  The database is the only truth;
every process cache can be rebuilt from it.
"""
from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from .model import DENY, PERMIT, Entry

SCHEMA = "permission"
DEFAULT_SCHEME = "default"

_DDL = """
CREATE TABLE IF NOT EXISTS {s}.schemes (
    scheme_id TEXT PRIMARY KEY,
    document JSONB NOT NULL,
    version INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {s}.active_scheme (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    scheme_id TEXT NOT NULL,
    selected_by TEXT NOT NULL,
    selected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {s}.scheme_instances (
    scheme_id TEXT PRIMARY KEY,
    rules_digest TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    rules JSONB
);
CREATE TABLE IF NOT EXISTS {s}.scheme_nodes (
    scheme_id TEXT NOT NULL,
    tree TEXT NOT NULL,
    id TEXT NOT NULL,
    parent TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    path TEXT[] NOT NULL,
    PRIMARY KEY (scheme_id, tree, id)
);
CREATE INDEX IF NOT EXISTS scheme_nodes_path ON {s}.scheme_nodes USING GIN (path);
CREATE INDEX IF NOT EXISTS scheme_nodes_parent ON {s}.scheme_nodes (scheme_id, tree, parent, id);
CREATE INDEX IF NOT EXISTS scheme_nodes_kind ON {s}.scheme_nodes (scheme_id, tree, kind);
CREATE TABLE IF NOT EXISTS {s}.scheme_entries (
    scheme_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    effect TEXT NOT NULL CHECK (effect IN ('permit', 'deny')),
    p_node TEXT NOT NULL,
    a_node TEXT NOT NULL,
    r_node TEXT NOT NULL,
    disabled BOOLEAN NOT NULL DEFAULT FALSE,
    version INTEGER NOT NULL DEFAULT 1,
    issued_by TEXT NOT NULL DEFAULT '',
    issued_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    message_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (scheme_id, entry_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS scheme_entries_content ON {s}.scheme_entries (scheme_id, effect, p_node, a_node, r_node)
    WHERE expires_at IS NULL;
CREATE INDEX IF NOT EXISTS scheme_entries_p ON {s}.scheme_entries (scheme_id, p_node);
CREATE INDEX IF NOT EXISTS scheme_entries_r ON {s}.scheme_entries (scheme_id, r_node);
CREATE INDEX IF NOT EXISTS scheme_entries_expiry ON {s}.scheme_entries (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS scheme_entries_message ON {s}.scheme_entries (message_id, p_node) WHERE message_id <> '';
CREATE TABLE IF NOT EXISTS {s}.state (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    version BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS {s}.scheme_objects (
    scheme_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    relations JSONB NOT NULL DEFAULT '[]',
    PRIMARY KEY (scheme_id, object_id)
);
CREATE TABLE IF NOT EXISTS {s}.scheme_sources (
    scheme_id TEXT PRIMARY KEY,
    source_scheme_id TEXT NOT NULL,
    rules JSONB NOT NULL,
    digest TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {s}.scheme_merges (
    scheme_id TEXT PRIMARY KEY,
    source_scheme_id TEXT NOT NULL,
    from_digest TEXT NOT NULL,
    to_digest TEXT NOT NULL,
    conflicts JSONB NOT NULL DEFAULT '[]',
    unrestored JSONB NOT NULL DEFAULT '[]',
    merged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_again JSONB NOT NULL DEFAULT '[]',
    merged_by TEXT NOT NULL DEFAULT 'portal',
    auth_diff JSONB,
    regenerated BOOLEAN NOT NULL DEFAULT TRUE
);
"""


def _table_ddl(ddl: str) -> dict[str, str]:
    """``ddl`` split per table: each ``CREATE TABLE`` with the statements after it (its indexes)."""
    marker = "CREATE TABLE IF NOT EXISTS {s}."
    chunks = {}
    for chunk in ddl.split(marker)[1:]:
        chunks[chunk.split(" ", 1)[0]] = marker + chunk
    return chunks
# The tables holding one scheme's instance.
INSTANCE_TABLES = ("scheme_nodes", "scheme_entries", "scheme_objects", "scheme_instances")

_ENTRY_COLUMNS = ("entry_id, effect, p_node, a_node, r_node, disabled, version, issued_by,"
                  " extract(epoch FROM issued_at) AS issued_at, extract(epoch FROM expires_at) AS expires_at, message_id")
# Content of a lasting row: what makes it unique.
Content = tuple[str, str, str, str]
# One relation of an object: (relation, principal, target).
Relation = tuple[str, str, str]


def new_entry_id() -> str:
    return "entry_" + secrets.token_hex(12)


def entry_from_row(row: Any) -> Entry:
    return Entry(
        entry_id=row["entry_id"], effect=row["effect"], p=row["p_node"], a=row["a_node"], r=row["r_node"],
        disabled=bool(row["disabled"]), version=int(row["version"]), issued_by=row["issued_by"],
        issued_at=float(row["issued_at"] or 0.0),
        expires_at=None if row["expires_at"] is None else float(row["expires_at"]), message_id=row["message_id"],
    )


@dataclass(frozen=True)
class Snapshot:
    """What one decision reads in one statement.

    ``version`` is the instance version number; ``scheme_id`` and
    ``scheme_version`` name the scheme whose instance the paths come from
    (the active one when asked so); ``principal`` is the principal's stored
    path and ``resources`` the stored paths of the requested resources.
    """

    version: int
    scheme_id: str
    scheme_version: int
    principal: list[str] | None
    resources: dict[str, list[str]]


class EntryConflict(ValueError):
    """The row was edited since it was read (``current_version``), or another row already has this content."""

    def __init__(self, message: str, current_version: int | None = None, *, object_id: str = "",
                 expected_version: int | None = None) -> None:
        super().__init__(message)
        self.current_version = current_version
        self.object_id = object_id
        self.expected_version = expected_version


class _Shared:
    """What every Store of one database shares: the connection pool and whether the tables exist."""

    def __init__(self, database, extra: tuple[tuple[str, str], ...]) -> None:
        self.database = database
        self.extra = extra
        self.ready = False
        self.lock = threading.Lock()


class Store:
    """The instance of one scheme (``scheme_id``) in PostgreSQL.

    ``extra_ddl`` lets the layers above keep their own tables in the same
    schema; ``{s}`` in it stands for the schema name.  ``scoped`` gives the
    Store of another scheme on the same database.
    """

    def __init__(self, database=None, *, scheme_id: str = DEFAULT_SCHEME, extra_ddl: Sequence[tuple[str, str]] = (),
                 _shared: _Shared | None = None) -> None:
        self._shared = _shared or _Shared(database, tuple(extra_ddl))
        self.scheme_id = scheme_id

    def scoped(self, scheme_id: str) -> "Store":
        return self if scheme_id == self.scheme_id else Store(scheme_id=scheme_id, _shared=self._shared)

    @property
    def database(self):
        shared = self._shared
        if shared.database is None:
            with shared.lock:
                if shared.database is None:
                    from management_common.portal_database import portal_database

                    shared.database = portal_database(schema=SCHEMA)
        return shared.database

    @property
    def schema(self) -> str:
        return self.database.schema

    def sql(self, statement: str) -> str:
        return statement.replace("{s}", '"' + self.schema + '"')

    def ensure(self) -> None:
        """Create missing tables on first use; existing tables are not touched (no DDL locks on live tables)."""
        shared = self._shared
        if shared.ready:
            return
        database = self.database
        with shared.lock:
            if shared.ready:
                return
            tables = {**_table_ddl(_DDL), **dict(shared.extra)}
            with database.transaction() as connection:
                present = {row[0] for row in connection.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = %s AND tablename = ANY(%s)",
                    (self.schema, list(tables)),
                ).fetchall()}
                if present != set(tables):
                    # Only the missing tables: an index statement on a live table would wait for its writers.
                    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (self.schema + ":ddl",))
                    connection.executescript(self.sql("".join(ddl for name, ddl in tables.items() if name not in present)))
            shared.ready = True

    @contextmanager
    def transaction(self, connection=None) -> Iterator[Any]:
        """The caller's connection when given (joining its transaction), else a new transaction."""
        self.ensure()
        if connection is not None:
            yield connection
            return
        with self.database.transaction() as own:
            yield own

    @contextmanager
    def read(self, connection=None) -> Iterator[Any]:
        self.ensure()
        if connection is not None:
            yield connection
            return
        with self.database.read() as own:
            yield own

    def execute(self, connection, statement: str, parameters: Sequence[Any] = ()):
        return connection.execute(self.sql(statement), tuple(parameters))

    # -- reads -----------------------------------------------------------------------

    def snapshot(self, principal: str, resources: Sequence[str], *, active: bool = False) -> Snapshot:
        """In one statement: the version number, the principal's stored path and the stored paths of ``resources``.

        The paths come from this Store's scheme, or with ``active`` from the
        scheme the permission system uses, whichever it is now.
        """
        with self.read() as connection:
            row = self.execute(
                connection,
                "WITH chosen AS (SELECT CASE WHEN %s THEN coalesce((SELECT scheme_id FROM {s}.active_scheme), %s)"
                "   ELSE %s END AS scheme_id)"
                " SELECT chosen.scheme_id,"
                " coalesce((SELECT version FROM {s}.schemes s WHERE s.scheme_id = chosen.scheme_id), 0) AS scheme_version,"
                " coalesce((SELECT version FROM {s}.state), 0) AS version,"
                " (SELECT path FROM {s}.scheme_nodes n WHERE n.scheme_id = chosen.scheme_id AND tree = 'P' AND id = %s) AS principal,"
                " (SELECT coalesce(json_object_agg(id, path), '{}'::json) FROM {s}.scheme_nodes n"
                "   WHERE n.scheme_id = chosen.scheme_id AND tree = 'R' AND id = ANY(%s)) AS resources"
                " FROM chosen",
                (active, DEFAULT_SCHEME, self.scheme_id, principal, list(resources)),
            ).fetchone()
        return Snapshot(int(row["version"]), row["scheme_id"], int(row["scheme_version"]), row["principal"],
                        dict(row["resources"]))

    def version(self, connection=None) -> int:
        with self.read(connection) as connection:
            row = self.execute(connection, "SELECT version FROM {s}.state").fetchone()
        return int(row["version"]) if row is not None else 0

    def entries_for(self, p_nodes: Sequence[str]) -> list[Entry]:
        """The enabled rows on ``p_nodes``: what a decision for a principal on them reads."""
        with self.read() as connection:
            return self.entries(connection, where="p_node = ANY(%s) AND NOT disabled", parameters=(list(p_nodes),))

    def entries(self, connection, *, where: str = "TRUE", parameters: Sequence[Any] = (), limit: int | None = None,
                offset: int = 0, order: str = "entry_id") -> list[Entry]:
        statement = f"SELECT {_ENTRY_COLUMNS} FROM {{s}}.scheme_entries WHERE scheme_id = %s AND ({where}) ORDER BY {order}"
        if limit is not None:
            statement += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        rows = self.execute(connection, statement, (self.scheme_id, *parameters)).fetchall()
        return [entry_from_row(row) for row in rows]

    def count(self, connection, *, where: str = "TRUE", parameters: Sequence[Any] = ()) -> int:
        return int(self.execute(connection, f"SELECT count(*) FROM {{s}}.scheme_entries WHERE scheme_id = %s AND ({where})",
                                (self.scheme_id, *parameters)).fetchone()[0])

    def contents(self, connection) -> dict[Content, bool]:
        """Every lasting row's content and whether it is disabled."""
        rows = self.execute(connection, "SELECT effect, p_node, a_node, r_node, disabled FROM {s}.scheme_entries"
                                        " WHERE scheme_id = %s AND expires_at IS NULL", (self.scheme_id,)).fetchall()
        return {(row["effect"], row["p_node"], row["a_node"], row["r_node"]): bool(row["disabled"]) for row in rows}

    def paths(self, tree: str, ids: Iterable[str], connection=None) -> dict[str, list[str]]:
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        with self.read(connection) as connection:
            rows = self.execute(connection, "SELECT id, path FROM {s}.scheme_nodes WHERE scheme_id = %s AND tree = %s AND id = ANY(%s)",
                                (self.scheme_id, tree, ids)).fetchall()
        return {row["id"]: list(row["path"]) for row in rows}

    def nodes(self, connection) -> dict[tuple[str, str], tuple[str, str, tuple[str, ...]]]:
        """Every stored element: ``(tree, id) -> (parent, kind, path)``."""
        rows = self.execute(connection, "SELECT tree, id, parent, kind, path FROM {s}.scheme_nodes WHERE scheme_id = %s",
                            (self.scheme_id,)).fetchall()
        return {(row["tree"], row["id"]): (row["parent"], row["kind"], tuple(row["path"])) for row in rows}

    def under(self, tree: str, node: str, connection=None) -> list[str]:
        """The stored elements at or under ``node``."""
        with self.read(connection) as connection:
            rows = self.execute(connection, "SELECT id FROM {s}.scheme_nodes WHERE scheme_id = %s AND tree = %s AND path @> ARRAY[%s]::text[]",
                                (self.scheme_id, tree, node)).fetchall()
        return [row["id"] for row in rows]

    def children(self, tree: str, parent: str, *, after: str = "", limit: int = 200, connection=None) -> list[dict[str, Any]]:
        with self.read(connection) as connection:
            rows = self.execute(
                connection, "SELECT id, kind, path FROM {s}.scheme_nodes WHERE scheme_id = %s AND tree = %s AND parent = %s AND id > %s"
                " ORDER BY id LIMIT %s", (self.scheme_id, tree, parent, after, limit),
            ).fetchall()
        return [{"id": row["id"], "kind": row["kind"], "path": list(row["path"])} for row in rows]

    def search(self, tree: str, query: str, *, limit: int) -> list[dict[str, Any]]:
        """Stored elements whose ID contains ``query`` (case-insensitive), shortest IDs first."""
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self.read() as connection:
            rows = self.execute(
                connection, "SELECT id, kind, path FROM {s}.scheme_nodes WHERE scheme_id = %s AND tree = %s AND id ILIKE %s"
                " ORDER BY length(id), id LIMIT %s", (self.scheme_id, tree, pattern, limit)).fetchall()
        return [{"id": row["id"], "kind": row["kind"], "path": list(row["path"])} for row in rows]

    def covered(self, permit: Iterable[str], deny: Iterable[str], *, kind: str = "", ids: Sequence[str] | None = None) -> list[str]:
        """Stored R elements under a ``permit`` node and under no ``deny`` node; optionally of one kind or among ``ids``."""
        permit, deny = sorted(permit), sorted(deny)
        if not permit:
            return []
        clauses, parameters = ["scheme_id = %s", "tree = 'R'", "path && %s::text[]"], [self.scheme_id, permit]
        if deny:
            clauses.append("NOT path && %s::text[]")
            parameters.append(deny)
        if kind:
            clauses.append("kind = %s")
            parameters.append(kind)
        if ids is not None:
            clauses.append("id = ANY(%s)")
            parameters.append(list(ids))
        with self.read() as connection:
            rows = self.execute(connection, f"SELECT id FROM {{s}}.scheme_nodes WHERE {' AND '.join(clauses)} ORDER BY id", parameters).fetchall()
        return [row["id"] for row in rows]

    def instance(self, connection=None) -> dict[str, Any] | None:
        """``{rules_digest, generated_at}`` of this scheme's instance, or None before it is generated."""
        with self.read(connection) as connection:
            row = self.execute(connection, "SELECT rules_digest, extract(epoch FROM generated_at) AS generated_at"
                                           " FROM {s}.scheme_instances WHERE scheme_id = %s", (self.scheme_id,)).fetchone()
        return None if row is None else {"rules_digest": row["rules_digest"], "generated_at": float(row["generated_at"])}

    def basis(self, connection=None) -> dict[str, Any] | None:
        """The whole rules this scheme's instance was generated from, or None (no instance, or saved before they were kept)."""
        with self.read(connection) as connection:
            row = self.execute(connection, "SELECT rules FROM {s}.scheme_instances WHERE scheme_id = %s",
                               (self.scheme_id,)).fetchone()
        return None if row is None or row["rules"] is None else dict(row["rules"])

    def objects(self, connection, object_ids: Sequence[str] | None = None, *, lock: bool = False) -> dict[str, frozenset[Relation]]:
        """The objects the instance knows (all, or those among ``object_ids``) with the relations it applied."""
        statement, parameters = "SELECT object_id, relations FROM {s}.scheme_objects WHERE scheme_id = %s", [self.scheme_id]
        if object_ids is not None:
            statement += " AND object_id = ANY(%s)"
            parameters.append(list(object_ids))
        if lock:
            statement += " FOR UPDATE"
        return {row["object_id"]: frozenset(tuple(item) for item in row["relations"])
                for row in self.execute(connection, statement, parameters).fetchall()}

    # -- writes (inside a transaction) --------------------------------------------------

    def bump(self, connection) -> None:
        """Add one to the version number, in the caller's transaction: every process drops its decision cache."""
        self.execute(connection, "INSERT INTO {s}.state (singleton, version) VALUES (TRUE, 1)"
                                 " ON CONFLICT (singleton) DO UPDATE SET version = {s}.state.version + 1")

    def place(self, connection, tree: str, element: str, parent_path: Sequence[str], *, kind: str = "") -> bool:
        """Put ``element`` under the last node of ``parent_path``; its stored descendants move with it.

        Returns whether anything changed.
        """
        if element in parent_path:
            raise ValueError(f"{element} cannot be placed under its own descendant")
        path, parent = [*parent_path, element], parent_path[-1]
        row = self.execute(connection, "SELECT parent, path, kind FROM {s}.scheme_nodes WHERE scheme_id = %s AND tree = %s AND id = %s"
                           " FOR UPDATE", (self.scheme_id, tree, element)).fetchone()
        if row is not None and list(row["path"]) == path and row["parent"] == parent and row["kind"] == kind:
            return False
        if row is None:
            self.execute(connection, "INSERT INTO {s}.scheme_nodes (scheme_id, tree, id, parent, kind, path) VALUES (%s, %s, %s, %s, %s, %s)",
                         (self.scheme_id, tree, element, parent, kind, path))
        else:
            self.execute(connection, "UPDATE {s}.scheme_nodes SET parent = %s, kind = %s, path = %s WHERE scheme_id = %s AND tree = %s AND id = %s",
                         (parent, kind, path, self.scheme_id, tree, element))
        # Descendants stored before this element (or under its old place) follow it.
        self.execute(
            connection,
            "UPDATE {s}.scheme_nodes SET path = %s::text[] || path[array_position(path, %s) + 1:]"
            " WHERE scheme_id = %s AND tree = %s AND path @> ARRAY[%s]::text[] AND id <> %s"
            " AND path[1:%s] IS DISTINCT FROM %s::text[]",
            (path, element, self.scheme_id, tree, element, element, len(path), path),
        )
        if tree == "P":
            self.bump(connection)
        return True

    def remove(self, connection, tree: str, element: str) -> list[Entry]:
        """Take an element out of its tree with every row naming it on that side; its stored children go up to its parent.

        Returns the removed rows.
        """
        row = self.execute(connection, "DELETE FROM {s}.scheme_nodes WHERE scheme_id = %s AND tree = %s AND id = %s RETURNING path",
                           (self.scheme_id, tree, element)).fetchone()
        if row is not None:
            above = list(row["path"])[:-1]
            self.execute(
                connection,
                "UPDATE {s}.scheme_nodes SET path = %s::text[] || path[array_position(path, %s) + 1:],"
                " parent = CASE WHEN parent = %s THEN %s ELSE parent END"
                " WHERE scheme_id = %s AND tree = %s AND path @> ARRAY[%s]::text[]",
                (above, element, element, above[-1], self.scheme_id, tree, element),
            )
        column = "p_node" if tree == "P" else "r_node"
        removed = self.delete(connection, where=f"{column} = %s", parameters=(element,))
        if tree == "P" and row is not None:
            self.bump(connection)
        return removed

    def add(self, connection, entries: Iterable[Entry]) -> list[Entry]:
        """Insert rows; a lasting row whose content is already stored is left as it is.  Returns the inserted rows."""
        entries = list(entries)
        if not entries:
            return []
        rows = [(entry.entry_id, entry.effect, entry.p, entry.a, entry.r, entry.disabled, entry.version, entry.issued_by,
                 entry.issued_at or None, entry.expires_at, entry.message_id) for entry in entries]
        inserted = {row["entry_id"] for row in self.execute(
            connection,
            "INSERT INTO {s}.scheme_entries (scheme_id, entry_id, effect, p_node, a_node, r_node, disabled, version, issued_by,"
            " issued_at, expires_at, message_id)"
            " SELECT %s, e.entry_id, e.effect, e.p_node, e.a_node, e.r_node, e.disabled, e.version, e.issued_by,"
            "  to_timestamp(e.issued_at), to_timestamp(e.expires_at), e.message_id"
            " FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::text[], %s::boolean[], %s::int[], %s::text[],"
            "  %s::float8[], %s::float8[], %s::text[])"
            "  AS e(entry_id, effect, p_node, a_node, r_node, disabled, version, issued_by, issued_at, expires_at, message_id)"
            " ON CONFLICT DO NOTHING RETURNING entry_id",
            [self.scheme_id, *(list(column) for column in zip(*rows))],
        ).fetchall()}
        added = [entry for entry in entries if entry.entry_id in inserted]
        if added:
            self.bump(connection)
        return added

    def add_contents(self, connection, contents: Iterable[Content]) -> int:
        """Insert lasting rows with new IDs for contents not stored yet; returns how many were inserted."""
        return len(self.add(connection, [Entry(new_entry_id(), *content) for content in dict.fromkeys(contents)]))

    def delete(self, connection, *, where: str, parameters: Sequence[Any] = ()) -> list[Entry]:
        """Delete the rows matching ``where``; returns them."""
        rows = self.execute(connection, f"DELETE FROM {{s}}.scheme_entries WHERE scheme_id = %s AND ({where}) RETURNING {_ENTRY_COLUMNS}",
                            (self.scheme_id, *parameters)).fetchall()
        removed = [entry_from_row(row) for row in rows]
        if removed:
            self.bump(connection)
        return removed

    def delete_contents(self, connection, contents: Iterable[Content]) -> list[Entry]:
        """Delete the lasting rows with these contents, whatever their IDs."""
        contents = list(dict.fromkeys(contents))
        if not contents:
            return []
        effects, ps, as_, rs = (list(column) for column in zip(*contents))
        return self.delete(
            connection, where="expires_at IS NULL AND (effect, p_node, a_node, r_node) IN"
            " (SELECT * FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[]))", parameters=(effects, ps, as_, rs))

    def update(self, connection, entry_id: str, *, expected_version: int | None, effect: str, p: str, a: str, r: str,
               disabled: bool) -> Entry:
        """Change one lasting row in place, compared with ``expected_version`` when given; raises KeyError or EntryConflict."""
        current = self.execute(connection, "SELECT version, expires_at IS NOT NULL AS temporary, p_node FROM {s}.scheme_entries"
                                           " WHERE scheme_id = %s AND entry_id = %s FOR UPDATE", (self.scheme_id, entry_id)).fetchone()
        if current is None:
            raise KeyError(entry_id)
        if current["temporary"]:
            raise EntryConflict(f"{entry_id} 是临时凭证，只能撤销")
        if expected_version is not None and int(current["version"]) != expected_version:
            raise EntryConflict("表项已被修改", int(current["version"]), object_id=entry_id, expected_version=expected_version)
        clash = self.execute(connection, "SELECT entry_id FROM {s}.scheme_entries WHERE scheme_id = %s AND expires_at IS NULL"
                                         " AND effect = %s AND p_node = %s AND a_node = %s AND r_node = %s AND entry_id <> %s",
                             (self.scheme_id, effect, p, a, r, entry_id)).fetchone()
        if clash is not None:
            raise EntryConflict(f"已有相同内容的表项 {clash['entry_id']}")
        row = self.execute(
            connection, f"UPDATE {{s}}.scheme_entries SET effect = %s, p_node = %s, a_node = %s, r_node = %s, disabled = %s,"
            f" version = version + 1 WHERE scheme_id = %s AND entry_id = %s RETURNING {_ENTRY_COLUMNS}",
            (effect, p, a, r, disabled, self.scheme_id, entry_id)).fetchone()
        self.bump(connection)
        return entry_from_row(row)

    def set_objects(self, connection, objects: Mapping[str, Iterable[Relation]]) -> None:
        """Record objects as known to the instance, with the relations it applied."""
        if objects:
            self.execute(
                connection, "INSERT INTO {s}.scheme_objects (scheme_id, object_id, relations)"
                " SELECT %s, o.id, o.relations::jsonb FROM unnest(%s::text[], %s::text[]) AS o(id, relations)"
                " ON CONFLICT (scheme_id, object_id) DO UPDATE SET relations = EXCLUDED.relations",
                (self.scheme_id, list(objects), [json.dumps(sorted(relations)) for relations in objects.values()]))

    def forget_objects(self, connection, object_ids: Sequence[str]) -> dict[str, frozenset[Relation]]:
        """Forget objects; returns those that were known, with their relations."""
        rows = self.execute(connection, "DELETE FROM {s}.scheme_objects WHERE scheme_id = %s AND object_id = ANY(%s)"
                                        " RETURNING object_id, relations", (self.scheme_id, list(object_ids))).fetchall()
        return {row["object_id"]: frozenset(tuple(item) for item in row["relations"]) for row in rows}

    def set_instance(self, connection, rules_digest: str, rules: Mapping[str, Any] | None) -> None:
        """Record the rules the instance is now generated from: their digest and, when known, the whole rules."""
        self.execute(connection, "INSERT INTO {s}.scheme_instances (scheme_id, rules_digest, generated_at, rules)"
                                 " VALUES (%s, %s, now(), %s::jsonb) ON CONFLICT (scheme_id) DO UPDATE SET"
                                 " rules_digest = EXCLUDED.rules_digest, generated_at = now(), rules = EXCLUDED.rules",
                     (self.scheme_id, rules_digest, None if rules is None else json.dumps(rules, ensure_ascii=False)))

    def set_basis(self, connection, rules: Mapping[str, Any], *, replace: bool = False) -> None:
        """Keep the whole rules of an instance saved before they were kept (its digest already matches them);
        ``replace`` moves a kept basis on to rules the instance is equally generated from (same digest)."""
        self.execute(connection, "UPDATE {s}.scheme_instances SET rules = %s::jsonb WHERE scheme_id = %s"
                                 + ("" if replace else " AND rules IS NULL"),
                     (json.dumps(rules, ensure_ascii=False), self.scheme_id))

    def clear(self, connection, *, keep_temporary: bool) -> None:
        """Empty this scheme's instance (temporary credentials stay when ``keep_temporary``)."""
        for table in INSTANCE_TABLES:
            if table == "scheme_entries" and keep_temporary:
                self.execute(connection, "DELETE FROM {s}.scheme_entries WHERE scheme_id = %s AND expires_at IS NULL", (self.scheme_id,))
            else:
                self.execute(connection, f"DELETE FROM {{s}}.{table} WHERE scheme_id = %s", (self.scheme_id,))
        self.bump(connection)

    def drop(self, connection) -> None:
        """Delete this scheme's instance (the scheme itself is deleted)."""
        for table in INSTANCE_TABLES:
            self.execute(connection, f"DELETE FROM {{s}}.{table} WHERE scheme_id = %s", (self.scheme_id,))

    def copy_to(self, connection, target: str) -> None:
        """Copy this scheme's instance — elements, lasting rows, relations — to scheme ``target``, keeping row IDs."""
        self.execute(connection, "INSERT INTO {s}.scheme_nodes (scheme_id, tree, id, parent, kind, path)"
                                 " SELECT %s, tree, id, parent, kind, path FROM {s}.scheme_nodes WHERE scheme_id = %s",
                     (target, self.scheme_id))
        self.execute(connection, "INSERT INTO {s}.scheme_entries (scheme_id, entry_id, effect, p_node, a_node, r_node, disabled, version)"
                                 " SELECT %s, entry_id, effect, p_node, a_node, r_node, disabled, version FROM {s}.scheme_entries"
                                 " WHERE scheme_id = %s AND expires_at IS NULL", (target, self.scheme_id))
        self.execute(connection, "INSERT INTO {s}.scheme_objects (scheme_id, object_id, relations)"
                                 " SELECT %s, object_id, relations FROM {s}.scheme_objects WHERE scheme_id = %s",
                     (target, self.scheme_id))

    def insert_nodes(self, connection, rows: Sequence[tuple[str, str, str, str, Sequence[str]]]) -> None:
        """Insert ``(tree, id, parent, kind, path)`` rows in one statement."""
        if rows:
            self.execute(
                connection,
                "INSERT INTO {s}.scheme_nodes (scheme_id, tree, id, parent, kind, path)"
                " SELECT %s, t, i, p, k, string_to_array(x, chr(31))"
                " FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::text[]) AS n(t, i, p, k, x)",
                (self.scheme_id, [row[0] for row in rows], [row[1] for row in rows], [row[2] for row in rows],
                 [row[3] for row in rows], [chr(31).join(row[4]) for row in rows]))
            if any(row[0] == "P" for row in rows):
                self.bump(connection)

    def update_nodes(self, connection, rows: Sequence[tuple[str, str, str, str, Sequence[str]]]) -> None:
        """Set parent, kind and path of stored ``(tree, id, parent, kind, path)`` rows in one statement."""
        if rows:
            self.execute(
                connection,
                "UPDATE {s}.scheme_nodes n SET parent = u.p, kind = u.k, path = string_to_array(u.x, chr(31))"
                " FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::text[]) AS u(t, i, p, k, x)"
                " WHERE n.scheme_id = %s AND n.tree = u.t AND n.id = u.i",
                ([row[0] for row in rows], [row[1] for row in rows], [row[2] for row in rows], [row[3] for row in rows],
                 [chr(31).join(row[4]) for row in rows], self.scheme_id))
            if any(row[0] == "P" for row in rows):
                self.bump(connection)

    def delete_nodes(self, connection, keys: Sequence[tuple[str, str]]) -> int:
        """Delete stored elements ``(tree, id)`` with every row naming them on that side; returns the rows deleted."""
        if not keys:
            return 0
        trees, ids = [key[0] for key in keys], [key[1] for key in keys]
        self.execute(connection, "DELETE FROM {s}.scheme_nodes n USING unnest(%s::text[], %s::text[]) AS d(t, i)"
                                 " WHERE n.scheme_id = %s AND n.tree = d.t AND n.id = d.i", (trees, ids, self.scheme_id))
        if "P" in trees:
            self.bump(connection)
        removed = self.delete(connection, where="p_node = ANY(%s) OR r_node = ANY(%s)",
                              parameters=([key[1] for key in keys if key[0] == "P"], [key[1] for key in keys if key[0] == "R"]))
        return len(removed)


__all__ = ["Content", "DEFAULT_SCHEME", "DENY", "EntryConflict", "INSTANCE_TABLES", "PERMIT", "Relation", "SCHEMA", "Snapshot",
           "Store", "entry_from_row", "new_entry_id"]
