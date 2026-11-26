"""Exact identity retirement, separate from explicit directory-content deletion."""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from management_common import directory_dependencies as deps
from management_common.directory_paths import cleanup_location, cleanup_path, DirectoryComparison, validate_removal
from management_common.directory_cleanup_store import DirectoryCleanupStore
from management_common.json_columns import json_value
from management_common.portal_database import _database_schema


_INITIAL_REGISTRATION = ContextVar('initial_identity_directory_registration', default=None)


@contextmanager
def _initial_registration(protection):
    """Creation owns this transaction-bound attestation and its lifetime."""
    token = _INITIAL_REGISTRATION.set(protection)
    try:
        yield
    finally:
        _INITIAL_REGISTRATION.reset(token)


def record_identity_directory(identity, root, *, connection):
    """Society calls after writing/locking its authoritative identity row."""
    if not identity or not root:
        raise ValueError('Identity and authoritative directory are required')
    protection = _INITIAL_REGISTRATION.get()
    if protection is not None:
        protection.assert_directory(Path(root))
        deps.replace_initial_identity(connection, identity, Path(root), protection=protection)
        return
    deps.replace(connection, 'identity', identity, [('', identity, Path(root), 'owned')])


def retire_identity_directory(identity, root, *, connection, actor, deleted_at):
    from .workspace_store import get_workspace_store
    from .directory_registrations import _unregister_identity_metadata
    queue = DirectoryCleanupStore(connection=connection)
    queue.lock(connection)
    deps.require_ready(connection)
    s = deps.schema()
    location = cleanup_location(root)
    previous = connection.execute(f'SELECT root,plan FROM {s}.identity_retirements WHERE identity=%s', (identity,)).fetchone()
    if previous:
        if previous['root'] != location:
            raise ValueError('Retired identity directory changed')
        return dict(previous['plan'])
    entries = [dict(row) for row in connection.execute(f'SELECT * FROM {s}.directory_dependencies '
        'WHERE owner_id=%s ORDER BY kind,object_id,slot', (identity,)).fetchall()]
    own = [e for e in entries if e['kind'] == 'identity' and e['object_id'] == identity]
    if len(own) != 1 or own[0]['location'] != location:
        raise deps.DirectoryDependenciesMissing('Identity directory has no matching migrated owner record')
    selected = [e for e in entries if e['disposition'] == 'owned' and e['kind'] in {'identity', 'workspace', 'artifact'}]
    selected_keys = {(e['kind'], e['object_id'], e['slot']) for e in selected}
    comparison = DirectoryComparison()
    roots = []
    for entry in selected:
        path = cleanup_path(entry['location'])
        validate_removal(path)
        if not any(comparison.contains(other, path) for other in roots):
            roots = [other for other in roots if not comparison.contains(path, other)] + [path]
    preserved, frozen = set(), []
    for path in sorted(roots):
        protections = sorted({row['location'] for row in deps.overlaps(connection, path, comparison=comparison,
            kinds=['identity', 'workspace', 'artifact', 'resource'])
            if (row['kind'], row['object_id'], row['slot']) not in selected_keys
            and (row['kind'] not in {'identity', 'workspace'}
                 or comparison.contains(path, cleanup_path(row['location'])))})
        # A foreign ancestor also protects this allocation. Keep its location
        # in the frozen plan; the worker uses exactly these cuts.
        preserved.update(str(cleanup_path(item)) for item in protections)
        frozen.append((path, protections))
    store = get_workspace_store()
    workspace_ids = sorted({e['object_id'] for e in selected if e['kind'] == 'workspace'})
    workspace_paths = []
    for wid in workspace_ids:
        row = connection.execute(f'SELECT * FROM {store.table} WHERE workspace_id=%s FOR UPDATE', (wid,)).fetchone()
        expected = next(e for e in selected if e['kind'] == 'workspace' and e['object_id'] == wid)
        if (not row or row['deleted_at'] or row['owner_id'] != identity
                or cleanup_location(cleanup_path(row['path'])) != expected['location']):
            raise deps.DirectoryDependenciesMissing('Workspace ownership changed without dependency refresh')
        workspace_paths.append(str(cleanup_path(row['path'])))
        store.mark_deleted(wid, deleted_at=deleted_at, deleted_by=actor, connection=connection)
        _unregister_identity_metadata(wid, connection=connection, actor=actor)
    artifacts = [e['object_id'] for e in selected if e['kind'] == 'artifact']
    if artifacts:
        table = '"' + _database_schema('directory_retention') + '".artifacts'
        connection.execute(f'DELETE FROM {table} WHERE owner_id=%s AND location=ANY(%s::text[])', (identity, artifacts))
    cleanup_ids = [queue.enqueue(path, owner_id=identity, operation_id='identity-retirement:' + identity,
        actor=actor, connection=connection, preserved_locations=protections) for path, protections in frozen]
    _unregister_identity_metadata(identity, connection=connection, actor=actor)
    deps.replace(connection, 'identity', identity, [])
    plan = {'plan_id': 'identity-retirement:' + identity, 'resource_ids': [identity, *workspace_ids],
        'workspace_ids': workspace_ids, 'workspace_paths': workspace_paths,
        'preserved_paths': sorted(preserved), 'cleanup_roots': [str(path) for path in sorted(roots)],
        'cleanup_ids': cleanup_ids}
    connection.execute(f'INSERT INTO {s}.identity_retirements(identity,root,plan) VALUES (%s,%s,%s::jsonb)',
                       (identity, location, json.dumps(plan)))
    return plan


