from __future__ import annotations

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
