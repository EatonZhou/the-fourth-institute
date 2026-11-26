"""Disposable template views, derived from the same ownership as installation.

No IDs here identify live Residents. The body remains the full primitive graph;
the hierarchy only declares which owned nodes a Resident menu can unfold.
"""
from . import structure_layout


def annotate(projection, body, parent, roles, scopes, keys, *, source=None, role_key='', duty=False):
    graph = projection['body']
    visible = {r['key'] for section in ('roles', 'components') for r in graph[section]}
    residents, bindings, owners = {}, {}, {}
    titles = {'facade': '门面', 'router': 'Router', 'steward': 'Steward'}
    for key, rid in roles.items():
        local = key + '_resident'
        if local not in visible:
            continue
        own = not duty and (key == role_key if role_key else key != 'member')
        if not own:
            binding_key = 'target' if duty and key == 'steward' else 'members' if key == 'member' else 'peer' if role_key and key in {'router', 'steward'} else key
            bindings[local] = {'key': binding_key, 'type': 'resident', 'direction': 'both',
                               'cardinality': 'many' if key == 'member' else 'one', 'required': key != 'member'}
            continue
        role = next((s for s in scopes if s.get('role', {}).get('key') == key), None)
        meta = role.get('role', {}) if role else {}
        resident = {'key': local, 'title': titles.get(key, key), 'role_key': key,
                    'source': meta.get('source', {}), 'duties': [], 'memories': [], 'members': []}
        resident['settings'] = source['body'].get('resident', {}) if role_key and source else source.get('body', {}).get('resident_settings', {}).get(key, {}) if source else {}
        residents[local] = resident
        memory_ids = set(meta.get('memories', {}).values())
        records = {s['id']: s for s in [*scopes, *(b for b in body['function_blocks'] if b['id'] in memory_ids)]}
        for scope in records.values():
            record = scope.get('duty', {})
            if record.get('holder_id') != rid:
                continue
            members = [keys[e['id']] for e in scope['elements'] if e['id'] in keys]
            params = record.get('parameters', {})
            task = params.get('family_task')
            item = {'key': task or scope['id'], 'title': scope['title'], 'members': members,
                    'memory': record.get('memory'), 'prompt': params.get('prompt', '')}
            if task:
                resident['duties'].append(item)
            elif record.get('memory'):
                memory_key = next((name for name, identity in meta.get('memories', {}).items() if identity == scope['id']), '')
                resident['memories'].append({**record['memory'], 'key': memory_key, 'title': scope['title'], 'source': '角色记忆'})
            for member in members:
                if member != local:
                    owners[member] = local
                    resident['members'].append(member)
        if role_key and source:
            resident['source'] = {name: source.get(name, '') for name in ('template_id', 'version')}
            resident['editable'] = True
            # Even message-only duties have a menu entry, with an honest empty
            # local graph instead of fabricated Events or duplicate relays.
            for item in source['body']['duties']:
                if not any(d['key'] == item['key'] for d in resident['duties']):
                    resident['duties'].append({**item, 'members': [], 'memory': {'level': 'on_demand', 'prompt': item['guide']}})
    # The legacy recipe's task owner is still explicit; do not invent a role
    # source or make it writable as a second independent definition.
    from .family_graph import task_entries
    for wire, trigger, task in task_entries(body, parent):
        owner = residents.get(task.get('role', '') + '_resident')
        if owner and not any(d['key'] == trigger for d in owner['duties']):
            owner['duties'].append({'key': trigger, 'title': task['title'], 'members': [keys[wire['id']]],
                'memory': {'level': 'on_demand', 'prompt': task['guide']}, 'prompt': task['prompt'], 'control': keys[wire['id']]})

    def interface(local, key, title, kind, cardinality='one', required=True):
        if local not in visible:
            graph['components'].append({'key': local, 'kind': 'event_source', 'title': title, 'config': {}})
            visible.add(local)
        bindings[local] = {'key': key, 'type': kind, 'direction': 'both', 'cardinality': cardinality, 'required': required}

    if role_key:
        interface('binding_family', 'family', '所属 Family', 'family')
        if 'facade_resident' not in visible:
            interface('binding_facade', 'facade', 'Family 门面', 'resident')
        if 'member_resident' not in visible:
            interface('binding_members', 'members', '成员集合', 'resident', 'many', False)
        peer = 'steward' if role_key == 'router' else 'router'
        if peer + '_resident' not in visible:
            interface('binding_peer', 'peer', titles[peer], 'resident')
        shared = next((key for key in visible if key.endswith('member_done')), 'binding_member_done')
        interface(shared, 'member_done', '共享完成事件', 'event_source')
    if not duty:
        # Family-owned primitives remain one shared interface at organization
        # level. Their full graph is retained for the explicit expanded view.
        public = [r['key'] for r in graph['components'] if r['key'] not in owners and r['key'] not in bindings]
        if public:
            shared = 'binding_family' if role_key else 'binding_platform'
            if not role_key:
                interface(shared, 'platform', 'Family 公共接口', 'family')
            owners.update({key: shared for key in public})
            for wire in graph['wires']:
                if wire['key'] not in owners:
                    owners[wire['key']] = shared
    graph['presentation'] = {'level': 'duty' if duty else 'role' if role_key else 'organization',
                             'residents': residents, 'bindings': bindings, 'owners': owners}
    for local, binding in bindings.items():
        incoming = any(w['target'] == local for w in graph['wires'])
        outgoing = any(w['source'] == local for w in graph['wires'])
        binding['direction'] = 'both' if incoming and outgoing else 'out' if outgoing else 'in'
    for row in graph['roles']:
        if row['key'] in bindings and row['from'] != 'target':
            row['from'] = 'binding'
    # All views and application use these coordinates. Collapsing a role never
    # moves its visible Resident anchor away from the placement geometry.
    layout = structure_layout.positions(graph)
    if not duty:
        def owner(key):
            return owners.get(key, key)
        top = [r['key'] for section in ('roles', 'components') for r in graph[section] if r['key'] not in owners]
        edges = [{**w, 'source': owner(w['source']), 'target': owner(w['target'])} for w in graph['wires']]
        compact = structure_layout.positions({'roles': [{'key': key} for key in top],
            'wires': [w for w in edges if w['source'] != w['target']]})
        # Unconnected, explicitly declared interfaces are still visible. A
        # compact grid avoids fitting a tall column of unused declarations.
        if len(compact) > 4 and len({p['x'] for p in compact.values()}) == 1:
            compact = {key: {'x': 25 + (i // 3) * 30, 'y': 25 + (i % 3) * 25} for i, key in enumerate(top)}
        layout.update(compact)
        for key in set(owners.values()):
            members = [r['key'] for r in graph['components'] if owners.get(r['key']) == key]
            origin = compact[key]
            for i, member in enumerate(members):
                layout[member] = {'x': origin['x'] + 20 + (i // 4) * 20, 'y': origin['y'] + (i % 4 - 1.5) * 16}
    graph['layout'] = layout
    return projection
