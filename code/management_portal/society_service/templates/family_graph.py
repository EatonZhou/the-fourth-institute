"""Family recipe editing projects the same graph that installation generates.

The projection is disposable: local role keys stand for application-time IDs.
Only recipe fields are saved; platform-owned wiring has a single runtime owner.
"""
from copy import deepcopy

from .registry import TemplateError
from .. import family_tasks


def task_entries(body, block):
    owned = {e['id'] for e in block['elements']}
    for wire in body['program']['wires']:
        if wire['id'] not in owned:
            continue
        for profile in wire.get('config', {}).get('invoke_profiles', {}).values():
            for trigger, task in profile.get('arguments', {}).get('family_tasks', {}).get('value', {}).items():
                yield wire, trigger, task


def apply_tasks(body, block, tasks):
    if not isinstance(tasks, dict):
        raise TemplateError('Family tasks 必须是场景对象')
    if block['family'].get('role_bindings'):
        from .. import family_roles
        available = family_roles.configured(body, block)
        if set(tasks) - set(available):
            raise TemplateError('Family 模板包含未安装的工作场景')
        for role in family_roles.role_blocks(body, block):
            for duty in family_roles.duties(body, role):
                trigger = duty['duty']['parameters'].get('family_task')
                fields = tasks.get(trigger, {})
                if not isinstance(fields, dict) or set(fields) - {'guide', 'prompt'}:
                    raise TemplateError('Family 场景只允许修改指南和触发提示词')
                if 'guide' in fields:
                    duty['duty']['memory']['prompt'] = fields['guide']
                if 'prompt' in fields:
                    duty['duty']['parameters']['prompt'] = fields['prompt']
        return
    available = {trigger for _, trigger, _ in task_entries(body, block)}
    if set(tasks) - available:
        raise TemplateError('Family 模板包含不受支持的工作场景')
    for trigger, fields in tasks.items():
        if not isinstance(fields, dict) or set(fields) - {'guide', 'prompt'}:
            raise TemplateError('Family 场景只允许修改指南和触发提示词')
    for _, trigger, task in task_entries(body, block):
        task.update(deepcopy(tasks.get(trigger, {})))
    try:
        family_tasks.validate_routes(body, block)
    except ValueError as exc:
        raise TemplateError(str(exc)) from exc


def generate(recipe, *, resolve_roles=False):
    from .familize import _values, reconcile_in_body

    roles = {key: 'resident_' + str(index).zfill(12)
             for index, key in enumerate(('facade', 'steward', 'router', 'member'), 1)}
    values = {key: value for key, value in recipe.items() if key not in {'tasks', 'resident_settings', 'role_templates'}}
    block = {'id': 'fbi_000000000001', 'title': 'Family', 'elements': [],
             'family': {**_values(values), **roles, 'title': 'Family', 'members': [roles['member']]}}
    body = {'lineage_id': 'lineage_000000000001', 'nodes': [{'id': rid} for rid in roles.values()],
            'function_blocks': [block], 'program': {'components': [], 'wires': []}}
    reconcile_in_body(body, block, 'template-preview')
    if resolve_roles and recipe.get('role_templates'):
        from . import role
        from .. import family_roles
        family_roles.assemble(body, block, role.resolve(recipe['role_templates']), 'template-preview')
        reconcile_in_body(body, block, 'template-preview', installing=True)
    apply_tasks(body, block, recipe.get('tasks', {}))
    return body, block, roles


def local_key(key, roles):
    for role, identity in roles.items():
        key = key.replace(identity, role + '_resident')
    return key


def placement(recipe, destination):
    from . import structure_layout
    return structure_layout.placed(project({'kind': 'familize', 'body': recipe})['body'], destination)


def place_generated(body, block, layout):
    """Position newly assembled Family content from its real recipe projection.

    Existing members are external anchors and retain their own coordinates.
    This runs only on explicit assembly, never during roster reconciliation.
    """
    family = block['family']
    roles = {role: family[role] for role in ('facade', 'steward', 'router')}
    from ..family_definition import family_elements
    keys = {row['id']: row['key'] for row in family_elements(body, block)}
    for component in body['program']['components']:
        key = local_key(keys.get(component['id'], ''), roles)
        if key in layout:
            component.update(layout[key])


