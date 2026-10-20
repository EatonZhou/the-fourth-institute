"""Registry documents owned by ResourceService, stored in PostgreSQL.

Portal keeps everything it depends on in the database
(decision_86aed2f0e8c7). Every document belongs to a collection
(``resident``, ``repo`` ...) and carries the collection revision at
which it was last written, so caches refresh by reading only what changed.
Dev and Release share one registry.
"""
from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from typing import Any

from management_common.json_columns import json_text, json_value
from management_common.format_tags import schema_matches
from management_common.portal_database import portal_database

SCHEMA = "resource_registry"
SCHEMA_VERSION = "tfi.resource-registry.v2"

_DDL = (
    "CREATE TABLE registry_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    # Revisions come from one sequence so a rolled-back write never hands its
    # revision to a later commit; caches keyed by revision stay correct.
    "CREATE SEQUENCE registry_revision_seq",
    "CREATE TABLE registry_collections (collection TEXT PRIMARY KEY, revision BIGINT NOT NULL)",
    """
    CREATE TABLE registry_documents (
        collection TEXT NOT NULL,
        document_id TEXT NOT NULL,
        revision BIGINT NOT NULL,
        document_json JSONB NOT NULL,
        PRIMARY KEY (collection, document_id)
    )
    """,
    "CREATE INDEX registry_documents_revision_idx ON registry_documents(collection, revision)",
    "CREATE TABLE retired_identities (resource_id TEXT PRIMARY KEY)",
)
_TABLES = frozenset({"registry_meta", "registry_collections", "registry_documents", "retired_identities"})


