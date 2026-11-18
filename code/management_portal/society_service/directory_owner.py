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
    from management_common.directory_dependencies import prepare_write
    prepare_write(connection)
    from management_common.directory_move_store import cancel_owner_moves
    cancel_owner_moves(connection, identity)
    from management_portal.resources.directory_identity_lifecycle import retire_identity_directory
    from .lineage_data_columns import now_iso_millis
    timestamp = deleted_at or now_iso_millis()
    # Ownership/dependencies were registered at creation or during the explicit
    # offline migration. The resource owner persists the exact cleanup plan in
    # this transaction; neither this caller nor its worker discovers a tree.
    return retire_identity_directory(identity, folder, connection=connection,
                                     actor=actor, deleted_at=timestamp)


def record_resident_directory(connection, resident_id, lineage_id):
    """Index the current root from canonical membership, never a usage binding."""
    from .lineage_storage import lineage_folders
    from management_portal.resources.directory_identity_lifecycle import record_identity_directory
    root = lineage_folders([lineage_id], connection=connection)[lineage_id]
    record_identity_directory(resident_id, root / 'residents' / resident_id, connection=connection)


def record_lineage_directory(connection, lineage_id, project_id, *, include_residents=False):
    """A Project handoff relocates this Lineage and only its indexed members."""
    from .lineage_storage import lineage_folder_for_project
    from management_portal.resources.directory_identity_lifecycle import record_identity_directory
    root = lineage_folder_for_project(lineage_id, project_id)
    record_identity_directory(lineage_id, root, connection=connection)
    if include_residents:
        rows = connection.execute(
            'SELECT r.resident_id FROM lineage_nodes n JOIN residents r ON r.resident_id=n.node_id '
            'WHERE n.lineage_id=%s AND r.deleted_at IS NULL ORDER BY r.resident_id',
            (lineage_id,),
        ).fetchall()
        for row in rows:
            rid = row['resident_id']
            record_identity_directory(rid, root / 'residents' / rid, connection=connection)


def migrate_identity_directories(connection):
    """One offline backfill of authoritative roots; never called by deletion.

    The install/migration owner provides an external connection in the same
    transaction as its Resource/Workspace backfill. Its search_path is not a
    Society identity or schema selector.
    """
    from .lineage_storage import lineage_folder_for_project
    from management_common.portal_database import _database_schema
    from management_portal.resources.directory_identity_lifecycle import record_identity_directory
    schema = '"' + _database_schema('lineage') + '"'
    roots = {}
    for row in connection.execute(
        f'SELECT lineage_id,project_id,directory_json FROM {schema}.lineages WHERE deleted_at IS NULL ORDER BY lineage_id',
    ).fetchall():
        lid = row['lineage_id']
        directory_metadata(json_value(row['directory_json']), lid)
        root = lineage_folder_for_project(lid, str(row['project_id'] or ''))
        record_identity_directory(lid, root, connection=connection)
        roots[lid] = root
    resident_count = 0
    for row in connection.execute(
        f'SELECT r.resident_id,r.directory_json,n.lineage_id FROM {schema}.residents r '
        f'LEFT JOIN {schema}.lineage_nodes n ON n.node_id=r.resident_id '
        'WHERE r.deleted_at IS NULL ORDER BY r.resident_id',
    ).fetchall():
        rid = row['resident_id']
        directory_metadata(json_value(row['directory_json']), rid)
        if row['lineage_id'] not in roots:
            raise RuntimeError('Live Resident has no live canonical Lineage: ' + rid)
        record_identity_directory(rid, roots[row['lineage_id']] / 'residents' / rid, connection=connection)
        resident_count += 1
    return {'lineages': len(roots), 'residents': resident_count}


