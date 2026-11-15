"""Personal reference transfer with real isolated association/Workbench stores."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal import paper_conversations as conversations, paper_chat_research as research
from management_portal.portal_ui_state import get_portal_ui_state_store
from management_portal.hatg.contracts import InstructionError
from management_portal.workbench_projections import service, native, http
from management_portal.workbench_projections.store import Store, ProjectionError

APP = 'workbench_111111111111'
MAIN = 'lineage_111111111111'
PRIVATE = 'lineage_222222222222'
SHARED = 'resident_111111111111'
ALICE = 'resident_222222222222'
BOB = 'resident_333333333333'
SCOPE = 'surface-chat:alice:paper:paper:paper_111111111111'
BOBSCOPE = SCOPE.replace(':alice:', ':bob:')


@pytest.fixture
def setup(monkeypatch, tmp_path):
    store = Store()
    bindings = {'main': {'lineage_id': MAIN, 'residents': {'paper:review': SHARED, BOBSCOPE: BOB}},
                'personal-alice': {'lineage_id': PRIVATE, 'residents': {SCOPE: ALICE}}}
    store.create(APP, {'title': 'Paper fixture', 'html': '<p>Paper</p>', 'bindings': bindings}, 'editor')
    store.publish(APP, 'editor', 1)
    runtime = service.ProjectionService(store)
    monkeypatch.setattr(service, 'get_service', lambda: runtime)
    monkeypatch.setattr(native, 'get_service', lambda: runtime)
    monkeypatch.setattr(runtime, 'builtin_application', lambda name, actor: store.get(APP, actor, published=True))
    monkeypatch.setattr(store, 'require_write', lambda app, actor: None if actor == 'editor' else (_ for _ in ()).throw(PermissionError('editor required')))
    monkeypatch.setattr(get_portal_ui_state_store(), 'key_lock_root', tmp_path / 'associations')
    state = {'revoked': False, 'reads': [], 'shared': True}
    def graph(actor, lineage):
        state['reads'].append((actor, lineage))
        if lineage == PRIVATE:
            raise PermissionError('editor has no private AMA grant')
        return {'nodes': [{'id': SHARED}] if state['shared'] else [], 'program': {'components': []}}
    monkeypatch.setattr(service, 'lineage_graph', graph)
    def call(actor, instruction, args, **kwargs):
        assert instruction == 'GetResident', 'read path must not create/move/send'
        rid = args['resident_id']
        owner = 'alice' if rid == ALICE else 'bob'
        if actor != owner or state['revoked']:
            raise InstructionError('instruction.forbidden', 'revoked', http_status=403)
        return {'resident': {'resident_id': rid, 'lineage_id': PRIVATE if rid == ALICE else MAIN,
                             'created_by': owner, 'title': owner,
                             'workspaces': [{'workspace_id': 'workspace_111111111111', 'is_owned': True}]}}
    monkeypatch.setattr(conversations.isa_client, 'call', call)
    return runtime, state


def export():
    return http.post('/api/workbenches/personal-conversations/export',
                     {'scope': SCOPE, 'expected_resident_id': ALICE}, 'alice')


def detach():
    return http.post('/api/workbenches/personal-conversations/detach',
                     {'workbench_id': APP, 'scope': SCOPE, 'expected_resident_id': ALICE}, 'editor')


def test_editor_updates_and_publishes_shared_roles_without_private_read(setup):
    runtime, state = setup
    app = runtime.store.get(APP, 'editor')
    assert native.with_binding_status(app, 'editor')['binding_issues'] == []
    changed = deepcopy(app['bindings'])
    changed['main']['residents']['paper:summary'] = SHARED
    saved = runtime.apply_update(APP, {'expected_revision': 1, 'bindings': changed}, 'editor')
    runtime.apply_update(APP, {'expected_revision': saved['revision'], 'published_revision': saved['revision']}, 'editor')
    assert all(lineage == MAIN for actor, lineage in state['reads'])
    state['shared'] = False
    with pytest.raises(ProjectionError, match='Resident'):
        runtime.check_bindings(changed, 'editor')


def test_two_users_restore_original_identity_and_revocation_is_immediate(setup):
    _, state = setup
    assert conversations.lookup(actor='alice', scope=SCOPE)['resident_id'] == ALICE
    assert conversations.lookup(actor='bob', scope=BOBSCOPE)['resident_id'] == BOB
    with pytest.raises(PermissionError):
        conversations.lookup(actor='bob', scope=SCOPE)
    assert research.catalog({}, actor='alice')['legacy'][0]['resident_id'] == ALICE
    first = export()
    assert first['workspace_key'] == 'paper:paper_111111111111'
    assert export() == first
    assert detach()['state'] == 'detached'
    assert detach()['state'] == 'detached'
    assert conversations.lookup(actor='alice', scope=SCOPE)['resident_id'] == ALICE
    assert 'personal-alice' not in setup[0].store.get(APP, 'editor')['bindings']
    state['revoked'] = True
    with pytest.raises(InstructionError):
        conversations.lookup(actor='alice', scope=SCOPE)
    assert research.catalog({}, actor='alice')['legacy'] == []
    assert get_portal_ui_state_store().get('alice', conversations.NAMESPACE, SCOPE)


def test_migration_refuses_missing_source_wrong_owner_conflicts_and_duplicates(setup):
    runtime, _ = setup
    with pytest.raises(ProjectionError, match='导出'):
        detach()
    with pytest.raises(PermissionError):
        conversations.export({'scope': SCOPE, 'expected_resident_id': ALICE}, 'bob')
    with pytest.raises(ProjectionError) as error:
        conversations.export({'scope': SCOPE + '-missing', 'expected_resident_id': ALICE}, 'alice')
    assert error.value.code == 'paper.association_source_missing'
    with pytest.raises(ProjectionError):
        conversations.export({'scope': SCOPE, 'expected_resident_id': BOB}, 'alice')
    export()
    app = runtime.store.get(APP, 'editor')
    changed = deepcopy(app['bindings'])
    changed['main']['residents'][SCOPE] = ALICE
    # Fixture injects corrupt old data below the public mutation boundary.
    runtime.store.update(APP, {'bindings': changed}, 'editor', app['revision'])
    runtime.store.publish(APP, 'editor', app['revision'] + 1)
    with pytest.raises(ProjectionError) as error:
        conversations.lookup(actor='alice', scope=SCOPE)
    assert error.value.code == 'projection.role_ambiguous'


def test_detach_reenters_after_save_before_publish_failure(setup, monkeypatch):
    runtime, state = setup
    export()
    original = runtime.store.publish
    monkeypatch.setattr(runtime.store, 'publish', lambda *args: (_ for _ in ()).throw(TimeoutError('publish unavailable')))
    with pytest.raises(TimeoutError):
        detach()
    assert conversations.lookup(actor='alice', scope=SCOPE)['resident_id'] == ALICE
    monkeypatch.setattr(runtime.store, 'publish', original)
    assert detach()['state'] == 'detached'
    assert all(lineage == MAIN for actor, lineage in state['reads'])


def test_personal_roles_cannot_be_added_rebound_removed_or_created_as_shared(setup):
    runtime, _ = setup
    current = runtime.store.get(APP, 'editor')['bindings']
    for mutation in ('add', 'replace', 'delete'):
        changed = deepcopy(current)
        roles = changed['personal-alice']['residents']
        if mutation == 'add': roles[SCOPE + '-new'] = ALICE
        elif mutation == 'replace': roles[SCOPE] = BOB
        else: del roles[SCOPE]
        with pytest.raises(ProjectionError):
            runtime.check_binding_changes(current, changed, 'editor')
    with pytest.raises(ProjectionError, match='新建个人研究'):
        native.ensure('paper', actor='alice', resident_scope=SCOPE + '-new', create_role=True)
    from management_portal.http_ui_state import UiStateHttp
    with pytest.raises(PermissionError):
        UiStateHttp._check_mutable_namespace(conversations.NAMESPACE)


def test_non_paper_surface_keeps_deleted_reference_contract(setup, monkeypatch):
    runtime, state = setup
    app = runtime.store.get(APP, 'editor')
    app['bindings']['main']['residents']['surface-chat:alice:wall:old'] = BOB
    def missing(*args, **kwargs):
        raise InstructionError('instruction.not_found', 'deleted', http_status=404)
    monkeypatch.setattr(conversations.isa_client, 'call', missing)
    assert native.with_binding_status(app, 'editor')['binding_issues'] == []
    def unavailable(*args, **kwargs):
        raise InstructionError('instruction.owner_unavailable', 'offline', http_status=503)
    monkeypatch.setattr(conversations.isa_client, 'call', unavailable)
    with pytest.raises(InstructionError, match='offline'):
        native.with_binding_status(app, 'editor')


def test_shared_role_cleanup_never_deletes_personal_history(setup, monkeypatch):
    runtime, _ = setup
    app = runtime.store.get(APP, 'editor')
    binding = app['bindings']['main']
    monkeypatch.setattr(runtime.store, 'role_request', lambda *args: pytest.fail('personal creation receipt must not be consulted'))
    assert runtime._drop_deleted_created_roles(app, binding, {'nodes': [{'id': SHARED}]}, keep='new') == []
    with pytest.raises(ProjectionError, match='个人会话'):
        runtime.release_roles(app, 'editor', {BOBSCOPE: BOB})
    from management_portal.workbench_projections import resource_lifecycle
    monkeypatch.setattr(get_portal_ui_state_store(), 'list_namespace', lambda namespace: [{
        'actor': 'alice', 'key': SCOPE, 'value': {'resident_id': ALICE,
            'metadata': {'resource_id': 'paper_111111111111', 'workbench_role': SCOPE}}}])
    assert resource_lifecycle.release_resource('paper_111111111111', actor='editor') == set()
