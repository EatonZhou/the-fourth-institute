"""Codex turns that end without a final answer or are interrupted: continuation policy and limits,
interrupt and Goal pause.

:module: execution.service
:importance: P2
"""
from __future__ import annotations

import threading
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.codex_app_server_protocol import (
    CodexCompactionInProgress,
    CodexThreadWriterBusy,
    CodexTurnContinuationRequired,
    CodexTurnInterrupted,
    CodexTurnRetryableFailure,
)
from management_portal.execution import (
    backend_contracts,
    codex_backend,
    codex_turn_run,
    provider_continuation,
)
from management_portal.execution.backend_contracts import BackendInvocation
from management_portal.execution.contracts import (
    ExecutionRecoveryDeferred,
    ExecutionRequest,
    SessionOptions,
)
from management_portal.execution.harness import ProviderErrorRecoveryPolicy
from management_portal.execution.session_manager import RuntimeSession


@pytest.fixture
def recorded_codex_input(monkeypatch):
    """Recovery-policy cases start after Codex has recorded the original input.

    Delivery absence/uncertainty and resend behavior are exercised separately
    against real rollout fixtures in test_codex_input_recovery.py.
    """
    monkeypatch.setattr(
        codex_turn_run, "read_codex_turn_input_recorded", lambda *_args, **_kwargs: True,
    )


@pytest.fixture
def no_continue_pause(monkeypatch):
    """Continue at once: these tests are about whether a turn continues, not when."""
    monkeypatch.setattr(
        "management_portal.execution.provider_continuation.wait_before_continuation",
        lambda _policy, context: not context.is_cancelled(),
    )


@pytest.mark.parametrize(("reason", "start_reason"), [
    ("missing_final_response", "provider_recovery"),
 ])
