from __future__ import annotations

import hashlib
import os
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from psycopg import Error as DatabaseError
from psycopg import IntegrityError, InterfaceError, OperationalError

from management_common.postgres_database import PostgresDatabase

def _database(target: object) -> PostgresDatabase:
    if isinstance(target, PostgresDatabase):
        return target
    owned = getattr(target, "database", None)
    if isinstance(owned, PostgresDatabase):
        return owned
    raise TypeError("connect() requires a PostgresDatabase or a store that owns one")


class _ConnectionContext(AbstractContextManager[Any]):
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database
        self._context = database.connect()
        self.connection: Any = None

    def __enter__(self) -> Any:
        self.connection = self._context.__enter__()
        return self.connection

    def __exit__(self, exc_type, exc, traceback) -> bool | None:
        try:
            return self._context.__exit__(exc_type, exc, traceback)
        finally:
            self.database.close()


def connect(target: object) -> _ConnectionContext:
    """Open a test connection through the same PostgreSQL boundary as Portal."""

    return _ConnectionContext(_database(target))


class QueryGuard:
    def __init__(
        self,
        connection: Any,
        *,
        forbidden: tuple[str, ...] = (),
        statements: list[str] | None = None,
    ) -> None:
        self.connection = connection
        self.forbidden = tuple(item.casefold() for item in forbidden)
        self.statements = statements

    def execute(self, statement: str, *args: Any, **kwargs: Any) -> Any:
        normalized = " ".join(str(statement).casefold().split())
        if self.statements is not None:
            self.statements.append(normalized)
        matched = next((item for item in self.forbidden if item in normalized), "")
        if matched:
            raise AssertionError(f"forbidden query fragment: {matched}")
        return self.connection.execute(statement, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)


def register_owned_workspace(connection: Any, resident_id: str) -> str:
    """Register the Workspace a live Resident row owns, in the caller's transaction.

    A live ``residents`` row must name its owned Workspace; tests that write
    rows directly register one with a stable id derived from the Resident.
    """
    from management_portal.resources.workspace_store import get_workspace_store

    workspace_id = "workspace_" + hashlib.sha256(resident_id.encode()).hexdigest()[:12]
    path = Path(os.environ["PORTAL_LINEAGE_FOLDER_ROOT"]) / "owned" / resident_id / "workspace"
    return get_workspace_store().ensure(workspace_id, str(path), connection=connection)


def guard_queries(
    connection: Any,
    *,
    forbidden: tuple[str, ...] = (),
    statements: list[str] | None = None,
) -> QueryGuard:
    return QueryGuard(connection, forbidden=forbidden, statements=statements)


__all__ = (
    "DatabaseError",
    "IntegrityError",
    "InterfaceError",
    "OperationalError",
    "connect",
    "guard_queries",
    "register_owned_workspace",
)
