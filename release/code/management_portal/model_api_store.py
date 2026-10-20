"""Model API registrations and explicit service-route bindings in PostgreSQL.

Registration order is presentation only. Routes select an API by ID, and never
fall back to a different registration when the bound API is disabled.
"""
from __future__ import annotations
import os
from datetime import datetime, timezone
from functools import lru_cache
from psycopg.types.json import Jsonb
from management_common.domain_database import DomainDatabase

API_FIELDS = {
    "kind": "TEXT", "provider": "TEXT", "enabled": "BOOLEAN", "label": "TEXT",
    "base_url": "TEXT", "api_key_file": "TEXT", "api_key_env": "TEXT", "config_dir": "TEXT",
    "default_model": "TEXT", "timeout_s": "DOUBLE PRECISION", "max_retries": "INTEGER",
    "cost_tier": "TEXT", "privacy_tier": "TEXT", "priority": "INTEGER",
    "models": "JSONB", "default_headers": "JSONB", "default_parameters": "JSONB",
}
SERVICE_ROUTES = {("dsh", "deepseek"): "deepseek", ("opencode", "openrouter"): "openrouter"}
DDL = """
CREATE TABLE IF NOT EXISTS apis (
    api_id TEXT PRIMARY KEY CHECK (api_id <> ''),
    position BIGINT NOT NULL,
    """ + ",".join(f"{key} {kind}" for key, kind in API_FIELDS.items()) + """,
    version BIGINT NOT NULL CHECK (version >= 1),
    updated_at TIMESTAMPTZ NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS routes (
    backend TEXT NOT NULL,
    route_id TEXT NOT NULL,
    api_id TEXT NOT NULL REFERENCES apis(api_id) ON DELETE CASCADE,
    PRIMARY KEY (backend,route_id)
);
CREATE INDEX IF NOT EXISTS api_routes_target ON routes(api_id);
CREATE TABLE IF NOT EXISTS relay_resources (
    resource_id TEXT PRIMARY KEY CHECK (resource_id <> ''),
    position BIGINT NOT NULL,
    resource_json JSONB NOT NULL CHECK (jsonb_typeof(resource_json) = 'object'),
    updated_at TIMESTAMPTZ NOT NULL,
    updated_by TEXT NOT NULL
);
"""


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase("model_apis", "registry", DDL)


def database():
    return _database(os.getenv("PORTAL_DATABASE_URL"), os.getenv("PORTAL_DATABASE_NAMESPACE"))


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def body(entry):
    return {key: value for key, value in entry.items() if key not in {"id", "version"}}


def _decode(row):
    return {"id": row["api_id"], **{key: row[key] for key in API_FIELDS if row[key] is not None}}


def _values(entry):
    values = {key: value for key, value in entry.items() if key not in {"id", "version", "routes"}}
    if set(values) - API_FIELDS.keys():
        raise ValueError(f"Unknown Model API fields: {sorted(set(values) - API_FIELDS.keys())}")
    return {key: Jsonb(value) if API_FIELDS[key] == "JSONB" else value for key, value in values.items()}


def apis():
    with database().read() as connection:
        return [(_decode(row), int(row["version"]))
                for row in connection.execute("SELECT * FROM apis ORDER BY position,api_id")]


def route_bindings(api_id):
    with database().read() as c:
        return [dict(row) for row in c.execute(
            "SELECT backend,route_id FROM routes WHERE api_id=%s ORDER BY backend,route_id", (api_id,))]


def route_api_id(backend, route_id):
    with database().read() as c:
        row = c.execute("SELECT api_id FROM routes WHERE backend=%s AND route_id=%s", (backend, route_id)).fetchone()
    return row["api_id"] if row else None


def _bind_routes(c, entry, bindings):
    if not isinstance(bindings, list):
        raise ValueError("Model API routes must be an array")
    keys = []
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"backend", "route_id"}:
            raise ValueError("A Model API route requires backend and route_id")
        key = (binding["backend"], binding["route_id"])
        if key not in SERVICE_ROUTES or SERVICE_ROUTES[key] != entry.get("provider") or entry.get("kind") != "openai_compatible_chat":
            raise ValueError("Model API does not support the requested service route")
        if key in keys:
            raise ValueError("Duplicate Model API route")
        keys.append(key)
    c.execute("DELETE FROM routes WHERE api_id=%s", (entry["id"],))
    for backend, route_id in keys:
        existing = c.execute("SELECT api_id FROM routes WHERE backend=%s AND route_id=%s", (backend, route_id)).fetchone()
        if existing:
            raise ValueError("Route already bound; remove its existing binding before assigning another API")
        c.execute("INSERT INTO routes(backend,route_id,api_id) VALUES (%s,%s,%s)", (backend, route_id, entry["id"]))


def api_version(api_id):
    with database().read() as c:
        row = c.execute("SELECT version FROM apis WHERE api_id=%s", (api_id,)).fetchone()
    return None if row is None else int(row[0])


def create_api(entry, actor, *, secret=None):
    values = _values(entry)
    with database().transaction("apis") as c:
        position = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM apis").fetchone()[0]
        fields = ["api_id", "position", *values, "version", "updated_at", "updated_by"]
        result = c.execute(
            f"INSERT INTO apis({','.join(fields)}) VALUES ({','.join(['%s'] * len(fields))}) ON CONFLICT(api_id) DO NOTHING",
            (entry["id"], position, *values.values(), 1, now(), actor))
        if not result.rowcount:
            return False
        if "routes" in entry:
            _bind_routes(c, entry, entry["routes"])
        if secret is not None:
            from .execution.account_hosting import write_secret_file
            path = str(write_secret_file(entry["id"], secret))
            c.execute("UPDATE apis SET api_key_file=%s WHERE api_id=%s", (path, entry["id"]))
            entry["api_key_file"] = path
        return True


def update_api(api_id, changes, actor, *, secret=None):
    values = _values(changes)
    with database().transaction("apis") as c:
        row = c.execute("SELECT * FROM apis WHERE api_id=%s FOR UPDATE", (api_id,)).fetchone()
        if row is None:
            return None
        stored = {**_decode(row), **{k: v for k, v in changes.items() if k != "routes"}}
        version = int(row["version"]) + 1
        sets = [*(f"{key}=%s" for key in values), "version=%s", "updated_at=%s", "updated_by=%s"]
        c.execute(f"UPDATE apis SET {','.join(sets)} WHERE api_id=%s",
                  (*values.values(), version, now(), actor, api_id))
        bindings = changes.get("routes")
        if bindings is None:
            bindings = [dict(r) for r in c.execute("SELECT backend,route_id FROM routes WHERE api_id=%s", (api_id,))]
        _bind_routes(c, stored, bindings)
        if secret is not None:
            from .execution.account_hosting import write_secret_file
            path = str(write_secret_file(api_id, secret))
            c.execute("UPDATE apis SET api_key_file=%s WHERE api_id=%s", (path, api_id))
            stored["api_key_file"] = path
        return stored, version


def delete_api(api_id):
    with database().transaction("apis") as c:
        return bool(c.execute("DELETE FROM apis WHERE api_id=%s", (api_id,)).rowcount)


def relay_resources():
    with database().read() as c:
        rows = c.execute("SELECT resource_id,resource_json FROM relay_resources ORDER BY position,resource_id").fetchall()
    return [{"id": row["resource_id"], **row["resource_json"]} for row in rows]
