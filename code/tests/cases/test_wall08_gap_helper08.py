""":importance: P2"""
from __future__ import annotations

import multiprocessing
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_common.json_columns import json_value
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.accounts import ExecutionAccount, ExecutionAccounts
from management_portal.execution.backends import BackendContext, BackendInvocation, CodexBackend
from management_portal.execution.account_adapters import CodexAccountRoute
from management_portal.execution.contracts import ExecutionInputControl, ExecutionRequest, ExecutionResult, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.session_manager import RuntimeSession


def deny_network(*args, **kwargs):
    raise AssertionError('helper08 fixture may not connect to a service or Provider')


@pytest.fixture(autouse=True)
def network_is_forbidden(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', deny_network)


class SyntheticAccountAdapter:
    provider = "codex"

    def __init__(self):
        self._accounts = tuple(
            ExecutionAccount(
                account_id=f"codex_{route}",
                provider="codex",
                route_id=route,
                capacity_key=f"codex:{route}",
                max_concurrency=1,
            )
            for route in ("helper-a", "helper-b")
        )

    def accounts(self):
        return self._accounts

    def resolve(self, route_id, options, *, bound=False):
        selected = route_id or "helper-a"
        return next(account for account in self._accounts if account.route_id == selected)


def scheduler_at(*, source_capacity=1):
    return Scheduler(capacity_limits={'codex:helper-a': source_capacity, 'codex:helper-b': 1},
                     accounts=ExecutionAccounts((SyntheticAccountAdapter(),)),
                     dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0,
                     quota_reserve_enabled=False)


def request_for(route='helper-a', *, actor='helper08-synthetic', agent_id='helper08-synthetic-agent'):
    return ExecutionRequest.new(actor=actor, agent_id=agent_id, prompt='fixture only; never sent externally',
                                service_type='agent_session', provider='codex', operation='turn',
                                session=SessionOptions(backend_route_id=route))


def claim_request(scheduler, request, worker_id):
    scheduler.register_worker(worker_id=worker_id, build_id='helper08-fixture', capabilities=['agent_session:codex'])
    scheduler.submit(request)
    claim = scheduler.claim(worker_id=worker_id, capabilities=['agent_session:codex'])
    assert claim['claimed'] is True
    owner = {'worker_id': worker_id, 'claim_token': claim['claim_token']}
    scheduler.start(request.execution_id, **owner)
    return owner






def request_row(scheduler, execution_id):
    with scheduler._connect() as connection:
        return dict(connection.execute('SELECT * FROM executions WHERE execution_id=%s', (execution_id,)).fetchone())


def test_capacity_full_destination_rejects_atomically_then_releases_source_slot():
    """目标路由容量已满时改派被原子拒绝，目标空出后改派成功并释放原路由槽位"""
    scheduler = scheduler_at()
    first = request_for('helper-a')
    first_owner = claim_request(scheduler, first, 'helper08-capacity-worker')
    second = request_for('helper-b')
    second_owner = claim_request(scheduler, second, 'helper08-capacity-worker')
    before = request_row(scheduler, first.execution_id)
    events_before = list(scheduler._transient_event_items[first.execution_id])
    rejected = scheduler.reassign_capacity(first.execution_id, **first_owner, backend_route_id='helper-b')
    assert rejected == {'accepted': False, 'capacity_key': 'codex:helper-a',
                        'changed': False, 'reason': 'capacity_full'}
    assert request_row(scheduler, first.execution_id) == before
    assert list(scheduler._transient_event_items[first.execution_id]) == events_before
    scheduler.finish(ExecutionResult(execution_id=second.execution_id, status='completed', output={}), **second_owner)
    moved = scheduler.reassign_capacity(first.execution_id, **first_owner, backend_route_id='helper-b')
    assert moved == {'accepted': True, 'capacity_key': 'codex:helper-b', 'changed': True}
    assert json_value(request_row(scheduler, first.execution_id)['request_json'])['session']['backend_route_id'] == 'helper-b'
    same = scheduler.reassign_capacity(first.execution_id, **first_owner, backend_route_id='helper-b')
    assert same == {'accepted': True, 'capacity_key': 'codex:helper-b', 'changed': False}
    next_source = request_for('helper-a')
    claim_request(scheduler, next_source, 'helper08-capacity-worker')
    next_destination = request_for('helper-b')
    scheduler.submit(next_destination)
    assert scheduler.claim(worker_id='helper08-capacity-worker', capabilities=['agent_session:codex'])['claimed'] is False


def test_capacity_independent_schedulers_cannot_overbook_one_destination_slot():
    """两个独立 Scheduler 并发改派到同一目标槽位时只有一个成功"""
    scheduler = scheduler_at(source_capacity=2)
    peer = scheduler_at(source_capacity=2)
    requests = [request_for(), request_for()]
    owners = [claim_request(scheduler, request, 'helper08-capacity-worker') for request in requests]
    before = [request_row(scheduler, request.execution_id) for request in requests]
    ready = Barrier(2)

    def move(index):
        ready.wait(timeout=10)
        return [scheduler, peer][index].reassign_capacity(
            requests[index].execution_id, **owners[index], backend_route_id='helper-b')

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(move, range(2)))
    assert sorted(receipt['accepted'] for receipt in receipts) == [False, True]
    for index, receipt in enumerate(receipts):
        if receipt['accepted']:
            assert receipt == {'accepted': True, 'capacity_key': 'codex:helper-b', 'changed': True}
            assert json_value(request_row(scheduler, requests[index].execution_id)['request_json'])['session']['backend_route_id'] == 'helper-b'
        else:
            assert receipt == {'accepted': False, 'capacity_key': 'codex:helper-a',
                               'changed': False, 'reason': 'capacity_full'}
            assert request_row(scheduler, requests[index].execution_id) == before[index]
    routes = [json_value(request_row(scheduler, request.execution_id)['request_json'])['session']['backend_route_id']
              for request in requests]
    assert sorted(routes) == ['helper-a', 'helper-b']


