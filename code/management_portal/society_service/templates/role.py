"""The two Family role recipes, composed from the existing duty/memory owners.

A role has one Resident. Family-bound triggers keep using the platform's
existing delivery protocol; neither a recipe nor its snapshot grants rights.
"""
from copy import deepcopy

from .registry import TemplateError, result

KEYS = ('router', 'steward')


def references():
    from .builtins import template_id
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
    from . import store
    if set(refs) - set(KEYS):
        raise TemplateError('Family 仅接受 Router 和 Steward 角色引用')
    resolved = {}
    for key, identity in refs.items():
        item = store.get(identity)
        if item['kind'] != 'role' or item['body']['role_key'] != key:
            raise TemplateError('Family 角色引用与角色类型不符')
        resolved[key] = {'template_id': identity, 'version': item['version'], 'body': deepcopy(item['body'])}
    return resolved


class RoleKind:
    key = 'role'
    title = 'Family 角色'
    targets = ('resident',)

    def normalize(self, template):
        from .familize import _validate_resident_settings
        from .. import family_tasks, function_block_authoring
        body = template['body']
        if set(body) != {'role_key', 'resident', 'memories', 'duties', 'bindings'} or body.get('role_key') not in KEYS:
            raise TemplateError('角色需要类型、一个 Resident 配置、分层记忆、职责及外部绑定')
        _validate_resident_settings({body['role_key']: body['resident']})
        if body['bindings'] != ['family', 'facade', 'members', 'peer', 'member_done']:
            raise TemplateError('角色须保留所属 Family、门面、名册、同伴及共享完成事件的外部绑定')
        if not isinstance(body['memories'], list) or not isinstance(body['duties'], list):
            raise TemplateError('角色记忆与职责必须为列表')
        seen = set()
        for memory in body['memories']:
            if set(memory) != {'key', 'level', 'prompt'} or memory['key'] in seen:
                raise TemplateError('角色记忆需要唯一 key、level 和 prompt')
            seen.add(memory['key'])
            if memory['level'] == 'trigger' or not function_block_authoring.normalize_memory({k: memory[k] for k in ('level', 'prompt')}):
                raise TemplateError('角色常驻记忆使用 turn、session 或 on_demand；触发提示词归职责')
        seen = set()
        for duty in body['duties']:
            if set(duty) != {'key', 'title', 'guide', 'prompt'} or duty['key'] in seen:
                raise TemplateError('角色职责需要唯一 key、title、guide 和 prompt')
            key = duty['key']; seen.add(key)
            if key not in family_tasks.TASKS or family_tasks.TASKS[key][1] != body['role_key'] or key == 'progress':
                raise TemplateError('职责不属于此角色')
            fake = {'family': {}, 'elements': [{'id': 'wire', 'kind': 'wire'}]}
            family_tasks.validate_routes({'function_blocks': [], 'program': {'wires': [{'id': 'wire', 'config': {'invoke_profiles': {
                'effect': {'arguments': {'family_tasks': {'value': {key: {**duty, 'role': body['role_key']}}}}}}}}]}}, fake)
        return template

    def editor(self):
        return {'fields': [{'name': name, 'type': 'json', 'label': label} for name, label in (
            ('resident', '节点配置'), ('memories', '分层记忆'), ('duties', '职责与触发提示词'))]}

    def editor_graph(self, template):
        from .. import family_roles
        return family_roles.project(template)

    def describe(self, template):
        return {'persistent': True, 'steps': ['绑定所属 Family 的对应 Resident，安装此角色的配置、记忆和职责；共享对象保持外部引用。']}

    def _apply(self, ctx, dry_run):
        from .. import family_roles, lineage_graph_edits
        if len(ctx.target['resident_ids']) != 1:
            raise TemplateError('请选择所属 Family 的对应角色 Resident')
        def edit(body):
            return family_roles.install(body, ctx.target['resident_ids'][0], ctx.template, ctx.actor)['id']
        # Node backend choices are a distinct lifecycle operation: binding an
        # existing Resident never silently overwrites its account/model.
        if ctx.template['body']['resident']:
            raise TemplateError('已有 Resident 的后端配置请通过节点设置显式修改；角色配置用于新 Family 起始节点')
        _, bid = lineage_graph_edits.commit_lineage_edit(ctx.lineage_id, ctx.actor, edit,
            reason='安装 Family 角色', dry_run=dry_run)
        return result('预览安装角色' if dry_run else '已安装角色',
            function_block_ids=[] if dry_run else [bid],
            preview=[{'title': ctx.template['title'], 'summary': '复用对应 Resident，添加角色记忆与职责；不覆盖其他职责或记忆'}] if dry_run else [])

    def preview(self, ctx):
        return self._apply(ctx, True)

    def apply(self, ctx):
        return self._apply(ctx, False)


KIND = RoleKind()
