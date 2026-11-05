"""Logical Operator ownership comes from durable graph handoffs, not live pending state."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace

import pytest

from management_common.principal import OperationIdentity
from management_portal.society_service import (
    lineage_domain, lineage_operator_runtime, lineage_primitives,
    resident_conversation_stream,
    state_store, trigger_path_storage, trigger_paths,
    lineage_program_execution, lineage_program_terminal_reconciliation,
)
from tests.cases.test_operator_completion_claim import completion  # noqa: F401
from tests.cases.test_lineage_operator_runtime import (
    _invocation, OPERATOR_ID, EXECUTOR_ID, TARGET_ID,
)
from tests.cases.canonical_domain_fixture import canonical_projection

OTHER_LINEAGE = 'lineage_bbbbbbbbbbbb'
OTHER_OPERATOR = 'selector_bbbbbbbbbbbb'
AT = '2026-11-05T10:00:00Z'


@pytest.fixture
def lineage_id():
    identifier = 'lineage_aaaaaaaaaaaa'
    canonical_projection(identifier)
    return identifier


def register(lineage, message, *, executor=EXECUTOR_ID, operator=OPERATOR_ID,
             occurrence='event_owner', wire=None, parent_kind='operator'):
    """Use the same registration owner that accepts a real executor delivery."""
    event = lineage_primitives.make_event('manual.triggered', occurred_at=AT,
        identity=OperationIdentity.for_actor('sample_admin').to_dict(), correlation=occurrence)
    path = trigger_paths.begin_event(lineage, 'event_source_owner', event.to_dict(),
                                    cause_kind='input', cause_id=occurrence)
    with trigger_paths.data_store().database.transaction() as connection:
        parent = trigger_path_storage.admit_step(connection, trigger_path_id=path,
            node_id=operator, kind=parent_kind, activation_key=event.id, created_at=AT)
    event = lineage_primitives.normalize_event({**event.to_dict(),
        'payload': {'trigger_path_id': path, 'trigger_step_id': parent}})
    lineage_domain._register_program_agent_invocation(lineage, executor, message, event,
        operator if wire is None else wire, 'sample_admin')
    return event


def test_operator_owner_survives_pending_pop_reopen_and_definition_edit(lineage_id, monkeypatch):
    """领取并删除算子定义后，同一输入仍属于原逻辑算子，不会改归普通输出"""
    store = state_store.get_lineage_data_store()
    definition = store.lineage_definition(lineage_id)
    definition['program'] = {'components': [{'id': OPERATOR_ID, 'kind': 'selector'}]}
    store.put_lineage_definition(lineage_id, definition,
        {'schema': 'tfi.lineage-runtime.v1', 'lineage_id': lineage_id})
    event = register(lineage_id, 'msg_owner')
    pending = {**_invocation('msg_owner', event.id), 'program': True, 'event': event.to_dict()}
    lineage_operator_runtime.update_pending_delivery(lineage_id, OPERATOR_ID, pending)
    assert trigger_paths.operator_input_owner(lineage_id, EXECUTOR_ID, 'msg_owner') == OPERATOR_ID
    assert lineage_operator_runtime.pop_pending_delivery(lineage_id, OPERATOR_ID, 'msg_owner')
    replacement = deepcopy(definition)
    replacement['program'] = {}
    replacement['nodes'] = []
    store.put_lineage_definition(lineage_id, replacement,
        {'schema': 'tfi.lineage-runtime.v1', 'lineage_id': lineage_id})
    state_store.reset_lineage_data_store_for_tests()
    reopened = state_store.get_lineage_data_store()
    assert reopened is not store
    # A fresh owner uses persisted edges; no definition/pending cache can help.
    monkeypatch.setattr(reopened, 'lineage_definition', lambda *a, **k:
        pytest.fail('Input ownership must not inspect current definitions'))
    monkeypatch.setattr(lineage_operator_runtime, 'read_operator_runtime', lambda *a, **k:
        pytest.fail('Input ownership must not inspect pending deliveries'))
    for _ in range(2):
        assert trigger_paths.operator_input_owner(lineage_id, EXECUTOR_ID, 'msg_owner') == OPERATOR_ID


def test_executor_ordinary_input_and_operator_output_are_not_operator_owned(lineage_id):
    """同执行器普通新输入及算子输出业务成员使用真实 Route，不会被误归执行器领取"""
    register(lineage_id, 'msg_operator')
    register(lineage_id, 'msg_ordinary', occurrence='ordinary', wire='wire_output')
    register(lineage_id, 'msg_downstream', executor=TARGET_ID, occurrence='downstream', wire='wire_output')
    register(lineage_id, 'msg_event', occurrence='direct_event', parent_kind='event')
    assert trigger_paths.operator_input_owner(lineage_id, EXECUTOR_ID, 'msg_operator') == OPERATOR_ID
    assert trigger_paths.operator_input_owner(lineage_id, EXECUTOR_ID, 'msg_ordinary') is None
    assert trigger_paths.operator_input_owner(lineage_id, TARGET_ID, 'msg_downstream') is None
    assert trigger_paths.operator_input_owner(lineage_id, EXECUTOR_ID, 'msg_event') is None
    assert trigger_paths.operator_input_owner(lineage_id, EXECUTOR_ID, 'msg_chat_without_handoff') is None


def test_exact_resident_lineage_and_message_scope(lineage_id):
    """相同消息号跨团队、跨执行器保持各自归属，不读取另一团队或别的输入"""
    register(lineage_id, 'msg_shared')
    register(OTHER_LINEAGE, 'msg_shared', operator=OTHER_OPERATOR, occurrence='other_lineage')
    register(lineage_id, 'msg_shared', executor=TARGET_ID,
             operator=OTHER_OPERATOR, occurrence='other_executor')
    assert trigger_paths.operator_input_owner(lineage_id, EXECUTOR_ID, 'msg_shared') == OPERATOR_ID
    assert trigger_paths.operator_input_owner(OTHER_LINEAGE, EXECUTOR_ID, 'msg_shared') == OTHER_OPERATOR
    assert trigger_paths.operator_input_owner(lineage_id, TARGET_ID, 'msg_shared') == OTHER_OPERATOR
    assert trigger_paths.operator_input_owner(OTHER_LINEAGE, TARGET_ID, 'msg_shared') is None
    assert trigger_paths.operator_input_owner(lineage_id, EXECUTOR_ID, 'msg_missing') is None


def test_multiple_paths_keep_one_owner_but_conflicting_operators_fail(lineage_id):
    """同一逻辑算子多个来源保持唯一归属；两个算子占用同一输入明确拒绝猜测"""
    register(lineage_id, 'msg_shared', occurrence='first')
    register(lineage_id, 'msg_shared', occurrence='second')
    assert trigger_paths.operator_input_owner(lineage_id, EXECUTOR_ID, 'msg_shared') == OPERATOR_ID
    register(lineage_id, 'msg_shared', operator=OTHER_OPERATOR, occurrence='conflict')
    with pytest.raises(ValueError, match='more than one Packet Operator'):
        trigger_paths.operator_input_owner(lineage_id, EXECUTOR_ID, 'msg_shared')


def test_concurrent_operator_terminal_cannot_fall_back_to_ordinary_output(completion, monkeypatch):
    """真实领取者慢读期间，重复终态仍按持久归属跳过，不派发普通输出"""
    c = completion
    event = register(c.lid, 'msg_claim')
    lineage_operator_runtime.update_pending_delivery(c.lid, OPERATOR_ID,
        {**c.pending, 'event': event.to_dict()})
    c.graph['program']['wires'].append({'id': 'wire_executor_output', 'kind': 'route',
        'source': EXECUTOR_ID, 'target': TARGET_ID, 'config': {}})
    reading, release = Event(), Event()
    routed_sources, settled_leaves = [], []
    c.ports.state.now_iso = lambda: AT
    c.ports.state.lineage_scope = lambda *a, **k: nullcontext()
    c.ports.resident = SimpleNamespace(
        completed_message_evidence=lambda *a: {'reply': 'raw executor reply'},
        notify_program_resident_invocation_terminal=lambda *a, **k:
            pytest.fail('Duplicate Operator callback must not send an ordinary notification'))
    c.ports.flow = SimpleNamespace(dispatch_lineage_program_event=lambda *a, **k: {})
    terminal = lineage_program_terminal_reconciliation
    owner = lineage_program_execution
    ports = terminal.ProgramTerminalPorts(c.ports.state, c.ports.resident,
        SimpleNamespace(program_execution_ports=lambda: c.ports,
                        drain_program_event_wire_backlog=lambda *a, **k: {}),
        SimpleNamespace(lineage_ids_for_resident=lambda _: [c.lid],
                        lineage_execution_authority=c.gate))
    monkeypatch.setattr(terminal, '_mark_sources_idle', lambda *a, **k:
        ([(c.lid, EXECUTOR_ID, {'id': EXECUTOR_ID})], 1, 0))
    monkeypatch.setattr(terminal, 'dispatch_program_resident_work_finished', lambda *a, **k: [])
    monkeypatch.setattr(terminal, '_settle_paths_by_lineage', lambda *a: [])
    def settle(*a, **k):
        assert a == (c.lid,)
        assert k['resident_id'] == EXECUTOR_ID
        assert k['message_id'] == 'msg_claim'
        assert k['status'] == 'completed'
        settled_leaves.append((a[0], k['resident_id'], k['message_id']))
        return []
    monkeypatch.setattr(owner.lineage_program_resident_inputs, 'settle_resident_handoffs', settle)
    def reply(*a, **k):
        reading.set()
        assert release.wait(5)
        return {}, 'selector', SimpleNamespace(packets=[], by_source_port={})
    monkeypatch.setattr(owner, '_operator_reply', reply)
    monkeypatch.setattr(owner, '_settle_operator_turn', lambda *a, **k: None)
    monkeypatch.setattr(owner, '_program_packet_wires', lambda *a: [])
    monkeypatch.setattr(owner.lineage_program_operator_execution, 'has_changed_output_topology', lambda *a: False)
    monkeypatch.setattr(owner, 'dispatch_program_packets', lambda *a, **k:
        routed_sources.append(a[2]) or {'deliveries': [], 'operators': []})
    output = lineage_primitives.make_event('resident.work_finished', payload={'trigger_path_id': 'path_output'})
    monkeypatch.setattr(trigger_paths, 'begin_resident_output', lambda *a, **k: output)
    monkeypatch.setattr(trigger_paths, 'event_settled', lambda *a, **k: None)
    def complete():
        return terminal.handle_resident_message_terminal(EXECUTOR_ID, 'sample_admin', 'msg_claim',
            lineage_id=c.lid, ports=ports)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(complete)
        try:
            assert reading.wait(5)
            result = pool.submit(complete).result(timeout=5)
            assert result['agent_result_count'] == 0
            assert settled_leaves == [(c.lid, EXECUTOR_ID, 'msg_claim')]
            assert routed_sources == []
        finally:
            release.set()
            assert first.result(timeout=5)['operator_result_count'] == 1
    assert routed_sources == [OPERATOR_ID]


def test_committed_unlinked_handoff_is_settled_after_dispatch_cancel(completion, monkeypatch):
    """登记已提交但回执丢失，取消输入后只结算精确叶，不再派发普通输出或算子决策"""
    c = completion
    mid, at = 'msg_cancelled_owner', '2026-11-05T10:45:00Z'
    canonical_projection(EXECUTOR_ID, lineage_id=c.lid)
    event = lineage_primitives.make_event('manual.triggered', occurred_at=at,
        identity=OperationIdentity.for_actor('sample_admin').to_dict())
    path = trigger_paths.begin_event(c.lid, 'event_source_cancel', event.to_dict(),
                                    cause_kind='input', cause_id='msg_source_cancel')
    event = lineage_primitives.normalize_event({**event.to_dict(),
                                              'payload': {'trigger_path_id': path}})
    owner = lineage_program_execution
    terminal = lineage_program_terminal_reconciliation
    scope = SimpleNamespace(kind='selector', config={'operator_implementation': 'resident'},
        snapshot={}, nodes={EXECUTOR_ID: {'id': EXECUTOR_ID}},
        operator={'resident_executor_id': EXECUTOR_ID}, plan={})
    monkeypatch.setattr(owner, '_ready_inbox', lambda *a:
        (None, {'expected_wire_ids': ['wire_input'], 'inputs': {}}, {'wire_input'}))
    monkeypatch.setattr(owner, '_operator_scope', lambda *a, **k: scope)
    monkeypatch.setattr(owner.lineage_program_operator_execution, 'inputs_from_claim', lambda *a:
        SimpleNamespace(packets=[{'key': 'default'}], enveloped_packet_ids=set(), source_titles={}))
    monkeypatch.setattr(owner.lineage_program_operator_execution, 'resident_prompt', lambda *a: 'decide')
    monkeypatch.setattr(owner.lineage_instruction_context, 'resident_context', lambda *a: {})
    def commit_then_lose_ack(*args):
        lineage_domain._register_program_agent_invocation(*args)
        raise RuntimeError('test injection: registration reply lost after durable handoff commit')
    c.ports.resident = SimpleNamespace(send_node_message=lambda *a, **k: {'message_id': mid},
        register_program_agent_invocation=commit_then_lose_ack,
        notify_program_resident_invocation_terminal=lambda *a, **k:
            pytest.fail('No ordinary notification allowed'))
    c.ports.state.lineage_scope = lambda *a, **k: nullcontext()
    c.ports.state.now_iso = lambda: at
    result = owner.try_dispatch_operator(c.lid, 'sample_admin', OPERATOR_ID, event, ports=c.ports)
    assert result['status'] == 'failed'
    assert mid not in lineage_operator_runtime.read_operator_runtime(c.lid, OPERATOR_ID)['pending_deliveries']
    assert trigger_paths.operator_input_owner(c.lid, EXECUTOR_ID, mid) == OPERATOR_ID
    before = trigger_paths.get(c.lid, path)
    assert before['executions'] == []
    assert next(step for step in before['steps'] if step['kind'] == 'resident')['status'] == 'pending'
    resident_conversation_stream.get_repository().set_input_state(
        EXECUTOR_ID, mid, 'cancelled', updated_at=at)
    trigger_paths.event_settled(path, 'event_source_cancel', event.id)
    monkeypatch.setattr(terminal, '_match_terminal_nodes', lambda *a, **k:
        terminal._TerminalMatch(matched_node_count=1, terminal_nodes={(c.lid, EXECUTOR_ID)}))
    monkeypatch.setattr(owner, 'finish_agent_completion', lambda *a, **k:
        pytest.fail('Cancelled Operator executor input must not become ordinary output'))
    monkeypatch.setattr(owner, 'finish_operator_completion', lambda *a, **k:
        pytest.fail('Cancelled Operator decision must not run again'))
    ports = terminal.ProgramTerminalPorts(c.ports.state, c.ports.resident,
        SimpleNamespace(program_execution_ports=lambda: c.ports,
                        drain_program_event_wire_backlog=lambda *a, **k: {}),
        SimpleNamespace(lineage_execution_authority=c.gate))
    for _ in range(2):
        result = terminal.handle_resident_message_terminal(EXECUTOR_ID, 'sample_admin', mid,
            terminal_status='cancelled', completed_at=at, lineage_id=c.lid, ports=ports)
        assert result['agent_result_count'] == result['operator_result_count'] == 0
        assert result['program_events'] == []
    observed = trigger_paths.get(c.lid, path)
    leaf = next(step for step in observed['steps'] if step['kind'] == 'resident')
    operator = next(step for step in observed['steps'] if step['kind'] == 'operator')
    assert (observed['status'], operator['status'], leaf['status']) == ('failed', 'failed', 'cancelled')
