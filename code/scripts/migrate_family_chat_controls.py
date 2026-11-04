"""Explicit one-time LineageData v65 → v66 conversion of Family chat controls.

The old M excluded routing and hid the conversation. Preserve that routing
choice as Exclude, stop hiding history, and leave the new notification Mute
off. Existing requests keep exactly their frozen routing choice. Default is
a complete conversion and validation followed by rollback; --apply commits.
Coordinate the schema switch with loading the v66 SocietyService code.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.portal_database import portal_database
from management_portal.society_service import state_store


def convert(connection):
    if state_store.LINEAGE_DATA_SCHEMA != 'tfi.lineage-data.v66':
        raise ValueError('This migration requires the exact v66 owner code')
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':schema-init', 0))")
    schema = connection.execute("SELECT value FROM lineage_meta WHERE key='schema' FOR UPDATE").fetchone()
    if schema and schema['value'] == state_store.LINEAGE_DATA_SCHEMA:
        from management_portal.society_service.resident_reporting import install_schema
        install_schema(connection)
        if not state_store.lineage_data_schema_matches(connection):
            raise ValueError('Current LineageData has an unexpected structure')
        return {'schema': schema['value'], 'changed': False}
    if not schema or schema['value'] != 'tfi.lineage-data.v65':
        raise ValueError('Expected exact LineageData v65')
    connection.execute('LOCK TABLE family_chat_preferences, family_chat_inputs IN ACCESS EXCLUSIVE MODE')
    before = {}
    for table, key in [('family_chat_preferences', 'actor'), ('family_chat_inputs', 'message_id')]:
        columns = {r['column_name'] for r in connection.execute(
            'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s', (table,)).fetchall()}
        if columns != {'facade_id', key, 'muted_member_ids', 'solo_member_id'}:
            raise ValueError('Unexpected legacy Family control structure: ' + table)
        before[table] = [tuple(r) for r in connection.execute(
            f'SELECT facade_id,{key},muted_member_ids,solo_member_id FROM {table} ORDER BY facade_id,{key}').fetchall()]
        connection.execute(f'ALTER TABLE {table} RENAME COLUMN muted_member_ids TO excluded_member_ids')
    connection.execute("ALTER TABLE family_chat_preferences ADD COLUMN muted_member_ids TEXT[] NOT NULL DEFAULT '{}'")
    for table, key in [('family_chat_preferences', 'actor'), ('family_chat_inputs', 'message_id')]:
        after = [tuple(r) for r in connection.execute(
            f'SELECT facade_id,{key},excluded_member_ids,solo_member_id FROM {table} ORDER BY facade_id,{key}').fetchall()]
        if before[table] != after:
            raise ValueError('Family control identity or routing snapshot changed')
    connection.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (state_store.LINEAGE_DATA_SCHEMA,))
    from management_portal.society_service.resident_reporting import install_schema
    install_schema(connection)
    if not state_store.lineage_data_schema_matches(connection):
        raise ValueError('Converted LineageData does not match the current structure')
    return {'schema': state_store.LINEAGE_DATA_SCHEMA, 'changed': True,
            'preferences': len(before['family_chat_preferences']), 'routing_snapshots': len(before['family_chat_inputs'])}


class PreviewRollback(Exception):
    pass


def migrate(database, *, apply=False):
    result = {}
    try:
        with database.transaction() as connection:
            result = convert(connection)
            if not apply:
                raise PreviewRollback()
    except PreviewRollback:
        pass
    return {**result, 'committed': apply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(migrate(portal_database(schema='lineage'), apply=args.apply)))


if __name__ == '__main__':
    main()
