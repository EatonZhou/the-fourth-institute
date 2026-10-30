"""ExecutionRuntime checkpoints and recovery: start activity, live context usage, reclaimed attempts,
lost claims and Provider recovery state.

:module: execution.service
:importance: P2
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.backend_contracts import BackendResult
from management_portal.execution.contracts import (
    ExecutionRecoveryDeferred,
    ExecutionRequest,
    SessionOptions,
)
from management_portal.execution.harness import AgentHarness
from management_portal.execution.runtime import (
    ExecutionRuntime,
    RuntimeContext,
)
from management_portal.execution.session_manager import RuntimeSession
from management_portal.execution.transport import ExecutionClaimOwnershipLost


@pytest.mark.parametrize(("reason", "expected_reason"), [
    ("", "provider_recovery"),
    ("backend_service_update", "backend_service_update"),
    ("context_compaction", "context_compaction"),
])
def test_execution_runtime_recovery_does_not_repeat_start_activity(
    tmp_path: Path, reason: str, expected_reason: str,
) -> None:
    """恢复执行时不重复发出启动类事件，并标明轮次开始原因"""
    class RecoveredProvider:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            _context.observe_provider_event({
                "method": "turn/started",
                "params": {
                    "threadId": "thread-recovered",
                    "turn": {"id": "turn-recovered", "status": "inProgress"},
                },
            })
            return BackendResult(
                output={"message": "done", "turn_id": "turn-recovered"},
                backend_session_id="thread-recovered",
                backend_route_id="one",
            )

        def interrupt(self, **_values):
            return True

    session = RuntimeSession(
        mode="persistent",
        binding_id="resident:recovered",
        backend_session_id="thread-recovered",
        backend_route_id="one",
        is_new_backend_session=False,
    )
    session_manager = mock.Mock()
    session_manager.prepare.return_value = session
    projector = mock.Mock()
    runtime = ExecutionRuntime(
        agent_providers={"codex": RecoveredProvider()},
        agent_harness=AgentHarness(()),
        session_manager=session_manager,
        agent_data_projector=projector,
        state_root=tmp_path,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:recovered",
            backend_route_id="one",
        ),
    )
    events: list[str] = []
    activities: list[dict] = []
    checkpoints: list[dict] = []

    def record_event(event_type, details):
        events.append(event_type)
        if event_type == "provider_event":
            activities.append(details)

    runtime.execute(
        request,
        RuntimeContext(
            recovery_checkpoint={
                "provider": "codex",
                "backend_session_id": "thread-recovered",
                "backend_route_id": "one",
                "turn_id": "turn-recovered",
                "resumable": True,
                "turn_start_reason": reason,
            },
            emit_event=record_event,
            checkpoint_provider=checkpoints.append,
        ),
    )

    assert "runtime_started" not in events
    assert "context_prepared" not in events
    assert "service_started" not in events
    assert "service_completed" in events
    assert activities[0]["turn_start_reason"] == expected_reason
    assert any(
        checkpoint.get("turn_start_reason") == "continuation"
        for checkpoint in checkpoints
    )


def test_execution_runtime_checkpoints_live_context_usage(
    tmp_path: Path,
) -> None:
    """ExecutionRuntime 把轮次中的实时上下文用量和窗口大小写入检查点"""
    class LiveUsageProvider:
        provider_id = "codex"

        def execute(self, _invocation, context):
            context.set_interrupt_target("thread-live", "turn-live", "one")
            context.observe_context_usage(
                {
                    "input_tokens": 153_000,
                    "cached_input_tokens": 149_000,
                    "total_tokens": 153_700,
                },
                258_400,
            )
            return BackendResult(
                output={"message": "done"},
                backend_session_id="thread-live",
                backend_route_id="one",
            )

        def interrupt(self, **_values):
            return True

    checkpoints: list[dict] = []
    runtime = ExecutionRuntime(
        agent_providers={"codex": LiveUsageProvider()},
        agent_harness=AgentHarness(()),
        state_root=tmp_path,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(mode="persistent", binding_id="resident:live"),
    )

    runtime.execute(
        request,
        RuntimeContext(
            checkpoint_provider=lambda checkpoint: checkpoints.append(
                checkpoint
            ),
        ),
    )

    live = next(
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.get("context_usage")
    )
    assert live["context_usage"]["input_tokens"] == 153_000
    assert live["model_context_window"] == 258_400
    assert live["context_usage_at"].endswith("Z")


def test_execution_runtime_serializes_reclaimed_local_attempt(
    tmp_path: Path,
) -> None:
    """同一执行在本地被重新认领时，新尝试等旧尝试交接结束后才调用 Provider"""
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    first_handover = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    class BlockingProvider:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            nonlocal call_count
            with call_lock:
                call_count += 1
                call_number = call_count
            if call_number == 1:
                first_started.set()
                assert release_first.wait(3)
            else:
                second_started.set()
            return BackendResult(
                output={"message": f"attempt {call_number}"},
                backend_session_id=f"thread-{call_number}",
                backend_route_id="zero",
            )

        def interrupt(self, **_values):
            release_first.set()
            return True

    store = AgentDataStore()
    runtime = ExecutionRuntime(
        agent_providers={"codex": BlockingProvider()},
        agent_harness=AgentHarness(()),
        agent_data_projector=AgentDataProjector(store),
        state_root=tmp_path / "runtime",
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue after reclaim",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(mode="stateless"),
    )
    results: list[BackendResult] = []
    errors: list[BaseException] = []

    def execute(context: RuntimeContext) -> None:
        try:
            results.append(runtime.execute(
                request,
                context,
            ))
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(
        target=execute,
        args=(RuntimeContext(
            accepted_at="2026-09-04T15:05:18.020Z",
            handover_requested=first_handover.is_set,
        ),),
    )
    second = threading.Thread(
        target=execute,
        args=(RuntimeContext(
            accepted_at="2026-09-04T15:05:18.020Z",
        ),),
    )
    first.start()
    assert first_started.wait(1)
    second.start()
    assert not second_started.wait(0.2)
    first_handover.set()
    release_first.set()
    first.join(3)
    second.join(3)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ExecutionRecoveryDeferred)
    assert second_started.is_set()
    assert len(results) == 1
    assert results[0].output["message"] == "attempt 2"


def test_execution_runtime_releases_projection_when_start_event_loses_claim(
    tmp_path: Path,
) -> None:
    """启动事件发现 claim 已丢失时释放投影，之后的重试可正常完成"""
    class CompletingProvider:
        provider_id = "codex"

        def execute(self, _invocation, _context):
            return BackendResult(
                output={"message": "recovered"},
                backend_session_id="thread-recovered",
                backend_route_id="zero",
            )

        def interrupt(self, **_values):
            return True

    store = AgentDataStore()
    runtime = ExecutionRuntime(
        agent_providers={"codex": CompletingProvider()},
        agent_harness=AgentHarness(()),
        agent_data_projector=AgentDataProjector(store),
        state_root=tmp_path / "runtime",
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="resume after start event claim loss",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(mode="stateless"),
    )
    accepted_at = "2026-09-04T15:05:18.020Z"
    ownership_lost = threading.Event()

    def lose_claim(*_args) -> None:
        ownership_lost.set()
        raise ExecutionClaimOwnershipLost("claim replaced")

    with pytest.raises(ExecutionClaimOwnershipLost):
        runtime.execute(
            request,
            RuntimeContext(
                accepted_at=accepted_at,
                emit_event=lose_claim,
                handover_requested=ownership_lost.is_set,
            ),
        )

    result = runtime.execute(
        request,
        RuntimeContext(accepted_at=accepted_at),
    )

    assert result.output["message"] == "recovered"


def test_execution_runtime_checkpoints_provider_recovery_state(
    tmp_path: Path,
) -> None:
    """Provider 设置的恢复状态写入执行检查点"""
    state = {
        "kind": "turn_and_continue",
        "phase": "ready_to_continue",
        "original_turn_id": "turn-before-compact",
        "continuation_prompt": "continue",
    }

    class TransitioningProvider:
        provider_id = "codex"

        def execute(self, _invocation, context):
            context.set_interrupt_target(
                "thread-transition",
                "turn-before-compact",
                "one",
            )
            context.set_provider_recovery_state(state)
            return BackendResult(
                output={"message": "done"},
                backend_session_id="thread-transition",
                backend_route_id="one",
            )

        def interrupt(self, **_values):
            return True

    checkpoints: list[dict] = []
    runtime = ExecutionRuntime(
        agent_providers={"codex": TransitioningProvider()},
        agent_harness=AgentHarness(()),
        state_root=tmp_path,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:transition",
        ),
    )

    runtime.execute(
        request,
        RuntimeContext(checkpoint_provider=checkpoints.append),
    )

    transition_checkpoint = next(
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.get("provider_recovery_state")
    )
    assert transition_checkpoint["provider_recovery_state"] == state


def test_execution_runtime_recovers_completed_provider_after_handover(
    tmp_path: Path,
) -> None:
    """Provider 完成后遇到 worker 交接时检查点保存最终结果，恢复时直接投影不再调用 Provider

    :module: execution.persistence
    :design: decision_b1ab45c4b3ec
    :design-point:
    """
    class CompletingProvider:
        provider_id = "codex"

        def __init__(self) -> None:
            self.execute_count = 0

        def execute(self, _invocation, context):
            self.execute_count += 1
            context.set_interrupt_target(
                "thread-completed",
                "turn-completed",
                "one",
            )
            return BackendResult(
                output={
                    "message": "durable final response",
                    "turn_id": "turn-completed",
                },
                backend_session_id="thread-completed",
                backend_route_id="one",
                usage={"total_tokens": 42},
            )

        def interrupt(self, **_values):
            return True

    provider = CompletingProvider()
    projector = mock.Mock()
    checkpoints: list[dict] = []
    runtime = ExecutionRuntime(
        agent_providers={"codex": provider},
        agent_harness=AgentHarness(()),
        agent_data_projector=projector,
        state_root=tmp_path,
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="finish across worker handover",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="stateless",
            backend_route_id="one",
        ),
    )

    with pytest.raises(ExecutionRecoveryDeferred):
        runtime.execute(
            request,
            RuntimeContext(
                checkpoint_provider=checkpoints.append,
                handover_requested=lambda: True,
            ),
        )

    terminal_checkpoint = checkpoints[-1]
    assert terminal_checkpoint["provider_completed"] is True
    assert terminal_checkpoint["output"]["message"] == (
        "durable final response"
    )
    projector.complete.assert_not_called()

    result = runtime.execute(
        request,
        RuntimeContext(recovery_checkpoint=terminal_checkpoint),
    )

    assert provider.execute_count == 1
    assert result.output["message"] == "durable final response"
    projector.complete.assert_called_once_with(
        execution_id=request.execution_id,
        backend_route_id="one",
        backend_session_id="thread-completed",
        turn_id="turn-completed",
        response="durable final response",
        usage={"total_tokens": 42},
    )


from tests.cases.compatibility_test_support import setUpModule, tearDownModule
