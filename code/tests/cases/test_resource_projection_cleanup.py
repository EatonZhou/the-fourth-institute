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
isa_call = isa_client.call


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
    state = SimpleNamespace(graph=graph, blocked=False, lost_response=False, refuse_finalization=False, calls=[])
    def call(actor, name, args, **kwargs):
        state.calls.append((name, copy.deepcopy(args)))
        if name == 'GetResident':
            if not any(n['id'] == args['resident_id'] for n in graph['nodes']):
                raise InstructionError('instruction.not_found', 'deleted')
            return {'resident': {'resident_id': RID, 'lineage_id': LID}}
        if name == 'GetLineage':
            return {'lineage': copy.deepcopy(graph)}
        if name == 'ApplyLineageChanges':
            if 'version' not in kwargs:
                assert args['operations'] == [{'op': 'delete', 'kind': 'resident', 'id': RID}]
                assert not any(n['id'] == RID for n in graph['nodes'])
                if state.refuse_finalization:
                    raise InstructionError('instruction.forbidden', 'no current Resident grant')
                return {'deleted_ids': [RID]}
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


def test_release_finishes_when_its_workbench_was_deleted_with_the_project(projection, monkeypatch):
    """A committed Workbench tombstone does not block discussion cleanup."""
    from management_portal.workbench_projections import service
    from management_portal.resources import directory_registrations
    associate(projection, 'workbench_111111111111')
    entry = projection.store.get('alice', resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE, 'discussion')['value']
    entry['metadata'].update(workbench_id='workbench_111111111111', workbench_role='chat')
    projection.store.put('alice', resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE, 'discussion', entry)
    monkeypatch.setattr(directory_registrations, 'is_deleted', lambda rid: rid == 'workbench_111111111111')
    monkeypatch.setattr(service, 'get_service', lambda: pytest.fail('Never read a deleted Workbench'))
    assert resource_lifecycle.release_resource('workbench_111111111111', actor='owner') == {RID}
    assert not projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)


def test_live_workbench_read_failure_is_not_treated_as_deletion(projection, monkeypatch):
    from management_portal.workbench_projections import service
    from management_portal.resources import directory_registrations
    associate(projection, 'paper_111111111111')
    entry = projection.store.get('alice', resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE, 'discussion')['value']
    entry['metadata'].update(workbench_id='workbench_111111111111', workbench_role='chat')
    projection.store.put('alice', resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE, 'discussion', entry)
    monkeypatch.setattr(directory_registrations, 'is_deleted', lambda rid: False)
    def denied(*args):
        raise PermissionError('denied')
    monkeypatch.setattr(service, 'get_service', lambda: SimpleNamespace(store=SimpleNamespace(get=denied)))
    with pytest.raises(PermissionError, match='denied'):
        resource_lifecycle.release_resource('paper_111111111111', actor='owner')
    assert projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)


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


@pytest.mark.parametrize("completed", [True, False])
def test_forbidden_projection_replay_requires_committed_deletion_proof(projection, monkeypatch, completed):
    from management_portal import society_service

    resource_id = 'plan_111111111111'
    associate(projection, resource_id)
    projection.refuse_finalization = True
    projection.graph['nodes'] = [node for node in projection.graph['nodes'] if node['id'] != RID]
    completion = Mock(return_value=completed)
    monkeypatch.setattr(society_service, 'get_society_service', lambda: SimpleNamespace(
        resident=SimpleNamespace(resident_deletion_complete=completion),
    ))
    if completed:
        assert resource_lifecycle.release_resource(resource_id, actor='owner') == {RID}
        assert not projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)
    else:
        with pytest.raises(InstructionError, match='no current Resident grant'):
            resource_lifecycle.release_resource(resource_id, actor='owner')
        assert projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)
    completion.assert_called_once_with(RID, LID)


@pytest.mark.parametrize("deleted,lineage,pending,projection_pending,complete", [
    (True, LID, False, False, True),
    (False, LID, False, False, False),
    (True, "lineage_222222222222", False, False, False),
    (True, LID, True, False, False),
    (True, LID, False, True, False),
])
def test_deletion_completion_requires_exact_resident_tombstone(
    monkeypatch, deleted, lineage, pending, projection_pending, complete,
):
    from management_portal.society_service import resident_workspace_domain as domain

    history = Mock(return_value={"deleted_at": "2026-11-28T06:54:00Z", "lineage_id": lineage} if deleted else None)
    store = SimpleNamespace(database=SimpleNamespace(snapshot=nullcontext), resident_deletion=history,
                            projection_outbox=SimpleNamespace(resident_resource_projection_pending=lambda _: projection_pending))
    monkeypatch.setattr(domain.resident_catalog, 'get_catalog', lambda: SimpleNamespace(state_store=store))
    monkeypatch.setattr(domain.resident_record_domain, 'lifecycle_operations', lambda: SimpleNamespace(pending=lambda _: pending))
    assert domain.resident_deletion_complete(RID, LID) is complete
    history.assert_called_once_with(RID)


