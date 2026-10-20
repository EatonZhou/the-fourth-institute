#!/usr/bin/env python3
"""Lineage v59 -> v60: one application_targets table, Portal-wide notifications in the lineage schema.

The six per-application configuration tables become rows of the core table
application_targets, keyed by the application name the maintenance script
already uses (``tau``, ``semantic_search:wall`` …).  discussion_reply_configuration
has no application any more; the migration drops it and refuses when it still
holds a row.  The Notice Board schema's human_notifications table (the
notifications that belong to no Lineage) becomes the lineage record
portal_human_notifications.  Run while every Portal service is stopped;
without ``--apply`` the script only reads.
"""
from __future__ import annotations

from management_common.format_tags import schema_matches

import argparse
import json

from psycopg import sql

OLD_SCHEMA = "tfi.lineage-data.v59"
NEW_SCHEMA = "tfi.lineage-data.v60"
# table -> (key column, application name or name prefix for the key, target column)
OLD_TABLES = {
    "resident_discovery_configuration": ("singleton", "resident_discovery", "resident_id"),
    "resident_summary_configuration": ("singleton", "resident_summary", "resident_id"),
    "notice_classifier_configuration": ("singleton", "notice_classifier", "resident_id"),
    "semantic_search_residents": ("surface", "semantic_search:", "resident_id"),
    "resource_chat_lineages": ("resource_type", "resource_chat:", "lineage_id"),
    "tau_configuration": ("singleton", "tau", "lineage_id"),
}
RETIRED_TABLE = "discussion_reply_configuration"


def _portal_notifications(connection) -> list[dict]:
    from management_common.database_time import iso_time
    from management_common.portal_database import _database_schema

    notice = _database_schema("portal_notice")
    if not connection.execute("SELECT to_regclass(%s) AS present", (notice + ".human_notifications",)).fetchone()["present"]:
        return []
    rows = connection.execute(sql.SQL(
        "SELECT id,title,message,severity,trigger_path_id,created_by,created_at,recipients "
        "FROM {}.human_notifications ORDER BY created_at,id").format(sql.Identifier(notice))).fetchall()
    connection.execute(sql.SQL("DROP TABLE {}.human_notifications").format(sql.Identifier(notice)))
    return [{**{key: row[key] or "" for key in ("id", "title", "message", "severity", "trigger_path_id", "created_by")},
             "lineage_id": "", "created_at": iso_time(row["created_at"]) or "",
             "recipients": list(row["recipients"] or [])} for row in rows]


def upgrade(connection):
    # DROP TABLE takes ACCESS EXCLUSIVE; never queue live readers behind it.
    connection.execute("SET LOCAL lock_timeout = '5s'")
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':schema-init', 0))")
    version = connection.execute("SELECT value FROM lineage_meta WHERE key='schema' FOR UPDATE").fetchone()["value"]
    if not any(schema_matches(version, expected) for expected in (OLD_SCHEMA, NEW_SCHEMA)):
        raise ValueError(f"Expected {OLD_SCHEMA} or {NEW_SCHEMA}, found {version}")
    from management_portal import application_targets
    from management_portal.society_service import lineage_human_notifications
    from management_portal.society_service.state_store import LINEAGE_DATA_SCHEMA, lineage_data_schema_matches

    if not schema_matches(LINEAGE_DATA_SCHEMA, NEW_SCHEMA):
        raise ValueError(f"The code declares {LINEAGE_DATA_SCHEMA}; this migration writes {NEW_SCHEMA}")
    result = {"previous_schema": version, "schema": NEW_SCHEMA, "targets": 0, "notifications": 0}
    if schema_matches(version, OLD_SCHEMA):
        if connection.execute(f"SELECT 1 FROM {RETIRED_TABLE} LIMIT 1").fetchone():
            raise ValueError(f"{RETIRED_TABLE} still holds a row; no application reads it")
        application_targets.create_schema(connection)
        for table, (key, name, field) in OLD_TABLES.items():
            application = "%s::text" if key == "singleton" else f"%s::text || {key}"
            result["targets"] += connection.execute(
                f"INSERT INTO application_targets (application,{field},revision,admission_paused,reason,updated_by,updated_at) "
                f"SELECT {application},{field},revision,admission_paused,reason,updated_by,updated_at FROM {table}",
                (name,),
            ).rowcount
        for table in (*OLD_TABLES, RETIRED_TABLE):
            connection.execute(f"DROP TABLE {table}")
        connection.execute(
            "CREATE TABLE portal_human_notifications (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
            "notifications_json JSONB NOT NULL)"
        )
        notifications = _portal_notifications(connection)
        if notifications:
            document = lineage_human_notifications._normalize({"notifications": notifications})
            connection.execute("INSERT INTO portal_human_notifications VALUES (1,%s)",
                               (json.dumps(document, ensure_ascii=False, sort_keys=True),))
        result["notifications"] = len(notifications)
        connection.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (NEW_SCHEMA,))
        connection.execute("ANALYZE application_targets")
    if not lineage_data_schema_matches(connection):
        raise ValueError(f"Lineage data structure does not match {NEW_SCHEMA} after the upgrade")
    return result


def migrate(*, apply=False):
    from management_common.portal_database import portal_database

    database = portal_database(schema="lineage")
    if not apply:
        # A preview only reads: running the DROP and rolling back still holds the table locks.
        with database.read() as connection:
            version = connection.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()["value"]
        return {"previous_schema": version, "schema": NEW_SCHEMA, "applied": False,
                "would_apply": schema_matches(version, OLD_SCHEMA)}
    return {**database.write(upgrade), "applied": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(migrate(apply=parser.parse_args().apply)))
