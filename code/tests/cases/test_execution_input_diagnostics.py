"""输入关联的执行诊断只读白名单。

:module: execution.service
:importance: P1
"""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.execution.service import ExecutionService
from management_portal.execution.service_rpc import execution_input_diagnostics
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.contracts import ExecutionRequest
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.execution import execution_inputs


@pytest.fixture
def admitted_execution():
    store = get_agent_data_store()
    request = ExecutionRequest.new(actor='original', agent_id='resident_target', prompt='private prompt',
                                   service_type='agent_session', provider='codex', operation='turn')
    store.reserve_execution(execution_id=request.execution_id, agent_id=request.agent_id,
                            actor_ref=request.actor, provider=request.provider)
    execution_inputs.attach(store, request.execution_id, [{'message_id': 'input', 'role': 'primary'}])
    return store, request


@pytest.fixture
def diagnostic_server(monkeypatch):
    from management_common import society_rpc_client
    authorize = Mock(return_value={"available": True, "resident_id": "resident_target",
        "message_id": "input", "execution_input_id": "input", "execution_id": "execution-target"})
    monkeypatch.setattr(society_rpc_client, "resolve_input_execution", authorize)
    server = SimpleNamespace(
        agent_data=SimpleNamespace(input_diagnostics=Mock(return_value={
            "agent_id": "resident_target", "actor_ref": "original-actor",
            "status": "completed", "inputs": [{"message_id": "input", "role": "primary"}],
            "created_at": "2026-11-01T01:00:00Z", "started_at": "2026-11-01T01:01:00Z",
            "finished_at": "2026-11-01T01:02:00Z",
            "provider_turn_id": "private-turn", "usage": {"private": "secret"},
            "execution_submission": {"recorded": False, "submitted_at": "unknown",
                                     "service_type": "unknown", "operation": "unknown"},
            "provider_link": {"session_ref_recorded": False, "turn_id_recorded": False},
        })),
        scheduler=SimpleNamespace(input_diagnostics=Mock(return_value=None)),
    )
    return server, authorize


def params(**changes):
    return {"actor": "current-reader", "resident_id": "resident_target",
            "message_id": "input", **changes}


@pytest.mark.parametrize("missing", ["actor", "resident_id", "message_id"])
def test_missing_identity_never_reads_execution(diagnostic_server, missing):
    """缺少当前身份或任一关联键时拒绝读取。"""
    server, authorize = diagnostic_server
    with pytest.raises(ValueError):
        execution_input_diagnostics(server, params(**{missing: ""}))
    authorize.assert_not_called()
    server.agent_data.input_diagnostics.assert_not_called()
    server.scheduler.input_diagnostics.assert_not_called()


def test_denied_requester_is_checked_before_any_execution_read(diagnostic_server):
    """当前请求者被拒绝时不读取运行事实且不借用原提交者。"""
    server, authorize = diagnostic_server
    authorize.side_effect = PermissionError("input denied")
    with pytest.raises(PermissionError):
        execution_input_diagnostics(server, params())
    authorize.assert_called_once_with(**params())
    server.agent_data.input_diagnostics.assert_not_called()
    server.scheduler.input_diagnostics.assert_not_called()


@pytest.mark.parametrize("substitution", ['execution_id', 'resident_id', 'execution_input_id'])
def test_execution_substitution_is_hidden(diagnostic_server, admitted_execution, substitution):
    """更换执行、Resident或未接纳的输入不能获得生命周期切片。"""
    server, authorize = diagnostic_server
    server.agent_data, request = admitted_execution
    authorize.return_value.update(execution_id=request.execution_id)
    authorize.return_value[substitution] = 'unrelated'
    with pytest.raises(KeyError):
        execution_input_diagnostics(server, params())
    server.scheduler.input_diagnostics.assert_not_called()