@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_backend_continues_provider_turn_without_final_response(
    reason: str, start_reason: str,
) -> None:
    """codex 轮次没有最终回答时发送续接提示继续，并记录 provider_recovery 开始原因"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-000000000482"
    client.run_turn.side_effect = [
        CodexTurnContinuationRequired(
            thread_id,
            "turn-without-final",
            reason,
        ),
        mock.Mock(
            thread_id=thread_id,
            turn_id="turn-after-continuation",
            response="done",
            token_usage={},
        ),
    ]
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
    )
    recovery_states: list[dict] = []
    start_reasons: list[str] = []
    result = codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id=thread_id,
                backend_route_id="one",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        backend_contracts.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
            set_provider_recovery_state=recovery_states.append,
            turn_start_reason_changed=start_reasons.append,
        ),
    )

    assert result.output["message"] == "done"
    assert client.run_turn.call_count == 2
    assert client.run_turn.call_args_list[1].args[1] == (
        provider_continuation.INTERRUPTED_TURN_CONTINUATION
    )
    reasons = [state["reason"] for state in recovery_states if "reason" in state]
    assert reasons and set(reasons) == {reason}
    assert start_reasons == [start_reason]


def test_codex_interrupt_reaches_turn_before_blocking_goal_pause() -> None:
    """codex 中断先送达轮次，不被阻塞的 Goal 暂停请求拖延"""
    from concurrent.futures import ThreadPoolExecutor

    interrupted = threading.Event()
    pausing = threading.Event()
    release = threading.Event()
    client = mock.Mock()
    client.interrupt_turn.side_effect = lambda *_: interrupted.set() or {}

    def pause(method, params, **kwargs):
        assert method == "thread/goal/set"
        assert params == {"threadId": "thread-stop", "status": "paused"}
        pausing.set()
        release.wait(5)
        return {}

    client.request.side_effect = pause
    backend = mock.Mock()
    backend.client_factory.return_value = client
    with ThreadPoolExecutor(max_workers=1) as executor:
        stopped = executor.submit(
            codex_backend.CodexBackend.interrupt, backend,
            backend_session_id="thread-stop", turn_id="turn-stop",
            backend_route_id="route-stop", runtime_options={"goal": {"objective": "finish"}},
        )
        try:
            assert pausing.wait(1)
            assert interrupted.is_set(), "Goal pause must not delay turn/interrupt"
        finally:
            release.set()
        assert stopped.result(timeout=1) is True
    client.interrupt_turn.assert_called_once_with("thread-stop", "turn-stop")


def test_codex_interrupt_preserves_goal_pause_semantics(subtests, isolated_portal_case):
    """codex 中断在有轮次时中断轮次、有 Goal 时暂停 Goal，两者都未成功时返回失败"""
    for turn_id, goal, pause_fails, expected in [
    ("turn-stop", None, False, True),
    ("turn-stop", {}, True, True),
    ("", {}, False, True),
    ("", {}, True, False),
    ("", None, False, False),
]:
        with subtests.test(turn_id=turn_id, goal=goal, pause_fails=pause_fails, expected=expected), isolated_portal_case() as case_path:
            client = mock.Mock()
            if pause_fails:
                client.request.side_effect = TimeoutError("Goal API unavailable")
            backend = mock.Mock()
            backend.client_factory.return_value = client
            result = codex_backend.CodexBackend.interrupt(
                backend, backend_session_id="thread-stop", turn_id=turn_id,
                backend_route_id="route-stop", runtime_options={"goal": goal},
            )
            assert result is expected
            assert client.interrupt_turn.call_count == bool(turn_id)
            assert client.request.call_count == isinstance(goal, dict)


@pytest.mark.usefixtures("no_continue_pause")
@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_interrupt_attempts_goal_pause_even_if_turn_request_fails_contracts(subtests, isolated_portal_case):
    """codex 轮次中断失败仍尝试暂停 Goal，线程被占用或正在压缩时延后重试

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_codex_interrupt_attempts_goal_pause_even_if_turn_request_fails'), isolated_portal_case() as case_path:
        client = mock.Mock()
        client.interrupt_turn.side_effect = RuntimeError("turn interrupt failed")
        backend = mock.Mock()
        backend.client_factory.return_value = client
        with pytest.raises(RuntimeError, match="turn interrupt failed"):
            codex_backend.CodexBackend.interrupt(
                backend, backend_session_id="thread-stop", turn_id="turn-stop",
                backend_route_id="route-stop", runtime_options={"goal": {}},
            )
        client.request.assert_called_once_with(
            "thread/goal/set", {"threadId": "thread-stop", "status": "paused"}, timeout=30.0,
        )
    with subtests.test(contract='test_codex_backend_continues_after_unexplained_provider_interruption'), isolated_portal_case() as case_path:
        client = mock.Mock()
        thread_id = "019f0000-0000-7000-8000-000000000487"
        client.run_turn.side_effect = [
            CodexTurnInterrupted(thread_id, "turn-interrupted"),
            mock.Mock(
                thread_id=thread_id,
                turn_id="turn-after-interruption",
                response="recovered",
                token_usage={},
            ),
        ]
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd="/tmp",
            session=SessionOptions(
                mode="persistent",
                binding_id="provider:interruption-recovery",
                backend_route_id="one",
            ),
        )

        result = codex_backend.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
                provider_error_recovery=ProviderErrorRecoveryPolicy(),
            ),
            backend_contracts.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )

        assert result.output["message"] == "recovered"
        assert client.run_turn.call_count == 2
        assert client.run_turn.call_args_list[1].args[1] == "continue"
    with subtests.test(contract='test_codex_backend_does_not_continue_interruption_without_harness_policy'), isolated_portal_case() as case_path:
        client = mock.Mock()
        thread_id = "019f0000-0000-7000-8000-000000000488"
        client.run_turn.side_effect = CodexTurnInterrupted(
            thread_id,
            "turn-interrupted",
        )
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="work",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd="/tmp",
            session=SessionOptions(
                mode="persistent",
                binding_id="provider:no-interruption-recovery",
                backend_route_id="one",
            ),
        )

        with pytest.raises(CodexTurnInterrupted):
            codex_backend.CodexBackend(
                client_factory=lambda _socket_path: client,
            ).execute(
                BackendInvocation(
                    request=request,
                    session=RuntimeSession(
                        mode="persistent",
                        binding_id=request.session.binding_id,
                        backend_session_id=thread_id,
                        backend_route_id="one",
                        is_new_backend_session=False,
                    ),
                    user_prompt=request.prompt,
                    system_context="",
                    attach_system_context=False,
                    is_new_backend_session=False,
                ),
                backend_contracts.BackendContext(
                    emit_event=lambda *_args: None,
                    cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None,
                ),
            )

        assert client.run_turn.call_count == 1
    with subtests.test(contract='test_codex_backend_bounds_repeated_turns_without_final_response'), isolated_portal_case() as case_path:
        client = mock.Mock()
        thread_id = "019f0000-0000-7000-8000-000000000483"
        client.run_turn.side_effect = [
            CodexTurnContinuationRequired(
                thread_id,
                f"turn-without-final-{index}",
                "missing_final_response",
            )
            for index in range(3)
        ] + [RuntimeError("unbounded continuation guard")]
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd="/tmp",
            session=SessionOptions(
                mode="persistent",
                binding_id="provider:test-session",
                backend_route_id="one",
            ),
        )

        with pytest.raises(RuntimeError, match="continuation limit"):
            codex_backend.CodexBackend(
                client_factory=lambda _socket_path: client,
            ).execute(
                BackendInvocation(
                    request=request,
                    session=RuntimeSession(
                        mode="persistent",
                        binding_id="",
                        backend_session_id=thread_id,
                        backend_route_id="one",
                        is_new_backend_session=False,
                    ),
                    user_prompt=request.prompt,
                    system_context="",
                    attach_system_context=False,
                    is_new_backend_session=False,
                ),
                backend_contracts.BackendContext(
                    emit_event=lambda *_args: None,
                    cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None,
                ),
            )

        assert client.run_turn.call_count == 3
    with subtests.test(contract='test_codex_backend_defers_thread_owned_by_another_process'), isolated_portal_case() as case_path:
        client = mock.Mock()
        thread_id = "019f0000-0000-7000-8000-000000000486"
        client.run_turn.side_effect = CodexThreadWriterBusy(
            f"thread {thread_id} already has an active writer"
        )
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue when the terminal releases the session",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd="/tmp",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:writer-busy",
                backend_route_id="zero",
            ),
        )
        recovery_states: list[dict] = []

        with pytest.raises(ExecutionRecoveryDeferred) as raised:
            codex_backend.CodexBackend(
                client_factory=lambda _socket_path: client,
            ).execute(
                BackendInvocation(
                    request=request,
                    session=RuntimeSession(
                        mode="persistent",
                        binding_id=request.session.binding_id,
                        backend_session_id=thread_id,
                        backend_route_id="zero",
                        is_new_backend_session=False,
                    ),
                    user_prompt=request.prompt,
                    system_context="",
                    attach_system_context=False,
                    is_new_backend_session=False,
                ),
                backend_contracts.BackendContext(
                    emit_event=lambda *_args: None,
                    cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None,
                    set_provider_recovery_state=recovery_states.append,
                ),
            )

        assert raised.value.retry_after_seconds == 30
        assert recovery_states[-1] == {
            "kind": "thread_writer_busy",
            "thread_id": thread_id,

            "provider_error_continue_count": 0,
            "original_input_recorded": False,
            "recovery_budget": {"attempts": 1, "turn_id": "", "completed_item_ids": []},
        }
    with subtests.test(contract='test_codex_backend_defers_provider_compaction_without_failing_task'), isolated_portal_case() as case_path:
        client = mock.Mock()
        thread_id = "019f0000-0000-7000-8000-000000000487"
        client.run_turn.side_effect = CodexCompactionInProgress(
            "Codex context compaction is still active"
        )
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="continue after provider compaction",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd="/tmp",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:compaction-in-progress",
                backend_route_id="zero",
            ),
        )
        recovery_states: list[dict] = []

        with pytest.raises(ExecutionRecoveryDeferred) as raised:
            codex_backend.CodexBackend(
                client_factory=lambda _socket_path: client,
            ).execute(
                BackendInvocation(
                    request=request,
                    session=RuntimeSession(
                        mode="persistent",
                        binding_id=request.session.binding_id,
                        backend_session_id=thread_id,
                        backend_route_id="zero",
                        is_new_backend_session=False,
                    ),
                    user_prompt=request.prompt,
                    system_context="",
                    attach_system_context=False,
                    is_new_backend_session=False,
                ),
                backend_contracts.BackendContext(
                    emit_event=lambda *_args: None,
                    cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None,
                    set_provider_recovery_state=recovery_states.append,
                ),
            )

        assert raised.value.retry_after_seconds == 5
        assert recovery_states[-1] == {
            "kind": "thread_writer_busy",
            "thread_id": thread_id,

            "provider_error_continue_count": 0,
            "original_input_recorded": False,
            "recovery_budget": {"attempts": 0, "turn_id": "", "completed_item_ids": []},
        }


