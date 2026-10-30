"""Worker control and outcomes: nonconforming claims, cancellation wake-ups, appended input, deferred
recovery, terminal publish and structured interruptions.

:module: execution.service
:importance: P2
"""
from __future__ import annotations

from tests.cases.execution_test_support import run_worker_once

import json
from tests.cases.database_test_support import connect
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_common.json_columns import json_value

from management_portal.agent_prompt_context import (
    portal_system_instructions,
)
from management_portal.codex_app_server_protocol import CodexTurnInterrupted
from management_portal.execution.backend_contracts import BackendResult
from management_portal.execution.contracts import (
    ExecutionRecoveryDeferred,
    ExecutionRequest,
    ExecutionResult,
    SessionOptions,
)
from management_portal.execution.runtime import ExecutionRuntime
from management_portal.execution.scheduler import (
    Scheduler,
    SchedulerClient,
)
from management_portal.execution.transport import ExecutionClaimOwnershipLost
from management_portal.execution.worker import ExecutionWorker
from tests.cases.execution_layer_test_support import _stack, _close


def test_worker_terminally_fails_a_claimed_nonconforming_request(
    tmp_path: Path,
) -> None:
    """认领到不合规请求时 worker 直接以失败终止，不调用 Provider 也不重试"""
    class BackendMustNotRun:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            raise AssertionError("an invalid request must not reach its Provider")

        def interrupt(self, **_values):
            return True

    scheduler, server, thread, client, service = _stack(tmp_path)
    runtime = ExecutionRuntime(
        agent_providers={"codex": BackendMustNotRun()},
        state_root=tmp_path / "execution-state",
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:invalid-request-worker",
        scheduler=SchedulerClient(
            worker_id="explore-1:invalid-request-worker",
            build_id="test-build",
            capabilities=runtime.capabilities(),
            service_client=client,
        ),
        runtime=runtime,
        state_root=tmp_path / "execution-state",
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="must fail before Provider execution",
        service_type="agent_session",
        provider="codex",
        operation="turn",
    )

    try:
        worker.register()
        service.submit(request)
        with connect(scheduler.database) as connection:
            stored = json_value(connection.execute(
                'SELECT request_json FROM executions WHERE execution_id = %s',
                (request.execution_id,),
            ).fetchone()[0])
            stored["schema"] = "invalid"
            connection.execute(
                'UPDATE executions SET request_json = %s WHERE execution_id = %s',
                (
                    json.dumps(stored, ensure_ascii=False, separators=(",", ":")),
                    request.execution_id,
                ),
            )

        assert run_worker_once(worker) is True
        failed = service.status(request.execution_id, actor="sample_admin")
        assert failed["status"] == "failed"
        assert failed["scheduler"]["attempts"] == 1
        assert failed["scheduler"]["owner_worker_id"] == ""
        assert failed["scheduler"]["lease_expires_at"] == 0
        assert failed["result"]["status"] == "failed"
        assert "invalid execution request schema" in failed["error"]
        assert run_worker_once(worker) is False
    finally:
        _close(server, thread)


