"""The rows of the templates domain: its database, content identity and published snapshots."""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache

from management_common.domain_database import DomainDatabase
from management_common.relational_rows import read_rows as sql_rows, sync_rows
from management_common.database_time import encode_times, decode_times


def read_rows(connection, table):
    return [decode_times(row, ('created_at', 'updated_at', 'installed_at')) for row in sql_rows(connection, table)]

CONTENT = ("kind", "category", "title", "description", "targets", "parameters", "body")
FIELDS = (
    *CONTENT,
    "installed_from_store_id",
    "installed_store_version",
    "installed_store_hash",
    "builtin_key",
    "builtin_source",
    "version",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
)
STORE_TIMES = ("published_at", "updated_at", "unpublished_at")
STORE_FIELDS = ("source_template_id", "publisher", "version", "release_note", *CONTENT,
                "content_hash", "versions", "active", *STORE_TIMES)
DDL = """
CREATE TABLE IF NOT EXISTS templates (
 template_id TEXT PRIMARY KEY, kind TEXT NOT NULL, category TEXT NOT NULL,
 title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', targets JSONB NOT NULL,
 parameters JSONB NOT NULL, body JSONB NOT NULL, builtin_key TEXT, builtin_source TEXT,
 version INTEGER NOT NULL, created_at TIMESTAMPTZ, created_by TEXT, updated_at TIMESTAMPTZ, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS installed_builtins (builtin_key TEXT PRIMARY KEY, installed_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS template_id_redirects (old_id TEXT PRIMARY KEY, template_id TEXT NOT NULL);
ALTER TABLE templates ADD COLUMN IF NOT EXISTS installed_from_store_id TEXT;
ALTER TABLE templates ADD COLUMN IF NOT EXISTS installed_store_version INTEGER;
ALTER TABLE templates ADD COLUMN IF NOT EXISTS installed_store_hash TEXT;
CREATE TABLE IF NOT EXISTS template_store_items (
 store_id TEXT PRIMARY KEY, source_template_id TEXT NOT NULL UNIQUE, publisher TEXT NOT NULL,
 version INTEGER NOT NULL, release_note TEXT NOT NULL DEFAULT '',
 kind TEXT NOT NULL, category TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 targets JSONB NOT NULL, parameters JSONB NOT NULL, body JSONB NOT NULL,
 content_hash TEXT NOT NULL, versions JSONB NOT NULL DEFAULT '[]'::jsonb,
 active BOOLEAN NOT NULL DEFAULT TRUE, published_at TIMESTAMPTZ NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL, unpublished_at TIMESTAMPTZ
);
"""


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase("templates", "templates", DDL)


def database():
    return _database(
        os.getenv("PORTAL_DATABASE_URL"), os.getenv("PORTAL_DATABASE_NAMESPACE")
    )


def content_hash(template):
    return hashlib.sha256(
        json.dumps(
            {key: template[key] for key in CONTENT},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def write_rows(connection, rows):
    sync_rows(
        connection,
        "templates",
        ("template_id",),
        FIELDS,
        [encode_times({key: row.get(key) for key in ("template_id", *FIELDS)}, ('created_at', 'updated_at')) for row in rows],
        json_columns=("targets", "parameters", "body"),
    )


def store_items(connection):
    return [decode_times(row, STORE_TIMES) for row in connection.execute("SELECT * FROM template_store_items")]


def write_store_items(connection, rows):
    sync_rows(connection, "template_store_items", ("store_id",), STORE_FIELDS,
              [encode_times(row, STORE_TIMES) for row in rows],
              json_columns=("targets", "parameters", "body", "versions"))


def resolve_id(connection, identity):
    row = connection.execute('SELECT template_id FROM template_id_redirects WHERE old_id=%s', (identity,)).fetchone()
    return row['template_id'] if row else identity


def get_persisted(template_id):
    """Read the installed recipe without refreshing or installing builtins."""
    with database().read() as connection:
        template_id = resolve_id(connection, template_id)
        row = connection.execute(
            "SELECT * FROM templates WHERE template_id=%s", (template_id,)
        ).fetchone()
        if row is None:
            raise KeyError(template_id)
        return decode_times(row, ('created_at', 'updated_at'))
