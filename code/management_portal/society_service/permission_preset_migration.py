"""One-time rename of the stored permission preset ``default`` (2026-11-27).

Every Provider used to name its restricted tier ``default`` while the actual
default was ``full-access``. The tier now has a key of its own per Provider;
stored settings move to the key of their Resident's Provider, and the derived
``profile`` follows. Settings of Providers without such a tier lose the
preset. A later start finds no ``default`` and writes nothing; the module can
be removed once every instance has started with it.
"""
from __future__ import annotations

import logging
from typing import Any

from management_common.json_columns import json_value

from .lineage_data_columns import json_column

RENAMED = {"codex": "ask", "claude": "restricted", "kimi": "manual", "antigravity": "portal-tools-only"}


def migrate_settings(backend: str, settings: dict[str, Any]) -> dict[str, Any]:
    target = RENAMED.get(backend, "")
    out = dict(settings)
    if target:
        out["permission_preset"] = target
    else:
        out.pop("permission_preset", None)
    if out.get("profile") == "permissions:default":
        out["profile"] = f"permissions:{target}" if target else ""
    return out


def migrate(database: Any = None) -> int:
    """Rewrite every stored ``permission_preset="default"``; return the number of records changed."""
    if database is None:
        from . import lineage_aggregate
        database = lineage_aggregate.lineage_catalog_store().state_store.database

    def write(connection: Any) -> int:
        rows = connection.execute("""
            SELECT c.resident_id,r.backend,c.execution_settings_json FROM resident_execution_configuration c
            JOIN residents r USING(resident_id)
            WHERE c.execution_settings_json::jsonb->>'permission_preset'='default'
            ORDER BY c.resident_id FOR UPDATE OF c
        """).fetchall()
        for row in rows:
            settings = migrate_settings(str(row["backend"] or ""), json_value(row["execution_settings_json"]))
            connection.execute(
                "UPDATE resident_execution_configuration SET execution_settings_json=%s WHERE resident_id=%s",
                (json_column(settings), row["resident_id"]),
            )
        return len(rows)

    count = database.write(write)
    if count:
        logging.getLogger(__name__).warning("Permission preset 'default' renamed in %d stored Resident settings", count)
    return count