def _encode(document: Mapping[str, Any]) -> str:
    return json_text(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RegistryWriter:
    """Changes made inside one registry transaction."""

    def __init__(self, connection: Any, collections: tuple[str, ...], schema: str) -> None:
        self._connection = connection
        self._schema = f'"{schema}"'
        self._schema_name = schema
        self._collections = collections
        self._changed: set[str] = set()
        self._revisions: dict[str, int] = {}
        self._previous: dict[str, int] = {}
        # True once the writer's own transaction committed; a writer nested in
        # an enclosing transaction leaves the commit (or rollback) to it.
        self.committed = False

    @property
    def connection(self) -> Any:
        """The open transaction the changes are written in, for records that must commit with them."""
        return self._connection

    def _check(self, collection: str) -> None:
        if collection not in self._collections:
            raise ValueError(f"registry collection {collection!r} is not locked by this transaction")

    def get(self, collection: str, document_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            f"SELECT document_json FROM {self._schema}.registry_documents WHERE collection=%s AND document_id=%s",
            (collection, document_id),
        ).fetchone()
        return None if row is None else json_value(row["document_json"])

    def get_many(self, collection: str, document_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = sorted({str(value) for value in document_ids if str(value)})
        if not ids:
            return {}
        rows = self._connection.execute(
            f"SELECT document_id, document_json FROM {self._schema}.registry_documents "
            "WHERE collection=%s AND document_id = ANY(%s)",
            (collection, ids),
        ).fetchall()
        return {str(row["document_id"]): json_value(row["document_json"]) for row in rows}

    def documents(self, collection: str) -> dict[str, dict[str, Any]]:
        """Every document of a collection, read under this transaction's lock."""
        rows = self._connection.execute(
            f"SELECT document_id, document_json FROM {self._schema}.registry_documents WHERE collection=%s ORDER BY document_id",
            (collection,),
        ).fetchall()
        return {str(row["document_id"]): json_value(row["document_json"]) for row in rows}

    def _next_revision(self, collection: str) -> int:
        if collection not in self._revisions:
            previous = self._connection.execute(
                f"SELECT revision FROM {self._schema}.registry_collections WHERE collection=%s", (collection,),
            ).fetchone()
            self._previous[collection] = int(previous["revision"]) if previous else 0
            row = self._connection.execute(
                f"INSERT INTO {self._schema}.registry_collections(collection, revision) "
                f"VALUES (%s, nextval('{self._schema}.registry_revision_seq')) "
                "ON CONFLICT(collection) DO UPDATE SET revision=excluded.revision "
                "RETURNING revision",
                (collection,),
            ).fetchone()
            self._revisions[collection] = int(row["revision"])
        return self._revisions[collection]

    def put(self, collection: str, document_id: str, document: Mapping[str, Any]) -> None:
        self._check(collection)
        from .retired_identity_boundary import check_registry_write
        check_registry_write(self._connection, self._schema_name, collection, str(document_id), document)
        revision = self._next_revision(collection)
        self._connection.execute(
            f"INSERT INTO {self._schema}.registry_documents(collection, document_id, revision, document_json) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT(collection, document_id) DO UPDATE "
            "SET revision=excluded.revision, document_json=excluded.document_json",
            (collection, str(document_id), revision, _encode(document)),
        )
        self._changed.add(collection)

    def delete(self, collection: str, document_id: str) -> bool:
        self._check(collection)
        deleted = self._connection.execute(
            f"DELETE FROM {self._schema}.registry_documents WHERE collection=%s AND document_id=%s",
            (collection, str(document_id)),
        ).rowcount
        if deleted:
            self._next_revision(collection)
            self._changed.add(collection)
        return bool(deleted)

    @property
    def revisions(self) -> dict[str, int]:
        """Collection revisions this transaction wrote."""
        return dict(self._revisions)

    def advances(self, collection: str, cached_revision: int) -> int | None:
        """The revision a cache at ``cached_revision`` may move to after this
        committed write, or ``None`` when the cache must refresh instead."""
        if not self.committed or collection not in self._revisions:
            return None
        if self._previous.get(collection) != cached_revision:
            return None
        return self._revisions[collection]


class RegistryStore:
    """One PostgreSQL schema of registry documents shared by every Portal process."""

    def __init__(self) -> None:
        self.database = portal_database(schema=SCHEMA)
        self._ready = False
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return

            def initialize(connection: Any) -> None:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':schema-init', 0))"
                )
                tables = {
                    str(row["table_name"])
                    for row in connection.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
                    ).fetchall()
                }
                if not tables:
                    for statement in _DDL:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO registry_meta(key, value) VALUES ('schema', %s)", (SCHEMA_VERSION,),
                    )
                    return
                row = connection.execute("SELECT value FROM registry_meta WHERE key='schema'").fetchone() \
                    if "registry_meta" in tables else None
                if tables != _TABLES or row is None or not schema_matches(row["value"], SCHEMA_VERSION):
                    raise RuntimeError("Resource registry database does not match its schema")

            self.database.write(initialize)
            self._ready = True

    def revision(self, collection: str) -> int:
        self._ensure()
        with self.database.read_current() as connection:
            row = connection.execute(
                "SELECT revision FROM registry_collections WHERE collection=%s", (collection,),
            ).fetchone()
        return int(row["revision"]) if row else 0

    def revisions(self, collections: Iterable[str]) -> dict[str, int]:
        names = sorted({str(value) for value in collections})
        self._ensure()
        with self.database.read_current() as connection:
            rows = connection.execute(
                "SELECT collection, revision FROM registry_collections WHERE collection = ANY(%s)", (names,),
            ).fetchall()
        found = {str(row["collection"]): int(row["revision"]) for row in rows}
        return {name: found.get(name, 0) for name in names}

    def get(self, collection: str, document_id: str) -> dict[str, Any] | None:
        self._ensure()
        with self.database.read_current() as connection:
            row = connection.execute(
                "SELECT document_json FROM registry_documents WHERE collection=%s AND document_id=%s",
                (collection, str(document_id)),
            ).fetchone()
        return None if row is None else json_value(row["document_json"])

    def get_many(self, collection: str, document_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = sorted({str(value) for value in document_ids if str(value)})
        if not ids:
            return {}
        self._ensure()
        with self.database.read_current() as connection:
            rows = connection.execute(
                "SELECT document_id, document_json FROM registry_documents "
                "WHERE collection=%s AND document_id = ANY(%s)",
                (collection, ids),
            ).fetchall()
        return {str(row["document_id"]): json_value(row["document_json"]) for row in rows}

    def document_revisions(self, collection: str, document_ids: Iterable[str]) -> dict[str, int]:
        """Read small revision keys before fetching changed document bodies."""
        ids = sorted({str(value) for value in document_ids if str(value)})
        if not ids:
            return {}
        self._ensure()
        with self.database.read_current() as connection:
            rows = connection.execute(
                "SELECT document_id, revision FROM registry_documents "
                "WHERE collection=%s AND document_id = ANY(%s)",
                (collection, ids),
            ).fetchall()
        return {str(row["document_id"]): int(row["revision"]) for row in rows}

    def snapshot(self, collection: str) -> tuple[int, dict[str, dict[str, Any]]]:
        """Every document of a collection and the revision they were read at."""
        self._ensure()
        with self.database.snapshot() as connection:
            row = connection.execute(
                "SELECT revision FROM registry_collections WHERE collection=%s", (collection,),
            ).fetchone()
            rows = connection.execute(
                "SELECT document_id, document_json FROM registry_documents WHERE collection=%s ORDER BY document_id",
                (collection,),
            ).fetchall()
        return (int(row["revision"]) if row else 0), {
            str(item["document_id"]): json_value(item["document_json"]) for item in rows
        }

    def snapshots(self, *collections: str) -> dict[str, tuple[int, dict[str, dict[str, Any]]]]:
        """Related collection revisions and contents in one committed snapshot."""
        self._ensure()
        with self.database.snapshot():
            return {name: self.snapshot(name) for name in collections}

    def changes_since(self, collection: str, revision: int) -> tuple[int, dict[str, dict[str, Any]], int]:
        """Documents written after ``revision``, the current revision and document count."""
        self._ensure()
        with self.database.snapshot() as connection:
            row = connection.execute(
                "SELECT revision FROM registry_collections WHERE collection=%s", (collection,),
            ).fetchone()
            rows = connection.execute(
                "SELECT document_id, document_json FROM registry_documents "
                "WHERE collection=%s AND revision > %s",
                (collection, int(revision)),
            ).fetchall()
            count = connection.execute(
                "SELECT count(*) AS n FROM registry_documents WHERE collection=%s", (collection,),
            ).fetchone()
        return (int(row["revision"]) if row else 0), {
            str(item["document_id"]): json_value(item["document_json"]) for item in rows
        }, int(count["n"])

    def documents(self, collection: str) -> dict[str, dict[str, Any]]:
        """Every document of a collection, ordered by document id."""
        return self.snapshot(collection)[1]

    def ids(self, collection: str) -> list[str]:
        self._ensure()
        with self.database.read_current() as connection:
            rows = connection.execute(
                "SELECT document_id FROM registry_documents WHERE collection=%s ORDER BY document_id", (collection,),
            ).fetchall()
        return [str(row["document_id"]) for row in rows]

    @contextmanager
    def writer(self, *collections: str, connection: Any = None) -> Iterator[RegistryWriter]:
        """A transaction that holds the write lock of each named collection."""
        names_set = {str(value) for value in collections}
        names = tuple(sorted(names_set))
        if not names:
            raise ValueError("registry transaction requires a collection")
        self._ensure()
        current = self.database._current_connection()
        outermost = connection is None and (current is None or not current.in_transaction)
        if connection is not None and not connection.in_transaction:
            raise ValueError('Borrowed registry writer requires an active owning transaction')
        context = self.database.transaction() if connection is None else nullcontext(connection)
        with context as connection:
            for name in names:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (self.database.schema + ":registry:" + name,),
                )
            writer = RegistryWriter(connection, names, self.database.schema)
            yield writer
        writer.committed = outermost


