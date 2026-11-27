"""Installed Router/Steward composition as it is read: roles, their duties and who owns an element.

The Family remains the sole binding owner. Duties own executable structure
and editable guides; role records only retain identities and source versions.
``family_role_composition`` installs and reconciles this structure.
"""

REVIEW_ELEMENT_KEYS = frozenset({
    "periodic_review", "notify_periodic_review_task", "notify_periodic_review",
    "review_activity", "record_review_activity",
})


def role_blocks(body, parent):
    ids = set(parent['family'].get('role_bindings', {}).values())
    return [b for b in body['function_blocks'] if b['id'] in ids and b.get('role')]


def duties(body, role):
    ids = set(role['role']['duties'].values())
    return [b for b in body['function_blocks'] if b['id'] in ids]


def elements(body, parent):
    return [e for role in role_blocks(body, parent) for block in [role, *duties(body, role)] for e in block['elements']]


def owner(key):
    """The (role, duty) an element key belongs to, or None when no role duty owns it."""
    if key in {'router', 'route'}:
        return 'router', 'route'
    if key in {'summary_check', 'check_summary', 'summary_timer', 'summary_rollup', 'request_summary'}:
        return 'steward', 'summary'
    if key in REVIEW_ELEMENT_KEYS:
        return 'steward', 'review'
    if key == 'member_queue' or key.startswith(('watch_member_queue:', 'notify_member_queue')):
        return 'steward', 'queue'
    if key.startswith(('member_failed', 'watch_member_failed:', 'notify_member_failed',
                       'recovery_check', 'recovery_arrival', 'watch_recovery_arrival:',
                       'recovery_finished', 'watch_recovery_finished:', 'verify_recovery', 'check_intake_blocked',
                       'router_failed', 'relay_failed', 'route_failed', 'delivery_failed',
                       'watch_router_failed', 'watch_relay_failed', 'watch_route_failed', 'watch_delivery_failed',
                       'recover_router_failed', 'recover_relay_failed', 'recover_route_failed',
                       'recover_delivery_failed', 'recheck_recovery')):
        return 'steward', 'recovery'
    return None


def configured(body, parent):
    result = {}
    for role in role_blocks(body, parent):
        for duty in duties(body, role):
            parameters = duty['duty']['parameters']
            key = parameters.get('family_task')
            if key:
                result[key] = {'role': role['role']['key'], 'title': duty['title'],
                    'guide': duty['duty'].get('memory', {}).get('prompt', ''), 'prompt': parameters['prompt']}
    return result


def with_task_prompt(parameters, prompt):
    from string import Template
    if not parameters.get('family_task'):
        raise ValueError('此职责没有 Family 触发提示词')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
        raise ValueError('触发提示词须为 1–20000 字')
    try:
        Template(prompt).substitute(facts='', guide_path='', guide_version='')
        if 'facts' not in Template(prompt).get_identifiers():
            raise ValueError('触发提示词须保留 ${facts}')
    except (KeyError, ValueError) as exc:
        raise ValueError('触发提示词须保留 ${facts}，仅支持 facts、guide_path、guide_version') from exc
    return {**parameters, 'prompt': prompt}


def validate(body, block):
    role = block['role']
    parent = next((b for b in body['function_blocks'] if b['id'] == role['family_id']), None)
    if not parent or parent.get('family', {}).get('role_bindings', {}).get(role['key']) != block['id']:
        raise ValueError('角色与所属 Family 绑定冲突')
    residents = [e['id'] for e in block['elements'] if e['kind'] == 'resident']
    if residents != [parent['family'][role['key']]]:
        raise ValueError('角色必须恰好包含 Family 绑定的一个 Resident')