def test_worker_control_wait_wakes_immediately_for_cancellation_contracts(tmp_path, subtests, isolated_portal_case):
    """取消请求立即唤醒 worker 的控制等待，排队或启动中的 codex 轮次接收持久追加输入

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_worker_control_wait_wakes_immediately_for_cancellation'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, client, service = _stack(tmp_path)
        worker = SchedulerClient(
            worker_id="explore-1:control-wait-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        )
        try:
            worker.register()
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="wait for explicit cancellation",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )
            service.submit(request)
            claim = worker.claim(wait_seconds=0.1)
            token = claim["claim_token"]
            worker.start(request.execution_id, token)

            result: dict[str, object] = {}
            waiter = threading.Thread(
                target=lambda: result.update(
                    worker.wait_for_control(
                        request.execution_id,
                        token,
                        timeout_seconds=2.0,
                    )
                )
            )
            waiter.start()
            time.sleep(0.1)
            assert waiter.is_alive()

            started_at = time.monotonic()
            service.cancel(request.execution_id, actor="sample_admin")
            waiter.join(timeout=1)

            assert not waiter.is_alive()
            assert time.monotonic() - started_at < 0.75
            assert result == {
                "cancel_requested": True,
                "handover_requested": False,
                "status": "running",
            }
        finally:
            _close(server, thread)
    with subtests.test(contract='test_starting_codex_turn_accepts_durable_appended_input'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, client, service = _stack(tmp_path)
        worker = SchedulerClient(
            worker_id="explore-1:starting-input-control-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        )
        try:
            worker.register()
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="start the task",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )
            service.submit(request)
            claim = worker.claim(wait_seconds=0.1)
            token = claim["claim_token"]
            worker.start(request.execution_id, token)

            submitted = service.append_input(
                request.execution_id,
                actor="sample_admin",
                input=[{"type": "text", "text": "include this when the turn starts"}],
            )
            control = worker.wait_for_control(
                request.execution_id,
                token,
                timeout_seconds=1.0,
            )["turn_controls"][0]

            assert submitted["status"] == "pending"
            assert control["expected_turn_id"] == ""
            assert control["input"] == [
                {"type": "text", "text": "include this when the turn starts"}
            ]
        finally:
            _close(server, thread)
    with subtests.test(contract='test_queued_codex_turn_accepts_durable_appended_input'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, client, service = _stack(tmp_path)
        worker = SchedulerClient(
            worker_id="explore-1:queued-input-control-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        )
        try:
            worker.register()
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="start the task",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )
            service.submit(request)

            submitted = service.append_input(
                request.execution_id,
                actor="sample_admin",
                input=[{"type": "text", "text": "include this after dispatch"}],
            )
            claim = worker.claim(wait_seconds=0.1)
            token = claim["claim_token"]
            worker.start(request.execution_id, token)
            control = worker.wait_for_control(
                request.execution_id,
                token,
                timeout_seconds=1.0,
            )["turn_controls"][0]

            assert submitted["status"] == "pending"
            assert control["expected_turn_id"] == ""
            assert control["input"] == [
                {"type": "text", "text": "include this after dispatch"}
            ]
        finally:
            _close(server, thread)
    with subtests.test(contract='test_scheduler_heartbeat_does_not_change_business_updated_at'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, client, service = _stack(tmp_path)
        worker = SchedulerClient(
            worker_id="explore-1:heartbeat-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        )
        try:
            worker.register()
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="renew only the lease",
                service_type="agent_session",
                provider="codex",
                operation="turn",
            )
            service.submit(request)
            claim = worker.claim(wait_seconds=0.1)
            token = claim["claim_token"]
            started = worker.start(request.execution_id, token)

            worker.heartbeat(request.execution_id, token)
            current = service.status(request.execution_id)

            assert current["scheduler"]["updated_at"] == (
                started["scheduler"]["updated_at"]
            )
            assert current["scheduler"]["lease_expires_at"] >= (
                started["scheduler"]["lease_expires_at"]
            )
        finally:
            _close(server, thread)
    with subtests.test(contract='test_scheduler_serializes_concurrent_turns_by_execution_binding'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler, server, thread, client, service = _stack(tmp_path)
        worker = SchedulerClient(
            worker_id="explore-1:binding-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        )
        try:
            worker.register()
            requests = [
                ExecutionRequest.new(
                    actor="sample_admin",
                    prompt=prompt,
                    service_type="agent_session",
                    provider="codex",
                    operation="turn",
                    session=SessionOptions(
                        mode="persistent",
                        binding_id=binding_id,
                    ),
                )
                for prompt, binding_id in (
                    ("first", "resident:test-main"),
                    ("second", "resident:test-main"),
                    ("parallel", "resident:other:main"),
                )
            ]
            for request in requests:
                service.submit(request)

            first = worker.claim()
            parallel = worker.claim()

            assert first["execution"]["request"]["execution_id"] == requests[0].execution_id
            assert parallel["execution"]["request"]["execution_id"] == requests[2].execution_id
            assert service.status(requests[1].execution_id)["status"] == "queued"
        finally:
            _close(server, thread)
    with subtests.test(contract='test_execution_preparation_persists_codex_route_before_queueing'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _scheduler, server, thread, _client, service = _stack(tmp_path)
        try:
            request = ExecutionRequest.new(
                actor="sample_admin",
                prompt="route before scheduling",
                service_type="agent_session",
                provider="codex",
                operation="turn",
                system_instructions=portal_system_instructions("resident"),
            )

            submitted = service.submit(request)

            assert submitted["request"]["session"]["backend_route_id"] == "one"
            queued_event = next(
                event
                for event in submitted["events"]
                if event["type"] == "queued"
            )
            assert queued_event["details"]["capacity_key"] == "codex:one"
        finally:
            _close(server, thread)


def test_execution_wait_wakes_immediately_when_worker_finishes_cancelled(
    tmp_path: Path,
) -> None:
    """worker 以 cancelled 结束执行时等待者立即被唤醒"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    worker_id = "dev:terminal-wake-worker"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(
        worker_id=worker_id,
        build_id="test-build",
        capabilities=capabilities,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="stop this turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:terminal-wake",
            backend_route_id="zero",
        ),
    )
    scheduler.submit(request)
    claim = scheduler.claim(
        worker_id=worker_id,
        capabilities=capabilities,
    )
    claim_token = str(claim["claim_token"])
    scheduler.start(
        request.execution_id,
        worker_id=worker_id,
        claim_token=claim_token,
    )
    running = scheduler.snapshot(request.execution_id)
    result: dict[str, object] = {}
    waiter = threading.Thread(
        target=lambda: result.update(
            scheduler.wait_for_change(
                request.execution_id,
                after_updated_at=str(running["scheduler"]["updated_at"]),
                after_sequence=max(
                    int(event["sequence"])
                    for event in running["events"]
                ),
                timeout_seconds=2.0,
            )
        )
    )
    waiter.start()
    time.sleep(0.1)
    assert waiter.is_alive()

    started_at = time.monotonic()
    scheduler.finish(
        ExecutionResult(
            execution_id=request.execution_id,
            status="cancelled",
            error="stopped by user",
        ),
        worker_id=worker_id,
        claim_token=claim_token,
    )
    waiter.join(timeout=1)

    assert not waiter.is_alive()
    assert time.monotonic() - started_at < 0.75
    assert result["status"] == "cancelled"


