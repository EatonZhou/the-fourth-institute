"""Resource deletion and slot replacement release their complete owned projection.

:module: spec.design-governance
:importance: P2
"""
import copy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import isa_client, lineage_workflows, resident_handoff
from management_portal.hatg.contracts import InstructionError
from management_portal.portal_ui_state import PortalUIStateStore
from management_portal.workbench_projections import resource_lifecycle
from management_portal.test_framework.tau_residents import TauResidentAssignments

RID = 'resident_111111111111'
LID = 'lineage_111111111111'


@pytest.fixture
def projection(monkeypatch):
    graph = {'revision': 3, 'nodes': [{'id': RID}, {'id': 'resident_222222222222'}],
             'program': {'components': [{'id': 'event_source_111111111111'}, {'id': 'effect_111111111111'}, {'id': 'event_source_222222222222'}],
                         'wires': [{'id': 'wire_111111111111', 'source': 'event_source_111111111111', 'target': 'effect_111111111111'},
                                   {'id': 'wire_222222222222', 'source': 'effect_111111111111', 'target': RID}]}}
    flow_store = Mock()
    flow_store.lineage_lock.side_effect = lambda _: nullcontext()
    flow_store.for_resident.return_value = [{'steps': {'event': 'event_source_111111111111', 'effect': 'effect_111111111111'}}]
    monkeypatch.setattr(lineage_workflows, 'get_workflow_binding_store', lambda: flow_store)
    state = SimpleNamespace(graph=graph, blocked=False, lost_response=False, calls=[])
    def call(actor, name, args, **kwargs):
        state.calls.append((name, copy.deepcopy(args)))
        if name == 'GetResident':
            if not any(n['id'] == args['resident_id'] for n in graph['nodes']):
                raise InstructionError('instruction.not_found', 'deleted')
            return {'resident': {'resident_id': RID, 'lineage_id': LID}}
        if name == 'GetLineage':
            return {'lineage': copy.deepcopy(graph)}
        if name == 'ApplyLineageChanges':
            assert kwargs['version'] == graph['revision']
            if state.blocked:
                raise RuntimeError('busy')
            deleted = {op['id'] for op in args['operations']}
            graph['nodes'] = [n for n in graph['nodes'] if n['id'] not in deleted]
            graph['program']['components'] = [n for n in graph['program']['components'] if n['id'] not in deleted]
            graph['program']['wires'] = [w for w in graph['program']['wires'] if not {w['source'], w['target']} & deleted]
            graph['revision'] += 1
            if state.lost_response:
                state.lost_response = False
                raise RuntimeError('lost response')
            return {'deleted_ids': sorted(deleted)}
        if name == 'DeleteResident':
            assert not any(n['id'] == RID for n in graph['nodes'])
            return {'resident_id': RID}
        raise AssertionError(name)
    monkeypatch.setattr(isa_client, 'call', call)
    store = PortalUIStateStore()
    monkeypatch.setattr(resident_handoff, 'get_portal_ui_state_store', lambda: store)
    state.store = store
    return state


def associate(state, resource_id, actor='alice', scope='discussion'):
    state.store.put(actor, resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE, scope,
                    {'resident_id': RID, 'lineage_id': LID, 'metadata': {'resource_id': resource_id}})


@pytest.mark.parametrize('kind', ['skill', 'plan', 'decision', 'paper', 'review'])
def test_resource_release_removes_resident_and_owned_flow_preserves_other_nodes(projection, kind):
    """释放资源时删除其讨论 Resident 和专属事件流，保留 Lineage 中其他节点"""
    resource_id = kind + '_111111111111'
    associate(projection, resource_id)
    projection.store.put('alice', resident_handoff.INVOCATION_CREATION_NAMESPACE,
                         'discussion', {'resident_id': RID})
    projection.store.put('bob', resident_handoff.INVOCATION_CREATION_NAMESPACE,
                         'interrupted', {'resident_id': RID})
    assert resource_lifecycle.release_resource(resource_id, actor='owner') == {RID}
    assert projection.graph['nodes'] == [{'id': 'resident_222222222222'}]
    assert projection.graph['program'] == {'components': [{'id': 'event_source_222222222222'}], 'wires': []}
    assert not projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)
    assert not projection.store.list_namespace(resident_handoff.INVOCATION_CREATION_NAMESPACE)


