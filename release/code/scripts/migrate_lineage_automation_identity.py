#!/usr/bin/env python3
"""Add the explicit automation identity column, without assigning any User.

After deployment, configure identities through authenticated UpdateLineage.
This schema migration never changes event cursors, pauses or fire budgets.
"""
from __future__ import annotations

from management_common.format_tags import schema_matches

import argparse
import json

OLD_SCHEMA = "tfi.lineage-data.v53"
NEW_SCHEMA = "tfi.lineage-data.v54"


def upgrade(connection):
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':schema-init', 0))")
    previous = connection.execute("SELECT value FROM lineage_meta WHERE key='schema' FOR UPDATE").fetchone()["value"]
    if not any(schema_matches(previous, expected) for expected in (OLD_SCHEMA, NEW_SCHEMA)):
        raise ValueError(f"Expected {OLD_SCHEMA} or {NEW_SCHEMA}, found {previous}")
    if schema_matches(previous, OLD_SCHEMA):
        connection.execute("ALTER TABLE lineages ADD COLUMN run_as_principal_json TEXT")
        connection.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (NEW_SCHEMA,))
    from management_portal.society_service.state_store import lineage_data_schema_matches
    if not lineage_data_schema_matches(connection):
        raise RuntimeError("Migrated Lineage schema does not match the current contract")
    return {"previous_schema": previous, "schema": NEW_SCHEMA}


def main():
    from psycopg import connect
    from psycopg.rows import dict_row
    from psycopg.sql import Identifier, SQL
    from scripts.migrate_domain_records import source_url

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--schema", default="lineage")
    args = parser.parse_args()
    with connect(source_url(), row_factory=dict_row) as connection:
        connection.execute(SQL("SET LOCAL search_path TO {}, public").format(Identifier(args.schema)))
        result = upgrade(connection)
        if not args.apply:
            connection.rollback()
    print(json.dumps({**result, "applied": args.apply}))


if __name__ == "__main__":
    main()