def test_active_codex_turn_accepts_durable_appended_input(
    tmp_path: Path,
) -> None:
    """运行中的 codex 轮次接收持久追加输入，按 control_id 幂等，送达后记入消息来源"""
    _scheduler, server, thread, client, service = _stack(tmp_path)
    worker = SchedulerClient(
        worker_id="explore-1:input-control-worker",
        build_id="test-build",
        capabilities=["agent_session:codex"],
        service_client=client,
    )
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="start the task",
            agent_id="resident_333333333333",
            service_type="agent_session",
            provider="codex",
            operation="turn",
        )
        service.submit(request)
        claim = worker.claim(wait_seconds=0.1)
        token = claim["claim_token"]
        worker.start(request.execution_id, token)
        worker.checkpoint(
            request.execution_id,
            token,
            {
                "provider": "codex",
                "backend_session_id": "thread-active",
                "backend_route_id": "one",
                "turn_id": "turn-active",
                "resumable": True,
            },
        )

        control_id = "control-111111111111111111111111"
        store = server.agent_data
        store.begin_execution(execution_id=request.execution_id, agent_id=request.agent_id, actor_ref=request.actor,
                              provider="codex", started_at="2026-09-11T00:00:00Z")
        session = store.ensure_provider_session(provider="codex", backend_route_id="one", backend_session_id="thread-active",
                                                agent_id=request.agent_id, actor_ref=request.actor)
        store.bind_execution_provider_session(request.execution_id, provider_session_ref=session["provider_session_ref"])
        store.record_input_acceptance(request.execution_id, agent_id=request.agent_id, provider_turn_id="turn-active")
        message_id = "msg_" + "c" * 32
        store.display_chat_message(request.agent_id, {
            "message_id": message_id, "role": "user", "author_id": "CASE-A",
            "content_parts": [{"type": "text", "text": "focus on the UI first"}], "created_at": "2026-09-11T00:00:01Z",
        })
        source_segment_id = store.chat_message_provenance(request.agent_id, message_id)["segment_id"]
        submitted = service.append_input(
            request.execution_id,
            actor="CASE-A",
            input=[{"type": "text", "text": "focus on the UI first"}],
            control_id=control_id,
            source_segment_id=source_segment_id,
        )
        assert submitted["status"] == "pending"
        retried = service.append_input(
            request.execution_id,
            actor="CASE-A",
            input=[{"type": "text", "text": "focus on the UI first"}],
            control_id=control_id,
        )
        assert retried["control_id"] == control_id
        assert retried["status"] == "pending"
        control = worker.wait_for_control(
            request.execution_id,
            token,
            timeout_seconds=1.0,
        )["turn_controls"][0]

        assert control["schema"] == "tfi.execution-input-control.v1"
        assert control["expected_turn_id"] == "turn-active"
        assert control["input"] == [
            {"type": "text", "text": "focus on the UI first"}
        ]
        delivered = worker.complete_input_control(
            request.execution_id,
            token,
            control["control_id"],
            delivered=True,
        )
        assert delivered["status"] == "delivered"
        assert delivered["control_id"] == control["control_id"]
        assert service.input_control(control_id, agent_id=request.agent_id)["status"] == "delivered"
        with pytest.raises(KeyError):
            service.input_control(control_id, agent_id="resident_other")
        assert store.chat_message_provenance(request.agent_id, message_id)["provider_turn_id"] == "turn-active"
        assert service.input_control(control_id, agent_id=request.agent_id)["status"] == "delivered"
        completed_retry = service.append_input(
            request.execution_id,
            actor="CASE-A",
            input=[{"type": "text", "text": "focus on the UI first"}],
            control_id=control_id,
        )
        assert completed_retry["status"] == "delivered"
    finally:
        _close(server, thread)


