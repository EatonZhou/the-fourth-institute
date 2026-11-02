"""Offline-only conversions retained for installed releases predating the protocol.

Runtime owners accept only their current schema. Compatibility belongs to the
explicit maintenance transaction, never to service startup.
"""
from collections.abc import Mapping


def migrate_move_safety_schema(connection, schema):
    from management_common.directory_move_store import initialize_move_schema
    table = f'"{schema}".moves'
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None:
        initialize_move_schema(connection, schema)
        return
    columns = {row[0] for row in connection.execute(
        'SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s',
        (schema, 'moves')).fetchall()}
    if {'staging', 'file_identity', 'staging_identity'} <= columns:
        return
    if not {'staging', 'file_identity'} <= columns:
        pending = connection.execute(f"SELECT operation_id FROM {table} "
            "WHERE phase NOT IN ('completed','aborted','cancelled')").fetchall()
        if pending:
            raise RuntimeError('Resolve legacy directory moves before safety migration')
    for name in ('staging', 'file_identity', 'staging_identity'):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} TEXT NOT NULL DEFAULT ''")


def migrate_creation_schema(connection, schema):
    from management_portal.resources.creation import initialize_creation_schema
    table = f'"{schema}".resource_creations'
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is not None:
        connection.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS aborted BOOLEAN NOT NULL DEFAULT FALSE')
        connection.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS cleanup_complete BOOLEAN NOT NULL DEFAULT FALSE')
    initialize_creation_schema(connection, schema)


def retire_workspace_creation_schema(connection, schema):
    table = f'"{schema}".workspace_creations'
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None:
        return
    connection.execute(f'LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE')
    if connection.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
        raise RuntimeError('Nonempty legacy workspace_creations requires explicit ownership reconciliation')
    connection.execute(f'DROP TABLE {table}')


def migrate_context_file_references(context_refs):
    from management_portal.resource_refs import _address_from_item
    from management_portal.resources.file_references import file_reference_from_address
    result = dict(context_refs)
    resources = result.get('resources')
    if not isinstance(resources, list):
        return result
    migrated = []
    for item in resources:
        address = _address_from_item(item)
        reference = file_reference_from_address(address)
        if reference is not None:
            item = ({**item, 'file_reference': reference} if isinstance(item, Mapping)
                    else {'address': address, 'file_reference': reference})
        migrated.append(item)
    result['resources'] = migrated
    return result
