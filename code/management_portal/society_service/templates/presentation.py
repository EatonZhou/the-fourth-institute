"""Disposable template views, derived from the same ownership as installation.

No IDs here identify live Residents. The body remains the full primitive graph;
the hierarchy only declares which owned nodes a Resident menu can unfold.
"""
import re

from . import structure_layout


def references(value, path='config'):
    """Executable references, never prose mentioning an identity."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'script' and isinstance(item, str):
                # Family Effects can consume bound runtime arguments directly.
                # Read formal argument expressions, not IDs in prompt prose.
                executable='\n'.join(line for line in item.splitlines() if not line.lstrip().startswith('#'))
                for name in re.findall(r'\$\{arguments\.(facade|router|steward|members|family_id|member_done)\}', executable):
                    identity={'members':'member_resident','family_id':'family','member_done':'member_done'}.get(name,name+'_resident')
                    yield identity, path + '.script.arguments.' + name
            if key not in {'prompt', 'guide', 'family_tasks', 'script', 'description', 'title'}:
                yield from references(item, path + '.' + key)
    elif isinstance(value, list):
        for item in value:
            yield from references(item, path)
    elif isinstance(value, str):
        for key in re.findall(r'\{\{([a-zA-Z0-9_:.-]+)\}\}', value):
            yield key, path


def task_entries(body, block):
    """The (wire, trigger, task) triples of the Family tasks a Family block's own wires carry."""
    owned = {e['id'] for e in block['elements']}
    for wire in body['program']['wires']:
        if wire['id'] not in owned:
            continue
        for profile in wire.get('config', {}).get('invoke_profiles', {}).values():
            for trigger, task in profile.get('arguments', {}).get('family_tasks', {}).get('value', {}).items():
                yield wire, trigger, task