def test_worker_propagates_running_cancellation_promptly(
    tmp_path: Path,
) -> None:
    """取消运行中的执行时 worker 立即中断 Provider 并以 cancelled 结束"""
    _scheduler, server, thread, client, service = _stack(tmp_path)
    started = threading.Event()
    interrupted = threading.Event()

    class BlockingBackend:
        provider_id = "codex"

        def execute(self, invocation, context):
            started.set()
            while not context.is_cancelled():
                time.sleep(0.01)
            raise InterruptedError("cancelled by test")

        def interrupt(self, **values):
            interrupted.set()
            return True

    runtime = ExecutionRuntime(
        agent_providers={"codex": BlockingBackend()},
        state_root=tmp_path / "execution-state",
    )
    scheduler_client = SchedulerClient(
        worker_id="explore-1:cancel-worker",
        build_id="test-build",
        capabilities=runtime.capabilities(),
        service_client=client,
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:cancel-worker",
        scheduler=scheduler_client,
        runtime=runtime,
    )
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="block until cancelled",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            system_instructions=portal_system_instructions("resident"),
        )
        service.submit(request)
        runner = threading.Thread(target=run_worker_once, args=(worker,))
        runner.start()
        assert started.wait(1)
        begin = time.monotonic()
        service.cancel(request.execution_id, actor="sample_admin")
        runner.join(timeout=2)
        assert not runner.is_alive()
        assert time.monotonic() - begin < 1.5
        assert interrupted.is_set()
        assert service.status(request.execution_id)["status"] == "cancelled"
    finally:
        _close(server, thread)


