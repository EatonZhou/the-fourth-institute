"""Unregister Workspaces and bindings atomically with durable file cleanup."""
from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_common.directory_paths import lexical_path, validate_removal
from management_common.directory_retention import enqueue_directory_cleanup
from management_common.instance_locations import located_path
from management_portal.resources.workspace_store import get_workspace_store, now


def delete_workspaces(connection, *, workspace_ids=(), paths=(), owner_ids=(), deleted_at, deleted_by,
                      preserve_resources=False):
    """An owned outer tree includes every nested registration, regardless of users.

    Deleting an owning identity also removes its directories outside this tree.
    """
    from management_portal.resources.directory_registrations import unregister_tree
    result = unregister_tree([lexical_path(located_path(str(path))) for path in paths],
        connection=connection, actor=deleted_by, workspace_ids=workspace_ids,
        owner_ids=owner_ids, deleted_at=deleted_at, preserve_resources=preserve_resources)
    return result['workspace_paths']


def unregister_workspace(workspace_id, *, connection, actor, authorize):
    store = get_workspace_store()
    store.cleanup.lock(connection)
    connection.execute(f'SELECT workspace_id FROM {store.table} '
                       'WHERE workspace_id=%s AND deleted_at IS NULL FOR UPDATE',
                       (workspace_id,)).fetchone()
    row = store.rows([workspace_id], connection=connection).get(workspace_id)
    if row is None:
        raise KeyError(workspace_id)
    authorize(store.descriptor(row))
    delete_workspaces(connection, workspace_ids=[workspace_id], deleted_at=now(), deleted_by=actor)
    descriptor = store.descriptor(store.rows([workspace_id], connection=connection, include_deleted=True)[workspace_id])
    descriptor['attributes']['directory_cleanup'] = store.cleanup.status(owner_id=workspace_id, connection=connection)
    return descriptor


def remove_deleted_folder(folder, *, connection, owner_id, actor='', deleted_at=None,
                          preserve_resources=False):
    """Enlist only the resource owner's safe cleanup roots in this transaction."""
    if not owner_id or connection is None:
        raise ValueError('Directory cleanup requires its owner transaction')
    folder = lexical_path(located_path(str(folder)))
    validate_removal(folder)
    queue = get_directory_cleanup_store()
    queue.lock(connection)
    from management_portal.resources.directory_registrations import unregister_tree
    result = unregister_tree([folder], owner_ids=[owner_id], connection=connection,
                             deleted_at=deleted_at or now(), actor=actor,
                             preserve_resources=preserve_resources)
    result['cleanup_ids'] = [enqueue_directory_cleanup(
        root, owner_id=owner_id, operation_id='delete-folder:' + owner_id,
        actor=actor, connection=connection) for root in result['cleanup_roots']]
    return result
