"""Cancellation must reach the worker even when no provider event is produced.

:importance: P2
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from management_portal.execution.contracts import (
    ExecutionInputControl,
    ExecutionRequest,
    ExecutionResult,
    SessionOptions,
)
from management_portal.execution.scheduler import Scheduler, SchedulerClient
from management_portal.execution.service import (
    ExecutionPreparation,
    ExecutionService,
    ExecutionServiceServer,
)
from management_portal.execution.transport import (
    ExecutionServiceClient,
    ExecutionServiceUnavailable,
)


@pytest.mark.parametrize("release", ["requeue", "handover", "expired"])
def test_cancelled_claim_release_always_commits_a_terminal_result(tmp_path: Path, release: str) -> None:
    """已取消执行的 claim 在重排队、交接或租约过期后都以 cancelled 终态结束，未送达输入记为失败

    Stop wins a recovery race, including ownerless rows written by old workers.

    :design: decision_6ad0384e020d
    :design-point:
    """
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    request = ExecutionRequest.new(actor="test-actor", prompt="cancel recovery",
                                   service_type="agent_session", provider="codex", operation="turn",
                                   session=SessionOptions(backend_route_id="zero"))
    scheduler.submit(request)
    worker = "test:cancel-recovery"
    scheduler.register_worker(worker_id=worker, build_id="test", capabilities=["agent_session:codex"])
    claim = scheduler.claim(worker_id=worker, capabilities=["agent_session:codex"])
    token = claim["claim_token"]
    scheduler.start(request.execution_id, worker_id=worker, claim_token=token)
    scheduler.checkpoint(request.execution_id, worker_id=worker, claim_token=token,
                         checkpoint={"resumable": True, "backend_session_id": "test-thread",
                                     "turn_id": "test-turn"})
    control = ExecutionInputControl.new(
        execution_id=request.execution_id, actor="test-actor",
        input=[{"type": "text", "text": "pending input"}], expected_turn_id="test-turn",
    )
    scheduler.submit_input_control(control)
    scheduler.cancel(request.execution_id, actor="test-actor", reason="user Stop")
    if release == "requeue":
        scheduler.requeue_claim(request.execution_id, worker_id=worker, claim_token=token,
                                reason="provider recovery deferred", retry_after_seconds=3600)
    elif release == "handover":
        scheduler.set_worker_state(worker, "draining")
        handover = scheduler.handover_worker(worker)
        # Its Provider turn may outlive the exited Worker: interrupted first.
        assert handover["requeued_execution_ids"] == [request.execution_id]
        assert handover["blocked_execution_ids"] == []
    else:
        with scheduler.changes.connect() as connection:
            connection.execute('UPDATE executions SET lease_expires_at=to_timestamp(1) WHERE execution_id=%s',
                               (request.execution_id,))
        scheduler.maintain()
    if release in {"handover", "expired"}:
        # Stop still wins: the turn never runs again.  The lost Worker's
        # Provider turn waits for the next Worker to interrupt it, shown as
        # stopping, and then ends cancelled.
        waiting = scheduler.snapshot(request.execution_id)
        assert waiting["status"] == "queued"
        assert waiting["scheduler"]["cancel_requested"] is True
        assert waiting["events"][-1]["details"]["abandoned"]["status"] == "cancelled"
        if release == "handover":
            worker = "test:cancel-recovery-replacement"
            scheduler.register_worker(worker_id=worker, build_id="test", capabilities=["agent_session:codex"])
        relay = scheduler.claim(worker_id=worker, capabilities=["agent_session:codex"])
        assert relay["execution"]["execution_id"] == request.execution_id
        token = relay["claim_token"]
        scheduler.start(request.execution_id, worker_id=worker, claim_token=token)
        scheduler.finish(ExecutionResult(execution_id=request.execution_id, status="cancelled",
                                         error="execution cancellation finalized after worker lease expired"),
                         worker_id=worker, claim_token=token)
    snapshot = scheduler.snapshot(request.execution_id)
    assert snapshot["status"] == "cancelled"
    assert snapshot["result"]["status"] == "cancelled"
    assert snapshot["result"]["execution_id"] == request.execution_id
    assert snapshot["result"]["backend_session_id"] == "test-thread"
    assert snapshot["scheduler"]["owner_worker_id"] == ""
    assert all(e["details"].get("abandoned") for e in snapshot["events"] if e["type"] == "requeued")
    receipt = scheduler.input_control(control.control_id)
    assert receipt["status"] == "failed"
    assert receipt["error"] == "provider_turn_finished_before_delivery"
    with pytest.raises(PermissionError):
        scheduler.finish(ExecutionResult(execution_id=request.execution_id, status="completed"),
                         worker_id=worker, claim_token=token)
    before = snapshot["result"]
    scheduler.maintain()
    assert scheduler.snapshot(request.execution_id)["result"] == before


@pytest.mark.parametrize("release", ["maintain", "nonresumable_handover"])
def test_cancelled_live_owner_is_not_finalized_by_maintenance_or_handover(
    tmp_path: Path, release: str,
) -> None:
    """仍有存活 worker 的已取消执行不被维护或交接提前终结，由 worker 结束为 cancelled

    :design: decision_6ad0384e020d
    :design-point:
    """
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    request = ExecutionRequest.new(actor="test-actor", prompt="live provider",
                                   service_type="chat_completion", provider="openai", operation="generate")
    worker = "test:live-owner"
    scheduler.submit(request)
    scheduler.register_worker(worker_id=worker, build_id="test", capabilities=["chat_completion"])
    claim = scheduler.claim(worker_id=worker, capabilities=["chat_completion"])
    token = claim["claim_token"]
    scheduler.start(request.execution_id, worker_id=worker, claim_token=token)
    scheduler.cancel(request.execution_id, actor="test-actor")
    if release == "nonresumable_handover":
        scheduler.set_worker_state(worker, "draining")
        assert scheduler.handover_worker(worker)["blocked_execution_ids"] == [request.execution_id]
    scheduler.maintain()
    snapshot = scheduler.snapshot(request.execution_id)
    assert snapshot["status"] == "running"
    assert not snapshot["result"]
    assert snapshot["scheduler"]["owner_worker_id"] == worker
    assert scheduler.finish(
        ExecutionResult(execution_id=request.execution_id, status="completed"),
        worker_id=worker, claim_token=token,
    )["status"] == "cancelled"


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_acknowledgement_keeps_result_for_reconnecting_waiter(
    tmp_path: Path, terminal: str,
) -> None:
    """执行结果被确认后，重新连接的等待者仍拿到原终态结果

    An acknowledgement must not turn a valid terminal into a protocol error.

    :design: decision_6ad0384e020d
    :design-point:
    """
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0,
    )
    server = ExecutionServiceServer(
        tmp_path / "receipt.sock", scheduler, instance="test", build_id="test",
        preparation=ExecutionPreparation(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(1)
    service = ExecutionService(client=ExecutionServiceClient(tmp_path / "receipt.sock"))
    request = ExecutionRequest.new(
        actor="test-actor", prompt="receipt replay", service_type="chat_completion",
        provider="openai", operation="generate",
    )
    try:
        scheduler.submit(request)
        scheduler.register_worker(
            worker_id="test:receipt", build_id="test", capabilities=["chat_completion"],
        )
        claim = scheduler.claim(worker_id="test:receipt", capabilities=["chat_completion"])
        scheduler.start(request.execution_id, worker_id="test:receipt", claim_token=claim["claim_token"])
        expected = ExecutionResult(
            execution_id=request.execution_id, status=terminal,
            output={"message": "the committed final"} if terminal == "completed" else {},
            error="stopped" if terminal == "cancelled" else "",
        )
        scheduler.finish(expected, worker_id="test:receipt", claim_token=claim["claim_token"])
        service.acknowledge(request.execution_id, actor="test-actor")
        service.acknowledge(request.execution_id, actor="test-actor")
        result = service.wait(request.execution_id, actor="test-actor", timeout_seconds=1)
        assert result["result"] == expected.to_payload()
        assert result["status"] == terminal
        assert result["scheduler"]["acknowledged_at"]
        assert result["request"] == {}
        with pytest.raises(KeyError):
            service.wait(request.execution_id, actor="other", timeout_seconds=1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("terminal", ["completed", "cancelled"])
def test_terminal_wait_observes_another_scheduler_generation(
    terminal: str,
) -> None:
    """滚动重启时旧调度器实例的等待者也能及时看到新实例写入的终态

    A rolling restart cannot rely on another process's Condition notify.

    :design: decision_6ad0384e020d
    :design-point:
    """
    old = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    new = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    request = ExecutionRequest.new(
        actor="test-actor", prompt="rolling restart", service_type="chat_completion",
        provider="openai", operation="generate",
    )
    old.submit(request)
    new.register_worker(worker_id="test:new", build_id="new", capabilities=["chat_completion"])
    claim = new.claim(worker_id="test:new", capabilities=["chat_completion"])
    new.start(request.execution_id, worker_id="test:new", claim_token=claim["claim_token"])
    snapshot = old.snapshot(request.execution_id, include_events=False)
    waiting = threading.Event()
    result: dict = {}
    original_wait = old.changes.signal("execution").wait

    def observed_wait(timeout=None):
        waiting.set()
        return original_wait(timeout)

    def observe():
        result.update(old.wait_for_change(
            request.execution_id, after_updated_at=snapshot["scheduler"]["updated_at"],
            include_events=False, timeout_seconds=2,
        ))

    waiter = threading.Thread(target=observe, daemon=True)
    with mock.patch.object(old.changes.signal("execution"), "wait", side_effect=observed_wait):
        try:
            waiter.start()
            assert waiting.wait(1)
            started = time.monotonic()
            new.finish(
                ExecutionResult(execution_id=request.execution_id, status=terminal),
                worker_id="test:new", claim_token=claim["claim_token"],
            )
            waiter.join(timeout=0.75)
            assert not waiter.is_alive(), "terminal hidden until the old generation's long poll expires"
            assert time.monotonic() - started < 0.75
            assert result["result"]["status"] == terminal
        finally:
            waiter.join(timeout=3)


def test_cancellation_delivery_retry_keeps_the_same_execution_identity_contracts(subtests, isolated_portal_case):
    """取消请求因控制面替换失败时按同一执行 ID 重发，普通等待保持长轮询"""
    with subtests.test(contract='test_cancellation_delivery_retry_keeps_the_same_execution_identity'), isolated_portal_case() as case_path:
        client = mock.Mock()
        client.request.side_effect = [
            ExecutionServiceUnavailable("control socket replaced"),
            {"status": "running"},
            {"status": "cancelled", "result": {"status": "cancelled"}},
        ]
        with mock.patch("management_portal.execution.service.time.sleep") as delay:
            result = ExecutionService(client=client).wait(
                "quiet-execution", actor="sample_admin", timeout_seconds=60,
                cancellation_requested=lambda: True,
            )
        calls = client.request.call_args_list
        assert [call.args[0] for call in calls] == [
            "execution.cancel", "execution.cancel", "execution.wait",
        ]
        assert calls[0].args == calls[1].args
        assert all(call.args[1]["execution_id"] == "quiet-execution" for call in calls)
        assert calls[-1].args[1]["timeout_seconds"] == 30
        delay.assert_called_once_with(0.25)
        assert result["status"] == "cancelled"
    with subtests.test(contract='test_ordinary_execution_wait_keeps_long_polling'), isolated_portal_case() as case_path:
        client = mock.Mock()
        client.request.return_value = {
            "status": "completed", "result": {"status": "completed"},
        }
        ExecutionService(client=client).wait(
            "ordinary-execution", actor="sample_admin", timeout_seconds=60,
        )
        client.request.assert_called_once()
        assert client.request.call_args.args[1]["timeout_seconds"] == 30


@pytest.mark.parametrize("already_requested", [False, True])
@pytest.mark.parametrize("immediate_notification", [False, True])
def test_business_stop_reaches_quiet_execution_without_provider_output(
    tmp_path: Path, already_requested: bool, immediate_notification: bool,
) -> None:
    """Provider 没有输出时业务停止也在一秒内送达 worker，执行以 cancelled 结束"""
    from management_portal.society_service.coordinator import LineageCoordinator

    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    server = ExecutionServiceServer(
        tmp_path / "quiet.sock", scheduler,
        instance="test", build_id="test-build",
        preparation=ExecutionPreparation(),
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    assert server.started.wait(1)
    waiting = threading.Event()
    stop_requested = threading.Event()
    cancellation_delivered = threading.Event()
    outcomes: dict = {}
    errors: list[Exception] = []
    control_trace: list[tuple[str, str, float]] = []

    class ObservedClient(ExecutionServiceClient):
        def request(self, method, params=None, **kwargs):
            # Signal only after the initial cursor has been consumed: the next
            # real RPC has no new provider output to wake it.
            if method == "execution.wait" and params.get("after_updated_at"):
                waiting.set()
            control_trace.append((method, "start", time.monotonic()))
            try:
                return super().request(method, params, **kwargs)
            finally:
                control_trace.append((method, "end", time.monotonic()))

    client = ObservedClient(tmp_path / "quiet.sock", timeout=2)
    service = ExecutionService(client=client)
    coordinator = LineageCoordinator(
        resume_residents=lambda: {}, resume_resident=lambda _: {},
        reconcile_all=lambda: {}, reconcile_lineage=lambda _: {},
        cancel_execution=lambda execution_id, actor: service.cancel(execution_id, actor=actor),
    )
    if immediate_notification:
        coordinator.start()
    worker = SchedulerClient(
        worker_id="test:quiet-worker", build_id="test-build",
        capabilities=["agent_session:codex"], service_client=client,
    )
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="quiet operation", service_type="agent_session",
        provider="codex", operation="turn",
    )
    waiter = None
    control = None
    try:
        service.submit(request)
        worker.register()
        claim = worker.claim(wait_seconds=0.1)
        assert claim["claimed"]
        token = claim["claim_token"]
        worker.start(request.execution_id, token)
        if already_requested:
            stop_requested.set()

        def wait_for_result():
            try:
                outcomes.update(service.wait(
                    request.execution_id, actor="sample_admin", timeout_seconds=5,
                    cancellation_requested=None if immediate_notification else stop_requested.is_set,
                ))
            except Exception as error:
                errors.append(error)

        def confirm_worker_cancellation():
            try:
                state = worker.wait_for_control(
                    request.execution_id, token, timeout_seconds=5,
                )
                if state.get("cancel_requested"):
                    cancellation_delivered.set()
                    worker.finish(ExecutionResult(
                        execution_id=request.execution_id, status="cancelled",
                        error="worker confirmed cancellation",
                    ), token)
            except Exception as error:
                errors.append(error)

        control = threading.Thread(target=confirm_worker_cancellation, daemon=True)
        control.start()
        waiter = threading.Thread(target=wait_for_result, daemon=True)
        waiter.start()
        if not already_requested:
            assert waiting.wait(2), "execution did not enter its quiet wait"
            # Let the real socket request enter its server-side wait.
            time.sleep(0.05)
        started = time.monotonic()
        stop_requested.set()
        if immediate_notification:
            coordinator.notify_execution_cancellation(request.execution_id, "sample_admin")
        assert cancellation_delivered.wait(1), (
            f"Stop was not forwarded while the provider was silent: {errors!r}; {control_trace!r}"
        )
        assert time.monotonic() - started < 1
        waiter.join(timeout=2)
        assert not waiter.is_alive()
        assert not errors
        assert outcomes["status"] == "cancelled"
        assert outcomes["result"]["status"] == "cancelled"
    finally:
        # Unblock our own synthetic execution on a failing implementation;
        # this cleanup is not part of the cancellation timing assertion.
        service.cancel(request.execution_id, actor="sample_admin", reason="test cleanup")
        if control is not None:
            control.join(timeout=2)
        if waiter is not None:
            waiter.join(timeout=2)
        coordinator.stop()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
