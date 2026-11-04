"""Society's directory facts and lifecycle coordination.

Ownership belongs to the existing identity rows. Directory cleanup
contains deletion snapshots only; current locations still come from each owner.
"""
from management_common.json_columns import json_value


def directory_metadata(value, owner_id, *, creating=False):
    if value is None:
        if not creating:
            raise RuntimeError('Directory ownership requires the offline migration')
        return {'owner_id': owner_id}
    value = dict(value)
    if set(value) != {'owner_id'} or value.get('owner_id') != owner_id:
        raise ValueError('Invalid object directory owner_id')
    return value


def upgrade_ownership(connection):
    """Remove retired directory classification in the installer's transaction.

    Preserve the owning identity and every unrelated field. No filesystem or
    runtime identity changes; repeated application has no effect.
    """
    from psycopg import sql
    from management_common.portal_database import _database_schema
    counts = {}
    for table in ('residents', 'lineages'):
        counts[table] = connection.execute(sql.SQL(
            "UPDATE {} SET directory_json=directory_json - 'origin' "
            "WHERE directory_json ? 'origin'").format(
                sql.Identifier(_database_schema('lineage'), table))).rowcount
    return counts


def delete_folder(connection, *, table, identity, folder, actor, deleted_at=None):
    """Delete a directory according to the owning identity, in its transaction."""
    if table not in {'residents', 'lineages'}:
        raise ValueError('Unknown Society directory owner')
    key = 'resident_id' if table == 'residents' else 'lineage_id'
    row = connection.execute(f'SELECT directory_json FROM {table} WHERE {key}=%s FOR UPDATE',
                             (identity,)).fetchone()
    if row is None:
        raise KeyError(identity)
    directory_metadata(json_value(row['directory_json']), identity)
    from management_common.directory_move_store import cancel_owner_moves
    cancel_owner_moves(connection, identity)
    from .resident_workspace_deletion import remove_deleted_folder, delete_workspaces
    from .lineage_data_columns import now_iso_millis
    timestamp = deleted_at or now_iso_millis()
    # Ownership includes separately registered directories outside this tree.
    delete_workspaces(connection, owner_ids=[identity], deleted_at=timestamp, deleted_by=actor)
    from management_portal.resources.directory_registrations import unregister_identity
    unregister_identity(identity, connection=connection, actor=actor)
    return remove_deleted_folder(folder, connection=connection, owner_id=identity,
                                 actor=actor, deleted_at=timestamp)