@pytest.mark.usefixtures("no_continue_pause")
@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_backend_continues_typed_interruption_with_provider_detail(
) -> None:
    """带 Provider 错误详情的 codex 中断按 Harness 策略发送 continue 续接"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-00000000048a"
    client.run_turn.side_effect = [
        CodexTurnInterrupted(
            thread_id,
            "turn-interrupted",
            "provider internal error",
        ),
        mock.Mock(
            thread_id=thread_id,
            turn_id="turn-after-interruption",
            response="recovered",
            token_usage={},
        ),
    ]
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="work",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:detailed-interruption-recovery",
            backend_route_id="one",
        ),
    )

    result = codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id=request.session.binding_id,
                backend_session_id=thread_id,
                backend_route_id="one",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
            provider_error_recovery=ProviderErrorRecoveryPolicy(),
        ),
        backend_contracts.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert result.output["message"] == "recovered"
    assert client.run_turn.call_count == 2
    assert client.run_turn.call_args_list[1].args[1] == "continue"


@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_backend_continues_capacity_failure_once() -> None:
    """codex 因模型容量不足失败后等待 3 秒发送一次 continue，并记录续接事件"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-00000000048c"
    client.run_turn.side_effect = [
        CodexTurnRetryableFailure(
            thread_id,
            "turn-capacity",
            "Selected model is at capacity. Please try a different model.",
            "capacity",
        ),
        mock.Mock(
            thread_id=thread_id,
            turn_id="turn-after-capacity",
            response="recovered",
            token_usage={},
        ),
    ]
    events: list[tuple[str, dict]] = []
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="work",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:capacity-recovery",
            backend_route_id="one",
        ),
    )

    sleep = mock.Mock()
    with mock.patch.object(provider_continuation.time, "sleep", sleep):
        result = codex_backend.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
                provider_error_recovery=ProviderErrorRecoveryPolicy(),
            ),
            backend_contracts.BackendContext(
                emit_event=lambda kind, payload: events.append((kind, payload)),
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )

    assert result.output["message"] == "recovered"
    assert client.run_turn.call_count == 2
    # The continuation went out three seconds after the failure.
    assert sum(call.args[0] for call in sleep.call_args_list) == 3.0
    assert client.run_turn.call_args_list[1].args[1] == "continue"
    assert events[0] == (
        "agent_harness_provider_error_continue",
        {"attempt": 1, "max_attempts": 1, "provider": "codex",
         "reason": "provider_error",
         "error": "Codex turn failed: Selected model is at capacity. Please try a different model."},
    )
    # The continuation is recorded as it goes out.
    assert [(kind, details["reason"], details["prompt"], details["input_delivery"]) for kind, details in events[1:]] == [
        ("recovery_input_sent", "provider_error", "continue", "continued"),
    ]