def annotate(projection, body, parent, roles, scopes, keys, *, source=None, role_key='', duty=False):
    graph = projection['body']
    visible = {r['key'] for section in ('roles', 'components') for r in graph[section]}
    residents, bindings, owners = {}, {}, {}
    titles = {'facade': '门面', 'router': 'Router', 'steward': 'Steward'}
    own_keys = {keys[e['id']] for scope in scopes for e in scope['elements'] if e['id'] in keys}
    if role_key == 'router':
        own_keys.update({'route_relay', 'relay'})
    used = {}
    for section in ('components', 'wires'):
        for row in graph[section]:
            for end in ('source', 'target'):
                if end in row:
                    used.setdefault(row[end], []).append({'component': row['key'], 'path': end})
            if row['key'] in own_keys:
                for key, path in references(row.get('config', {})):
                    used.setdefault(key, []).append({'component': row['key'], 'path': path})
    # Installation context is not itself a visible graph interface. Preserve
    # only externally referenced Residents, including unbound collection slots.
    graph['roles'] = [r for r in graph['roles'] if not role_key or r['key'] == role_key + '_resident' or r['key'] in used]
    visible = {r['key'] for section in ('roles', 'components') for r in graph[section]}
    for key in roles:
        local = key + '_resident'
        if local in used and local not in visible:
            graph['roles'].append({'key': local, 'from': 'binding', 'title': titles.get(key, '成员集合') if key != 'member' else '成员集合'})
            visible.add(local)
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

    router = residents.get('router_resident')
    interactions = []
    if router:
        route = next((d for d in router['duties'] if d['key'] == 'route'), None)
        for local in ('route_relay', 'relay'):
            if local in visible or any(w['key'] == local for w in graph['wires']):
                owners[local] = 'router_resident'
                router['members'].append(local)
                if route and local not in route['members']:
                    route['members'].append(local)
        # These describe actual public Effect calls, not executable wires.
        for source_key, target, purpose in (
            ('router', 'router_resident', '未命中直达时，交给 Router 选人'),
            ('router', 'member_resident', '直连／引用／续谈命中时转交原请求'),
            ('route_relay', 'member_resident', '转交所选成员'),
            ('router', 'steward_resident', '目标不可用→招募；额度不足→额度复核'),
            ('route_relay', 'steward_resident', '职责缺口→招募；额度不足→额度复核；格式无效／低置信→路由复核'),
        ):
            if source_key not in visible or target not in visible:
                continue
            interactions.append({'key': 'interaction:' + source_key + ':' + target,
                'source': source_key, 'target': target, 'label': purpose, 'kind': 'handoff',
                'collapsed_label': '转交所选成员' if target == 'member_resident' else
                                   '招募／额度／路由复核' if target == 'steward_resident' else purpose})
            for use in used.get(target, []):
                if use['component'] == source_key or source_key == 'route_relay' and use['component'] == 'relay':
                    use['purpose'] = purpose

    if role_key:
        from .. import family_roles
        for row in graph['components']:
            local = row['key']
            if local in own_keys:
                continue
            if local in owners:
                continue
            other = family_roles.owner(local)
            remote = 'facade' if local == 'facade_inbox' else other[0] if other and other[0] != role_key else None
            if remote:
                anchor = remote + '_resident'
                if anchor not in visible:
                    graph['roles'].append({'key': anchor, 'from': 'binding', 'title': titles[remote]})
                    visible.add(anchor)
                bindings[anchor] = {'key': 'peer' if remote in {'router', 'steward'} else remote,
                    'type': 'resident', 'cardinality': 'one', 'required': True}
                used.setdefault(anchor, []).append({'component': local, 'path': 'external primitive'})
                owners[local] = anchor

    if role_key:
        if 'family' in used:
            interface('binding_family', 'family', '所属 Family', 'family')
            used['binding_family'] = used['family']
        shared = next((key for key in visible if key.endswith('member_done') and key in used), None)
        if shared:
            interface(shared, 'member_done', '共享完成事件', 'event_source')
    if not duty:
        # Family-owned primitives remain one shared interface at organization
        # level. Their full graph is retained for the explicit expanded view.
        public = [r['key'] for r in graph['components'] if r['key'] not in owners and r['key'] not in bindings]
        if public:
            shared = 'binding_family' if role_key else 'binding_platform'
            interface(shared, 'family' if role_key else 'platform', 'Family 公共接口', 'family')
            used.setdefault(shared, []).extend({'component': key, 'path': 'external primitive'} for key in public)
            owners.update({key: shared for key in public})
            for wire in graph['wires']:
                if wire['key'] not in owners:
                    owners[wire['key']] = shared
    graph['presentation'] = {'level': 'duty' if duty else 'role' if role_key else 'organization',
                             'residents': residents, 'bindings': bindings, 'owners': owners,
                             'interactions': interactions}
    for local, binding in bindings.items():
        binding['uses'] = used.get(local, [])
        incoming = any(owners.get(w['target'], w['target']) == local for w in graph['wires'])
        outgoing = any(owners.get(w['source'], w['source']) == local for w in graph['wires'])
        binding['direction'] = 'both' if incoming and outgoing else 'out' if outgoing else 'in' if incoming else 'reference'
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
        edges = [{**w, 'source': owner(w['source']), 'target': owner(w['target'])} for w in
                 [*graph['wires'], *(interactions if role_key == 'router' else [])]]
        compact = structure_layout.positions({'roles': [{'key': key} for key in top],
            'wires': [w for w in edges if w['source'] != w['target']]})
        # Explicit executable references can be visible without a wire. Their
        # evidence stays a reference; never fabricate an event or connecting edge.
        if len(compact) > 4 and len({p['x'] for p in compact.values()}) == 1:
            compact = {key: {'x': 25 + (i // 3) * 30, 'y': 25 + (i % 3) * 25} for i, key in enumerate(top)}
        layout.update(compact)
        for key in set(owners.values()):
            members = [r['key'] for r in graph['components'] if owners.get(r['key']) == key]
            origin = compact[key]
            for i, member in enumerate(members):
                layout[member] = {'x': origin['x'] + 20 + (i // 4) * 20, 'y': origin['y'] + (i % 4 - 1.5) * 16}
    if role_key == 'router':
        # The role's expanded view reads left-to-right through the actual
        # selection and relay stages; the Steward is a side recipient.
        layout.update({key:{'x':x,'y':y} for key,x,y in (
            ('facade_resident',12,50),('router',32,50),('router_resident',52,50),
            ('route_relay',72,50),('member_resident',92,50),('steward_resident',72,84),
        ) if key in visible})
    graph['layout'] = layout
    return projection