def test_tau_sample_release_uses_complete_projection_cleanup(projection):
    """释放 TAU 样例成员时一并删除其事件源、Effect 和连线"""
    from management_portal.test_framework.tau_cleanup import _release_member
    _release_member(LID, RID, 'sample')
    assert projection.graph['program'] == {'components': [{'id': 'event_source_222222222222'}], 'wires': []}


def test_workbench_binding_failure_keeps_resource_ownership_for_retry(projection, monkeypatch):
    """工作台角色解绑失败时保留资源关联，重试成功后才清除"""
    from management_portal.workbench_projections import service
    associate(projection, 'paper_111111111111')
    entry = projection.store.get('alice', resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE, 'discussion')['value']
    entry['metadata'].update(workbench_id='app', workbench_role='chat')
    projection.store.put('alice', resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE, 'discussion', entry)
    app = {'bindings': {'main': {'residents': {'chat': RID}}}}
    def fail(*args):
        resident_handoff.release_resident(actor='owner', resident_id=RID, lineage_id=LID, forget_associations=False)
        raise RuntimeError('publish interrupted')
    runtime = SimpleNamespace(store=SimpleNamespace(get=lambda *args: app), release_roles=fail)
    monkeypatch.setattr(service, 'get_service', lambda: runtime)
    with pytest.raises(RuntimeError, match='publish interrupted'):
        resource_lifecycle.release_resource('paper_111111111111', actor='owner')
    assert projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)
    runtime.release_roles = lambda *args: None
    resource_lifecycle.release_resource('paper_111111111111', actor='owner')
    assert not projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)


def test_native_discussion_registers_resource_and_exact_role(projection, monkeypatch):
    """内置工作台的讨论 Resident 关联记录资源 ID 与所在工作台角色"""
    from management_portal.workbench_projections import native
    identity = {'resident_id': RID, 'lineage_id': LID}
    monkeypatch.setattr(native, 'binding', lambda *args, **kw: identity)
    monkeypatch.setattr(resident_handoff, 'ensure_invocation_resident', lambda **kw: identity)
    monkeypatch.setattr(native, 'get_service', lambda: SimpleNamespace(
        builtin_application=lambda *args: {'workbench_id': 'app'}))
    native.ensure('paper', actor='alice', resident_scope='chat', create_role=True,
                  metadata={'resource_id': 'paper_111111111111'})
    value = projection.store.get('alice', resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE, 'chat')['value']
    assert value['metadata'] == {'resource_id': 'paper_111111111111', 'workbench_id': 'app', 'workbench_role': 'chat'}


def test_resource_release_preserves_resident_reused_by_shared_role(projection, monkeypatch):
    """Resident 同时承担其他工作台角色时拒绝释放资源，不改动 Lineage"""
    from management_portal.workbench_projections import service
    projection.store.put('alice', resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE, 'chat', {
        'resident_id': RID, 'lineage_id': LID,
        'metadata': {'resource_id': 'paper_111111111111', 'workbench_id': 'app', 'workbench_role': 'chat'}})
    release = Mock()
    monkeypatch.setattr(service, 'get_service', lambda: SimpleNamespace(
        store=SimpleNamespace(get=lambda *args: {'bindings': {'main': {'residents': {'chat': RID, 'shared': RID}}}}),
        release_roles=release))
    with pytest.raises(ValueError, match='another workbench role'):
        resource_lifecycle.release_resource('paper_111111111111', actor='owner')
    release.assert_not_called()
    assert not any(c[0] in {'ApplyLineageChanges', 'DeleteResident'} for c in projection.calls)


