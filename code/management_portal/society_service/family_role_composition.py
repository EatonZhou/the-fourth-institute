"""Installing, reconciling and previewing the Router/Steward composition of a Family.

``family_roles`` reads the installed structure; this module changes it.  Duties
own executable structure and editable guides; role records only retain
identities and source versions.
"""
from copy import deepcopy
import secrets

from . import function_block_authoring as blocks
from .family_roles import duties, elements, owner, role_blocks


def _block(body, actor, title, elements=(), **fields):
    now = blocks._now()
    block = {'id': 'fbi_' + secrets.token_hex(6), 'title': title, 'template_id': None,
             'x': None, 'y': None, 'elements': list(elements), 'created_by': actor,
             'created_at': now, 'updated_by': actor, 'updated_at': now, **fields}
    body['function_blocks'].append(block)
    return block


def install(body, parent, key, source, actor):
    """Install one role's Resident, memories and duties; the count is what this call added."""
    family = parent['family']
    bindings = family.setdefault('role_bindings', {})
    instance = next((b for b in role_blocks(body, parent) if b['role']['key'] == key), None)
    if instance is None:
        if key in bindings:
            raise ValueError('角色引用已撤下；请核对 Family 生命周期，不能自动重建 Resident')
        rid = family[key]
        parent['elements'] = [e for e in parent['elements'] if e['id'] != rid]
        instance = _block(body, actor, key.title(), [{'id': rid, 'kind': 'resident', 'key': key + '_resident'}],
            template_id=source['template_id'], role={'key': key, 'family_id': parent['id'],
                'source': {k: source[k] for k in ('template_id', 'version')}, 'duties': {}, 'memories': {},
                'generated': {}, 'suppressed': []})
        bindings[key] = instance['id']
        parent['elements'].append({'id': instance['id'], 'kind': 'function_block', 'key': 'role:' + key})
    metadata = instance['role']
    existing = {b['id'] for b in body['function_blocks']}
    count = 0
    for item, section in [(m, 'memories') for m in source['body']['memories']] + [(d, 'duties') for d in source['body']['duties']]:
        name = item['key']
        if metadata[section].get(name) in existing:
            continue
        memory = {'level': item['level'], 'prompt': item['prompt']} if section == 'memories' else {'level': 'on_demand', 'prompt': item['guide']}
        parameters = {'source': {k: source[k] for k in ('template_id', 'version')}}
        if section == 'duties':
            parameters.update(family_task=name, prompt=item['prompt'])
        duty = _block(body, actor, item.get('title') or name, template_id=source['template_id'],
            duty={'holder_id': family[key], 'description': item.get('title') or name, 'category': 'common',
                  'position': len(blocks.duties(body, family[key])), 'memory': memory, 'parameters': parameters})
        metadata[section][name] = duty['id']
        instance['elements'].append({'id': duty['id'], 'kind': 'function_block', 'key': section + ':' + name})
        if section == 'duties':
            metadata['suppressed'] = [k for k in metadata['suppressed'] if owner(k) != (key, name)]
        count += 1
    return instance, count


def assemble(body, parent, sources, actor):
    for key, source in sources.items():
        install(body, parent, key, source, actor)


def reconcile(body, parent, actor, generate, *, installing=False):
    """Refresh roster references in installed duties, never reinstall a missing one."""
    roles = {b['role']['key']: b for b in role_blocks(body, parent)}
    owned = {(key, name): duty for key, role in roles.items() for duty in duties(body, role)
             for name, bid in role['role']['duties'].items() if bid == duty['id']}
    before_ids = {i['id'] for collection in ('components', 'wires') for i in body['program'][collection]}
    live = {e['id'] for d in owned.values() for e in d['elements']}
    for role in roles.values():
        metadata = role['role']
        if not installing:
            metadata['suppressed'] = sorted(set(metadata['suppressed']) | {
                key for key, identity in metadata['generated'].items() if identity not in live or identity not in before_ids})
    tracked = [e for d in owned.values() for e in d['elements'] if owner(e.get('key') or '')]
    tracked_ids = {e['id'] for e in tracked}
    authored = {i['id']: deepcopy(i) for collection in ('components', 'wires')
                for i in body['program'][collection] if i['id'] in tracked_ids}
    parent['elements'].extend(tracked)
    generate(body, parent, actor)
    for collection in ('components', 'wires'):
        for item in body['program'][collection]:
            old = authored.get(item['id'])
            if not old:
                continue
            # Ownership grants editing, too. Reconciliation refreshes explicit
            # Family arguments while retaining scripts, wiring, confirmations
            # and event choices authored in the duty workshop.
            if collection == 'wires':
                for profile_name, profile in old.get('config', {}).get('invoke_profiles', {}).items():
                    arguments = profile.get('arguments', {})
                    current = item.get('config', {}).get('invoke_profiles', {}).get(profile_name, {}).get('arguments', {})
                    for key in ('members', 'family', 'goal', 'threshold', 'steward', 'router', 'facade', 'family_id', 'lineage_id'):
                        if key in arguments and key in current:
                            arguments[key] = deepcopy(current[key])
            item.clear(); item.update(old)
    generated = [e for e in parent['elements'] if e['kind'] in {'component', 'wire'}]
    keep, removed = [], set()
    for element in generated:
        element_owner = owner(element.get('key') or '')
        if not element_owner or element_owner[0] not in parent['family']['role_bindings']:
            keep.append(element)
            continue
        duty, role = owned.get(element_owner), roles.get(element_owner[0])
        if not duty or not role or element['key'] in role['role']['suppressed']:
            removed.add(element['id'])
            continue
    for duty in owned.values():
        duty['elements'] = [e for e in duty['elements'] if e['id'] not in tracked_ids]
    for element in generated:
        element_owner = owner(element.get('key') or '')
        if element_owner in owned and element['id'] not in removed:
            owned[element_owner]['elements'].append(element)
    # A removed component also removes its observing/triggering wires. Shared
    # Family objects remain singular; no dangling references enter the program.
    body['program']['components'] = [c for c in body['program']['components'] if c['id'] not in removed]
    body['program']['wires'] = [w for w in body['program']['wires'] if w['id'] not in removed and not {w['source'], w['target']} & removed]
    surviving = {i['id'] for collection in ('components', 'wires') for i in body['program'][collection]}
    parent['elements'] = [e for e in parent['elements'] if e['kind'] not in {'component', 'wire'}] + [e for e in keep if e['id'] in surviving]
    for key, role in roles.items():
        role['role']['generated'] = {}
        for duty in duties(body, role):
            duty['elements'] = [e for e in duty['elements'] if e['kind'] not in {'component', 'wire'} or e['id'] in surviving]
            role['role']['generated'].update({e['key']: e['id'] for e in duty['elements'] if owner(e.get('key') or '')})
    # Effective guides/prompts live in the duty record, including message-only
    # recruit/route_review. Shared relay carries no second editable copy.
    for wire in body['program']['wires']:
        if wire['id'] not in {e['id'] for e in [*parent['elements'], *elements(body, parent)]}:
            continue
        for profile in wire.get('config', {}).get('invoke_profiles', {}).values():
            arguments = profile.get('arguments', {})
            if 'family_tasks' in arguments:
                entries = arguments['family_tasks'].get('value', {})
                entries = {k: v for k, v in entries.items() if v.get('role') not in parent['family']['role_bindings']}
                if entries:
                    arguments['family_tasks'] = {'value': entries}
                else:
                    arguments.pop('family_tasks')
