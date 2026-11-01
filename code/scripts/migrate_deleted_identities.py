"""Offline resource identity table rename; dry-run unless --apply is supplied.

Deploy the matching registry DDL and all identity-boundary consumers in the
same maintenance window. This command never stops services or reads secrets
into its report. PostgreSQL must have no other clients during application.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg import sql
from management_common.portal_database import portal_database_url, _database_schema
from scripts.instance_storage_migration import require_database_alone


def migrate(connection, schema, *, apply=False):
    tables = {row[0] for row in connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema=%s",
        (schema,)).fetchall()}
    old, new = 'retired_identities', 'deleted_identities'
    if old in tables and new in tables:
        raise RuntimeError('Both identity tables exist; refuse to merge identities')
    if old not in tables and new not in tables:
        raise RuntimeError('Resource identity table is missing')
    source = old if old in tables else new
    count = connection.execute(sql.SQL('SELECT count(*) FROM {}.{}').format(
        sql.Identifier(schema), sql.Identifier(source))).fetchone()[0]
    if apply and source == old:
        require_database_alone(connection)
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute(sql.SQL('ALTER TABLE {}.{} RENAME TO {}').format(
            sql.Identifier(schema), sql.Identifier(old), sql.Identifier(new)))
        connection.execute(sql.SQL('ALTER INDEX {}.{} RENAME TO {}').format(
            sql.Identifier(schema), sql.Identifier(old + '_pkey'), sql.Identifier(new + '_pkey')))
    return {'schema': schema, 'source': source, 'target': new,
            'identities': count, 'applied': apply, 'requires_offline_window': source == old}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    with psycopg.connect(portal_database_url()) as connection:
        if not args.apply:
            connection.execute('SET TRANSACTION READ ONLY')
        report = migrate(connection, _database_schema('resource_registry'), apply=args.apply)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
