"""Ownership and installation of the independently removable review workflow."""
from copy import deepcopy
import secrets
from . import family_review_effects, family_tasks, function_block_authoring
from .family_definition import (
    REVIEW_BLOCK_KEY, REVIEW_DUTY as KEY, REVIEW_ELEMENT_KEYS as ELEMENT_KEYS,
    bind_review_activity, find_review_duty as find, has_independent_review as independent, review_task as task,
)


def install(body, steward, actor, template, *, reconcile):
    parent = next((b for b in body['function_blocks'] if b.get('family', {}).get('steward') == steward), None)
    if not parent or parent['family'].get('personal_ama_id'):
        raise ValueError('请选择普通 Family 的 Steward')
    from . import family_roles
    if 'review' in family_roles.configured(body, parent):
        raise ValueError('此角色已安装周期回顾职责；请编辑该职责或撤下后再安装')
    if independent(parent['family']) and find(body, parent):
        raise ValueError('此 Family 已安装周期组织回顾；请在职责工坊编辑或撤下后再安装')
    # Adopt the original graph once; reinstall creates only this recipe's elements.
    owned = [e for e in parent['elements'] if e.get('key') in ELEMENT_KEYS]
    parent['elements'] = [e for e in parent['elements'] if e.get('key') not in ELEMENT_KEYS]
    family = parent['family']
    family['independent_duties'] = list(dict.fromkeys([*family.get('independent_duties', []), KEY]))
    bid = 'fbi_' + secrets.token_hex(6)
    now = function_block_authoring._now()
    block = {'id':bid, 'title':template['title'], 'template_id':template['template_id'],
             'x':None, 'y':None, 'elements':owned,
             'duty':{'holder_id':steward, 'description':template['description'] or template['title'],
                     'category':'common', 'position':len(function_block_authoring.duties(body, steward))},
             'created_at':now, 'updated_at':now, 'created_by':actor, 'updated_by':actor}
    body['function_blocks'].append(block)
    parent['elements'].append({'id':bid, 'kind':'function_block', 'key':REVIEW_BLOCK_KEY})
    program = body['program']
    def put(key, kind, **fields):
        element = next((e for e in owned if e.get('key') == key), None)
        collection = program['wires' if kind == 'route' else 'components']
        item = next((i for i in collection if element and i['id'] == element['id']), None)
        if item is None:
            item = {'id':('wire' if kind == 'route' else kind) + '_' + secrets.token_hex(6), 'kind':kind}
            collection.append(item)
            owned.append({'id':item['id'], 'kind':'wire' if kind == 'route' else 'component', 'key':key})
        item.update(deepcopy(fields))
        return item['id']
    recipe = template['body']
    timer = put('periodic_review', 'event_source', title='周期组织回顾', config={
        'event_type':'time.reached', 'schedule_mode':'recurring', 'recurrence_kind':'interval',
        'interval_seconds':recipe['interval_seconds'], 'misfire_policy':'coalesce_latest', 'overlap_policy':'drop'})
    check = put('notify_periodic_review_task', 'effect', title='按业务活动检查组织回顾', config={
        'script': family_review_effects.NOTICE, 'endpoints':[]})
    put('review_activity', 'effect', title='记录已完成业务活动', config={
        'script': family_review_effects.RECORD_ACTIVITY, 'endpoints':[]})
    entry = {**family_tasks.defaults('notify_periodic_review')[KEY], 'guide':recipe['guide'], 'prompt':recipe['prompt']}
    put('notify_periodic_review', 'route', source=timer, target=check, config={'unavailable_policy':'latest',
        'invoke_profiles':{'effect':{'arguments':{
            **family_review_effects.bindings(body['lineage_id'], parent['id'], parent['family']),
            'family_tasks':{'value':{KEY:entry}}}}}})
    reconcile(body, parent, actor)
    bind_review_activity(body, parent)
    return block


def capture(body, block, *, template_id, install_recipe, normalize):
    parent = next((b for b in body['function_blocks'] if b.get('family') and find(body, b) is block), None)
    if not parent:
        return None
    # The semantic recipe cannot silently discard arbitrary graph edits.
    entry = task(body, parent)
    timer_id = next((e['id'] for e in block['elements'] if e.get('key') == 'periodic_review'), None)
    timer = next((c for c in body['program']['components'] if c['id'] == timer_id), None)
    if not entry or not timer:
        raise ValueError('回顾职责缺少定时器或任务配置，无法保存为回顾模板')
    if block['duty'].get('memory'):
        raise ValueError('当前回顾模板仅支持周期与指南，请先将职责记忆整理到指南')
    value = {'kind':'family_duty', 'category':'structure', 'title':block['title'],
             'description':block['duty']['description'], 'targets':['resident'], 'parameters':[],
             'template_id':template_id, 'body':{'interval_seconds':timer['config']['interval_seconds'],
             'guide':entry['guide'], 'prompt':entry['prompt']}}
    probe = deepcopy(body)
    probe_parent = next(b for b in probe['function_blocks'] if b['id'] == parent['id'])
    probe_parent['family']['independent_duties'] = []
    probe_parent['elements'] = [e for e in probe_parent['elements'] if e['id'] != block['id']] + deepcopy(block['elements'])
    probe['function_blocks'] = [b for b in probe['function_blocks'] if b['id'] != block['id']]
    restored = install_recipe(probe, block['duty']['holder_id'], block['updated_by'], value)
    probe = normalize(probe, body, block['updated_by'])
    ids = {e['id'] for e in block['elements']}
    if {e['id'] for e in restored['elements']} != ids or any(
        sorted((i for i in body['program'][k] if i['id'] in ids), key=lambda i:i['id']) !=
        sorted((i for i in probe['program'][k] if i['id'] in ids), key=lambda i:i['id']) for k in ('components','wires')):
        raise ValueError('此职责含自定义结构或脚本；当前回顾模板仅支持周期与指南，请保留实例编辑')
    value.pop('template_id')
    return value
