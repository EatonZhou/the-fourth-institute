"""PostgreSQL access for Telepus state.

Telepus keeps its bridge state in the Portal PostgreSQL database, inside its
own schema (site setting ``deployment.telepus.database_schema``, default
``telepus``). The connection URL is the Portal database endpoint.

``connect`` returns a small connection object that the stores use the same way
in every call: ``execute`` with ``?`` placeholders, rows readable by column name
or position, and a ``with`` block that commits or rolls back and then closes.
``BEGIN IMMEDIATE`` takes a transaction-scoped advisory lock, so writers that
must not overlap (schema creation) run one at a time.
"""

from __future__ import annotations

import re
import zlib
from typing import Any, Iterable, Sequence

import psycopg

from .runtime import setting

DEFAULT_SCHEMA = "telepus"
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def schema_name(value: str | None = None) -> str:
    schema = str(value or setting("database_schema") or DEFAULT_SCHEMA).strip()
    if not _SCHEMA_RE.fullmatch(schema):
        raise ValueError(f"invalid Telepus database schema: {schema!r}")
    return schema


def database_url() -> str:
    # Tests run against the isolated server the Portal test suite selects.
    from management_common.portal_database import portal_database_url
    return portal_database_url()


class Row(dict):
    """A result row readable by column name and by position."""

    def __init__(self, names: Sequence[str], values: Sequence[Any]):
        super().__init__(zip(names, values))
        self._values = tuple(values)

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _row_factory(cursor: psycopg.Cursor) -> Any:
    names = [column.name for column in cursor.description or ()]
    return lambda values: Row(names, values)


def _postgres_sql(sql: str) -> str:
    # Telepus SQL uses ``?`` placeholders and no literal ``%`` or ``?``.
    return sql.replace("%", "%%").replace("?", "%s")


class Connection:
    def __init__(self, schema: str, lock_name: str):
        self.schema = schema_name(schema)
        self._lock_key = zlib.crc32(f"telepus:{self.schema}:{lock_name}".encode()) - 2**31
        self._conn = psycopg.connect(database_url(), row_factory=_row_factory)
        self._conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
        self._conn.execute(f'SET search_path TO "{self.schema}"')

    def execute(self, sql: str, params: Iterable[Any] = ()) -> psycopg.Cursor:
        if sql.strip().upper() == "BEGIN IMMEDIATE":
            return self._conn.execute("SELECT pg_advisory_xact_lock(%s)", (self._lock_key,))
        values = tuple(params)
        if values:
            return self._conn.execute(_postgres_sql(sql), values)
        return self._conn.execute(sql)

    def executescript(self, script: str) -> None:
        self._conn.execute(script)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def column_names(self, table: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s",
            (self.schema, table),
        ).fetchall()
        return {str(row[0]) for row in rows}

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()


def connect(schema: str, lock_name: str = "store") -> Connection:
    return Connection(schema, lock_name)
