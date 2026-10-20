#!/usr/bin/env python3
"""Assign Family resource IDs once, preserving all existing Function Block keys."""
from __future__ import annotations

from management_common.format_tags import schema_matches

import argparse
import json
import os
from pathlib import Path
import shlex

OLD_SCHEMA = 'tfi.lineage-data.v52'
NEW_SCHEMA = 'tfi.lineage-data.v53'


def upgrade(connection):
    from management_common.resource_identity import get_resource_type_registry
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':schema-init', 0))")
    version = connection.execute("SELECT value FROM lineage_meta WHERE key='schema' FOR UPDATE").fetchone()['value']
    if not any(schema_matches(version, expected) for expected in (OLD_SCHEMA, NEW_SCHEMA)):
        raise ValueError(f'Expected {OLD_SCHEMA} or {NEW_SCHEMA}, found {version}')
    if schema_matches(version, OLD_SCHEMA):
        connection.execute('ALTER TABLE families ADD COLUMN resource_id TEXT, ADD COLUMN created_at TIMESTAMPTZ, ADD COLUMN updated_at TIMESTAMPTZ')
        rows = connection.execute('SELECT f.family_id, f.deleted_at, b.created_at, b.updated_at, l.created_at AS lineage_created_at, l.updated_at AS lineage_updated_at FROM families f JOIN lineages l USING(lineage_id) LEFT JOIN function_blocks b ON b.function_block_id=f.family_id ORDER BY f.family_id FOR UPDATE OF f').fetchall()
        identity = get_resource_type_registry()
        used = set()
        for row in rows:
            resource_id = identity.new_id('family', used)
            used.add(resource_id)
            created_at = row['created_at'] or row['lineage_created_at']
            updated_at = row['deleted_at'] or row['updated_at'] or row['lineage_updated_at']
            if not created_at or not updated_at:
                raise ValueError('Missing source timestamps for ' + row['family_id'])
            connection.execute('UPDATE families SET resource_id=%s,created_at=%s,updated_at=%s WHERE family_id=%s',
                               (resource_id, created_at, updated_at, row['family_id']))
        connection.execute("ALTER TABLE families ALTER COLUMN resource_id SET NOT NULL, ALTER COLUMN created_at SET NOT NULL, ALTER COLUMN updated_at SET NOT NULL, ADD UNIQUE(resource_id), ADD CHECK(resource_id ~ '^family_[0-9a-f]{12}$')")
        connection.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (NEW_SCHEMA,))
        # Force one publication of the existing graph at its unchanged revision.
        connection.execute("DELETE FROM lineage_resource_projection WHERE lineage_id IN (SELECT lineage_id FROM families)")
    rows = [dict(row) for row in connection.execute('SELECT family_id,resource_id,lineage_id,deleted_at IS NOT NULL AS deleted FROM families ORDER BY family_id').fetchall()]
    return {'previous_schema': version, 'schema': NEW_SCHEMA, 'families': rows}


class PreviewRollback(Exception):
    def __init__(self, result):
        self.result = result


def migrate(*, apply=False):
    from management_common.portal_database import portal_database
    def execute(connection):
        result = {**upgrade(connection), 'applied': apply}
        if not apply:
            raise PreviewRollback(result)
        return result
    try:
        return portal_database(schema='lineage').write(execute)
    except PreviewRollback as preview:
        return preview.result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not os.getenv('PORTAL_DATABASE_URL'):
        from management_common.postgres_configuration import postgres_env_file
        for line in postgres_env_file().read_text().splitlines():
            if line.startswith('PORTAL_DATABASE_URL='):
                os.environ['PORTAL_DATABASE_URL'] = shlex.split(line.split('=', 1)[1])[0]
    result = migrate(apply=args.apply)
    families = result.pop('families')
    print(json.dumps({**result, 'total': len(families), 'deleted': sum(row['deleted'] for row in families),
                      'active': [row for row in families if not row['deleted']]}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