def test_ttl_prunes_usage_events_but_preserves_hourly_usage_and_all_agentdata():
    """清理过期终态执行时删除 token 用量事件，保留小时用量汇总和全部 AgentData"""
    scheduler = scheduler_at()
    store = AgentDataStore()
    request = request_for()
    owner = claim_request(scheduler, request, 'helper08-ttl-worker')
    observed_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    store.begin_execution(execution_id=request.execution_id, agent_id=request.agent_id, actor_ref=request.actor,
                          provider='codex', started_at=observed_at)
    session = store.ensure_provider_session(provider='codex', backend_route_id='helper-a',
                                             backend_session_id='helper08-native-synthetic',
                                             agent_id=request.agent_id, actor_ref=request.actor)
    store.bind_provider_session('helper08-binding', session['provider_session_ref'])
    store.bind_execution_provider_session(request.execution_id, provider_session_ref=session['provider_session_ref'])
    store.record_input_acceptance(request.execution_id, agent_id=request.agent_id, provider_turn_id='helper08-turn')
    segments = [store.put_segment(segment_type=kind, content=f'synthetic {kind}',
                                   created_by_actor_ref=request.actor, created_by_agent_id=request.agent_id,
                                   execution_id=request.execution_id) for kind in ['input', 'model', 'harness']]
    store.append_agent_segments(request.agent_id, segments, actor_ref=request.actor)
    usage = {'input_tokens': 11, 'output_tokens': 7, 'total_tokens': 18}
    assert scheduler.record_token_usage_delta(request.execution_id, **owner, event_id='helper08-usage',
                                              usage=usage, occurred_at=observed_at)['recorded'] is True
    store.finish_execution(request.execution_id, status='completed', finished_at=observed_at,
                           usage=usage, provider_turn_id='helper08-turn')
    scheduler.finish(ExecutionResult(execution_id=request.execution_id, status='completed', output={'message': 'synthetic'},
                                     usage=usage), **owner)

    def all_agentdata():
        with store.database.read() as connection:
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema=current_schema() ORDER BY table_name"
                )
            ]
            return {table: [tuple(row) for row in connection.execute('SELECT * FROM "' + table.replace('"', '""') + '"')]
                    for table in tables}

    before = all_agentdata()
    with scheduler._connect() as connection:
        hourly = [tuple(row) for row in connection.execute('SELECT * FROM execution_account_usage_hourly')]
        assert hourly
        assert connection.execute('SELECT COUNT(*) FROM execution_token_usage_events').fetchone()[0] == 1
    scheduler.acknowledge(request.execution_id, actor=request.actor)
    with scheduler._connect() as connection:
        connection.execute('UPDATE executions SET updated_at=%s WHERE execution_id=%s',
                           ('2000-01-01T00:00:00.000Z', request.execution_id))
        connection.commit()
    assert scheduler.prune_terminal(older_than_epoch=978307200) == 1
    with scheduler._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM execution_token_usage_events').fetchone()[0] == 0
        assert [tuple(row) for row in connection.execute('SELECT * FROM execution_account_usage_hourly')] == hourly
    assert all_agentdata() == before
    reopened = AgentDataStore()
    assert reopened.execution(request.execution_id)['status'] == 'completed'
    trace_ids = {
        item['segment_id']
        for item in reopened.read_sequence(reopened.trace_sequence_id(request.agent_id))['segments']
    }
    assert set(segments).issubset(trace_ids)
    assert reopened.provider_binding('helper08-binding')['provider_session_ref'] == session['provider_session_ref']


