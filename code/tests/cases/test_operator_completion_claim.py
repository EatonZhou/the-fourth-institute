"""Operator completion has a short claim and an unlocked continuation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import FrozenInstanceError
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from management_common.principal import OperationIdentity
from management_portal.society_service import lineage_program_execution as owner
from management_portal.society_service import lineage_operator_runtime as runtime
from management_portal.society_service import lineage_execution_authority as locks
from management_portal.society_service import lineage_primitives
from tests.cases.test_lineage_operator_runtime import (
    lineage_id, _invocation, OPERATOR_ID, EXECUTOR_ID, TARGET_ID,
)


@pytest.fixture
def completion(lineage_id, tmp_path, monkeypatch):
    event = lineage_primitives.make_event('resident.work_finished',
        identity=OperationIdentity.for_actor('sample_admin').to_dict(),
        payload={'trigger_step_id': 'step_test'})
    pending = {**_invocation('msg_claim', event.id), 'program': True, 'event': event.to_dict()}
    runtime.update_pending_delivery(lineage_id, OPERATOR_ID, pending)
    graph = {'lineage_id': lineage_id, 'nodes': [{'id': EXECUTOR_ID}, {'id': TARGET_ID}],
             'program': {'operator_executors': {EXECUTOR_ID: [OPERATOR_ID]},
                         'packet_operators': {OPERATOR_ID: {'kind': 'selector'}},
                         'wires': [{'id': 'wire_output', 'source': OPERATOR_ID,
                                    'target': TARGET_ID, 'kind': 'route', 'config': {}}]}}
    def gate(_):
        return locks.execution_authority(lineage_id, root=tmp_path)
    def resume(*_):
        assert locks._OWNERS[lineage_id] == get_ident()
        return 'sample_admin'
    ports = SimpleNamespace(state=SimpleNamespace(
        read_lineage=lambda *a, **k: deepcopy(graph), lineage_scope=lambda _: nullcontext(),
        lineage_execution_authority=gate, resume_program_execution_principal=resume))
    monkeypatch.setattr(owner, '_compiled_program', lambda g: g['program'])
    return SimpleNamespace(lid=lineage_id, pending=pending, graph=graph, gate=gate, ports=ports)


def prepare(c):
    return owner.prepare_operator_completion(c.lid, EXECUTOR_ID, 'msg_claim', 'sample_admin', ports=c.ports)


def test_expected_pop_does_not_consume_replacement(completion):
    """精确领取不删除同消息键的新记录"""
    c = completion
    expected = runtime.read_operator_runtime(c.lid, OPERATOR_ID)['pending_deliveries']['msg_claim']
    replacement = {**expected, 'replacement': True}
    runtime.update_pending_delivery(c.lid, OPERATOR_ID, replacement)
    assert runtime.pop_pending_delivery(c.lid, OPERATOR_ID, 'msg_claim', expected=expected) is None
    assert runtime.read_operator_runtime(c.lid, OPERATOR_ID)['pending_deliveries']['msg_claim'] == replacement
    assert runtime.pop_pending_delivery(c.lid, OPERATOR_ID, 'msg_claim', expected=replacement) == replacement


def test_prepare_concurrent_has_one_immutable_winner(completion):
    """并发完成仅一个领取者，领取对象不可修改"""
    c = completion
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: prepare(c), range(2)))
    winners = [item for item in results if item is not None]
    assert len(winners) == 1
    assert c.lid not in locks._OWNERS
    with pytest.raises(FrozenInstanceError):
        winners[0].actor = 'other'
    assert not runtime.read_operator_runtime(c.lid, OPERATOR_ID)['pending_deliveries']


def test_prepare_cas_rejects_replacement_during_principal_resume(completion):
    """核验后到原子删除前替换记录，旧完成不得领取"""
    c = completion
    expected = runtime.read_operator_runtime(c.lid, OPERATOR_ID)['pending_deliveries']['msg_claim']
    replacement = {**expected, 'replacement': True}
    def resume(*_):
        runtime.update_pending_delivery(c.lid, OPERATOR_ID, replacement)
        return 'sample_admin'
    c.ports.state.resume_program_execution_principal = resume
    assert prepare(c) is None
    assert runtime.read_operator_runtime(c.lid, OPERATOR_ID)['pending_deliveries']['msg_claim'] == replacement


def test_cancel_before_claim_cannot_be_restored(completion):
    """取消先发生时不再领取，也不恢复待处理记录"""
    c = completion
    runtime.cancel_event(c.lid, OPERATOR_ID, c.pending['event']['id'])
    assert prepare(c) is None
    assert not runtime.read_operator_runtime(c.lid, OPERATOR_ID)['pending_deliveries']


@pytest.mark.parametrize('change', ['none', 'delete', 'cancel_and_delete'])
def test_finish_reply_dispatch_settle_outside_authority(completion, monkeypatch, change):
    """领取后的慢回复不阻塞其他消息，改线仍由实际下游入口拒绝"""
    c = completion
    claim = prepare(c)
    reading, release, edited = Event(), Event(), Event()
    settled, delivered = [], []
    wire = deepcopy(c.graph['program']['wires'][0])
    def reply(*a, **k):
        assert c.lid not in locks._OWNERS
        reading.set()
        assert release.wait(5)
        return {}, 'selector', SimpleNamespace(packets=[], by_source_port={})
    def dispatch(*a, **k):
        assert c.lid not in locks._OWNERS
        event = a[3]
        invocation = {'source_wire_id': wire['id'], 'source_id': OPERATOR_ID,
                      'prompt': 'reply', 'packet': {'id': 'packet_test'}}
        # Exercise the real current-route rejection; successful delivery is not
        # needed here (its durable admission has separate direct coverage).
        if change != 'none':
            result = owner._dispatch_program_packet_to_agent(
                c.graph, TARGET_ID, event, invocation, 'sample_admin', ports=c.ports)
            assert result['status'] == 'dropped'
            assert result['reason'] == 'route_changed'
        else:
            delivered.append(event.id)
        return {'deliveries': [], 'operators': []}
    def settle(*a, **k):
        assert c.lid not in locks._OWNERS
        settled.append(a[3])
    monkeypatch.setattr(owner, '_operator_reply', reply)
    monkeypatch.setattr(owner, 'dispatch_program_packets', dispatch)
    monkeypatch.setattr(owner, '_program_packet_wires', lambda *a: [wire])
    monkeypatch.setattr(owner, '_settle_operator_turn', settle)
    monkeypatch.setattr(owner.lineage_program_operator_execution, 'has_changed_output_topology', lambda *a: False)
    def edit():
        with c.gate(c.lid):
            if change != 'none':
                c.graph['program']['wires'] = []
            if change == 'cancel_and_delete':
                runtime.cancel_event(c.lid, OPERATOR_ID, c.pending['event']['id'])
            edited.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(owner.finish_operator_completion, claim, ports=c.ports)
        try:
            assert reading.wait(5)
            editing = pool.submit(edit)
            assert edited.wait(5), 'Slow reply held the Lineage authority'
            editing.result()
        finally:
            release.set()
        assert future.result()['status'] == owner.lineage_program_operator_execution.completed_status('selector')
    assert settled == ['completed']
    assert len(delivered) == (change == 'none')
    assert not runtime.read_operator_runtime(c.lid, OPERATOR_ID)['pending_deliveries']


def test_legacy_entry_delegates_prepare_and_finish(monkeypatch):
    """旧入口只委托唯一两阶段实现"""
    calls = []
    claim = object()
    monkeypatch.setattr(owner, 'prepare_operator_completion', lambda *a, **k: calls.append(('prepare', a, k)) or claim)
    monkeypatch.setattr(owner, 'finish_operator_completion', lambda *a, **k: calls.append(('finish', a, k)) or {'ok': True})
    ports = object()
    assert owner.complete_operator_invocation('lid', 'rid', 'mid', 'actor', terminal_status='cancelled', ports=ports) == {'ok': True}
    assert calls[1] == ('finish', (claim,), {'terminal_status': 'cancelled', 'ports': ports})


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'completed'])
def test_finish_terminal_or_changed_topology_skips_reply(completion, monkeypatch, status):
    """失败取消和领取时已变更拓扑均只结算，不读取回复或派发"""
    c = completion
    if status == 'completed':
        # This differs from the pending output topology at the claim boundary.
        c.graph['program']['packet_operators'][OPERATOR_ID]['outgoing_packet_wire_ids'] = ['wire_new']
    claim = prepare(c)
    settled = []
    def settle(*a, **k):
        assert c.lid not in locks._OWNERS
        settled.append(a[3])
    monkeypatch.setattr(owner, '_settle_operator_turn', settle)
    monkeypatch.setattr(owner, '_operator_reply', lambda *a, **k: pytest.fail('No reply allowed'))
    result = owner.finish_operator_completion(claim, terminal_status=status, ports=c.ports)
    assert settled == (['discarded'] if status == 'completed' else [status])
    assert result['status'] == ('discarded' if status == 'completed' else 'failed')
    assert not runtime.read_operator_runtime(c.lid, OPERATOR_ID)['pending_deliveries']


def test_finish_agent_consumes_prepared_handoffs_without_settlement(completion, monkeypatch):
    """普通 Agent follow-up只消费已结算输入，不重新结算或持全局锁"""
    c = completion
    invocations = [{'trigger_path_id': 'path_test'}]
    calls = []
    def evidence(*a):
        assert c.lid not in locks._OWNERS
        calls.append('evidence')
        return {'reply': ''}
    def notify(*a, **k):
        assert c.lid not in locks._OWNERS
        assert a[3] == invocations
        calls.append('notify')
    c.ports.resident = SimpleNamespace(completed_message_evidence=evidence,
                                       notify_program_resident_invocation_terminal=notify)
    c.ports.flow = SimpleNamespace(dispatch_lineage_program_event=lambda *a, **k: pytest.fail('No routes'))
    monkeypatch.setattr(owner.lineage_program_resident_inputs, 'settle_resident_handoffs',
                        lambda *a, **k: pytest.fail('Already settled'))
    monkeypatch.setattr(owner.lineage_program_resident_inputs, 'path_resident_inputs', lambda *a: [])
    result = owner.finish_agent_completion(c.lid, EXECUTOR_ID, {'id': EXECUTOR_ID},
        'msg_claim', 'sample_admin', '2026-11-05T00:00:00Z', invocations, ports=c.ports)
    assert result['invocations'] == invocations
    assert calls == ['evidence', 'notify']


def test_legacy_agent_entry_only_settles_then_delegates(monkeypatch):
    """普通 Agent旧入口结算一次，交唯一follow-up实现"""
    calls, invocations = [], [{'trigger_path_id': 'path_test'}]
    monkeypatch.setattr(owner.lineage_program_resident_inputs, 'settle_resident_handoffs',
                        lambda *a, **k: calls.append('settle') or invocations)
    def finish(*a, **k):
        calls.append('finish')
        assert a[6] is invocations
        return {'done': True}
    monkeypatch.setattr(owner, 'finish_agent_completion', finish)
    assert owner.complete_agent_invocations('lid', 'rid', {}, 'mid', 'actor', 'at', ports=object()) == {'done': True}
    assert calls == ['settle', 'finish']