def test_retained_execution_has_unknown_scheduler_and_no_private_fields(diagnostic_server):
    """调度工作回收后保留长期状态且恢复点和原生轮次明确未知。"""
    server, _ = diagnostic_server
    result = execution_input_diagnostics(server, params())
    assert result == {
        "schema": "tfi.execution-input-diagnostics.v1", "available": True, "execution_id": "execution-target",
        "resident_id": "resident_target", "message_id": "input", "source": "agent_data",
        "status": "completed", "owner_worker_id": "unknown", "lease_expires_at": "unknown",
        "updated_at": "unknown", "cancel_requested": "unknown", "attempts": "unknown",
        "created_at": "2026-11-01T01:00:00Z", "started_at": "2026-11-01T01:01:00Z",
        "finished_at": "2026-11-01T01:02:00Z",
        "owner_worker": {"state": "unknown", "heartbeat_at": "unknown"},
        "recovery": {**dict.fromkeys(("provider_started", "provider_completed", "resumable", "turn_id_recorded",
                                     "abandoned", "abandoned_status"), "unknown"),
                     "provider_recovery_state": {"phase": "unknown"}},
        "native_turn_state": "unknown",
        "execution_submission": {"recorded": False, "submitted_at": "unknown",
                                 "service_type": "unknown", "operation": "unknown"},
        "provider_link": {"session_ref_recorded": False, "turn_id_recorded": False},
    }
    server.scheduler.input_diagnostics.assert_called_once_with("execution-target", resident_id="resident_target")


def test_facade_preserves_current_requester_and_all_association_keys():
    """客户端按原样传递当前请求者和全部关联键。"""
    client = Mock()
    service = ExecutionService(client=client)
    assert service.input_diagnostics(**params()) is client.request.return_value
    client.request.assert_called_once_with("execution.input_diagnostics", params())


def test_external_execution_selector_cannot_replace_the_resolved_association(diagnostic_server):
    """拒绝调用方塞入的执行选择器和自报授权标志。"""
    server, authorize = diagnostic_server
    for extras in ({"execution_id": "another-execution"}, {"authorized": True}):
        with pytest.raises(ValueError):
            execution_input_diagnostics(server, params(**extras))
    authorize.assert_not_called()
    server.agent_data.input_diagnostics.assert_not_called()


def test_unassociated_input_returns_unavailable_without_reading_scheduler(diagnostic_server):
    """尚无执行关联时明确返回不可用而不查询其他执行。"""
    server, authorize = diagnostic_server
    authorize.return_value = {"available": False, "reason": "execution_not_associated"}
    assert execution_input_diagnostics(server, params()) == {
        "schema": "tfi.execution-input-diagnostics.v1", **authorize.return_value,
    }
    server.agent_data.input_diagnostics.assert_not_called()
    server.scheduler.input_diagnostics.assert_not_called()


