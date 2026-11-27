"""The two Family role recipes, composed from the existing duty/memory owners.

A role has one Resident. Family-bound triggers keep using the platform's
existing delivery protocol; neither a recipe nor its snapshot grants rights.
"""
from copy import deepcopy

from .persistence import get_persisted
from .registry import TemplateError, template_id

KEYS = ('router', 'steward')


def references():
    return {key: template_id('family_role_' + key) for key in KEYS}


def definition(key):
    from .. import family_tasks
    duties = []
    for trigger, (_, owner, title, guide, objective) in family_tasks.TASKS.items():
        if owner != key or trigger == 'progress':
            continue
        duties.append({'key': trigger, 'title': title, 'guide': guide,
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
