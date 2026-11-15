"""User-owned Paper conversation references and explicit legacy transfer.

GET only reads. Export requires the authenticated owner; detach requires the
shared Workbench editor. Neither operation moves or creates a Resident.
"""
from copy import deepcopy

from management_common.principal import actor_principal
from . import isa_client
from .portal_ui_state import get_portal_ui_state_store
from .workbench_projections.personal_bindings import declarations, personal_scope
from .workbench_projections.store import ProjectionError, DEFINITION_FIELDS

NAMESPACE = 'paper.conversations'


def _scope(actor, scope):
    match = personal_scope(scope)
    if not match or match[1] != actor:
        raise PermissionError('Paper conversation belongs to a different user')
    return match[2]


def _app(actor):
    from .workbench_projections.service import get_service
    return get_service().builtin_application('paper', actor)


def references(actor):
    """One authority per scope. Legacy declarations are read-only import sources.

    An exported association wins only if any remaining declaration matches its
    receipt exactly; contradictory data never silently selects a winner.
    """
    app = _app(actor)
    legacy = declarations({alias: {**binding, 'residents': {
        scope: rid for scope, rid in binding.get('residents', {}).items()
        if personal_scope(scope) and personal_scope(scope)[1] == actor}}
        for alias, binding in app.get('bindings', {}).items()})
    rows = {r['key']: r['value'] for r in get_portal_ui_state_store().list(actor, NAMESPACE)}
    result = {}
    for scope, source in legacy.items():
        if personal_scope(scope)[1] == actor:
            result[scope] = {'resident_id': source['resident_id'], 'source': source,
                             'workbench_id': app['workbench_id'], 'state': 'legacy'}
    for scope, value in rows.items():
        _scope(actor, scope)
        if scope in result and (value.get('source') != result[scope]['source']
                               or value.get('workbench_id') != app['workbench_id']
                               or value.get('resident_id') != result[scope]['resident_id']):
            raise ProjectionError('paper.association_conflict', '个人关联与旧声明冲突', 409)
        result[scope] = value
    return result


def resolve(actor, scope, value):
    _scope(actor, scope)
    actual = isa_client.call(actor, 'GetResident', {'resident_id': value['resident_id']})['resident']
    if value['state'] == 'legacy' and actual['lineage_id'] != value['source']['lineage_id']:
        raise ProjectionError('paper.association_migration_required', '原会话已迁移，请显式接管用户关联', 409)
    return {'resident_id': value['resident_id'], 'lineage_id': actual['lineage_id'],
            'title': actual.get('title') or 'Legacy research'}


def lookup(*, actor, scope):
    _scope(actor, scope)
    value = references(actor).get(scope)
    if not value:
        return {}
    actual = resolve(actor, scope, value)
    return {k: actual[k] for k in ('resident_id', 'lineage_id')}


def export(payload, actor):
    if set(payload) != {'scope', 'expected_resident_id'}:
        raise ValueError('scope and expected_resident_id required')
    scope = payload['scope']
    workspace_key = _scope(actor, scope)
    if actor_principal(actor).kind != 'user':
        raise PermissionError('Only the authenticated conversation owner can export')
    store = get_portal_ui_state_store()
    with store.association_lock(actor, NAMESPACE, scope):
        values = references(actor)
        value = values.get(scope)
        if not value:
            raise ProjectionError('paper.association_source_missing', '旧会话声明不存在', 409)
        if value['resident_id'] != payload['expected_resident_id']:
            raise ProjectionError('paper.association_conflict', 'Resident 与确认对象不符', 409)
        actual = isa_client.call(actor, 'GetResident', {'resident_id': value['resident_id']})['resident']
        if actual.get('created_by') != actor:
            raise PermissionError('Legacy scope owner does not match Resident creator')
        owned = [w for w in actual.get('workspaces', []) if w.get('is_owned')]
        if len(owned) != 1:
            raise ProjectionError('paper.association_workspace_missing', '无法确认原 owned Workspace', 409)
        from .resident_handoff import INVOCATION_ASSOCIATION_NAMESPACE
        old = (store.get(actor, INVOCATION_ASSOCIATION_NAMESPACE, scope) or {}).get('value')
        if old and old.get('resident_id') != value['resident_id']:
            raise ProjectionError('paper.association_conflict', '旧调用关联与会话不一致', 409)
        if value['state'] != 'legacy':
            if value.get('workspace_id') != owned[0]['workspace_id']:
                raise ProjectionError('paper.association_conflict', '原 Workspace 身份发生变化', 409)
            store.remove(actor, INVOCATION_ASSOCIATION_NAMESPACE, scope)
            return value
        exported = {**value, 'state': 'exported', 'scope': scope, 'workspace_key': workspace_key,
                    'workspace_id': owned[0]['workspace_id']}
        store.put(actor, NAMESPACE, scope, exported)
        store.remove(actor, INVOCATION_ASSOCIATION_NAMESPACE, scope)
        return exported