def test_grouped_input_uses_authorized_holder_and_admitted_primary(diagnostic_server):
    """Family转交或分组后按授权的持有者和实际接纳输入二次匹配。"""
    server, authorize = diagnostic_server
    authorize.return_value.update(message_id="group-member", execution_input_id="input")
    result = execution_input_diagnostics(server, params(resident_id="facade", message_id="facade-message"))
    assert result['resident_id'] == 'resident_target' and result['message_id'] == 'group-member'
    server.agent_data.input_diagnostics.assert_called_once_with('execution-target', agent_id='resident_target', message_id='input')
    server.scheduler.input_diagnostics.assert_called_once_with('execution-target', resident_id='resident_target')


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("phase", ["prompt_sent", "completed", {"secret": "private"}])
def test_scheduler_whitelist_does_not_expose_checkpoint_body_or_mutate_work(completed, phase):
    """隔离调度恢复点只暴露已记录布尔事实且读取不改变运行。"""
    scheduler = Scheduler()
    request = ExecutionRequest.new(actor="original", agent_id="resident_target", prompt="private prompt",
                                   service_type="agent_session", provider="codex", operation="turn")
    scheduler.submit(request)
    checkpoint = {"provider_completed": completed, "resumable": True, "turn_id": "private-turn",
                  "output": {"text": "private output"}, "credentials": "private credentials",
                  "provider_recovery_state": {"phase": phase, "output": "private"},
                  "abandoned": {"status": "cancelled" if completed else "private", "error": "private error"},
                  "provider_started": "invalid-private-value"}
    with scheduler.changes.connect() as connection:
        connection.execute("UPDATE executions SET checkpoint_json=%s WHERE execution_id=%s",
                           (json.dumps(checkpoint), request.execution_id))
    before = scheduler.snapshot(request.execution_id)
    result = scheduler.input_diagnostics(request.execution_id, resident_id="resident_target")
    assert result['status'] == 'queued' and result['attempts'] == 0
    assert result['owner_worker_id'] == '' and result['lease_expires_at'] is None
    assert result['cancel_requested'] is False and result['updated_at']
    assert result['owner_worker'] == {'state': 'unknown', 'heartbeat_at': 'unknown'}
    assert result['recovery'] == {"provider_started": "unknown", "provider_completed": completed,
                                  "resumable": True, "turn_id_recorded": True, "abandoned": True,
                                  "abandoned_status": "cancelled" if completed else "unknown",
                                  "provider_recovery_state": {"phase": phase if isinstance(phase, str) else "unknown"}}
    assert result['native_turn_state'] == 'unknown'
    assert not any(value in json.dumps(result) for value in ('private', 'original', 'claim_token'))
    assert scheduler.snapshot(request.execution_id) == before
    with pytest.raises(KeyError):
        scheduler.input_diagnostics(request.execution_id, resident_id="another-resident")
    assert scheduler.input_diagnostics('absent', resident_id="resident_target") is None


def test_diagnostics_includes_only_registered_owner_worker_observation():
    """只读关联的Worker状态和心跳，不泄露进程、构建或领取令牌。"""
    scheduler = Scheduler()
    request = ExecutionRequest.new(actor="original", agent_id="resident_target", prompt="private",
                                   service_type="agent_session", provider="codex", operation="turn")
    scheduler.submit(request)
    scheduler.register_worker(worker_id='diagnostic-worker', build_id='private-build',
                              capabilities=['agent_session:codex'], state='active')
    with scheduler.changes.connect() as connection:
        connection.execute("UPDATE executions SET owner_worker_id=%s WHERE execution_id=%s",
                           ('diagnostic-worker', request.execution_id))
    result = scheduler.input_diagnostics(request.execution_id, resident_id='resident_target')
    assert result['owner_worker_id'] == 'diagnostic-worker'
    assert result['owner_worker']['state'] == 'active'
    assert result['owner_worker']['heartbeat_at'].endswith('Z')
    assert 'private' not in json.dumps(result)


