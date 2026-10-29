"""Add personal Family M/S preferences and input policy snapshots."""
from __future__ import annotations

import argparse
import json

from management_common.format_tags import schema_matches

OLD_SCHEMA = "tfi.lineage-data.v64"
NEW_SCHEMA = "tfi.lineage-data.v65"


def upgrade(connection):
    from management_portal.society_service.state_store import lineage_data_schema_matches

    connection.execute("SET LOCAL lock_timeout = '5s'")
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':schema-init', 0))")
    previous = connection.execute("SELECT value FROM lineage_meta WHERE key='schema' FOR UPDATE").fetchone()["value"]
    if not any(schema_matches(previous, value) for value in (OLD_SCHEMA, NEW_SCHEMA)):
        raise ValueError(f"Expected {OLD_SCHEMA} or {NEW_SCHEMA}, found {previous}")
    if schema_matches(previous, OLD_SCHEMA):
        from management_portal.society_service.family_chat_storage import create_schema
        create_schema(connection)
        connection.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (NEW_SCHEMA,))
    if not lineage_data_schema_matches(connection):
        raise ValueError("Migrated Lineage schema does not match the current contract")
    return {"previous_schema": previous, "schema": NEW_SCHEMA}


def migrate(*, apply=False):
    from management_common.portal_database import portal_database

    database = portal_database(schema="lineage")
    if not apply:
        with database.read() as connection:
            previous = connection.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()["value"]
        return {"previous_schema": previous, "schema": NEW_SCHEMA, "applied": False,
                "would_apply": schema_matches(previous, OLD_SCHEMA)}
    return {**database.write(upgrade), "applied": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    from management_common.postgres_configuration import host_database_url
    host_database_url()  # Resolve the registered site DB without printing credentials.
    print(json.dumps(migrate(apply=parser.parse_args().apply)))