def test_activity_limits_utf8_bytes_without_truncating_canonical_observer(subtests, isolated_portal_case):
    """活动事件中的 Provider 文本按 UTF-8 字节截到 16 KiB 内，原始事件观察者收到完整文本"""
    for text in ['a' * 15999, 'a' * 16000, 'a' * 16385, '界' * 6000, '🙂' * 5000]:
        with subtests.test(text=text), isolated_portal_case() as case_path:
            activities = []
            canonical = []
            context = BackendContext(emit_event=lambda kind, details: activities.append((kind, details)),
                                     cancellation_requested=lambda: False, set_interrupt_target=lambda *args: None,
                                     provider_event_observer=lambda provider, session, route, event: canonical.append(event))
            event = {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': text}}}
            context.observe_provider_event(event, provider='codex', backend_session_id='helper08-synthetic', backend_route_id='helper-a')
            assert canonical == [event] and canonical[0]['params']['item']['text'] == text
            observed = [details for kind, details in activities if kind == 'provider_event']
            assert len(observed) == 1
            summary = observed[0]['params']['item']['text']
            assert len(summary.encode('utf-8')) <= 16 * 1024
            assert text.startswith(summary) and '\ufffd' not in summary


@pytest.mark.parametrize('delivered', [True, False])
def test_codex_adapter_uses_durable_control_and_records_synthetic_sdk_outcome(tmp_path, delivered):
    """codex 适配器从持久化输入控制取追加输入，并按 SDK 结果记为已送达或失败"""
    scheduler = scheduler_at()
    request = request_for()
    owner = claim_request(scheduler, request, 'helper08-control-worker')
    scheduler.checkpoint(request.execution_id, **owner, checkpoint={'turn_id': 'helper08-turn', 'resumable': True})
    control = ExecutionInputControl.new(execution_id=request.execution_id, actor=request.actor,
                                         input=[{'type': 'text', 'text': 'synthetic extra input'}],
                                         expected_turn_id='helper08-turn')
    scheduler.submit_input_control(control)
    routed = []

    class SyntheticSDK:
        def run_turn(self, *args, **kwargs):
            pending = kwargs['turn_control_provider']()
            assert scheduler.input_control(control.control_id)['status'] == 'pending'
            assert pending['control_id'] == control.control_id
            assert pending['expected_turn_id'] == 'helper08-turn'
            assert pending['input'] == [{'type': 'text', 'text': 'synthetic extra input'}]
            routed.append(pending)
            kwargs['event_callback']({'method': 'item/completed', 'params': {
                'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': 'synthetic answer'}}})
            kwargs['turn_control_result_callback'](pending, delivered, '' if delivered else 'synthetic-sdk-rejected')
            return SimpleNamespace(thread_id='helper08-native', turn_id='helper08-turn', response='synthetic answer', token_usage={})

    route = CodexAccountRoute(account_id='helper-a', codex_home=tmp_path / 'empty-home',
                              socket_path=tmp_path / 'unused.sock', max_concurrency=1)
    registry = Mock()
    registry.get.return_value = route
    registry.find_thread_rollout.return_value = None
    activities = []
    context = BackendContext(emit_event=lambda kind, details: activities.append((kind, details)),
                             cancellation_requested=lambda: False, set_interrupt_target=lambda *args: None,
                             turn_control_provider=lambda: scheduler.wait_for_worker_control(
                                 request.execution_id, **owner, timeout_seconds=0.1)['turn_controls'][0],
                             turn_control_result_callback=lambda pending, accepted, error: scheduler.complete_input_control(
                                 pending['control_id'], execution_id=request.execution_id, **owner, delivered=accepted, error=error))
    result = CodexBackend(account_registry=registry, client_factory=lambda path: SyntheticSDK()).execute(
        BackendInvocation(request=request, session=RuntimeSession(mode='persistent', binding_id='',
                          backend_session_id='helper08-native', backend_route_id='helper-a', is_new_backend_session=False),
                          user_prompt=request.prompt, system_context='', attach_system_context=False,
                          is_new_backend_session=False), context)
    assert result.output['message'] == 'synthetic answer'
    assert len(routed) == 1
    receipt = scheduler.input_control(control.control_id)
    assert receipt['status'] == ('delivered' if delivered else 'failed')
    assert receipt['error'] == ('' if delivered else 'synthetic-sdk-rejected')
    assert any(kind == 'provider_event' for kind, details in activities)
