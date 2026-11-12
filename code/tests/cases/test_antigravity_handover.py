"""Non-reattachable ACP turns stay with their owner during planned rollouts.

:module: execution.service
:design: decision_075f66430606
:importance: P2
"""
from types import SimpleNamespace
import threading

import pytest

from management_portal.agent_prompt_context import portal_system_instructions
from management_portal.execution.contracts import ExecutionRequest, ExecutionResult, SessionOptions
from management_portal.execution.runtime import _ActiveExecution, _ExecutionAttempt
from management_portal.execution.scheduler import Scheduler


@pytest.fixture(autouse=True)
def acp_account(monkeypatch):
    from management_portal.execution.account_adapters import AntigravityAccounts
    from management_portal.execution.accounts import ExecutionAccount

    monkeypatch.setattr(AntigravityAccounts, "accounts", lambda self: (
        ExecutionAccount("antigravity_one", "antigravity", "one", "antigravity:one"),
    ))


def runtime_checkpoint(provider="antigravity", recovery_state=None):
    request = SimpleNamespace(provider=provider, operation="turn", execution_id="execution-test")
    attempt = SimpleNamespace(request=request, context_attached_now=False, recovery={},
                              session=SimpleNamespace(context_fingerprint=""))
    active = _ActiveExecution(service=None, provider=provider, runtime_options={},
                              backend_session_id="session-1", turn_id="execution-test",
                              provider_recovery_state=recovery_state or {})
    return _ExecutionAttempt._checkpoint_payload(attempt, active)


def running_execution(provider="antigravity", *, completed=False, checkpoint_provider=True):
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    scheduler.register_worker(worker_id="old", build_id="old", capabilities=[f"agent_session:{provider}"], pid=1)
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="isolated handover fixture", service_type="agent_session",
        provider=provider, operation="turn", system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(mode="stateless", backend_route_id="one"),
    )
    scheduler.submit(request)
    claim = scheduler.claim(worker_id="old", capabilities=[f"agent_session:{provider}"])
    assert claim["execution"]["execution_id"] == request.execution_id
    token = claim["claim_token"]
    scheduler.start(request.execution_id, worker_id="old", claim_token=token)
    checkpoint = {"resumable": True, "operation": "turn", "turn_id": request.execution_id,
                  "backend_session_id": "session-1", "provider_completed": completed}
    if checkpoint_provider:
        checkpoint["provider"] = provider
    scheduler.checkpoint(request.execution_id, worker_id="old", claim_token=token, checkpoint=checkpoint)
    scheduler.set_worker_state("old", "draining")
    return scheduler, request, token


def test_unfinished_acp_ids_do_not_assert_recovery():
    assert runtime_checkpoint()["resumable"] is False


@pytest.mark.parametrize("checkpoint_provider", [True, False])
def test_old_acp_resumable_flag_cannot_force_handover(checkpoint_provider):
    scheduler, request, token = running_execution(checkpoint_provider=checkpoint_provider)
    assert scheduler.prepare_handover(request.execution_id, worker_id="old", claim_token=token)["ready"] is False
    handover = scheduler.handover_worker("old")
    assert handover["blocked_execution_ids"] == [request.execution_id]
    assert handover["requeued_execution_ids"] == []
    with pytest.raises(RuntimeError, match="no resumable checkpoint"):
        scheduler.requeue_claim(request.execution_id, worker_id="old", claim_token=token, reason="handover")
    # The old owner can still commit the real result; no new prompt/claim is needed.
    scheduler.finish(ExecutionResult(execution_id=request.execution_id, status="completed", output={"message": "done"}),
                     worker_id="old", claim_token=token)
    assert scheduler.snapshot(request.execution_id)["status"] == "completed"


@pytest.mark.parametrize("provider,completed", [("codex", False), ("antigravity", True)])
def test_supported_turn_or_durable_result_can_handover(provider, completed):
    scheduler, request, token = running_execution(provider, completed=completed)
    assert scheduler.prepare_handover(request.execution_id, worker_id="old", claim_token=token)["ready"] is True
    handover = scheduler.handover_worker("old")
    assert handover["requeued_execution_ids"] == [request.execution_id]
    assert handover["blocked_execution_ids"] == []