_STORE: RegistryStore | None = None
_STORE_LOCK = threading.Lock()


def get_registry_store() -> RegistryStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = RegistryStore()
        return _STORE


def reset_registry_store_for_tests() -> None:
    global _STORE
    with _STORE_LOCK:
        _STORE = None


REGISTRY_CHANGE_PREFIX = "registry:"


def registry_change_key(collection: str, document_id: str) -> str:
    """The change path under which a registry document write is announced."""
    return f"{REGISTRY_CHANGE_PREFIX}{collection}/{document_id}"


def parse_registry_change_key(value: object) -> tuple[str, str] | None:
    """``(collection, document_id)`` of a registry change path, else ``None``."""
    text = str(value or "").strip()
    if not text.startswith(REGISTRY_CHANGE_PREFIX):
        return None
    collection, separator, document_id = text[len(REGISTRY_CHANGE_PREFIX):].partition("/")
    if not collection or not separator or not document_id:
        return None
    return collection, document_id


def announce_registry_changes(
    collection: str,
    changes: Mapping[str, str],
    *,
    resource_type: str = "",
) -> None:
    """Tell ResourceService that registry documents changed (``{id: kind}``).

    Index projections also compare registry revisions on every reconcile, so a
    failed announcement only delays the index.
    """
    if not changes:
        return
    import logging

    from . import get_resource_service

    service = get_resource_service()
    for document_id, kind in sorted(changes.items()):
        try:
            service.notify_source_changed(
                path=registry_change_key(collection, document_id),
                resource_id=document_id if resource_type else "",
                resource_type=resource_type, kind=kind,
            )
        except Exception:  # noqa: BLE001 - the reconcile pass still sees the new revision
            logging.getLogger(__name__).warning(
                "registry change announcement failed for %s/%s", collection, document_id, exc_info=True,
            )