def _without(app, scope, source):
    data = deepcopy(app['bindings'])
    current = declarations(data).get(scope)
    if current != source:
        raise ProjectionError('paper.association_conflict', '共享声明与导出回执不一致', 409)
    binding = data[source['alias']]
    del binding['residents'][scope]
    if source['alias'] != 'main' and not binding['residents'] and not binding.get('events'):
        del data[source['alias']]
    return data


def detach(payload, actor):
    """Remove exactly an owner-exported declaration; never borrow owner's ACL."""
    from .workbench_projections.service import get_service
    if set(payload) != {'workbench_id', 'scope', 'expected_resident_id'}:
        raise ValueError('workbench_id, scope and expected_resident_id required')
    scope = payload['scope']
    match = personal_scope(scope)
    if not match:
        raise ValueError('Invalid personal scope')
    owner = match[1]  # migration target, never the authorization principal
    service, associations = get_service(), get_portal_ui_state_store()
    with service.lock('updates:' + payload['workbench_id']), associations.association_lock(owner, NAMESPACE, scope):
        app = service.store.get(payload['workbench_id'], actor)
        service.store.require_write(app, actor)
        value = (associations.get(owner, NAMESPACE, scope) or {}).get('value')
        if not value or value.get('state') not in {'exported', 'detached'}:
            raise ProjectionError('paper.association_export_required', '请由会话所有者先导出关联', 409)
        if value['workbench_id'] != payload['workbench_id'] or value['resident_id'] != payload['expected_resident_id']:
            raise ProjectionError('paper.association_conflict', '导出回执不匹配', 409)
        published = service.store.get(app['workbench_id'], actor, published=True)
        present = declarations(app['bindings']).get(scope)
        published_present = declarations(published['bindings']).get(scope)
        if present:
            if app['revision'] != app['published_revision']:
                raise ProjectionError('projection.binding_draft', '先处理无关的未发布修改', 409)
            bindings = _without(app, scope, value['source'])
            service.check_bindings(bindings, actor)
            app = service.store.update(app['workbench_id'], {'bindings': bindings}, actor, app['revision'])
        elif published_present:
            # Recovery after save succeeded but publish/response failed. Only
            # the exact detach diff is eligible for automatic publication.
            expected = {**published, 'bindings': _without(published, scope, value['source'])}
            if any(app.get(k) != expected.get(k) for k in DEFINITION_FIELDS):
                raise ProjectionError('projection.binding_draft', '迁移保存后存在其他修改', 409)
        elif value['state'] != 'detached':
            # A successful publish can precede the association receipt write.
            if app['revision'] != app['published_revision']:
                raise ProjectionError('projection.binding_draft', '请先核对未发布修改', 409)
        if present or published_present:
            service.check_bindings(app['bindings'], actor)
            service.store.publish(app['workbench_id'], actor, app['revision'])
        value = {**value, 'state': 'detached'}
        associations.put(owner, NAMESPACE, scope, value)
        return {'workbench_id': app['workbench_id'], 'revision': app['revision'],
                'scope': scope, 'resident_id': value['resident_id'], 'state': 'detached'}