def test_lost_response_retries_finalization_and_removes_associations(projection):
    """删除节点后响应丢失，重试只补完收尾并清除关联，不重复修改 Lineage"""
    associate(projection, 'skill_111111111111')
    projection.lost_response = True
    with pytest.raises(RuntimeError, match='lost response'):
        resource_lifecycle.release_resource('skill_111111111111', actor='owner')
    resource_lifecycle.release_resource('skill_111111111111', actor='owner')
    assert not projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)
    edits = [args['operations'] for name, args in projection.calls if name == 'ApplyLineageChanges']
    assert len(edits) == 2
    assert sum(any(op['kind'] == 'component' for op in operations) for operations in edits) == 1


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


def test_resource_release_preserves_shared_personal_history(projection):
    resource_id = 'paper_111111111111'
    associate(projection, resource_id)
    associate(projection, resource_id, actor='bob', scope='surface-chat:bob:paper:history')
    with pytest.raises(ValueError, match='personal history'):
        resource_lifecycle.release_resource(resource_id, actor='owner')
    assert not projection.calls
    assert len(projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)) == 2


@pytest.mark.parametrize('allowed', [True, False])
def test_projection_outbox_uses_society_owner_with_original_actor(projection, monkeypatch, allowed):
    from management_portal.hatg import routing, socket_rpc
    from management_portal.hatg.contracts import InstructionResult, failed_result

    resource_id = 'plan_111111111111'
    associate(projection, resource_id)
    caller = 'original-owner' if allowed else 'original-member'
    forward = isa_client.call  # The fixture's owner implementation.
    monkeypatch.setattr(isa_client, 'call', isa_call)
    monkeypatch.setenv('PORTAL_ISA_ROUTING', 'socket')
    monkeypatch.setattr(routing, 'local_kernel', lambda owner: None)
    monkeypatch.setattr(routing, 'build_kernel', lambda owner: pytest.fail('Maintenance loaded a local kernel'))

    remote_calls = []

    def remote(owner, call, context, **kwargs):
        assert owner == 'lineage'
        assert context.caller == caller
        remote_calls.append(call.instruction)
        if not allowed and call.instruction == 'ApplyLineageChanges':
            return failed_result('inst_lin_denied', call.instruction,
                                 InstructionError('instruction.forbidden', 'Resident admin required'))
        if call.instruction == 'ApplyLineageChanges' and call.expected_version is None:
            assert all(node['id'] != RID for node in projection.graph['nodes'])
            result = {'deleted_ids': [RID]}
        else:
            result = forward(context.caller, call.instruction, call.arguments, version=call.expected_version)
        return InstructionResult('inst_lin_ok', call.instruction, 'committed', result)

    monkeypatch.setattr(socket_rpc, 'call_remote', remote)
    worker = resource_lifecycle.ResourceProjectionCleanup.__new__(resource_lifecycle.ResourceProjectionCleanup)
    worker.collection = 'projection_releases'
    entries = {resource_id: {'resource_id': resource_id, 'actor': caller, 'attempts': 0}}
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = (42,)
    writer = SimpleNamespace(put=lambda _, key, value: entries.__setitem__(key, value),
                             delete=lambda _, key: entries.pop(key))
    worker.store = SimpleNamespace(
        documents=lambda _: entries, get=lambda _, key: copy.deepcopy(entries.get(key)),
        writer=lambda _: nullcontext(writer), database=SimpleNamespace(
            schema='test', read=lambda: nullcontext(connection), advisory_lock=lambda _: nullcontext()),
    )
    before = copy.deepcopy(projection.graph)

    result, = worker.run_pending()

    assert 'ApplyLineageChanges' in remote_calls
    associations = projection.store.list_namespace(resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE)
    if allowed:
        assert result['status'] == 'completed'
        assert not entries and not associations
        assert all(node['id'] != RID for node in projection.graph['nodes'])
    else:
        assert result['status'] == 'retry'
        assert entries[resource_id]['error_code'] == 'instruction.forbidden'
        assert entries[resource_id]['attempts'] == 1
        assert associations and projection.graph == before


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


def test_paper_delete_is_not_blocked_by_projection_references(tmp_path, monkeypatch):
    """引用清理失败仍注销论文，Review 投影由重建的 owner 持久重试。"""
    from management_portal import paper_resources, paper_store, paper_state_store
    monkeypatch.setattr(paper_resources, '_paper_by_id', lambda _: {'paper_id': 'paper_111111111111'})
    paper_state_store.append('reviews', {'review_id': 'review_111111111111', 'paper_id': 'paper_111111111111'})
    clean = Mock(side_effect=[None, RuntimeError('review busy')])
    monkeypatch.setattr(resource_lifecycle, 'release_resource', clean)
    store = paper_store.PaperStore(tmp_path / 'papers')
    store.register({'id': 'paper_111111111111', 'title': 'Projection test'}, 'owner')
    monkeypatch.setattr(paper_resources, 'PAPER_STORE', store)
    result = paper_resources.delete_paper({'paper_id': 'paper_111111111111'}, 'owner')
    assert [c.args[0] for c in clean.call_args_list] == ['paper_111111111111', 'review_111111111111']
    assert store.paper_by_id('paper_111111111111') is None
    assert result['projection_cleanup_errors'] == [{'resource_id': 'review_111111111111', 'error': 'review busy'}]
    restarted = resource_lifecycle.ResourceProjectionCleanup()
    assert [entry['resource_id'] for entry in restarted.pending()] == ['review_111111111111']
    clean.side_effect = None
    restarted.run_pending()
    assert not restarted.pending()