def migrate_dependencies(connection, *, identities=None, identity_source=None, check_budget=lambda: None):
    """Rebuild resource dependencies and invoke an explicit identity owner port.

    Installation composes the source in this same transaction. Resource-only
    instances must explicitly pass an empty identity list; an omitted source
    cannot certify readiness by silently skipping an installed owner.
    """
    if (identities is None) == (identity_source is None):
        raise ValueError('Migration requires exactly one explicit identity source')
    if identity_source is not None and not callable(identity_source):
        raise ValueError('Migration identity source must be callable')
    from .registry_directory_records import COLLECTIONS, GUARD_CONDITION, refresh_directory_dependency
    from .workspace_store import initialize_directory_owner as initialize_workspaces, refresh_directory_dependency as refresh_workspace
    from .registry_store import initialize_directory_owner as initialize_registry
    check_budget()
    queue = DirectoryCleanupStore(connection=connection)
    queue.lock(connection)
    deps.prepare_write(connection)
    previous_migration = connection.execute("SELECT current_setting('tfi.directory_dependency_migration',true)").fetchone()[0]
    connection.execute("SELECT set_config('tfi.directory_dependency_migration','v1',true)")
    initialize_workspaces(connection)
    check_budget()
    initialize_registry(connection)
    check_budget()
    deps.initialize(connection)
    s = deps.schema()
    connection.execute(f'UPDATE {s}.directory_dependency_state SET ready=false')
    connection.execute(f'DELETE FROM {s}.directory_dependencies')
    connection.execute(f'DELETE FROM {s}.directory_dependency_dirty')
    connection.execute(f"ALTER TABLE {queue.table} ADD COLUMN IF NOT EXISTS preserved_locations jsonb NOT NULL DEFAULT '[]'")
    deps.install_claim_guard(connection, table=queue.table, kind='cleanup', key='cleanup_id')
    deps.install_claim_guard(connection, table=queue.reservations, kind='reservation', key='operation_id')
    for row in connection.execute(f'SELECT cleanup_id FROM {queue.table} WHERE completed_at IS NULL').fetchall():
        check_budget()
        queue.refresh_cleanup(row['cleanup_id'], connection=connection)
    for row in connection.execute(f'SELECT DISTINCT operation_id FROM {queue.reservations}').fetchall():
        check_budget()
        queue.refresh_reservations(row['operation_id'], connection=connection)
    workspace_table = '"' + _database_schema('workspace') + '".workspaces'
    deps.install_source_guard(connection, table=workspace_table, kind='workspace', key='workspace_id',
        watched=['path', 'owner_id', 'deleted_at'], condition='NEW.deleted_at IS NULL')
    for row in connection.execute(f'SELECT workspace_id FROM {workspace_table} WHERE deleted_at IS NULL').fetchall():
        check_budget()
        refresh_workspace(connection, row['workspace_id'])
    table = '"' + _database_schema('resource_registry') + '".registry_documents'
    deps.install_source_guard(connection, table=table, kind='resource',
        key="collection || ':' || {row}.document_id", watched=['collection', 'document_id', 'document_json'],
        condition=GUARD_CONDITION)
    for row in connection.execute(f'SELECT collection,document_id,document_json FROM {table} WHERE collection=ANY(%s)',
                                   (list(COLLECTIONS),)).fetchall():
        check_budget()
        refresh_directory_dependency(connection, row['collection'], row['document_id'], json_value(row['document_json']))
    from management_common.directory_retention import RetentionStore, refresh_artifact
    retention_table = RetentionStore(connection=connection).table
    if connection.execute('SELECT to_regclass(%s)', (retention_table,)).fetchone()[0]:
        deps.install_source_guard(connection, table=retention_table, kind='artifact', key='location',
            watched=['location', 'owner_id', 'category'])
        for row in connection.execute(f'SELECT location FROM {retention_table}').fetchall():
            check_budget()
            refresh_artifact(connection, row['location'])
    check_budget()
    if identity_source is not None:
        identity_source(connection)
    else:
        for identity, root in identities:
            check_budget()
            record_identity_directory(identity, root, connection=connection)
    check_budget()
    from management_common.directory_move_store import initialize_move_schema
    initialize_move_schema(connection, queue.database.schema)
    comparison = DirectoryComparison()
    examined = augmented = added = 0
    for row in connection.execute(f'SELECT cleanup_id,location,preserved_locations FROM {queue.table} WHERE completed_at IS NULL').fetchall():
        check_budget()
        examined += 1
        path = cleanup_path(row['location'])
        entries = deps.overlaps(connection, path, comparison=comparison, kinds=['identity', 'workspace', 'resource', 'artifact'])
        cuts = sorted(set(row['preserved_locations']) | {entry['location'] for entry in entries
            if entry['kind'] not in {'identity', 'workspace'} or comparison.contains(path, cleanup_path(entry['location']))})
        additions = set(cuts) - set(row['preserved_locations'])
        if additions:
            augmented += 1
            added += len(additions)
            connection.execute(f'UPDATE {queue.table} SET preserved_locations=%s::jsonb WHERE cleanup_id=%s',
                               (json.dumps(cuts), row['cleanup_id']))
    check_budget()
    connection.execute(f'UPDATE {s}.directory_dependency_state SET ready=true')
    connection.execute("SELECT set_config('tfi.directory_dependency_migration',%s,true)", (previous_migration or '',))
    return {'schema': 'identity-directory-dependencies.v1',
            'pending_tasks_examined': examined, 'pending_tasks_augmented': augmented,
            'preserved_locations_added': added, 'completed_tasks_modified': 0,
            'entries': connection.execute(
        f'SELECT count(*) FROM {s}.directory_dependencies').fetchone()[0]}
