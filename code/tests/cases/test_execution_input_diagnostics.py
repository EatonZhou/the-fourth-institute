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


@pytest.fixture
def diagnostic_server(monkeypatch):
    from management_portal.society_service import resident_input_diagnostics
    authorize = Mock(return_value={"available": True, "resident_id": "resident_target",
        "message_id": "input", "execution_input_id": "input", "execution_id": "execution-target"})
    monkeypatch.setattr(resident_input_diagnostics, "resolve_input_execution", authorize)
    server = SimpleNamespace(
        agent_data=SimpleNamespace(execution=Mock(return_value={
            "agent_id": "resident_target", "actor_ref": "original-actor",
            "status": "completed", "inputs": [{"message_id": "input", "role": "primary"}],
            "created_at": "2026-11-01T01:00:00Z", "started_at": "2026-11-01T01:01:00Z",
            "finished_at": "2026-11-01T01:02:00Z",
            "provider_turn_id": "private-turn", "usage": {"private": "secret"},
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
    server.agent_data.execution.assert_not_called()
    server.scheduler.input_diagnostics.assert_not_called()


def test_denied_requester_is_checked_before_any_execution_read(diagnostic_server):
    """当前请求者被拒绝时不读取运行事实且不借用原提交者。"""
    server, authorize = diagnostic_server
    authorize.side_effect = PermissionError("input denied")
    with pytest.raises(PermissionError):
        execution_input_diagnostics(server, params())
    authorize.assert_called_once_with(**params())
    server.agent_data.execution.assert_not_called()
    server.scheduler.input_diagnostics.assert_not_called()


@pytest.mark.parametrize("record", [None, {"agent_id": "other", "inputs": [{"message_id": "input"}]},
    {"agent_id": "resident_target", "inputs": [{"message_id": "another-input"}]}])
def test_execution_substitution_is_hidden(diagnostic_server, record):
    """更换执行、Resident或未接纳的输入不能获得生命周期切片。"""
    server, _ = diagnostic_server
    server.agent_data.execution.return_value = record
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
    server.agent_data.execution.assert_not_called()


def test_unassociated_input_returns_unavailable_without_reading_scheduler(diagnostic_server):
    """尚无执行关联时明确返回不可用而不查询其他执行。"""
    server, authorize = diagnostic_server
    authorize.return_value = {"available": False, "reason": "execution_not_associated"}
    assert execution_input_diagnostics(server, params()) == {
        "schema": "tfi.execution-input-diagnostics.v1", **authorize.return_value,
    }
    server.agent_data.execution.assert_not_called()
    server.scheduler.input_diagnostics.assert_not_called()


def test_grouped_input_uses_authorized_holder_and_admitted_primary(diagnostic_server):
    """Family转交或分组后按授权的持有者和实际接纳输入二次匹配。"""
    server, authorize = diagnostic_server
    authorize.return_value.update(message_id="group-member", execution_input_id="input")
    result = execution_input_diagnostics(server, params(resident_id="facade", message_id="facade-message"))
    assert result['resident_id'] == 'resident_target' and result['message_id'] == 'group-member'
    server.agent_data.execution.assert_called_once_with('execution-target')
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
