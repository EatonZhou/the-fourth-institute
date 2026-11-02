"""Persist current Handoff locations in the caller's transaction.

Workspace IDs resolve current paths; attempts and other historical facts remain
unchanged. This storage owner has no execution, authorization or move policy.
"""
from pathlib import Path
from copy import deepcopy
import json

from management_common.portal_database import _database_schema
from management_common.instance_locations import located_path, stored_location
from management_common.location_records import transform
from management_common.json_columns import json_value, json_text
from management_portal.resources.workspace_store import get_workspace_store


def location_tables():
    """Tables exclusively rewritten by this owner, including mixed history/current JSON."""
    schema = _database_schema('lineage')
    return {(schema, table) for table in (
        'lineage_handoff_items', 'workspace_handoff_work', 'lineage_handoff_invocations')}


def _tables():
    schema = _database_schema('lineage')
    return tuple(f'"{schema}".{name}' for name in (
        'lineage_handoff_items', 'workspace_handoff_work', 'lineage_edges'))


def _current_path(workspace_id, fallback, rows):
    if workspace_id:
        return stored_location(rows[workspace_id]['path']) if workspace_id in rows else ''
    return fallback


def synchronize(connection, *, handoff_id=None, relocate=None, clear_missing=False):
    """Enlist current references in an owner transaction; retain attempt snapshots."""
    items, work, edges = _tables()
    if connection.execute('SELECT to_regclass(%s)', (items,)).fetchone()[0] is None:
        return []
    lineages = f'"{_database_schema("lineage")}".lineages'
    # Match the graph/runtime owners' row lock before changing their state.
    connection.execute(f'SELECT l.lineage_id FROM {lineages} l WHERE l.deleted_at IS NULL '
        f'AND EXISTS (SELECT 1 FROM {edges} e WHERE e.lineage_id=l.lineage_id '
        "AND e.edge_type='handoff' AND e.deleted_at IS NULL" +
        (' AND e.edge_id=%s' if handoff_id else '') + ') ORDER BY l.lineage_id FOR NO KEY UPDATE',
        (handoff_id,) if handoff_id else ()).fetchall()
    changed_lineages = set()
    selected = connection.execute(f'SELECT i.*,e.lineage_id FROM {items} i JOIN {edges} e ON e.edge_id=i.edge_id '
        'WHERE e.deleted_at IS NULL' + (' AND i.edge_id=%s' if handoff_id else '') + ' FOR UPDATE OF i',
        (handoff_id,) if handoff_id else ()).fetchall()
    progress_rows = connection.execute(f'SELECT * FROM {work}' +
        (' WHERE edge_id=%s' if handoff_id else '') + ' FOR UPDATE',
        (handoff_id,) if handoff_id else ()).fetchall()
    # The generic location codec traverses history too. Decode raw progress so
    # writing current paths cannot renormalize or relocate attempt snapshots.
    def raw_progress(row):
        value = row['progress_json']
        return json.loads(value) if isinstance(value, str) else deepcopy(value)
    progress = {r['edge_id']: raw_progress(r) for r in progress_rows}
    ids = {r[k] for r in selected for k in ('source_workspace_id', 'target_workspace_id') if r[k]}
    for value in progress.values():
        for entry in value.get('items', []):
            ids.update(v for v in ((entry.get('target') or {}).get('workspace_id'),
                                   (entry.get('result') or {}).get('target_workspace_id')) if v)
    rows = get_workspace_store().rows(ids, connection=connection)
    receipts = []
    def mapped(value):
        return relocate(value) if relocate and value else value
    for row in selected:
        runtime = progress.get(row['edge_id'], {})
        entry = next((x for x in runtime.get('items', []) if x.get('item_id') == row['item_id']), None)
        target_ids = {v for v in (row['target_workspace_id'],
            ((entry or {}).get('target') or {}).get('workspace_id'),
            ((entry or {}).get('result') or {}).get('target_workspace_id')) if v}
        if len(target_ids) > 1:
            raise ValueError('Conflicting Handoff target Workspace identities')
        target_id = next(iter(target_ids), '')
        source_path = _current_path(row['source_workspace_id'], mapped(row['source_path']), rows)
        target_path = _current_path(target_id, mapped(row['target_path']), rows)
        if clear_missing and not target_id:
            candidates = [row['target_path'], ((entry or {}).get('target') or {}).get('path'),
                          ((entry or {}).get('result') or {}).get('target_path')]
            if any(v and (Path(located_path(v)).exists() or Path(located_path(v)).is_symlink()) for v in candidates):
                raise ValueError('Unregistered existing target requires its directory owner')
            target_path = ''
        if (source_path, target_path, target_id or row['target_workspace_id']) != (
                row['source_path'], row['target_path'], row['target_workspace_id']):
            changed_lineages.add(row['lineage_id'])
        connection.execute(f'UPDATE {items} SET source_path=%s,target_path=%s,target_workspace_id=%s '
                           'WHERE edge_id=%s AND item_id=%s',
                           (source_path, target_path, target_id or row['target_workspace_id'], row['edge_id'], row['item_id']))
        if entry is not None:
            if entry.get('target'):
                entry['target']['path'] = target_path
            if entry.get('result'):
                result = transform(entry['result'], mapped)
                if 'source_path' in result:
                    result['source_path'] = source_path
                if 'target_path' in result:
                    result['target_path'] = target_path
                entry['result'] = result
        receipts.append({'handoff_id': row['edge_id'], 'item_id': row['item_id'],
                         'source_path': source_path, 'target_workspace_id': target_id,
                         'target_path': target_path, 'previous_target_path': row['target_path']})
    if relocate:
        invocations = f'"{_database_schema("lineage")}".lineage_handoff_invocations'
        for row in connection.execute(f'SELECT edge_id,cwd FROM {invocations} WHERE cwd IS NOT NULL FOR UPDATE').fetchall():
            after = mapped(row['cwd'])
            if after != row['cwd']:
                connection.execute(f'UPDATE {invocations} SET cwd=%s WHERE edge_id=%s', (after, row['edge_id']))
        for row in selected:
            if row['transfer_parameters_json'] is not None:
                before = json_value(row['transfer_parameters_json'])
                after = transform(before, mapped)
                if after != before:
                    connection.execute(f'UPDATE {items} SET transfer_parameters_json=%s WHERE edge_id=%s AND item_id=%s',
                                       (json_text(after), row['edge_id'], row['item_id']))
    for row in progress_rows:
        value = progress[row['edge_id']]
        if value != raw_progress(row):
            changed_lineages.add(row['lineage_id'])
            connection.execute(f'UPDATE {work} SET progress_json=%s WHERE lineage_id=%s AND edge_id=%s',
                               (json.dumps(value, ensure_ascii=False), row['lineage_id'], row['edge_id']))
    for lineage_id in sorted(changed_lineages):
        connection.execute(f'UPDATE {lineages} SET runtime_revision=runtime_revision+1,'
                           'runtime_updated_at=clock_timestamp(),updated_at=clock_timestamp() '
                           'WHERE lineage_id=%s AND deleted_at IS NULL', (lineage_id,))
    return receipts


def relocate_references(connection, source, target):
    from management_portal.directory_move_owner import relocated
    return synchronize(connection, relocate=lambda value: relocated(value, source, target))