def project(template):
    duty = template['kind'] == 'family_duty'
    body, parent, roles = generate({} if duty else template['body'], resolve_roles=not duty)
    block = parent
    if duty:
        from .family_duty import install_review
        block = install_review(body, roles['steward'], 'template-preview',
                               {**template, 'template_id': template.get('template_id', '')})
    from .. import family_roles
    scopes = [block] if duty else [parent, *family_roles.role_blocks(body, parent)]
    for role in family_roles.role_blocks(body, parent):
        scopes.extend(family_roles.duties(body, role))
    return project_body(body, parent, roles, scopes, duty=duty, source=template)


def project_body(body, parent, roles, scopes, *, duty=False, role_key='', source=None):
    block = scopes[0]
    keys = {e['id']: e['key'] for b in body['function_blocks'] for e in b['elements']}
    keys = {identity: local_key(key, roles) for identity, key in keys.items()}
    keys.update({rid: role + '_resident' for role, rid in roles.items()})
    keys.update({body['lineage_id']: 'lineage', parent['id']: 'family'})
    owned = {e['id'] for scope in scopes for e in scope['elements']}
    wires = [w for w in body['program']['wires'] if w['id'] in owned]
    # Include referenced external nodes (e.g. the shared completion signal).
    visible = owned | {w[end] for w in wires for end in ('source', 'target')}

    def local(value):
        if isinstance(value, dict):
            return {key: local(item) for key, item in value.items()}
        if isinstance(value, list):
            return [local(item) for item in value]
        if isinstance(value, str):
            for identity, key in keys.items():
                value = value.replace(identity, '{{' + key + '}}')
        return value

    def row(item):
        return {'key': keys[item['id']], 'kind': item['kind'], 'title': item.get('title', ''),
                'config': local(item.get('config', {})),
                **({end: keys[item[end]] for end in ('source', 'target')} if 'source' in item else {})}

    controls = {}
    if duty:
        controls = {'periodic_review': [{'field': 'interval_seconds'}],
                    'notify_periodic_review': [{'field': 'guide'}, {'field': 'prompt'}]}
    else:
        controls = {'periodic_review': [{'field': 'review_interval_seconds'}],
                    'relay': [{'field': 'route_threshold'}], 'route': [{'field': 'goal'}]}
        for wire, trigger, task in task_entries(body, block):
            controls.setdefault(keys[wire['id']], []).extend(
                {'task': trigger, 'field': field, 'label': task['title'] + ' · ' + label, 'value': task[field]}
                for field, label in (('guide', '指南'), ('prompt', '触发提示词')))
    groups = []
    for scope in scopes:
        if scope.get('role'):
            descendants = {e['id'] for child in scopes if child['id'] in scope['role']['duties'].values() for e in child['elements']}
            groups.append({'key': keys.get(scope['id'], scope['id']), 'title': scope['title'],
                'members': [keys[e] for e in owned & (descendants | {r['id'] for r in scope['elements']}) if e in keys],
                'resident': scope['role']['key'] + '_resident'})
    projection = {'body': {
        'roles': [{'key': role + '_resident', 'from': ('target' if role == role_key else 'lineage_leader') if role_key else ('target' if role == ('steward' if duty else 'member') else 'new'),
                   'title': {'facade': '门面', 'router': 'Router', 'steward': 'Steward', 'member': '成员（按名册展开）'}[role]}
                  for role, rid in sorted(roles.items(), key=lambda pair: pair[0] != role_key) if (not duty and not role_key) or rid in visible or role == ('steward' if duty else role_key)],
        'components': [row(c) for c in body['program']['components'] if c['id'] in visible],
        'wires': [row(w) for w in wires], 'match': []}, 'controls': controls, 'groups': [] if role_key else groups}
    from .presentation import annotate
    return annotate(projection, body, parent, roles, scopes, keys, source=source, role_key=role_key, duty=duty)
