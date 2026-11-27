"""The two Family role recipes, composed from the existing duty/memory owners.

A role has one Resident. Family-bound triggers keep using the platform's
existing delivery protocol; neither a recipe nor its snapshot grants rights.
"""
from copy import deepcopy

from .persistence import get_persisted
from .registry import TemplateError, template_id

KEYS = ('router', 'steward')

# Defaults populate the editable duty title once. Views read that title from
# the recipe; Event names and runtime task guides keep their own semantics.
DUTY_TITLES = {
    'route': '门面收到消息后，将消息路由到最合适的成员处',
    'queue': '成员队列积压时，先整理待办，再按剩余工作安排分流或增员',
    'review': '周期回顾到期时，检查成员分工和近期活动，处理有依据的组织问题',
    'summary': '成员摘要需要归纳或门面材料变化时，归纳业务成员认知并更新门面摘要',
    'recruit': 'Router 发现职责缺口时，安排招募或调整分工，为原请求确定承接者',
    'route_review': '路由需要复核时，核对原请求与转交状态，必要时纠正同一请求的承接者',
    'recovery': '工作失败或接单阻塞时，核验故障并恢复原任务，保留取消和停止的决定',
    'quota': '业务成员额度不足时，核对有效切换策略并安排可用执行者继续原任务',
    'organization': 'Family 初始化、名册或背景变化时，核对职责分工并完成必要的组织调整',
}


def references():
    return {key: template_id('family_role_' + key) for key in KEYS}


def definition(key):
    from .. import family_tasks
    duties = []
    for trigger, (_, owner, title, guide, objective) in family_tasks.TASKS.items():
        if owner != key or trigger == 'progress':
            continue
        duties.append({'key': trigger, 'title': DUTY_TITLES[trigger], 'guide': guide,
                       'prompt': objective + '\n${facts}\n指南：${guide_path}（${guide_version}）'})
    identity = ('为当前原请求选择承接者；只按需读取候选资料，不执行业务正文。' if key == 'router' else
                '维护所属 Family 的分工、业务成员摘要与故障恢复；不另造原业务请求。')
    return {'role_key': key, 'resident': {},
            'memories': [{'key': 'identity', 'level': 'session', 'prompt': identity}],
            'duties': duties, 'bindings': ['family', 'facade', 'members', 'peer', 'member_done']}


def resolve(refs):
    if set(refs) - set(KEYS):
        raise TemplateError('Family 仅接受 Router 和 Steward 角色引用')
    resolved = {}
    for key, identity in refs.items():
        item = get_persisted(identity)
        if item['kind'] != 'role' or item['body']['role_key'] != key:
            raise TemplateError('Family 角色引用与角色类型不符')
        resolved[key] = {'template_id': identity, 'version': item['version'], 'body': deepcopy(item['body'])}
    return resolved