def test_worker_classifies_backend_error_after_cancel_request_as_cancelled(
    tmp_path: Path,
) -> None:
    """取消请求之后 Provider 报出的错误归为 cancelled"""
    _scheduler, server, thread, client, service = _stack(tmp_path)
    started = threading.Event()

    class ProviderStyleInterruptBackend:
        provider_id = "codex"

        def execute(self, invocation, context):
            started.set()
            while not context.is_cancelled():
                time.sleep(0.01)
            raise RuntimeError("provider reported an interrupted turn")

        def interrupt(self, **_values):
            return True

    runtime = ExecutionRuntime(
        agent_providers={"codex": ProviderStyleInterruptBackend()},
        state_root=tmp_path / "execution-state",
    )
    scheduler_client = SchedulerClient(
        worker_id="explore-1:provider-cancel-worker",
        build_id="test-build",
        capabilities=runtime.capabilities(),
        service_client=client,
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:provider-cancel-worker",
        scheduler=scheduler_client,
        runtime=runtime,
    )
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="cancel a provider that reports interruption as an error",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            system_instructions=portal_system_instructions("resident"),
        )
        service.submit(request)
        runner = threading.Thread(target=run_worker_once, args=(worker,))
        runner.start()
        try:
            assert started.wait(10), "test provider did not start"
            service.cancel(request.execution_id, actor="sample_admin")
            runner.join(timeout=10)

            assert not runner.is_alive()
            snapshot = service.status(request.execution_id)
            assert snapshot["status"] == "cancelled"
            assert snapshot["scheduler"]["cancel_requested"] is True
        finally:
            if runner.is_alive():
                service.cancel(request.execution_id, actor="sample_admin")
                runner.join(timeout=10)
    finally:
        _close(server, thread)


@pytest.mark.parametrize("checkpoint_turn", ["turn-existing", ""])
def test_worker_requeues_a_deferred_provider_recovery(
    tmp_path: Path,
    checkpoint_turn: str,
) -> None:
    """Provider 恢复被延后时 worker 写入可恢复检查点并重新排队，不发布终态"""
    class DeferredBackend:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            _context.set_provider_recovery_state({"kind": "thread_writer_busy"})
            raise ExecutionRecoveryDeferred("provider recovery deadline")

        def interrupt(self, **_values):
            return True

    class SchedulerStub:
        def __init__(self) -> None:
            self.requeued = None
            self.finish_called = False
            self.finished = None

        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            time.sleep(0.01)
            return {"cancel_requested": False}

        def heartbeat(self, *_args, **_kwargs):
            return {"cancel_requested": False}

        def emit(self, *_args, **_kwargs):
            return None

        def checkpoint(self, *_args, **_kwargs):
            self.last_checkpoint = _args[-1]
            return {}

        def reassign_capacity(self, *_args, **_kwargs):
            return {}

        def requeue(self, execution_id, claim_token, reason):
            assert self.last_checkpoint["resumable"] is True
            assert self.last_checkpoint["provider_recovery_state"]["kind"] == "thread_writer_busy"
            self.requeued = (execution_id, claim_token, reason)
            return {"status": "queued"}

        def finish(self, result, *_args, **_kwargs):
            self.finish_called = True
            self.finished = result
            return {}

    scheduler = SchedulerStub()
    worker = ExecutionWorker(
        build_id="current",
        worker_id="dev:current-worker",
        scheduler=scheduler,
        runtime=ExecutionRuntime(
            agent_providers={"codex": DeferredBackend()},
            state_root=tmp_path / "execution-state",
        ),
    )
    request = ExecutionRequest.new(
        actor="sample_peer",
        prompt="resume later",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(
            mode="stateless",
            backend_route_id="zero",
        ),
    )

    result = worker.execute_claim({
        "claim_token": "claim-current",
        "execution": {
            "execution_id": request.execution_id,
            "request": request.to_payload(),
            "checkpoint": {
                "resumable": True,
                "provider": "codex",
                "backend_session_id": "thread-existing",
                "backend_route_id": "zero",
                "turn_id": checkpoint_turn,
            },
        },
    })

    assert result["status"] == "requeued", scheduler.finished
    assert scheduler.requeued == (
        request.execution_id,
        "claim-current",
        "provider recovery deadline",
    )
    assert scheduler.finish_called is False


