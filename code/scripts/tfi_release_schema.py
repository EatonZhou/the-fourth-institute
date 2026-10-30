"""Explicit, transactional Lineage migrations for retained-data releases.

The caller owns the offline backup and a write transaction. No seed import or
automatic startup migration is permitted. Historical run-as settings are kept
in a separate archive schema; current releases use the Lineage Leader instead.
"""
from __future__ import annotations

import re


CURRENT = "tfi.lineage-data.v65"


def version(value):
    match = re.fullmatch(r"tfi\.lineage-data\.v(60|61|62|63|64|65)", str(value))
    if not match:
        raise ValueError("Unsupported Lineage migration origin")
    return int(match[1])


def columns_for(number):
    from management_portal.society_service.state_store import POSTGRES_LINEAGE_DATA_COLUMNS
    columns = dict(POSTGRES_LINEAGE_DATA_COLUMNS)
    if number < 65:
        for name in ("family_chat_preferences", "family_chat_inputs"):
            columns.pop(name)
    if number < 64:
        columns["families"] = tuple(c for c in columns["families"] if c != "auto_migrate_priority")
    if number < 63:
        columns["lineages"] = (*columns["lineages"], "run_as_principal_json")
    if number < 62:
        columns["families"] = tuple(c for c in columns["families"] if c != "auto_migrate")
    if number < 61:
        columns.pop("family_task_dispatches")
    return columns


def inspect(connection):
    from management_portal.society_service.state_store import LINEAGE_DATA_INDEXES, POSTGRES_TRIGGERS
    row = connection.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()
    number = version(row["value"] if row else None)
    expected = columns_for(number)
    actual = {}
    for row in connection.execute(
        "SELECT c.table_name,c.column_name FROM information_schema.columns c "
        "JOIN information_schema.tables t USING(table_catalog,table_schema,table_name) "
        "WHERE c.table_schema=current_schema() AND t.table_type='BASE TABLE' "
        "ORDER BY c.table_name,c.ordinal_position"
    ).fetchall():
        actual.setdefault(row["table_name"], []).append(row["column_name"])
    indexes = {r["indexname"] for r in connection.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()"
    ).fetchall()}
    triggers = {r["trigger_name"] for r in connection.execute(
        "SELECT DISTINCT trigger_name FROM information_schema.triggers WHERE trigger_schema=current_schema()"
    ).fetchall()}
    if ({k: tuple(v) for k, v in actual.items()} != expected
            or not LINEAGE_DATA_INDEXES.issubset(indexes) or triggers != POSTGRES_TRIGGERS):
        raise ValueError("Lineage structure differs from its declared migration origin")
    return {"previous_schema": f"tfi.lineage-data.v{number}", "schema": CURRENT,
            "would_apply": number != 65}


def upgrade(connection):
    connection.execute("SET LOCAL lock_timeout = '5s'")
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':schema-init', 0))")
    connection.execute("SELECT value FROM lineage_meta WHERE key='schema' FOR UPDATE")
    result = inspect(connection)
    number = version(result["previous_schema"])
    if number < 61:
        from management_portal.society_service.family_task_dispatch_storage import create_schema
        create_schema(connection)
    if number < 62:
        connection.execute("ALTER TABLE families ADD COLUMN auto_migrate BOOLEAN NOT NULL DEFAULT true")
    if number < 63:
        # Archive outside Lineage's strict domain tables. Namespace is part of
        # the key so independent test or installation schemas never collide.
        connection.execute("CREATE SCHEMA IF NOT EXISTS tfi_migration_archive")
        connection.execute("CREATE TABLE IF NOT EXISTS tfi_migration_archive.lineage_run_as ("
                           "source_schema TEXT NOT NULL,lineage_id TEXT NOT NULL,value JSONB,"
                           "PRIMARY KEY(source_schema,lineage_id))")
        connection.execute("INSERT INTO tfi_migration_archive.lineage_run_as "
                           "SELECT current_schema(),lineage_id,run_as_principal_json FROM lineages")
        connection.execute("ALTER TABLE lineages DROP COLUMN run_as_principal_json")
    if number < 64:
        connection.execute("ALTER TABLE families ADD COLUMN auto_migrate_priority TEXT[] NOT NULL DEFAULT '{}'")
    if number < 65:
        from management_portal.society_service.family_chat_storage import create_schema
        create_schema(connection)
    connection.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (CURRENT,))
    inspect(connection)
    return {**result, "applied": number != 65}


def main():
    import argparse
    import json
    from management_common.portal_database import portal_database
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database = portal_database(schema="lineage")
    if args.apply:
        result = database.write(upgrade)
    else:
        with database.read() as connection:
            result = inspect(connection)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
