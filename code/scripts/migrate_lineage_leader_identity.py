"""Retire the Lineage User automation identity (v62 to v63).

Preview by default; use --apply during coordinated activation with Lineage
writers stopped. Old code requires the removed column and cannot run against
v63. This migration does not alter leaders, accepted Events or permissions.
"""
from __future__ import annotations

import argparse
import json

from management_common.format_tags import schema_matches

OLD_SCHEMA = "tfi.lineage-data.v62"
NEW_SCHEMA = "tfi.lineage-data.v63"


def _inspect(connection, *, lock=False):
    row = connection.execute(
        "SELECT value FROM lineage_meta WHERE key='schema'" + (" FOR UPDATE" if lock else "")
    ).fetchone()
    previous = row["value"] if row is not None else None
    if not any(schema_matches(previous, version) for version in (OLD_SCHEMA, NEW_SCHEMA)):
        raise ValueError(f"Expected {OLD_SCHEMA} or {NEW_SCHEMA}, found {previous}")
    pending = schema_matches(previous, OLD_SCHEMA)
    count = 0
    if pending:
        count = connection.execute(
            "SELECT count(*) AS count FROM lineages "
            "WHERE run_as_principal_json IS NOT NULL AND btrim(run_as_principal_json::text) <> 'null'"
        ).fetchone()["count"]
    return {"previous_schema": previous, "schema": NEW_SCHEMA,
            "would_apply": pending, "retired_identity_count": count}


def upgrade(connection):
    from management_portal.society_service.state_store import lineage_data_schema_matches

    connection.execute("SET LOCAL lock_timeout = '5s'")
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':schema-init', 0))")
    report = _inspect(connection, lock=True)
    if report["would_apply"]:
        connection.execute("ALTER TABLE lineages DROP COLUMN run_as_principal_json")
        connection.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (NEW_SCHEMA,))
    if not lineage_data_schema_matches(connection):
        raise ValueError("Migrated Lineage schema does not match the current contract")
    return {**report, "changed": report["would_apply"]}


def migrate(*, apply=False):
    from management_common.portal_database import portal_database
    from management_portal.society_service.state_store import lineage_data_schema_matches

    database = portal_database(schema="lineage")
    if not apply:
        with database.snapshot() as connection:
            report = _inspect(connection)
            if not report["would_apply"] and not lineage_data_schema_matches(connection):
                raise ValueError("Lineage schema does not match the current contract")
        return {**report, "applied": False, "changed": False}
    return {**database.write(upgrade), "applied": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    from management_common.postgres_configuration import host_database_url
    host_database_url()  # Resolve the registered site DB without printing credentials.
    print(json.dumps(migrate(apply=arguments.apply)))