def test_codex_backend_does_not_continue_cancelled_interruption() -> None:
    """用户取消导致的 codex 中断不自动续接，以取消结束"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-00000000048b"
    cancelled = False

    def interrupt_turn(*_args, **_kwargs):
        nonlocal cancelled
        cancelled = True
        raise CodexTurnInterrupted(thread_id, "turn-cancelled", "cancelled")

    client.run_turn.side_effect = interrupt_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="work",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:cancelled-interruption",
            backend_route_id="one",
        ),
    )

    with pytest.raises(InterruptedError, match="cancelled"):
        codex_backend.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
                provider_error_recovery=ProviderErrorRecoveryPolicy(),
            ),
            backend_contracts.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: cancelled,
                set_interrupt_target=lambda *_args: None,
            ),
        )

    assert client.run_turn.call_count == 1


@pytest.mark.usefixtures("no_continue_pause")
@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_backend_continues_unexplained_interruption_only_once() -> None:
    """原因不明的 codex 中断只自动续接一次，再次中断时延后恢复"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-000000000489"
    client.run_turn.side_effect = [
        CodexTurnInterrupted(thread_id, "turn-interrupted-1"),
        CodexTurnInterrupted(thread_id, "turn-interrupted-2"),
        RuntimeError("automatic continue was not bounded"),
    ]
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="work",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:bounded-interruption-recovery",
            backend_route_id="one",
        ),
    )

    recovery_states: list[dict] = []
    with pytest.raises(ExecutionRecoveryDeferred) as raised:
        codex_backend.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
                provider_error_recovery=ProviderErrorRecoveryPolicy(),
            ),
            backend_contracts.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
                set_provider_recovery_state=recovery_states.append,
            ),
        )

    assert client.run_turn.call_count == 2
    assert client.run_turn.call_args_list[1].args[1] == "continue"
    assert raised.value.retry_after_seconds == 5
    assert recovery_states[-1]["phase"] == "ready_to_continue"
    assert recovery_states[-1]["original_turn_id"] == "turn-interrupted-2"
    assert recovery_states[-1]["provider_error_continue_count"] == 1
    assert recovery_states[-1]["recovery_budget"]["attempts"] == 2


