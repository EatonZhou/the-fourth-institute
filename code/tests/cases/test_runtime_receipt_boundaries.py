"""Runtime control receipts and lost owner replies retain their own boundaries."""
from types import SimpleNamespace
from unittest.mock import MagicMock
import json

import pytest

from management_portal.execution import service_rpc
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.contracts import (
    ExecutionRequest, ExecutionTurnSettingsControl, SessionOptions,
)
from management_portal.execution.scheduler import Scheduler
from management_portal.hatg import routing, socket_rpc
from management_portal.hatg.contracts import InstructionContext
from tests.cases.test_hatg_isa_kernel import fake, kernel, scheduler_call  # noqa: F401
from management_portal.agent_prompt_context import portal_system_instructions


@pytest.mark.parametrize("delivered", [True, False])
@pytest.mark.usefixtures("registered_execution_defaults")
def test_settings_completion_keeps_receipt_without_materializing_message(delivered):
    """设置更新和重放返回同一回执；不会按追加消息 schema 解析或写入。"""
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    worker_id = "fixture:settings-receipt"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(worker_id=worker_id, build_id="fixture", capabilities=capabilities)
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="synthetic turn", service_type="agent_session",
        provider="codex", operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(mode="stateless", backend_route_id="zero"),
    )
    scheduler.submit(request)
    claim = scheduler.claim(worker_id=worker_id, capabilities=capabilities)
    owner = {"worker_id": worker_id, "claim_token": claim["claim_token"]}
    scheduler.start(request.execution_id, **owner)
    scheduler.checkpoint(request.execution_id, **owner, checkpoint={"turn_id": "fixture-turn"})
    control = ExecutionTurnSettingsControl.new(
        execution_id=request.execution_id, actor=request.actor,
        settings={"service_tier": "priority"}, expected_turn_id="fixture-turn",
    )
    scheduler.submit_turn_settings_control(control)
    server = SimpleNamespace(scheduler=scheduler, agent_data=AgentDataStore())
    params = {**owner, "execution_id": request.execution_id, "control_id": control.control_id,
              "delivered": delivered, "error": "" if delivered else "targetUnavailable"}
    first = service_rpc.complete_input_control(server, params)
    again = service_rpc.complete_input_control(server, params)
    assert first == again == scheduler.input_control(control.control_id)
    assert first["status"] == ("delivered" if delivered else "failed")
    assert first["settings"] == {"service_tier": "priority"}
    assert first["error"] == params["error"]


@pytest.mark.parametrize("raw", [b'{"result":"\xe4\xb8', b'\xff'])
def test_unreadable_owner_bytes_keep_original_receipt_queryable(kernel, fake, monkeypatch, raw):
    """已提交响应截断或含非法 UTF-8 时仍返回原执行 ID 和 unknown，不重放写入。"""
    connection = MagicMock()
    stream = connection.makefile.return_value
    replies, sent = [], []

    def send(encoded):
        payload = json.loads(encoded)
        sent.append(payload)
        socket_rpc.serve(payload["params"], payload["id"], replies.append,
                         owner="execution", verified_caller="alice")

    connection.sendall.side_effect = send
    stream.readline.return_value = raw
    monkeypatch.setattr(socket_rpc, "connect_service", lambda *a, **k: (connection, "fixture.sock"))
    monkeypatch.setattr(socket_rpc, "_signed", lambda *a: {})
    monkeypatch.setattr(routing, "kernel_for", lambda owner, required=False: kernel if required else None)
    result = routing.execute(scheduler_call(), InstructionContext("alice", "web"))
    assert result.status == "failed"
    assert result.error["code"] == "instruction.owner_unavailable"
    assert result.error["commit_state"] == "unknown" and result.error["retryable"] is True
    assert result.instruction_id == sent[0]["params"]["instruction_id"]
    assert result.instruction_id == replies[0]["result"]["instruction_id"]
    assert len(sent) == len(replies) == len(fake.calls) == 1
    connection.close.assert_called_once()
    stream.close.assert_called_once()
    monkeypatch.setattr(routing, "kernel_for", lambda *a, **k: kernel)
    from tests.cases.test_instruction_execution_lookup import lookup
    assert lookup({"instruction_id": result.instruction_id}).raise_for_error()["status"] == "committed"
    assert lookup({"instruction_id": result.instruction_id}, "outsider").error["code"] == "instruction.not_found"
    assert len(fake.calls) == 1
