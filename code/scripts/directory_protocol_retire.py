"""Retirement cleanup for owned object directories outside all instance areas.

Invoked by the installer with all application clients stopped and PostgreSQL
available. Failure keeps the instance and durable queue intact for retry.
"""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def external_owned_directories(connection, instance):
    from management_common.instance_manifest import AREAS
    from management_common.portal_database import _database_schema
    from management_common.instance_locations import located_path
    from management_common.json_columns import json_value
    from management_portal.resources.directory_registrations import COLLECTIONS, locations
    boundaries = [instance.root, *(instance.area(area) for area in AREAS)]
    found = {}
    def collect(owner, path):
        absolute = Path(located_path(path))
        if not any(absolute.is_relative_to(root) for root in boundaries):
            from management_common.directory_paths import validate_removal
            validate_removal(absolute)
            found[str(absolute)] = owner
    workspace = '"' + _database_schema('workspace') + '".workspaces'
    if connection.execute('SELECT to_regclass(%s)', (workspace,)).fetchone()[0]:
        for row in connection.execute(f'SELECT owner_id,path FROM {workspace} WHERE deleted_at IS NULL').fetchall():
            collect(row['owner_id'], row['path'])
    registry = '"' + _database_schema('resource_registry') + '".registry_documents'
    if connection.execute('SELECT to_regclass(%s)', (registry,)).fetchone()[0]:
        for row in connection.execute(f'SELECT collection,document_id,document_json FROM {registry}').fetchall():
            document = json_value(row['document_json'])
            if row['collection'] in COLLECTIONS:
                for entry in locations(row['collection'], document):
                    collect(entry['owner_id'], entry['path'])
            elif row['collection'] == 'project_registry':
                for project in document.get('projects', []):
                    collect(project['directory']['owner_id'], project['path'])
    return found


def cleanup_external_owned():
    from management_common.instance_manifest import current_instance, AREAS
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    from management_common.directory_paths import cleanup_path
    from management_common.directory_retention import enqueue_directory_cleanup
    from management_common.directory_move_store import DirectoryMoveStore, cancel_owner_moves
    from management_portal.resources.creation import finish_instance_creations
    from management_common.owned_path_deletion import OwnedPathDeletionStore
    claim_errors = OwnedPathDeletionStore().recover()
    if claim_errors:
        raise RuntimeError('Claimed deletion remains pending: ' + '; '.join(claim_errors))
    creation_errors = finish_instance_creations()
    if creation_errors:
        raise RuntimeError('Creation cleanup remains pending: ' + '; '.join(creation_errors))
    # A crashed file-first move may have an unregistered target outside every
    # area. Restore/abort those operations before enumerating owner truth. The
    # installer already stopped clients; operation locks also exclude recovery.
    mover = DirectoryMoveStore()
    with mover.database.read() as connection:
        prepared = connection.execute(f"SELECT operation_id FROM {mover.table} WHERE phase IN ('prepared','files_ready')").fetchall()
    for row in prepared:
        with mover.operation_lock(row['operation_id']):
            mover.abort(row['operation_id'])
    queue = get_directory_cleanup_store()
    instance = current_instance()
    with queue.database.transaction() as connection:
        queue.lock(connection)
        # Retirement has stopped every client. No cwd/session reconciliation
        # remains; transfer the journal's deferred old-copy cleanup to the queue.
        owners = connection.execute(f"SELECT DISTINCT owner_id FROM {mover.table} WHERE phase='committed'").fetchall()
        for row in owners:
            cancel_owner_moves(connection, row['owner_id'])
        directories = external_owned_directories(connection, instance)
        boundaries = [instance.root, *(instance.area(area) for area in AREAS)]
        for task in queue.status(connection=connection):
            path = cleanup_path(task['location'])
            if not task['completed_at'] and not any(path.is_relative_to(root) for root in boundaries):
                directories[str(path)] = task['owner_id']
        ids = [enqueue_directory_cleanup(path, owner_id=owner, operation_id='instance-retire:' + instance.instance_id,
                             actor='instance-retirement', connection=connection) for path, owner in directories.items()]
    for cleanup_id in ids:
        queue.run_pending(cleanup_id=cleanup_id)
    pending = [task for cleanup_id in ids for task in queue.status(cleanup_id=cleanup_id) if not task['completed_at']]
    if pending:
        raise RuntimeError('External object cleanup remains pending: ' + ', '.join(task['location'] for task in pending))
    return {'external_owned_deleted': sorted(directories)}


if __name__ == '__main__':
    print(json.dumps(cleanup_external_owned(), ensure_ascii=False))
