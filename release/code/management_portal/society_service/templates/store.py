"""The templates domain and its one-time builtin installation ledger."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache

from management_common.domain_database import DomainDatabase
from management_common.relational_rows import read_rows as sql_rows, sync_rows
from management_common.database_time import encode_times, decode_times


def read_rows(connection, table):
    return [decode_times(row, ('created_at', 'updated_at', 'installed_at')) for row in sql_rows(connection, table)]

CONTENT = ("kind", "category", "title", "description", "targets", "parameters", "body")
FIELDS = (
    *CONTENT,
    "builtin_key",
    "builtin_source",
    "version",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
)
DDL = """
CREATE TABLE IF NOT EXISTS templates (
 template_id TEXT PRIMARY KEY, kind TEXT NOT NULL, category TEXT NOT NULL,
 title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', targets JSONB NOT NULL,
 parameters JSONB NOT NULL, body JSONB NOT NULL, builtin_key TEXT, builtin_source TEXT,
 version INTEGER NOT NULL, created_at TIMESTAMPTZ, created_by TEXT, updated_at TIMESTAMPTZ, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS installed_builtins (builtin_key TEXT PRIMARY KEY, installed_at TIMESTAMPTZ NOT NULL);
"""


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _write(connection, rows):
    sync_rows(
        connection,
        "templates",
        ("template_id",),
        FIELDS,
        [encode_times({key: row.get(key) for key in ("template_id", *FIELDS)}, ('created_at', 'updated_at')) for row in rows],
        json_columns=("targets", "parameters", "body"),
    )


def ensure_builtins():
    from . import normalize
    from .builtins import definitions

    with database().transaction("templates") as connection:
        rows = {row["template_id"]: row for row in read_rows(connection, "templates")}
        installed = {
            row["builtin_key"] for row in read_rows(connection, "installed_builtins")
        }
        for raw in definitions():
            template = normalize(raw)
            key = raw["builtin_key"]
            template_id = raw["template_id"]
            source = content_hash(template)
            previous = rows.get(template_id)
            if key not in installed:
                connection.execute(
                    "INSERT INTO installed_builtins(builtin_key,installed_at) VALUES (%s,%s)",
                    (key, now()),
                )
            elif previous is None:
                continue
            if previous is not None and (
                previous["builtin_source"] != content_hash(previous)
                or previous["builtin_source"] == source
            ):
                continue
            rows[template_id] = {
                **template,
                "template_id": template_id,
                "builtin_key": key,
                "builtin_source": source,
                "version": int(previous["version"]) + 1 if previous else 1,
                "created_at": previous["created_at"] if previous else now(),
                "created_by": previous["created_by"] if previous else "portal",
                "updated_at": now(),
                "updated_by": "portal",
            }
        _write(connection, rows.values())


def all_templates():
    ensure_builtins()
    with database().read() as connection:
        return sorted(
            read_rows(connection, "templates"),
            key=lambda row: (row["category"], row["title"], row["template_id"]),
        )


def get(template_id):
    ensure_builtins()
    with database().read() as connection:
        row = connection.execute(
            "SELECT * FROM templates WHERE template_id=%s", (template_id,)
        ).fetchone()
        if row is None:
            raise KeyError(template_id)
        return decode_times(row, ('created_at', 'updated_at'))


def save(template, actor, *, template_id=None, transform=None):
    ensure_builtins()
    with database().transaction("templates") as connection:
        rows = {row["template_id"]: row for row in read_rows(connection, "templates")}
        previous = rows.get(template_id)
        if template_id and previous is None:
            raise KeyError(template_id)
        if transform:
            template = transform(deepcopy(previous))
        identity = template_id or "template_" + secrets.token_hex(6)
        value = {
            **deepcopy(template),
            "template_id": identity,
            "builtin_key": previous.get("builtin_key") if previous else None,
            "builtin_source": previous.get("builtin_source") if previous else None,
            "version": int(previous["version"]) + 1 if previous else 1,
            "created_at": previous["created_at"] if previous else now(),
            "created_by": previous["created_by"] if previous else actor,
            "updated_at": now(),
            "updated_by": actor,
        }
        rows[identity] = value
        _write(connection, rows.values())
        return value


def delete(template_id):
    ensure_builtins()
    with database().transaction("templates") as connection:
        if not connection.execute(
            "DELETE FROM templates WHERE template_id=%s RETURNING template_id",
            (template_id,),
        ).fetchone():
            raise KeyError(template_id)


def restore(template_id, actor):
    from . import normalize
    from .builtins import definitions

    raw = next(
        (item for item in definitions() if item["template_id"] == template_id), None
    )
    if raw is None:
        from .registry import TemplateError

        raise TemplateError("只能恢复内置模板")
    template = normalize(raw)
    ensure_builtins()
    with database().transaction("templates") as connection:
        rows = {row["template_id"]: row for row in read_rows(connection, "templates")}
        previous = rows.get(template_id)
        value = {
            **template,
            "builtin_source": content_hash(template),
            "version": int(previous["version"]) + 1 if previous else 1,
            "created_at": previous["created_at"] if previous else now(),
            "created_by": previous["created_by"] if previous else actor,
            "updated_at": now(),
            "updated_by": actor,
        }
        rows[template_id] = value
        _write(connection, rows.values())
        return value
