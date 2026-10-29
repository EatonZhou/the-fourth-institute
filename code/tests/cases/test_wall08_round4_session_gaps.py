""":importance: P2"""
from __future__ import annotations

import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.execution.accounts import ExecutionAccounts
from management_portal.execution import service as service_module
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.contracts import ExecutionInputControl, ExecutionRequest, ExecutionResult, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.service import ExecutionPreparation, ExecutionService, ExecutionServiceServer
from management_portal.execution.session_manager import SessionManager
from management_portal.execution.transport import ExecutionServiceClient


@pytest.fixture
def isolated_rpc(tmp_path, monkeypatch):
    original_connect = socket.socket.connect

    def connect_local(connection, address):
        assert connection.family == socket.AF_UNIX and str(address).startswith(str(tmp_path))
        return original_connect(connection, address)

    monkeypatch.setattr(socket.socket, 'connect', connect_local)
    monkeypatch.setattr(service_module, 'get_notification_publisher', lambda: Mock())
    store = AgentDataStore()
    scheduler = Scheduler(accounts=ExecutionAccounts(()), capacity_limits={'codex:round4-fixture': 2},
                          dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    manager = SessionManager(agent_data=store)
    preparation = ExecutionPreparation(session_manager=manager, accounts=ExecutionAccounts(()))
    servers = []

    def start(name):
        server = ExecutionServiceServer(tmp_path / name, scheduler, agent_data=store, preparation=preparation)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        assert server.started.wait(2)
        servers.append((server, thread))
        return ExecutionService(client=ExecutionServiceClient(tmp_path / name, timeout=2))

    service = start('primary.sock')
    try:
        yield SimpleNamespace(store=store, scheduler=scheduler, manager=manager, service=service,
                              tmp_path=tmp_path, start=start)
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(2)
            assert not thread.is_alive()


def bind(manager, binding_id, session_id, *, route='route-a'):
    return manager.bind_session(binding_id, provider='codex', backend_session_id=session_id,
                                backend_route_id=route, )


def active_request(fixture):
    worker_id = 'round4-synthetic-worker'
    scheduler = fixture.scheduler
    scheduler.register_worker(worker_id=worker_id, build_id='round4-test', capabilities=['agent_session:codex'])
    request = ExecutionRequest.new(actor='round4-test', prompt='synthetic, never dispatched',
                                   service_type='agent_session', provider='codex', operation='turn',
                                   session=SessionOptions(backend_route_id='round4-fixture'))
    scheduler.submit(request)
    claim = scheduler.claim(worker_id=worker_id, capabilities=['agent_session:codex'])
    assert claim['claimed'] is True
    owner = {'worker_id': worker_id, 'claim_token': claim['claim_token']}
    scheduler.start(request.execution_id, **owner)
    scheduler.checkpoint(request.execution_id, **owner,
                         checkpoint={'turn_id': 'synthetic-turn', 'resumable': True})
    return request, owner


@pytest.mark.parametrize('first', [False, True])
@pytest.mark.parametrize('second', [False, True])
def test_round4_completion_is_first_terminal_wins_with_owner_fencing(isolated_rpc, first, second):
    """输入控制以首次完成结果为准，之后的完成被忽略，claim_token 不符时拒绝"""
    request, owner = active_request(isolated_rpc)
    control = ExecutionInputControl.new(execution_id=request.execution_id, actor='round4-test',
                                         input=[{'type': 'text', 'text': 'synthetic input'}],
                                         expected_turn_id='synthetic-turn')
    scheduler = isolated_rpc.scheduler
    submitted = scheduler.submit_input_control(control)
    assert submitted['status'] == 'pending'
    receipt = scheduler.complete_input_control(control.control_id, execution_id=request.execution_id,
                                                **owner, delivered=first)
    assert receipt['status'] == ('delivered' if first else 'failed')
    assert receipt['error'] == ('' if first else 'delivery_failed')
    assert scheduler.complete_input_control(control.control_id, execution_id=request.execution_id,
                                             **owner, delivered=second, error='late-error') == receipt
    with pytest.raises(PermissionError, match='ownership changed'):
        scheduler.complete_input_control(control.control_id, execution_id=request.execution_id,
                                         worker_id=owner['worker_id'], claim_token='wrong', delivered=True)
    assert scheduler.input_control(control.control_id) == receipt


def test_round4_input_rejects_stale_turn_and_conflicting_retry_without_extra_rows(isolated_rpc):
    """追加输入在 turn 已变或复用 control_id 换内容时被拒且不写记录，相同重试返回原结果"""
    request, owner = active_request(isolated_rpc)
    scheduler = isolated_rpc.scheduler
    arguments = {'execution_id': request.execution_id, 'actor': 'round4-test',
                 'input': [{'type': 'text', 'text': 'first'}], 'control_id': 'control-' + 'a' * 24}
    with pytest.raises(ValueError, match='turn changed'):
        scheduler.submit_input_control(ExecutionInputControl.new(**arguments, expected_turn_id='stale'))
    assert scheduler.input_control(arguments['control_id']) is None
    original = scheduler.submit_input_control(ExecutionInputControl.new(**arguments, expected_turn_id='synthetic-turn'))
    assert scheduler.submit_input_control(ExecutionInputControl.new(**arguments)) == original
    arguments['input'] = [{'type': 'text', 'text': 'different'}]
    with pytest.raises(ValueError, match='reused'):
        scheduler.submit_input_control(ExecutionInputControl.new(**arguments))
    assert scheduler.input_control(arguments['control_id']) == original


def test_round4_terminal_pruning_cascades_controls_but_retains_agent_data(isolated_rpc):
    """清理终态执行时连同其输入控制一起删除，保留 Provider 会话绑定"""
    request, owner = active_request(isolated_rpc)
    scheduler = isolated_rpc.scheduler
    control = ExecutionInputControl.new(execution_id=request.execution_id, actor=request.actor,
                                         input=[{'type': 'text', 'text': 'pending'}])
    scheduler.submit_input_control(control)
    bind(isolated_rpc.manager, 'retention-binding', 'synthetic-native-retained')
    scheduler.finish(ExecutionResult(execution_id=request.execution_id, status='completed',
                                     output={'message': 'synthetic terminal'}), **owner)
    assert scheduler.input_control(control.control_id)['status'] == 'failed'
    scheduler.acknowledge(request.execution_id, actor=request.actor)
    with scheduler.changes.connect() as connection:
        connection.execute('UPDATE executions SET updated_at=%s WHERE execution_id=%s',
                           ('2000-01-01T00:00:00.000Z', request.execution_id))
        connection.commit()
    assert scheduler.prune_terminal(older_than_epoch=978307200) == 1
    with pytest.raises(KeyError):
        scheduler.snapshot(request.execution_id)
    assert scheduler.input_control(control.control_id) is None
    assert request.execution_id not in scheduler.changes._transient_event_items
    assert isolated_rpc.manager.provider_binding('retention-binding')['backend_session_id'] == 'synthetic-native-retained'


def test_round4_rpc_errors_and_synthetic_actor_filter_do_not_mutate_execution(isolated_rpc):
    """未知 RPC 方法、调用者不符或执行不存在的请求报错且不改动执行"""
    request, owner = active_request(isolated_rpc)
    service = isolated_rpc.service
    original = isolated_rpc.scheduler.snapshot(request.execution_id)
    with pytest.raises(ValueError):
        service.client.request('unknown.round4.method', {})
    for execution_id, actor in [(request.execution_id, 'different-synthetic-actor'), ('missing-execution', request.actor)]:
        with pytest.raises(KeyError):
            service.client.request('execution.status', {'execution_id': execution_id, 'actor': actor})
    assert isolated_rpc.scheduler.snapshot(request.execution_id) == original


def test_round4_retiring_synthetic_worker_keeps_claim_and_cannot_claim_more(isolated_rpc):
    """draining 或 offline 的 Worker 保留已认领的执行，不能再认领排队执行"""
    request, owner = active_request(isolated_rpc)
    scheduler = isolated_rpc.scheduler
    queued = ExecutionRequest.new(actor='round4-test', prompt='never dispatched',
                                  service_type='agent_session', provider='codex', operation='turn',
                                  session=SessionOptions(backend_route_id='round4-fixture'))
    scheduler.submit(queued)
    for state in ['draining', 'offline']:
        scheduler.set_worker_state(owner['worker_id'], state)
        assert scheduler.claim(worker_id=owner['worker_id'], capabilities=['agent_session:codex'])['claimed'] is False
        assert scheduler.snapshot(request.execution_id)['scheduler']['owner_worker_id'] == owner['worker_id']
        assert scheduler.snapshot(queued.execution_id)['status'] == 'queued'


def test_round4_missing_notifications_do_not_hide_committed_status_from_two_readers(isolated_rpc):
    """缺少完成通知时两个服务端读取执行状态都得到已提交的完成状态"""
    request, owner = active_request(isolated_rpc)
    second = isolated_rpc.start('second-status.sock')
    params = {'execution_id': request.execution_id, 'actor': request.actor}
    assert second.client.request('execution.status', params)['status'] == 'running'
    isolated_rpc.scheduler.finish(ExecutionResult(execution_id=request.execution_id, status='completed',
                                                 output={'message': 'committed without notification'}), **owner)
    first_snapshot = isolated_rpc.service.client.request('execution.status', params)
    assert first_snapshot['status'] == 'completed'
    assert second.client.request('execution.status', params) == first_snapshot


def test_round4_concurrent_event_sequences_remain_unique_after_recursive_redaction(isolated_rpc, monkeypatch):
    """并发上报的执行事件序号唯一递增，嵌套字段中的凭据被脱敏"""
    request, owner = active_request(isolated_rpc)
    monkeypatch.setattr(service_module, 'execution_activity_path', lambda execution_id: isolated_rpc.tmp_path / 'activity.jsonl')
    monkeypatch.setattr(service_module, 'append_execution_activity', Mock())

    def send(index):
        return isolated_rpc.service.client.request('scheduler.event', {
            'execution_id': request.execution_id, **owner, 'event_type': 'provider_event',
            'details': {'index': index, 'nested': [{'Authorization': 'Bearer round4-fake-secret',
                                                   'message': 'password=round4-fake-secret'}]},
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(send, range(4))) == [{'ok': True}] * 4
    events = [event for event in isolated_rpc.scheduler.changes._transient_event_items[request.execution_id]
              if event['type'] == 'provider_event']
    sequences = [event['sequence'] for event in events]
    assert len(events) == 4 and len(set(sequences)) == 4 and sequences == sorted(sequences)
    assert 'round4-fake-secret' not in json.dumps(events)
    assert '[REDACTED]' in json.dumps(events)


@pytest.mark.parametrize('field', ['provider_session_ref', 'provider', 'backend_session_id', 'backend_route_id'])
@pytest.mark.parametrize('change', ['missing', 'wrong'])
def test_resolve_checks_every_required_expected_identity_field(isolated_rpc, field, change):
    """按预期读取绑定时任一身份字段缺失或不符都报告绑定已变"""
    manager = isolated_rpc.manager
    original = bind(manager, 'current', 'native-current')
    expected = dict(original)
    if change == 'missing':
        expected.pop(field)
    else:
        expected[field] = 'wrong-value'
    with pytest.raises(RuntimeError, match='binding changed'):
        manager.provider_binding('current', expected_binding=expected)
    assert manager.provider_binding('current', expected_binding=original) == original


def test_resolve_empty_route_and_missing_binding(isolated_rpc):
    """空路由的对应关系可按预期读取，缺失地址返回空

    路由为空的对应关系按完整预期读取成功，空预期被拒绝；不存在的地址按预期读取时返回空对象。
    """
    manager = isolated_rpc.manager
    original = bind(manager, 'current', 'native-current', route='')
    assert manager.provider_binding('current', expected_binding=original) == original
    with pytest.raises(RuntimeError):
        manager.provider_binding('current', expected_binding={})
    assert manager.provider_binding('missing', expected_binding=original) == {}


def test_bind_preserves_complete_context_fields(isolated_rpc, monkeypatch):
    """绑定会话时完整保存 context 指纹、注入时间与次数，工具名去重排序"""
    ensure = Mock(wraps=isolated_rpc.store.ensure_provider_session)
    monkeypatch.setattr(isolated_rpc.store, 'ensure_provider_session', ensure)
    result = isolated_rpc.manager.bind_session(
        'current', provider='codex', backend_session_id='native-current', backend_route_id='route-a',
          context_fingerprint='context-sentinel',
        context_injected_at='2026-09-12T00:00:00Z', context_injection_count=4,
        harness_tool_names=['write', 'read', 'read', ''],
    )
    assert result['context_fingerprint'] == 'context-sentinel'
    assert result['context_injected_at'] == '2026-09-12T00:00:00Z'
    assert result['context_injection_count'] == 4
    assert list(result['harness_tool_names']) == ['read', 'write']
    assert isolated_rpc.manager.provider_binding('current') == result


def test_restore_arriving_during_successor_replace_is_fenced(isolated_rpc, monkeypatch):
    """恢复会话期间绑定被后继替换时恢复失败，绑定保留后继会话"""
    manager = isolated_rpc.manager
    original = bind(manager, 'current', 'native-original')
    bind(manager, 'prepared', 'native-middle')
    receipt = manager.replace_session('current', 'prepared', expected_binding=original)
    successor = bind(manager, 'successor', 'native-final')
    entered = threading.Event()
    proceed = threading.Event()
    restore = isolated_rpc.store.restore_provider_binding

    def delayed_restore(*args, **kwargs):
        entered.set()
        assert proceed.wait(2)
        return restore(*args, **kwargs)

    monkeypatch.setattr(isolated_rpc.store, 'restore_provider_binding', delayed_restore)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(manager.restore_session, 'current', receipt)
        try:
            assert entered.wait(2)
            manager.replace_session('current', 'successor', expected_binding=receipt['current_binding'])
        finally:
            proceed.set()
        with pytest.raises(RuntimeError, match='changed after replacement'):
            future.result(2)
    assert manager.provider_binding('current')['provider_session_ref'] == successor['provider_session_ref']


def test_release_wrong_reference_and_post_delete_replay_are_bounded(isolated_rpc):
    """释放会话时引用不符则拒绝，释放后用旧引用重放不报错也不新增记录"""
    manager = isolated_rpc.manager
    original = bind(manager, 'current', 'native-original')
    with pytest.raises(RuntimeError, match='changed before release'):
        manager.release_session('current', expected_provider_session_ref='wrong-reference')
    assert manager.provider_binding('current') == original
    manager.release_session('current', expected_provider_session_ref=original['provider_session_ref'])
    manager.release_session('current', expected_provider_session_ref='stale-reference')
    assert manager.provider_binding('current') == {}
    with isolated_rpc.store.database.read() as connection:
        assert connection.execute('SELECT count(*) FROM provider_sessions').fetchone()[0] == 1
