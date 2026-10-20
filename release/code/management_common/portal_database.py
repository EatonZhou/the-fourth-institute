from __future__ import annotations

import os
import re

from .postgres_database import PostgresDatabase


_IDENTIFIER_PART = re.compile(r"[^a-z0-9_]+")


def portal_database_url() -> str:
    """Return the required unified Portal PostgreSQL endpoint."""

    dsn = str(os.getenv("PORTAL_DATABASE_URL") or "").strip()
    if not dsn:
        raise RuntimeError("Portal storage requires PORTAL_DATABASE_URL")
    return dsn


def _database_schema(schema: str) -> str:
    namespace = str(os.getenv("PORTAL_DATABASE_NAMESPACE") or "").strip().lower()
    clean_schema = _IDENTIFIER_PART.sub("_", schema.lower()).strip("_")
    clean_namespace = _IDENTIFIER_PART.sub("_", namespace).strip("_")
    if not clean_schema or namespace and not clean_namespace:
        raise ValueError("Portal database namespace and schema must contain letters")
    combined = (
        f"{clean_namespace}_{clean_schema}" if clean_namespace else clean_schema
    )
    if len(combined) > 63:
        raise ValueError("Portal database namespace is too long")
    return combined


def portal_database(
    *,
    schema: str,
    min_size: int | None = None,
    max_size: int | None = None,
) -> PostgresDatabase:
    """Open the PostgreSQL transaction boundary for one Portal schema."""

    selected_min = 0 if min_size is None else min_size
    selected_max = int(
        os.getenv("PORTAL_POSTGRES_DOMAIN_POOL_SIZE") or 8
    ) if max_size is None else max_size
    return PostgresDatabase(
        portal_database_url(),
        schema=_database_schema(schema),
        min_size=selected_min,
        max_size=selected_max,
    )