def test_completed_acp_provider_receipt_remains_recoverable():
    state = {"phase": "completed", "turn_id": "execution-test", "session_id": "session-1",
             "output": {"message": "done"}}
    assert runtime_checkpoint(recovery_state=state)["resumable"] is True
    assert runtime_checkpoint("codex")["resumable"] is True


def test_legacy_unknown_outcome_has_an_accurate_summary():
    from management_portal.society_service.resident_record_domain import runtime_error_payload, runtime_failure_summary
    from management_portal.execution.provider_failures import ProviderOutcomeUnknown

    raw = "Antigravity input delivery is uncertain after disconnection; refusing to replay it"
    assert runtime_error_payload(raw, code="network")["code"] == ProviderOutcomeUnknown.error_code
    assert runtime_failure_summary(raw, {"code": "network", "message": raw}) == "模型执行结果不确定，未自动重试"
    assert runtime_error_payload("connection reset")["code"] == "network"


def test_expired_legacy_acp_checkpoint_only_schedules_cleanup():
    from tests.cases.database_test_support import connect
    from management_common.json_columns import json_value

    scheduler, request, token = running_execution(checkpoint_provider=False)
    with connect(scheduler.database) as connection:
        connection.execute("UPDATE executions SET lease_expires_at = TIMESTAMPTZ '1970-01-01 00:00:01+00' WHERE execution_id = %s",
                           (request.execution_id,))
    scheduler.maintain()
    snapshot = scheduler.snapshot(request.execution_id)
    assert snapshot["status"] == "queued"
    with connect(scheduler.database) as connection:
        checkpoint = json_value(connection.execute("SELECT checkpoint_json FROM executions WHERE execution_id = %s", (request.execution_id,)).fetchone()[0])
    assert checkpoint["abandoned"]["status"] == "failed"
    assert checkpoint["resumable"] is False


@pytest.mark.parametrize("cancel", [False, True])
def test_draining_acp_worker_finishes_original_call(tmp_path, cancel):
    from management_portal.execution.backend_contracts import BackendResult
    from management_portal.execution.scheduler import SchedulerClient
    from management_portal.execution.worker import ExecutionWorker
    from tests.cases.execution_layer_test_support import _stack, _close

    scheduler, server, server_thread, client, service = _stack(tmp_path)
    started, draining, prepared, release = (threading.Event() for _ in range(4))
    outcome = {}

    class Runtime:
        def execute(self, request, context):
            context.checkpoint(runtime_checkpoint())
            started.set()
            assert release.wait(5)
            if context.is_cancelled():
                raise InterruptedError("Antigravity turn cancelled")
            assert not context.is_handover()
            return BackendResult(output={"message": "original result"}, backend_session_id="session-1")

        def interrupt(self, execution_id):
            release.set()

    worker_client = SchedulerClient(worker_id="old", build_id="old", capabilities=["agent_session:antigravity"], service_client=client)
    original_prepare = worker_client.prepare_handover

    def prepare(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        prepared.set()
        return result

    worker_client.prepare_handover = prepare
    worker = ExecutionWorker(build_id="old", worker_id="old", scheduler=worker_client, runtime=Runtime(), shutdown_requested=draining.is_set)
    runner = None
    try:
        worker.register()
        request = ExecutionRequest.new(actor="sample_admin", prompt="isolated worker test", service_type="agent_session",
                                       provider="antigravity", operation="turn", session=SessionOptions(mode="stateless", backend_route_id="one"))
        scheduler.submit(request)
        claim = worker.claim(wait_seconds=0)
        runner = threading.Thread(target=lambda: outcome.update(worker.execute_claim(claim, lease_seconds=30)))
        runner.start()
        assert started.wait(3)
        draining.set()
        scheduler.set_worker_state("old", "draining")
        assert prepared.wait(3)
        assert scheduler.snapshot(request.execution_id)["scheduler"]["owner_worker_id"] == "old"
        if cancel:
            scheduler.cancel(request.execution_id, actor="sample_admin")
        else:
            release.set()
        runner.join(5)
        assert not runner.is_alive()
        snapshot = scheduler.snapshot(request.execution_id)
        assert snapshot["status"] == ("cancelled" if cancel else "completed")
        assert snapshot["scheduler"]["attempts"] == 1
        assert not any(event["type"] == "requeued" for event in snapshot["events"])
    finally:
        release.set()
        if runner:
            runner.join(5)
        _close(server, server_thread)
