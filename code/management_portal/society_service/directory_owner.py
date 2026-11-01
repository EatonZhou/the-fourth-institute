"""Society's directory facts and lifecycle coordination.

Origin and ownership belong to the existing identity rows. Directory cleanup
contains deletion snapshots only; current locations still come from each owner.
"""
from management_common.json_columns import json_value


def directory_metadata(value, owner_id, *, creating=False):
    if value is None:
        if not creating:
            raise RuntimeError('Directory origin requires the offline migration')
        return {'origin': 'created', 'owner_id': owner_id}
    value = dict(value)
    if value.get('origin') not in {'created', 'adopted'} or value.get('owner_id') != owner_id:
        raise ValueError('Invalid object directory origin or owner_id')
    return value


def delete_folder(connection, *, table, identity, folder, actor):
    """Delete a directory according to the owning identity, in its transaction."""
    if table not in {'residents', 'lineages'}:
        raise ValueError('Unknown Society directory owner')
    key = 'resident_id' if table == 'residents' else 'lineage_id'
    row = connection.execute(f'SELECT directory_json FROM {table} WHERE {key}=%s FOR UPDATE',
                             (identity,)).fetchone()
    if row is None:
        raise KeyError(identity)
    metadata = directory_metadata(json_value(row['directory_json']), identity)
    from management_common.directory_move_store import cancel_owner_moves
    cancel_owner_moves(connection, identity)
    from .resident_workspace_deletion import remove_deleted_folder, delete_workspaces
    from .lineage_data_columns import now_iso_millis
    # An adopted folder stays, but objects explicitly owned by this identity
    # still follow their own origins, including created directories elsewhere.
    delete_workspaces(connection, owner_ids=[identity], deleted_at=now_iso_millis(), deleted_by=actor)
    from management_portal.resources.directory_registrations import unregister_identity
    unregister_identity(identity, connection=connection, actor=actor)
    return remove_deleted_folder(folder, connection=connection, owner_id=identity,
                                 origin=metadata['origin'], actor=actor)


