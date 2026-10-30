"""Worker generations and handover: registration fencing, draining, rollout, handover requeue, shutdown
and self-fencing.

:module: execution.service
:importance: P2
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.agent_prompt_context import (
    portal_system_instructions,
)
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.backend_contracts import BackendResult
from management_portal.execution.contracts import (
    ExecutionInputControl,
    ExecutionRequest,
    ExecutionResult,
    SessionOptions,
)
from management_portal.execution.runtime import ExecutionRuntime
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.transport import (
    ExecutionClaimOwnershipLost,
    ExecutionServiceUnavailable,
)
from management_portal.execution.worker import ExecutionWorker


def test_worker_registration_atomically_fences_replaced_generation_contracts(tmp_path, subtests, isolated_portal_case):
    """新一代 worker 激活时旧代原子转为 draining，退役 worker 不能自行恢复

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_worker_registration_atomically_fences_replaced_generation'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler()
        old_worker_id = "dev:execution-worker:old"
        new_worker_id = "dev:execution-worker:new"
        capabilities = ["chat_completion"]
        scheduler.register_worker(
            worker_id=old_worker_id,
            build_id="old-build",
            capabilities=capabilities,
        )

        scheduler.register_worker(
            worker_id=new_worker_id,
            build_id="new-build",
            capabilities=capabilities,
            state="standby",
        )
        activated = scheduler.activate_worker(
            new_worker_id,
            previous_worker_id=old_worker_id,
        )

        workers = {
            item["worker_id"]: item
            for item in scheduler.health()["workers"]
        }
        assert activated["previous_worker_id"] == old_worker_id
        assert workers[old_worker_id]["state"] == "draining"
        assert workers[new_worker_id]["state"] == "active"
        assert scheduler.claim(
            worker_id=old_worker_id,
            capabilities=capabilities,
        ) == {"claimed": False, "reason": "worker_not_active", "worker_state": "draining"}
    with subtests.test(contract='test_worker_activation_accepts_already_expired_previous_generation'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler()
        new_worker_id = "dev:execution-worker:new"
        scheduler.register_worker(
            worker_id=new_worker_id,
            build_id="new-build",
            capabilities=["chat_completion"],
            state="standby",
        )

        activated = scheduler.activate_worker(
            new_worker_id,
            previous_worker_id="dev:execution-worker:expired",
        )

        assert activated == {
            "worker_id": new_worker_id,
            "state": "active",
            "previous_worker_id": "dev:execution-worker:expired",
            "previous_state": "",
        }
        assert scheduler.health()["workers"][0]["state"] == "active"
    with subtests.test(contract='test_worker_activation_retires_other_generations_but_keeps_its_own'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler()
        release_worker_id = "release:execution-worker:old"
        dev_worker_id = "dev:execution-worker:new"
        capabilities = ["agent_session:codex"]
        scheduler.register_worker(
            worker_id=release_worker_id,
            build_id="release-old",
            capabilities=capabilities,
        )
        scheduler.register_worker(
            worker_id=dev_worker_id,
            build_id="dev-new",
            capabilities=capabilities,
            state="standby",
        )

        scheduler.activate_worker(dev_worker_id)

        workers = {
            item["worker_id"]: item
            for item in scheduler.health()["workers"]
        }
        assert workers[release_worker_id]["state"] == "draining"
        assert workers[dev_worker_id]["state"] == "active"
        assert scheduler.claim(
            worker_id=release_worker_id,
            capabilities=capabilities,
        ) == {"claimed": False, "reason": "worker_not_active", "worker_state": "draining"}

        # The Release pool then starts the same generation; it does not
        # retire the Dev Worker, so executions the Dev Worker took over in
        # this publish are not handed over a second time.
        release_new_id = "release:execution-worker:new"
        scheduler.register_worker(
            worker_id=release_new_id,
            build_id="dev-new",
            capabilities=capabilities,
            state="standby",
        )
        scheduler.activate_worker(
            release_new_id,
            previous_worker_id=release_worker_id,
        )

        workers = {
            item["worker_id"]: item
            for item in scheduler.health()["workers"]
        }
        assert workers[dev_worker_id]["state"] == "active"
        assert workers[release_new_id]["state"] == "active"
        assert workers[release_worker_id]["state"] == "draining"
        for worker_id in (dev_worker_id, release_new_id):
            assert scheduler.claim(
                worker_id=worker_id,
                capabilities=capabilities,
            ).get("reason") != "worker_not_active"

        # A retired Worker leaves by itself; its successor still names it.
        scheduler.handover_worker(release_worker_id)
        release_next_id = "release:execution-worker:next"
        scheduler.register_worker(
            worker_id=release_next_id,
            build_id="dev-new",
            capabilities=capabilities,
            state="standby",
        )
        assert scheduler.activate_worker(
            release_next_id,
            previous_worker_id=release_worker_id,
        )["previous_state"] == "offline"
    with subtests.test(contract='test_submit_wakes_worker_claim_wait_immediately'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler(
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        worker_id = "dev:execution-worker:submit-wakeup"
        capabilities = ["chat_completion"]
        scheduler.register_worker(
            worker_id=worker_id,
            build_id="test-build",
            capabilities=capabilities,
        )
        claimed: dict[str, object] = {}

        def wait_for_submission() -> None:
            claimed.update(
                scheduler.claim_wait(
                    worker_id=worker_id,
                    capabilities=capabilities,
                    timeout_seconds=5,
                )
            )

        waiting = threading.Thread(target=wait_for_submission)
        waiting.start()
        time.sleep(0.05)
        request = ExecutionRequest.new(
            actor="submit-wakeup-test",
            prompt="wake the waiting worker",
            service_type="chat_completion",
            provider="openai",
            operation="generate",
        )

        started = time.monotonic()
        scheduler.submit(request)
        waiting.join(timeout=0.5)

        assert not waiting.is_alive()
        assert time.monotonic() - started < 0.5
        assert claimed["claimed"] is True
        assert claimed["execution"]["execution_id"] == request.execution_id
    with subtests.test(contract='test_peer_scheduler_submit_wakes_worker_claim_wait_promptly'), isolated_portal_case() as case_path:
        tmp_path = case_path
        worker_scheduler = Scheduler(
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        submitting_scheduler = Scheduler(
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        worker_id = "release:execution-worker:peer-submit-wakeup"
        capabilities = ["chat_completion"]
        worker_scheduler.register_worker(
            worker_id=worker_id,
            build_id="release-build",
            capabilities=capabilities,
        )
        claimed: dict[str, object] = {}

        def wait_for_peer_submission() -> None:
            claimed.update(
                worker_scheduler.claim_wait(
                    worker_id=worker_id,
                    capabilities=capabilities,
                    timeout_seconds=5,
                )
            )

        waiting = threading.Thread(target=wait_for_peer_submission)
        waiting.start()
        time.sleep(0.05)
        request = ExecutionRequest.new(
            actor="peer-submit-wakeup-test",
            prompt="wake the worker through the shared Scheduler database",
            service_type="chat_completion",
            provider="openai",
            operation="generate",
        )

        started = time.monotonic()
        submitting_scheduler.submit(request)
        waiting.join(timeout=0.75)

        assert not waiting.is_alive()
        assert time.monotonic() - started < 0.75
        assert claimed["claimed"] is True
        assert claimed["execution"]["execution_id"] == request.execution_id
    with subtests.test(contract='test_streaming_updates_do_not_wake_idle_claim_waiters'), isolated_portal_case() as case_path:
        tmp_path = case_path
        worker_scheduler = Scheduler(
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        peer_scheduler = Scheduler(
            dispatch_interval_min_seconds=0,
            dispatch_interval_max_seconds=0,
        )
        worker_id = "dev:execution-worker:streaming-isolation"
        capabilities = ["agent_session:codex"]
        worker_scheduler.register_worker(
            worker_id=worker_id,
            build_id="test-build",
            capabilities=capabilities,
        )
        request = ExecutionRequest.new(
            actor="streaming-isolation-test",
            prompt="long running turn",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:streaming-isolation",
                backend_route_id="zero",
            ),
        )
        worker_scheduler.submit(request)
        claimed = worker_scheduler.claim(
            worker_id=worker_id,
            capabilities=capabilities,
        )
        owner = {
            "worker_id": worker_id,
            "claim_token": claimed["claim_token"],
        }
        worker_scheduler.start(request.execution_id, **owner)
        original_claim = worker_scheduler.claims.claim
        wait_result: dict[str, object] = {}
        first_check = threading.Event()

        def observed_claim(**kwargs):
            first_check.set()
            return original_claim(**kwargs)

        def wait_for_queue() -> None:
            wait_result.update(worker_scheduler.claim_wait(
                worker_id=worker_id,
                capabilities=capabilities,
                timeout_seconds=0.5,
            ))

        with mock.patch.object(
            worker_scheduler.claims,
            "claim",
            side_effect=observed_claim,
        ) as claim_call:
            waiting = threading.Thread(target=wait_for_queue, daemon=True)
            waiting.start()
            assert first_check.wait(1)
            time.sleep(0.05)
            for index in range(8):
                peer_scheduler.checkpoint(
                    request.execution_id,
                    **owner,
                    checkpoint={
                        "provider": "codex",
                        "turn_id": f"turn-{index}",
                        "resumable": True,
                    },
                )
            time.sleep(0.15)
            assert waiting.is_alive()
            assert claim_call.call_count == 1
            waiting.join(timeout=1)
            assert not waiting.is_alive()
            assert claim_call.call_count == 2
            assert wait_result["claimed"] is False
    with subtests.test(contract='test_retired_worker_cannot_promote_itself'), isolated_portal_case() as case_path:
        tmp_path = case_path
        scheduler = Scheduler()
        worker_id = "dev:execution-worker:retired"
        scheduler.register_worker(
            worker_id=worker_id,
            build_id="retired-build",
            capabilities=["chat_completion"],
        )
        scheduler.set_worker_state(worker_id, "draining")

        with pytest.raises(
            RuntimeError,
            match="retired worker cannot reactivate",
        ):
            scheduler.register_worker(
                worker_id=worker_id,
                build_id="retired-build",
                capabilities=["chat_completion"],
                state="active",
            )
        with pytest.raises(ValueError, match="can only retire"):
            scheduler.set_worker_state(worker_id, "active")

        worker = next(
            item
            for item in scheduler.health()["workers"]
            if item["worker_id"] == worker_id
        )
        assert worker["state"] == "draining"


def test_worker_handover_requeues_resumable_turn_without_terminal_projection(
    tmp_path: Path,
) -> None:
    """worker 交接时可恢复轮次带检查点回到队列由新 worker 接续，不产生终态"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    old_worker_id = "dev:worker:old"
    new_worker_id = "dev:worker:new"
    scheduler.register_worker(
        worker_id=old_worker_id,
        build_id="old",
        capabilities=["agent_session:codex"],
        pid=1,
    )
    scheduler.register_worker(
        worker_id=new_worker_id,
        build_id="new",
        capabilities=["agent_session:codex"],
        pid=2,
        state="standby",
    )
    request = ExecutionRequest.new(
        actor="sample_peer",
        prompt="continue the same accepted turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(
            mode="stateless",
            backend_route_id="zero",
        ),
    )
    scheduler.submit(request)
    claim = scheduler.claim(
        worker_id=old_worker_id,
        capabilities=["agent_session:codex"],
    )
    scheduler.start(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=claim["claim_token"],
    )
    scheduler.checkpoint(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=claim["claim_token"],
        checkpoint={
            "resumable": True,
            "backend_session_id": "thread-existing",
            "turn_id": "turn-existing",
        },
    )
    scheduler.activate_worker(
        new_worker_id,
        previous_worker_id=old_worker_id,
    )

    handover = scheduler.handover_worker(old_worker_id)
    resumed = scheduler.claim(
        worker_id=new_worker_id,
        capabilities=["agent_session:codex"],
    )

    assert handover["requeued_execution_ids"] == [request.execution_id]
    assert handover["blocked_execution_ids"] == []
    assert resumed["execution"]["execution_id"] == request.execution_id
    assert resumed["execution"]["checkpoint"]["turn_id"] == "turn-existing"
    assert resumed["execution"]["checkpoint"]["turn_start_reason"] == "backend_service_update"
    snapshot = scheduler.snapshot(request.execution_id)
    assert snapshot["status"] == "claimed"
    assert all(
        event["type"] not in {"failed", "cancelled"}
        for event in snapshot["events"]
    )
    with pytest.raises(PermissionError):
        scheduler.finish(
            ExecutionResult(
                execution_id=request.execution_id,
                status="completed",
                output={"message": "stale"},
            ),
            worker_id=old_worker_id,
            claim_token=claim["claim_token"],
        )


def test_draining_worker_cannot_receive_new_input_before_handover(
    tmp_path: Path,
) -> None:
    """交接中的旧 worker 只收到交接请求，追加输入留给接手的新 worker"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    old_worker_id = "dev:worker:old-input"
    new_worker_id = "dev:worker:new-input"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(
        worker_id=old_worker_id,
        build_id="old",
        capabilities=capabilities,
    )
    scheduler.register_worker(
        worker_id=new_worker_id,
        build_id="new",
        capabilities=capabilities,
        state="standby",
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="start on the old generation",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(mode="stateless", backend_route_id="zero"),
    )
    scheduler.submit(request)
    old_claim = scheduler.claim(
        worker_id=old_worker_id,
        capabilities=capabilities,
    )
    scheduler.start(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=old_claim["claim_token"],
    )
    scheduler.checkpoint(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=old_claim["claim_token"],
        checkpoint={
            "resumable": True,
            "backend_session_id": "thread-input",
            "turn_id": "turn-input",
        },
    )
    scheduler.activate_worker(
        new_worker_id,
        previous_worker_id=old_worker_id,
    )
    control = ExecutionInputControl.new(
        execution_id=request.execution_id,
        actor="sample_admin",
        input=[{"type": "text", "text": "send this to the latest worker"}],
        expected_turn_id="turn-input",
    )
    scheduler.submit_input_control(control)

    old_control = scheduler.wait_for_worker_control(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=old_claim["claim_token"],
        timeout_seconds=0.1,
    )
    assert old_control["handover_requested"] is True
    assert "input_controls" not in old_control

    scheduler.handover_worker(old_worker_id)
    new_claim = scheduler.claim(
        worker_id=new_worker_id,
        capabilities=capabilities,
    )
    new_control = scheduler.wait_for_worker_control(
        request.execution_id,
        worker_id=new_worker_id,
        claim_token=new_claim["claim_token"],
        timeout_seconds=0.1,
    )
    assert new_control["handover_requested"] is False
    assert [item["control_id"] for item in new_control["turn_controls"]] == [
        control.control_id
    ]


def test_draining_worker_requests_handover_and_keeps_claim_until_release(
    tmp_path: Path,
) -> None:
    """draining 的 worker 收到交接请求但保留 claim，不能认领新执行"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    old_worker_id = "dev:worker:old-drain"
    new_worker_id = "dev:worker:new-drain"
    capabilities = ["agent_session:codex"]
    scheduler.register_worker(
        worker_id=old_worker_id,
        build_id="old",
        capabilities=capabilities,
    )
    scheduler.register_worker(
        worker_id=new_worker_id,
        build_id="new",
        capabilities=capabilities,
        state="standby",
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue on the next generation",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(mode="stateless", backend_route_id="zero"),
    )
    scheduler.submit(request)
    old_claim = scheduler.claim(
        worker_id=old_worker_id,
        capabilities=capabilities,
    )
    scheduler.start(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=old_claim["claim_token"],
    )
    scheduler.activate_worker(
        new_worker_id,
        previous_worker_id=old_worker_id,
    )

    control = scheduler.wait_for_worker_control(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=old_claim["claim_token"],
        timeout_seconds=0.01,
    )
    heartbeat = scheduler.heartbeat(
        request.execution_id,
        worker_id=old_worker_id,
        claim_token=old_claim["claim_token"],
    )

    assert control["handover_requested"] is True
    assert heartbeat["cancel_requested"] is False
    assert scheduler.claim(
        worker_id=old_worker_id,
        capabilities=capabilities,
    ) == {"claimed": False, "reason": "worker_not_active", "worker_state": "draining"}


def test_rollout_wakes_old_long_poll_without_allowing_a_new_claim(
    tmp_path: Path,
) -> None:
    """发布换代时旧 worker 的长轮询被唤醒但不能认领，新执行由新 worker 认领"""
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    old_worker_id = "dev:execution-worker:old-long-poll"
    new_worker_id = "dev:execution-worker:new-long-poll"
    capabilities = ["chat_completion"]
    scheduler.register_worker(
        worker_id=old_worker_id,
        build_id="old-build",
        capabilities=capabilities,
    )
    old_claim: dict[str, object] = {}

    def wait_as_old_worker() -> None:
        old_claim.update(
            scheduler.claim_wait(
                worker_id=old_worker_id,
                capabilities=capabilities,
                timeout_seconds=2,
            )
        )

    waiting = threading.Thread(target=wait_as_old_worker)
    waiting.start()
    scheduler.register_worker(
        worker_id=new_worker_id,
        build_id="new-build",
        capabilities=capabilities,
        state="standby",
    )
    scheduler.activate_worker(
        new_worker_id,
        previous_worker_id=old_worker_id,
    )
    request = ExecutionRequest.new(
        actor="rollout-race-test",
        prompt="must be claimed by the replacement",
        service_type="chat_completion",
        provider="openai",
        operation="generate",
    )
    scheduler.submit(request)
    waiting.join(timeout=1)

    assert not waiting.is_alive()
    assert old_claim == {"claimed": False, "reason": "worker_not_active", "worker_state": "draining"}
    claimed = scheduler.claim(
        worker_id=new_worker_id,
        capabilities=capabilities,
    )
    assert claimed["claimed"] is True
    assert claimed["execution"]["execution_id"] == request.execution_id


def test_worker_interrupts_provider_after_handover_requested(
    tmp_path: Path,
) -> None:
    """worker 收到交接请求且 claim 已转移时中断 Provider，不发布结果"""
    provider_started = threading.Event()
    release_provider = threading.Event()
    ownership_changed = threading.Event()

    class BlockingBackend:
        provider_id = "codex"

        def __init__(self) -> None:
            self.interrupt_count = 0

        def execute(self, _invocation, _context):
            provider_started.set()
            assert release_provider.wait(3)
            return BackendResult(output={"message": "finished"})

        def interrupt(self, **_values):
            self.interrupt_count += 1
            return True

    class OwnershipChangingScheduler:
        def __init__(self) -> None:
            self.control_calls = 0
            self.finish_called = False

        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            self.control_calls += 1
            if self.control_calls == 1:
                raise ExecutionServiceUnavailable("service restarting")
            ownership_changed.set()
            return {"handover_requested": True}

        def heartbeat(self, *_args, **_kwargs):
            return {}

        def emit(self, *_args, **_kwargs):
            return None

        def checkpoint(self, *_args, **_kwargs):
            return {}

        def reassign_capacity(self, *_args, **_kwargs):
            return {}

        def requeue(self, *_args, **_kwargs):
            raise ExecutionClaimOwnershipLost(
                "execution claim ownership changed"
            )

        def finish(self, *_args, **_kwargs):
            self.finish_called = True
            raise AssertionError("a fenced Worker must not publish a result")

    backend = BlockingBackend()
    scheduler = OwnershipChangingScheduler()
    runtime = ExecutionRuntime(
        agent_providers={"codex": backend},
        agent_data_projector=AgentDataProjector(
            AgentDataStore()
        ),
        state_root=tmp_path / "execution-state",
    )
    worker = ExecutionWorker(
        build_id="old-build",
        worker_id="explore-1:old-worker",
        scheduler=scheduler,
        runtime=runtime,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="keep the accepted provider turn alive",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
    )
    result: dict[str, object] = {}

    runner = threading.Thread(
        target=lambda: result.update(
            worker.execute_claim(
                {
                    "claim_token": "old-claim-token",
                    "execution": {
                        "execution_id": request.execution_id,
                        "request": request.to_payload(),
                        "checkpoint": {},
                    },
                },
                lease_seconds=15,
            )
        )
    )
    runner.start()
    assert provider_started.wait(1)
    assert ownership_changed.wait(2)
    release_provider.set()
    runner.join(timeout=3)

    assert not runner.is_alive()
    assert result["status"] == "claim_lost"
    assert backend.interrupt_count == 1
    assert scheduler.finish_called is False


def test_worker_shutdown_fences_provider_without_publishing_cancelled(
    tmp_path: Path,
) -> None:
    """worker 关闭时中断 Provider 并放弃 claim，不发布 cancelled 结果"""
    provider_started = threading.Event()
    release_provider = threading.Event()
    shutdown = threading.Event()

    class BlockingBackend:
        provider_id = "codex"

        def __init__(self) -> None:
            self.interrupt_count = 0

        def execute(self, _invocation, _context):
            provider_started.set()
            assert release_provider.wait(2)
            return BackendResult(output={"message": "provider terminal"})

        def interrupt(self, **_values):
            self.interrupt_count += 1
            return True

    class SchedulerStub:
        def __init__(self) -> None:
            self.finish_called = False
            self.finished = None

        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            time.sleep(0.05)
            return {"cancel_requested": False}

        def heartbeat(self, *_args, **_kwargs):
            return {"cancel_requested": False}

        def emit(self, *_args, **_kwargs):
            return None

        def checkpoint(self, *_args, **_kwargs):
            return {}

        def reassign_capacity(self, *_args, **_kwargs):
            return {}

        def requeue(self, *_args, **_kwargs):
            return {"status": "queued"}

        def finish(self, result, *_args, **_kwargs):
            self.finish_called = True
            self.finished = result
            return {"status": result.status}

    scheduler = SchedulerStub()
    backend = BlockingBackend()
    runtime = ExecutionRuntime(
        agent_providers={"codex": backend},
        state_root=tmp_path / "execution-state",
    )
    complete_projection = mock.Mock(
        wraps=runtime.agent_data_projector.complete
    )
    runtime.agent_data_projector.complete = complete_projection
    worker = ExecutionWorker(
        build_id="old-build",
        worker_id="dev:old-worker",
        scheduler=scheduler,
        runtime=runtime,
        shutdown_requested=shutdown.is_set,
    )
    request = ExecutionRequest.new(
        actor="sample_peer",
        prompt="keep the accepted turn recoverable",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
        session=SessionOptions(
            mode="stateless",
            backend_route_id="zero",
        ),
    )
    result: dict[str, object] = {}
    runner = threading.Thread(
        target=lambda: result.update(worker.execute_claim({
            "claim_token": "old-claim",
            "execution": {
                "execution_id": request.execution_id,
                "request": request.to_payload(),
                "checkpoint": {
                    "resumable": True,
                    "provider": "codex",
                    "backend_session_id": "thread-existing",
                    "backend_route_id": "zero",
                    "turn_id": "turn-existing",
                },
            },
        }))
    )

    runner.start()
    assert provider_started.wait(1), scheduler.finished
    shutdown.set()
    time.sleep(0.1)
    release_provider.set()
    runner.join(timeout=2)

    assert not runner.is_alive()
    assert result["status"] == "claim_lost"
    assert scheduler.finish_called is False
    assert backend.interrupt_count == 1
    complete_projection.assert_not_called()


def test_worker_self_fences_when_control_plane_outage_exceeds_lease(
    tmp_path: Path,
) -> None:
    """控制面不可用超过租约时 worker 自行中断 Provider 并放弃 claim，不发布结果"""
    provider_started = threading.Event()

    class CancellableBackend:
        provider_id = "codex"

        def __init__(self) -> None:
            self.interrupt_count = 0

        def execute(self, _invocation, context):
            provider_started.set()
            while not context.is_cancelled():
                time.sleep(0.01)
            raise InterruptedError("worker lease expired")

        def interrupt(self, **_values):
            self.interrupt_count += 1
            return True

    class UnavailableScheduler:
        def __init__(self) -> None:
            self.finish_called = False

        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            raise ExecutionServiceUnavailable("service unavailable")

        def emit(self, *_args, **_kwargs):
            return None

        def checkpoint(self, *_args, **_kwargs):
            return {}

        def reassign_capacity(self, *_args, **_kwargs):
            return {}

        def finish(self, *_args, **_kwargs):
            self.finish_called = True
            raise AssertionError("a self-fenced Worker must not publish a result")

    backend = CancellableBackend()
    scheduler = UnavailableScheduler()
    runtime = ExecutionRuntime(
        agent_providers={"codex": backend},
        state_root=tmp_path / "execution-state",
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:self-fencing-worker",
        scheduler=scheduler,
        runtime=runtime,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="stop when the lease can no longer be renewed",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        system_instructions=portal_system_instructions("resident"),
    )
    result: dict[str, object] = {}
    runner = threading.Thread(
        target=lambda: result.update(
            worker.execute_claim(
                {
                    "claim_token": "claim-token",
                    "execution": {
                        "execution_id": request.execution_id,
                        "request": request.to_payload(),
                        "checkpoint": {},
                    },
                },
                lease_seconds=1,
            )
        )
    )

    runner.start()
    assert provider_started.wait(1)
    runner.join(timeout=3)

    assert not runner.is_alive()
    assert result["status"] == "claim_lost"
    assert backend.interrupt_count == 1
    assert scheduler.finish_called is False


from tests.cases.compatibility_test_support import setUpModule, tearDownModule
