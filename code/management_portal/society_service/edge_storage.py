"""Workspace handoff configuration owned by graph edges.

Only transfer-method parameters and invocation inputs retain bounded JSON.
Attempts and outcomes are supplied by the runtime owner, never stored here.
"""
from __future__ import annotations

from typing import Any
from management_common.location_records import located_record, stored_record

from .definition_collections import column, declarations, decode, typed_values, upsert_many, validate_fields

ITEM_FIELDS = {
    'label': 'text', 'source_workspace_id': 'text', 'source_path': 'text', 'source_scope': 'text',
    'transfer_mode': 'text', 'transfer_method': 'text', 'transfer_parameters': 'object',
    'target_workspace_id': 'text', 'target_path': 'text', 'target_scope': 'text', 'target_label': 'text',
}
INVOCATION_FIELDS = {
    'enabled': 'boolean', 'prompt': 'text', 'include_envelope': 'boolean',
    'context_refs': 'object', 'additional_skills': 'array', 'cwd': 'text',
}
COLUMNS = {
    'lineage_handoff_items': ('edge_id', 'item_id', 'position', *[column(k,v) for k,v in ITEM_FIELDS.items()]),
    'lineage_handoff_invocations': ('edge_id', *[column(k,v) for k,v in INVOCATION_FIELDS.items()]),
}


def create_schema(connection: Any) -> None:
    connection.execute(f'''CREATE TABLE lineage_handoff_items (
        edge_id TEXT NOT NULL REFERENCES lineage_edges(edge_id), item_id TEXT NOT NULL,
        position INTEGER NOT NULL, {declarations(ITEM_FIELDS)}, PRIMARY KEY(edge_id,item_id),
        CHECK(transfer_mode IN ('direct','copy')),
        CHECK(transfer_method IN ('direct','filesystem_copy','git_worktree')))''')
    connection.execute(f'''CREATE TABLE lineage_handoff_invocations (
        edge_id TEXT PRIMARY KEY REFERENCES lineage_edges(edge_id), {declarations(INVOCATION_FIELDS)})''')


def put_many(connection: Any, edges: list[dict]) -> None:
    """Reconcile handoff items in one batch, including removed/type-changed edges."""
    if not edges:
        return
    rows = []
    for edge in edges:
        items = edge.get('items') or []
        ids = [item['item_id'] for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate handoff item ID')
        for position, item in enumerate(items):
            validate_fields(item, {'item_id','label','source','transfer','target'}, 'handoff item')
            flat = {'label': item.get('label')}
            for prefix, keys in (('source',('workspace_id','path','scope')), ('target',('workspace_id','path','scope','label')), ('transfer',('mode','method','parameters'))):
                obj = item.get(prefix) or {}
                validate_fields(obj, set(keys), 'handoff ' + prefix)
                flat.update({prefix+'_'+k: obj[k] for k in keys if k in obj})
            rows.append({'edge_id': edge['id'], 'item_id': item['item_id'], 'position': position,
                         **typed_values(stored_record(flat), ITEM_FIELDS)})
    current = connection.execute('SELECT * FROM lineage_handoff_items WHERE edge_id=ANY(%s)',
                                 ([edge['id'] for edge in edges],)).fetchall()
    desired = {(row['edge_id'], row['item_id']) for row in rows}
    removed = [(row['edge_id'], row['item_id']) for row in current
               if (row['edge_id'], row['item_id']) not in desired]
    if removed:
        connection.executemany('DELETE FROM lineage_handoff_items WHERE edge_id=%s AND item_id=%s', removed)
    # Compare decoded values: PostgreSQL returns JSON objects/timestamps, whereas
    # typed_values prepares their SQL encodings. Paths stay in stored form.
    old = {(row['edge_id'], row['item_id']): (row['position'], decode(row, ITEM_FIELDS)) for row in current}
    changed = [row for row in rows if old.get((row['edge_id'], row['item_id'])) !=
               (row['position'], decode(row, ITEM_FIELDS))]
    upsert_many(connection, 'lineage_handoff_items', ('edge_id', 'item_id'), changed)


def read_many(connection: Any, edges: list) -> dict:
    result = {e['edge_id']: ({'items':[]} if e['edge_type']=='handoff' else {}) for e in edges}
    if not result:
        return result
    ids = list(result)
    for row in connection.execute('SELECT * FROM lineage_handoff_items WHERE edge_id=ANY(%s) ORDER BY edge_id,position', (ids,)).fetchall():
        flat = located_record(decode(row,ITEM_FIELDS))
        item = {'item_id':row['item_id'], **({'label':flat['label']} if 'label' in flat else {})}
        for prefix in ('source','transfer','target'):
            item[prefix] = {k.removeprefix(prefix+'_'):v for k,v in flat.items() if k.startswith(prefix+'_')}
        result[row['edge_id']].setdefault('items',[]).append(item)
    # IDs remain authoritative even before explicit reconciliation of old
    # materialized path fields. A deleted ID never falls back to an old root.
    from management_portal.resources.workspace_store import get_workspace_store
    ids = {item[prefix].get('workspace_id') for value in result.values()
           for item in value.get('items', []) for prefix in ('source', 'target')
           if item[prefix].get('workspace_id')}
    rows = get_workspace_store().rows(ids, connection=connection)
    for value in result.values():
        for item in value.get('items', []):
            for prefix in ('source', 'target'):
                wid = item[prefix].get('workspace_id')
                if wid:
                    item[prefix]['path'] = rows[wid]['path'] if wid in rows else ''
    return result
