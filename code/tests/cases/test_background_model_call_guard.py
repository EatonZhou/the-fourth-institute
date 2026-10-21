from __future__ import annotations

import sys
from pathlib import Path


AGENTS_ROOT = Path("/share/Agents")
if str(AGENTS_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTS_ROOT))

from _shared import portal_execution_client as client
from management_portal.execution import ExecutionClient, ExecutionRequest


class FakeExecutionService:
    def __init__(self) -> None:
        self.request = None
        self.waited = None

    def submit(self, request):
        self.request = request
        return {"execution_id": request.execution_id}

    def wait(self, execution_id, **kwargs):
        self.waited = (execution_id, kwargs)
        return {
            "status": "completed",
            "result": {"output": {"message": "ok"}},
        }


def test_execution_client_owns_submit_and_wait_protocol() -> None:
    """ExecutionClient 统一提交执行并等待结果"""
    service = FakeExecutionService()
    request = ExecutionRequest.new(
        actor="resident_0123456789ab",
        agent_id="resident_0123456789ab",
        prompt="hello",
        service_type="chat_completion",
        provider="deepseek_primary",
        operation="generate",
    )

    snapshot = ExecutionClient(service).execute(request, timeout_seconds=30)

    assert snapshot["status"] == "completed"
    assert service.request is request
    assert service.waited == (
        request.execution_id,
        {
            "actor": request.actor,
            "timeout_seconds": 30,
            "cancellation_requested": None,
            "on_events": None,
            "on_snapshot": None,
        },
    )


def test_callers_cannot_choose_a_model_route_for_a_resident() -> None:
    """调用者不能为 Resident 另选模型路由"""
    assert not hasattr(client, "run_model_agent")
    assert not hasattr(client, "run_script_agent")
    # A Resident's AI work is its own conversation turn, never a side request.
    assert not hasattr(client, "run_as_resident")


def test_background_agent_session_does_not_branch_on_provider(monkeypatch) -> None:
    """后台 agent 会话按账号配置接入未知 Provider"""
    observed: dict = {}

    def ensure_invocation_resident(**kwargs):
        observed.update(kwargs)
        return {"resident_id": "resident_0123456789ab", "lineage_id": "lineage_test"}

    def resolve_agent_account(account_id):
        observed["account_id"] = account_id
        return {"provider": "future_backend", "backend_route_id": "future-route"}

    monkeypatch.setattr(
        client,
        "_load_protocol_functions",
        lambda: (ensure_invocation_resident, None, None),
    )
    monkeypatch.setattr(client, "_execution_actor", lambda actor: actor)
    monkeypatch.setattr(
        client,
        "_resolve_agent_account",
        resolve_agent_account,
    )

    client.ensure_agent_session(
        actor="resident_0123456789ab",
        account_id="future_account",
        cwd="/tmp",
        resident_scope="background-agent:test",
    )

    assert observed["account_id"] == "future_account"
    assert observed["backend"] == "future_backend"
    assert observed["backend_route_id"] == "future-route"