def test_durable_submission_is_independent_of_preparing_and_queue_retention(admitted_execution, diagnostic_server):
    """隔离数据库区分预留和持久提交，调度回收后仍保留提交事实。"""
    import time
    from management_portal.execution.contracts import ExecutionResult
    from management_portal.execution.agent_data_store import AgentDataStore

    store, request = admitted_execution
    server, authorize = diagnostic_server
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    server.agent_data = store
    server.scheduler = scheduler
    authorize.return_value.update(execution_id=request.execution_id)
    before = execution_input_diagnostics(server, params())
    assert before['status'] == 'preparing' and before['source'] == 'agent_data'
    assert before['execution_submission'] == {'recorded': False, 'submitted_at': 'unknown',
                                             'service_type': 'unknown', 'operation': 'unknown'}
    assert before['provider_link'] == {'session_ref_recorded': False, 'turn_id_recorded': False}
    scheduler.submit(request)
    submitted = execution_input_diagnostics(server, params())
    assert submitted['execution_submission']['recorded'] is True
    assert submitted['execution_submission']['service_type'] == 'agent_session'
    assert submitted['execution_submission']['operation'] == 'turn'
    assert submitted['execution_submission']['submitted_at'].endswith('Z')
    scheduler.register_worker(worker_id='submission-test', build_id='test-build', capabilities=[request.capability], state='standby')
    scheduler.activate_worker('submission-test')
    claimed = scheduler.claim(worker_id='submission-test', capabilities=[request.capability])
    assert claimed['claimed']
    scheduler.start(request.execution_id, worker_id='submission-test', claim_token=claimed['claim_token'])
    store.begin_execution(execution_id=request.execution_id, agent_id=request.agent_id, actor_ref=request.actor,
                          provider=request.provider, started_at='2026-11-15T01:00:00Z')
    scheduler.finish(ExecutionResult(execution_id=request.execution_id, status='completed', output=''),
                     worker_id='submission-test', claim_token=claimed['claim_token'])
    scheduler.acknowledge(request.execution_id, actor=request.actor)
    scheduler.prune_terminal(older_than_epoch=time.time() + 86401)
    server.agent_data = AgentDataStore()
    retained = execution_input_diagnostics(server, params())
    assert retained['source'] == 'agent_data' and retained['status'] == 'completed'
    assert retained['execution_submission'] == submitted['execution_submission']
    assert 'private' not in json.dumps(retained) and 'request_digest' not in json.dumps(retained)


@pytest.mark.parametrize('invalid_service', [False, True])
def test_durable_projection_redacts_invalid_operation_and_reads_only_exact_provider_links(admitted_execution, invalid_service):
    """白名单拒绝异常操作值且只观察本执行的Provider关联。"""
    store, request = admitted_execution
    Scheduler().submit(request)
    store.begin_execution(execution_id=request.execution_id, agent_id=request.agent_id,
                          actor_ref=request.actor, provider=request.provider, started_at='2026-11-15T01:00:00Z')
    session = store.ensure_provider_session(provider='codex', backend_route_id='zero', backend_session_id='private-session',
                                             agent_id=request.agent_id, actor_ref=request.actor)
    store.bind_execution_provider_session(request.execution_id, provider_session_ref=session['provider_session_ref'])
    store.record_input_acceptance(request.execution_id, agent_id=request.agent_id, provider_turn_id='private-turn')
    with store.database.read() as connection:
        original = connection.execute('SELECT data_json FROM agent_operations WHERE operation_id=%s',
                                      ('execution_submission:' + request.execution_id,)).fetchone()['data_json']
    from management_common.json_columns import json_value
    facts = json_value(original)
    facts.update(service_type='private-unknown' if invalid_service else 'agent_session', operation='private-unknown')
    store.database.write(lambda connection: connection.execute('UPDATE agent_operations SET data_json=%s WHERE operation_id=%s',
                         (json.dumps(facts), 'execution_submission:' + request.execution_id)))
    result = store.input_diagnostics(request.execution_id, agent_id=request.agent_id, message_id='input')
    assert result['execution_submission']['recorded'] is True
    assert result['execution_submission']['operation'] == 'unknown'
    assert result['execution_submission']['service_type'] == ('unknown' if invalid_service else 'agent_session')
    assert result['provider_link'] == {'session_ref_recorded': True, 'turn_id_recorded': True}
    assert 'private' not in json.dumps(result) and 'request_digest' not in json.dumps(result)


def test_durable_projection_reads_legacy_text_operation_storage(admitted_execution):
    store, request = admitted_execution
    Scheduler().submit(request)
    store.database.write(lambda connection: connection.execute(
        'ALTER TABLE agent_operations ALTER COLUMN data_json TYPE TEXT USING data_json::text'))
    result = store.input_diagnostics(request.execution_id, agent_id=request.agent_id, message_id='input')
    assert result['execution_submission']['recorded'] is True
    assert result['execution_submission']['service_type'] == 'agent_session'
    assert result['execution_submission']['operation'] == 'turn'