@pytest.mark.usefixtures("no_continue_pause")
@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_backend_bounds_repeated_provider_system_errors() -> None:
    """codex 连续出现 Provider 系统错误时自动续接有次数上限，超出后延后恢复"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-000000000485"
    client.run_turn.side_effect = [
        CodexTurnContinuationRequired(
            thread_id,
            f"turn-system-error-{index}",
            "provider_system_error",
        )
        for index in range(3)
    ]
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:system-error-recovery",
            backend_route_id="one",
        ),
    )
    recovery_states: list[dict] = []

    with pytest.raises(ExecutionRecoveryDeferred) as raised:
        codex_backend.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
                provider_error_recovery=ProviderErrorRecoveryPolicy(),
            ),
            backend_contracts.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
                set_provider_recovery_state=recovery_states.append,
            ),
        )

    assert client.run_turn.call_count == 3
    assert client.run_turn.call_args_list[1].args[1] == "continue"
    assert raised.value.retry_after_seconds == 5
    assert recovery_states[-1]["phase"] == "ready_to_continue"
    assert recovery_states[-1]["transition_count"] == 3
    assert recovery_states[-1]["provider_error_continue_count"] == 1
    assert recovery_states[-1]["recovery_budget"]["attempts"] == 3


def test_codex_backend_does_not_require_new_compaction_for_replayed_item() -> None:
    """恢复时回放的历史压缩事件不要求生成新的压缩记录"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-000000000488"

    def resume_turn(*_args: object, **kwargs: object) -> object:
        callback = kwargs["event_callback"]
        assert callable(callback)
        callback({
            "method": "item/completed",
            "params": {
                "threadId": thread_id,
                "turnId": "historical-compaction-turn",
                "item": {"type": "contextCompaction"},
            },
        })
        return mock.Mock(
            thread_id=thread_id,
            turn_id="recovered-successor-turn",
            response="recovered result",
            token_usage={},
        )

    client.resume_turn.side_effect = resume_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:replayed-compaction",
            backend_route_id="zero",
        ),
    )
    backend = codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
    )
    existing = mock.Mock(fingerprint="already-committed-compaction")
    backend._latest_compaction = mock.Mock(return_value=existing)

    def record_new_compaction(**kwargs: object) -> None:
        if kwargs["required"]:
            raise RuntimeError(
                "Codex compact completed without a committed replacement context"
            )

    backend._record_new_compaction = mock.Mock(
        side_effect=record_new_compaction,
    )

    result = backend.execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id=request.session.binding_id,
                backend_session_id=thread_id,
                backend_route_id="zero",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
            recovery_checkpoint={"turn_id": "historical-compaction-turn"},
        ),
        backend_contracts.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert result.output["message"] == "recovered result"
    assert backend._record_new_compaction.call_args.kwargs["required"] is False


@pytest.mark.usefixtures("recorded_codex_input")
def test_codex_backend_recovery_uses_the_same_continuation_limit() -> None:
    """恢复中的 codex 轮次缺少最终回答时沿用同一续接次数上限"""
    client = mock.Mock()
    thread_id = "019f0000-0000-7000-8000-000000000484"
    client.resume_turn.side_effect = CodexTurnContinuationRequired(
        thread_id,
        "recovered-turn-without-final",
        "missing_final_response",
    )
    client.run_turn.side_effect = [
        CodexTurnContinuationRequired(
            thread_id,
            f"recovery-continuation-{index}",
            "missing_final_response",
        )
        for index in range(2)
    ] + [RuntimeError("unbounded recovery guard")]
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
    )

    with pytest.raises(RuntimeError, match="continuation limit"):
        codex_backend.CodexBackend(
            client_factory=lambda _socket_path: client,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id="",
                    backend_session_id=thread_id,
                    backend_route_id="one",
                    is_new_backend_session=False,
                ),
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
                recovery_checkpoint={"turn_id": "accepted-turn"},
            ),
            backend_contracts.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )

    assert client.resume_turn.call_count == 1
    assert client.run_turn.call_count == 2


from tests.cases.compatibility_test_support import setUpModule, tearDownModule
