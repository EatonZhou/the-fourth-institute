"""Stable runtime database types shared by Portal domain stores.

Portal persistence is PostgreSQL-only.  Domain modules import this contract
instead of depending on a legacy storage implementation or on psycopg's
concrete connection wrappers directly.
"""

from __future__ import annotations

try:
    from psycopg import Error as DatabaseError
    from psycopg import OperationalError as DatabaseOperationalError
except ImportError:  # pragma: no cover - orchestration-only system Python
    class DatabaseError(Exception):
        """Placeholder used until a runtime attempts to open PostgreSQL."""

    class DatabaseOperationalError(DatabaseError):
        pass

from .postgres_database import PostgresConnection as DatabaseConnection
from .postgres_database import PostgresRow as DatabaseRow


__all__ = (
    "DatabaseConnection",
    "DatabaseError",
    "DatabaseOperationalError",
    "DatabaseRow",
)
