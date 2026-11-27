"""The two Family role recipes, composed from the existing duty/memory owners.

A role has one Resident. Family-bound triggers keep using the platform's
existing delivery protocol; neither a recipe nor its snapshot grants rights.
``role_recipes`` holds the recipes themselves; this module installs and edits them.
"""
from .registry import TemplateError, result
from .role_recipes import KEYS, definition, references, resolve  # noqa: F401  (the recipes are part of this kind's surface)


def install(body, resident_id, template, actor):
    """Bind the role's Resident of a Family and install the recipe's memories and duties."""
    from .. import family_role_composition
    from .family_reconcile import reconcile_in_body
    key = template['body']['role_key']
    parent = next((b for b in body['function_blocks'] if b.get('family', {}).get(key) == resident_id), None)
    if not parent or parent['family'].get('personal_ama_id'):
        raise ValueError('角色必须绑定普通 Family 的对应 Resident，且类型不能冲突')
    instance, count = family_role_composition.install(body, parent, key, {'template_id': template['template_id'],
        'version': template.get('version', 1), 'body': template['body']}, actor)
    if not count:
        raise ValueError('此角色的职责与记忆已安装；请编辑现有职责或撤下后显式重装')
    reconcile_in_body(body, parent, actor, installing=True)
    return instance


def project(template):
    """The disposable graph an editor shows for one role recipe."""
    from .. import family_role_composition, family_roles
    from . import family_graph
    from .family_reconcile import reconcile_in_body
    key = template['body']['role_key']
    body, parent, roles = family_graph.generate({})
    source = {'template_id': template.get('template_id', ''), 'version': template.get('version', 1), 'body': template['body']}
    family_role_composition.assemble(body, parent, {key: source}, 'template-preview')
    reconcile_in_body(body, parent, 'template-preview', installing=True)
    instance = next(iter(family_roles.role_blocks(body, parent)))
    return family_graph.project_body(body, parent, roles, [instance, *family_roles.duties(body, instance)], role_key=key, source=template)


class RoleKind:
    key = 'role'
    title = 'Family 角色'
    targets = ('resident',)

    def normalize(self, template):
        from .family_reconcile import validate_resident_settings as _validate_resident_settings
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
        return project(template)

    def describe(self, template):
        return {'persistent': True, 'steps': ['绑定所属 Family 的对应 Resident，安装此角色的配置、记忆和职责；共享对象保持外部引用。']}

    def _apply(self, ctx, dry_run):
        from .. import lineage_graph_edits
        if len(ctx.target['resident_ids']) != 1:
            raise TemplateError('请选择所属 Family 的对应角色 Resident')
        def edit(body):
            return install(body, ctx.target['resident_ids'][0], ctx.template, ctx.actor)['id']
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