def test_worker_terminal_publish_wakes_blocked_heartbeat_before_return(
    tmp_path: Path,
) -> None:
    """worker 发布终态后立即唤醒阻塞的控制等待，不等心跳超时"""
    control_wait_started = threading.Event()
    terminal_published = threading.Event()
    control_wait_returned = threading.Event()

    class ImmediateBackend:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            assert control_wait_started.wait(1)
            return BackendResult(output={"message": "finished"})

        def interrupt(self, **_values):
            return True

    class BlockingControlScheduler:
        def __init__(self) -> None:
            self.finished: ExecutionResult | None = None

        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            control_wait_started.set()
            assert terminal_published.wait(3)
            control_wait_returned.set()
            raise ExecutionClaimOwnershipLost(
                "terminal publish released claim ownership"
            )

        def heartbeat(self, *_args, **_kwargs):
            return {}

        def emit(self, *_args, **_kwargs):
            return None

        def checkpoint(self, *_args, **_kwargs):
            return {}

        def reassign_capacity(self, *_args, **_kwargs):
            return {}

        def finish(self, result, _claim_token):
            self.finished = result
            terminal_published.set()
            return {"status": result.status}

    scheduler = BlockingControlScheduler()
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:test-worker",
        scheduler=scheduler,
        runtime=ExecutionRuntime(
            agent_providers={"codex": ImmediateBackend()},
            state_root=tmp_path / "execution-state",
        ),
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="finish without waiting for the heartbeat timeout",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
    )

    begin = time.monotonic()
    outcome = worker.execute_claim(
        {
            "claim_token": "claim-token",
            "execution": {
                "execution_id": request.execution_id,
                "request": request.to_payload(),
                "checkpoint": {},
            },
        },
        lease_seconds=15,
    )
    elapsed = time.monotonic() - begin

    assert outcome["status"] == "completed"
    assert scheduler.finished is not None
    assert control_wait_returned.is_set()
    assert elapsed < 1.0


def test_worker_transports_structured_provider_interruption(
    tmp_path: Path,
) -> None:
    """worker 把 Provider 中断作为结构化错误码和详情上报"""
    class InterruptedBackend:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            raise CodexTurnInterrupted("thread-1", "turn-1")

        def interrupt(self, **_values):
            return True

    class CapturingScheduler:
        def __init__(self) -> None:
            self.finished: ExecutionResult | None = None

        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            time.sleep(0.01)
            return {}

        def heartbeat(self, *_args, **_kwargs):
            return {}

        def emit(self, *_args, **_kwargs):
            return None

        def checkpoint(self, *_args, **_kwargs):
            return {}

        def reassign_capacity(self, *_args, **_kwargs):
            return {}

        def finish(self, result, _claim_token):
            self.finished = result
            return {"status": result.status}

    scheduler = CapturingScheduler()
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:test-worker",
        scheduler=scheduler,
        runtime=ExecutionRuntime(
            agent_providers={"codex": InterruptedBackend()},
            state_root=tmp_path / "execution-state",
        ),
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="surface the provider interruption",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
    )

    outcome = worker.execute_claim({
        "claim_token": "claim-token",
        "execution": {
            "execution_id": request.execution_id,
            "request": request.to_payload(),
            "checkpoint": {},
        },
    })

    assert outcome["status"] == "failed"
    assert scheduler.finished is not None
    assert scheduler.finished.error_code == "provider_turn_interrupted"
    assert scheduler.finished.error_details == {
        "provider": "codex",
        "provider_status": "interrupted",
        "thread_id": "thread-1",
        "turn_id": "turn-1",
    }
    assert "unknown error" not in scheduler.finished.error


from tests.cases.compatibility_test_support import setUpModule, tearDownModule
