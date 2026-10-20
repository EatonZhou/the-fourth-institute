"""Reject reuse of deleted resource identities at canonical write boundaries."""
from management_common.portal_database import portal_database


def reject_retired(connection, schema, resource_ids):
    table = f'"{schema}".retired_identities'
    if connection.execute('SELECT to_regclass(%s) AS name', (table,)).fetchone()['name'] is None:
        return
    ids = sorted(set(resource_ids))
    if not ids:
        return
    # Keep a maintenance retirement from committing between this check and
    # the caller's write. The maintenance owner takes the exclusive lock.
    for resource_id in ids:
        connection.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended(%s, 0))',
                           (schema + ':retired-identity:' + resource_id,))
    row = connection.execute(f'SELECT resource_id FROM {table} WHERE resource_id=ANY(%s) LIMIT 1', (ids,)).fetchone()
    if row:
        raise PermissionError('Cannot write or inherit from retired identity: ' + row['resource_id'])


def require_live_identity(connection, resource_id, *, parents=()):
    reject_retired(connection, portal_database(schema='resource_registry').schema,
                   [resource_id, *(parent for parent in parents if parent)])


def check_registry_write(connection, schema, collection, resource_id, document):
    if collection in {'resident', 'lineage', 'workbench', 'family'} and document.get('status') != 'deleted':
        reject_retired(connection, schema, [resource_id])