def test_failed_release_keeps_association_and_graph_for_retry(projection):
    """Lineage 修改失败时保留资源关联和图结构，重试后完成释放"""
    associate(projection, 'plan_111111111111')
    before = copy.deepcopy(projection.graph)
    projection.blocked = True
    with pytest.raises(RuntimeError, match='busy'):
        resource_lifecycle.release_resource('plan_111111111111', actor='owner')
    assert projection.graph == before
    assert projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)
    projection.blocked = False
    resource_lifecycle.release_resource('plan_111111111111', actor='owner')
    assert not projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)


def test_lost_response_retries_finalization_and_removes_associations(projection):
    """删除节点后响应丢失，重试只补完收尾并清除关联，不重复修改 Lineage"""
    associate(projection, 'skill_111111111111')
    projection.lost_response = True
    with pytest.raises(RuntimeError, match='lost response'):
        resource_lifecycle.release_resource('skill_111111111111', actor='owner')
    resource_lifecycle.release_resource('skill_111111111111', actor='owner')
    assert not projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)
    assert len([c for c in projection.calls if c[0] == 'ApplyLineageChanges']) == 1


def test_shared_workflow_refuses_cascade(projection):
    """事件流还连向其他 Resident 时拒绝级联删除"""
    associate(projection, 'paper_111111111111')
    projection.graph['program']['wires'].append({'id': 'wire_shared', 'source': 'effect_111111111111', 'target': 'resident_222222222222'})
    with pytest.raises(ValueError, match='shared'):
        resource_lifecycle.release_resource('paper_111111111111', actor='owner')
    assert not any(name == 'ApplyLineageChanges' for name, _ in projection.calls)


def test_shared_resident_refuses_cascade(projection):
    """Resident 还关联其他资源时拒绝级联删除且不发出任何指令"""
    associate(projection, 'paper_111111111111')
    associate(projection, 'paper_222222222222', scope='another')
    with pytest.raises(ValueError, match='another resource'):
        resource_lifecycle.release_resource('paper_111111111111', actor='owner')
    assert not projection.calls


def test_rotating_slot_deletes_old_projection_before_overwriting_mapping(tmp_path, projection):
    """轮换 TAU Resident 槽位先删除旧投影，删除失败时保留原映射"""
    assignments = TauResidentAssignments(tmp_path / 'slots.json')
    old = assignments.record_identity('workflow.paper', resident_id=RID, lineage_id=LID)
    projection.blocked = True
    with pytest.raises(RuntimeError, match='busy'):
        assignments.rotate('workflow.paper', changed_by='owner')
    assert assignments.assignment('workflow.paper') == old
    projection.blocked = False
    new = assignments.rotate('workflow.paper', changed_by='owner')
    assert new['slot_id'] != old['slot_id'] and not new['resident_id']
    assert projection.graph['program']['wires'] == []


def test_paper_delete_cleans_paper_and_its_reviews_before_removing_files(monkeypatch):
    """删除论文先清理论文及其评审的投影，评审清理失败时不删除文件"""
    from management_portal import paper_resources, paper_store
    monkeypatch.setattr(paper_resources, '_paper_by_id', lambda _: {'paper_id': 'paper_111111111111'})
    monkeypatch.setattr(paper_resources, '_all_reviews', lambda: [{'review_id': 'review_111111111111', 'paper_id': 'paper_111111111111'}])
    clean = Mock(side_effect=[None, RuntimeError('review busy')])
    monkeypatch.setattr(resource_lifecycle, 'release_resource', clean)
    remove = Mock()
    monkeypatch.setattr(paper_store.PAPER_STORE, 'delete_paper', remove)
    with pytest.raises(RuntimeError, match='review busy'):
        paper_resources.delete_paper({'paper_id': 'paper_111111111111'}, 'owner')
    assert [c.args[0] for c in clean.call_args_list] == ['paper_111111111111', 'review_111111111111']
    remove.assert_not_called()
