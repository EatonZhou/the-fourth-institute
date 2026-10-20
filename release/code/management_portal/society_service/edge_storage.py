"""Workspace handoff configuration owned by graph edges.

Only transfer-method parameters and invocation inputs retain bounded JSON.
Attempts and outcomes are supplied by the runtime owner, never stored here.
"""
from __future__ import annotations

from typing import Any

from .definition_collections import column, declarations, decode, typed_values, upsert, validate_fields

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


def put(connection: Any, edge: dict) -> None:
    eid = edge['id']
    items = edge.get('items') or []
    ids = [item['item_id'] for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate handoff item ID')
    connection.execute('DELETE FROM lineage_handoff_items WHERE edge_id=%s AND NOT (item_id=ANY(%s))', (eid,ids))
    for position, item in enumerate(items):
        validate_fields(item, {'item_id','label','source','transfer','target'}, 'handoff item')
        flat = {'label': item.get('label')}
        for prefix, keys in (('source',('workspace_id','path','scope')), ('target',('workspace_id','path','scope','label')), ('transfer',('mode','method','parameters'))):
            obj = item.get(prefix) or {}
            validate_fields(obj, set(keys), 'handoff ' + prefix)
            flat.update({prefix+'_'+k: obj[k] for k in keys if k in obj})
        upsert(connection, 'lineage_handoff_items', {'edge_id':eid,'item_id':item['item_id']}, {'position':position, **typed_values(flat,ITEM_FIELDS)})


def read_many(connection: Any, edges: list) -> dict:
    result = {e['edge_id']: ({'items':[]} if e['edge_type']=='handoff' else {}) for e in edges}
    if not result:
        return result
    ids = list(result)
    for row in connection.execute('SELECT * FROM lineage_handoff_items WHERE edge_id=ANY(%s) ORDER BY edge_id,position', (ids,)).fetchall():
        flat = decode(row,ITEM_FIELDS)
        item = {'item_id':row['item_id'], **({'label':flat['label']} if 'label' in flat else {})}
        for prefix in ('source','transfer','target'):
            item[prefix] = {k.removeprefix(prefix+'_'):v for k,v in flat.items() if k.startswith(prefix+'_')}
        result[row['edge_id']].setdefault('items',[]).append(item)
    return result
