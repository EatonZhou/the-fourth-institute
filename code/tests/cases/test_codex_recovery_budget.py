from __future__ import annotations

import copy
import json
import time
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest

from management_portal.codex_app_server_client import (
    CodexAppServerUnavailable,
    CodexThreadWriterBusy,
    CodexTurnAuthenticationError,
    CodexTurnConnectionLost,
    CodexTurnContinuationRequired,
    CodexTurnInterrupted,
    CodexTurnRetryableFailure,
    CodexTurnSafetyBlocked,
    CodexTurnResult,
)
from management_portal.execution.backends import BackendContext, BackendInvocation, CodexBackend
from management_portal.execution.contracts import ExecutionRecoveryDeferred, ExecutionRequest, SessionOptions
from management_portal.execution.codex_recovery import CodexRecoveryBudget, CodexRecoveryExhausted
from management_portal.execution.codex_rollout_history import read_codex_turn_error
from management_portal.execution.harness import AgentHarness, ProviderErrorRecoveryPolicy
from management_portal.execution.harness_provider_error_continue import ProviderErrorContinueFeature
from management_portal.execution.runtime import ExecutionRuntime
from management_portal.execution.scheduler import Scheduler, SchedulerClient
from management_portal.execution.service import ExecutionServiceServer
from management_portal.execution.session_manager import RuntimeSession
from management_portal.execution.worker import ExecutionWorker


def _invocation() -> BackendInvocation:
    request = ExecutionRequest.new(
        actor="CASE-A", prompt="isolated recovery test", service_type="agent_session",
        provider="codex", operation="turn", cwd="/tmp",
        session=SessionOptions(mode="persistent", binding_id="test:recovery-budget", backend_route_id="one"),
    )
    return BackendInvocation(
        request=request,
        session=RuntimeSession(
            mode="persistent", binding_id=request.session.binding_id,
            backend_session_id="019f0000-0000-7000-8000-000000000991",
            backend_route_id="one", is_new_backend_session=False,
        ),
        user_prompt=request.prompt, system_context="", attach_system_context=False,
        is_new_backend_session=False,
        provider_error_recovery=ProviderErrorRecoveryPolicy(),
    )


@pytest.fixture
def no_continue_pause(monkeypatch):
    """Continue at once: these tests are about whether a turn continues, not when."""
    monkeypatch.setattr(
        "management_portal.execution.backends.wait_before_continuation",
        lambda _policy, context: not context.is_cancelled(),
    )
    # These outage-budget fixtures model turns that already consumed their input.
    monkeypatch.setattr("management_portal.execution.backends.read_codex_turn_input_recorded", lambda *_a, **_k: True)


def test_native_authentication_failure_prevents_continuation(tmp_path, subtests, isolated_portal_case):
    """codex 原生身份认证失败停止继续且不消耗恢复预算"""
    for reason in ["missing_final_response", "provider_system_error"]:
        with subtests.test(reason=reason), isolated_portal_case() as case_path:
            tmp_path = case_path
            invocation = _invocation()
            thread_id = invocation.session.backend_session_id
            turn_id = "turn-authentication-failed"
            message = (
                "Your access token could not be refreshed because your refresh token was revoked. "
                "Please log out and sign in again."
            )
            path = tmp_path / "rollout.jsonl"
            path.write_text("".join(json.dumps({"type": "event_msg", "payload": payload}) + "\n"
                                    for payload in [
                                        {"type": "task_started", "turn_id": turn_id},
                                        {"type": "task_complete", "turn_id": turn_id,
                                         "error": {"message": message, "codexErrorInfo": "unauthorized"}},
                                    ]), encoding="utf-8")
            client = mock.Mock()
            client.run_turn.side_effect = CodexTurnContinuationRequired(thread_id, turn_id, reason)
            backend = CodexBackend(client_factory=lambda _socket_path: client)
            saved = []
            with mock.patch.object(backend.account_registry, "find_thread_rollout", return_value=path):
                with pytest.raises(CodexTurnAuthenticationError, match="refresh token was revoked") as raised:
                    backend.execute(invocation, BackendContext(
                        emit_event=lambda *_args: None,
                        cancellation_requested=lambda: False,
                        set_interrupt_target=lambda *_args: None,
                        set_provider_recovery_state=saved.append,
                    ))
            assert raised.value.error_details["retryable"] is False
            assert raised.value.turn_id == turn_id
            assert client.run_turn.call_count == 1
            client.resume_turn.assert_not_called()
            assert not any(state.get("recovery_budget", {}).get("attempts") for state in saved)


@pytest.mark.usefixtures("no_continue_pause")
def test_native_capacity_error_retains_continuation_policy_contracts(tmp_path, subtests, isolated_portal_case):
    """Codex 容量错误保留继续策略

    Codex 原生容量错误要求继续时，执行保留错误后继续策略并在原线程完成。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_native_capacity_error_retains_continuation_policy'), isolated_portal_case() as case_path:
        tmp_path = case_path
        invocation = _invocation()
        thread_id = invocation.session.backend_session_id
        client = mock.Mock()
        client.run_turn.side_effect = [
            CodexTurnContinuationRequired(thread_id, "turn-capacity", "missing_final_response"),
            CodexTurnResult(thread_id=thread_id, turn_id="turn-success", response="finished",
                            turn={"id": "turn-success", "status": "completed"}, token_usage={}),
        ]
        backend = CodexBackend(client_factory=lambda _socket_path: client)
        with (
            mock.patch.object(backend.account_registry, "find_thread_rollout", return_value=tmp_path / "rollout"),
            mock.patch("management_portal.execution.backends.read_codex_turn_error", return_value={
                "message": "The selected model is at capacity", "codexErrorInfo": "serverOverloaded",
            }),
        ):
            result = backend.execute(invocation, BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ))
        assert result.output["message"] == "finished"
        assert client.run_turn.call_count == 2
    with subtests.test(contract='test_safety_block_is_persisted_without_worker_or_harness_retry'), isolated_portal_case() as case_path:
        tmp_path = case_path
        invocation = _invocation()
        request = replace(invocation.request, runtime_options={"agent_harness": {
            "mode": "manual", "features": {"provider_error_continue": {"enabled": True}},
        }})
        scheduler = Scheduler(
            dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0,
        )
        provider_client = mock.Mock()
        rejection = CodexTurnSafetyBlocked(
            invocation.session.backend_session_id, "turn-safety",
            "This request was blocked by our safety systems. Reason: Potentially unintended activity.",
        )

        def run_turn(_thread_id, _prompt, **kwargs):
            kwargs["turn_started_callback"](rejection.turn_id)
            raise rejection

        provider_client.run_turn.side_effect = run_turn
        sessions = mock.Mock()
        sessions.prepare.return_value = invocation.session
        runtime = ExecutionRuntime(
            agent_providers={"codex": CodexBackend(client_factory=lambda _socket: provider_client)},
            agent_harness=AgentHarness((ProviderErrorContinueFeature(),)),
            session_manager=sessions, agent_data_projector=mock.Mock(), state_root=tmp_path / "runtime",
        )
        with ExecutionServiceServer(tmp_path / "s", scheduler) as server:
            transport = mock.Mock()
            transport.request.side_effect = lambda method, params, **_kwargs: server.dispatch(method, params)
            scheduler_client = SchedulerClient(
                worker_id="dev:safety-test", build_id="test", capabilities=["agent_session:codex"], service_client=transport,
            )
            scheduler_client.register()
            scheduler.submit(request)
            claim = scheduler.claim(worker_id="dev:safety-test", capabilities=["agent_session:codex"])
            assert claim["claimed"]
            outcome = ExecutionWorker(
                build_id="test", worker_id="dev:safety-test", scheduler=scheduler_client, runtime=runtime,
            ).execute_claim(claim)
            assert outcome["status"] == "failed"
            result = scheduler.snapshot(request.execution_id)["result"]
            assert result["error_code"] == "provider_safety_blocked"
            assert result["error_details"]["retryable"] is False
            assert result["error_details"]["provider_reason"] == rejection.error_details["provider_reason"]
            provider_client.run_turn.assert_called_once()
            provider_client.resume_turn.assert_not_called()
            assert not scheduler.claim(worker_id="dev:safety-test", capabilities=["agent_session:codex"])["claimed"]


def test_native_terminal_error_requires_exact_complete_task(tmp_path, subtests, isolated_portal_case):
    """原生终态错误仅从精确匹配的完整任务读取"""
    for case in ["exact", "adjacent", "wrong_boundary", "missing_start", "partial"]:
        with subtests.test(case=case), isolated_portal_case() as case_path:
            tmp_path = case_path
            error = {"message": "authentication failed", "codexErrorInfo": "unauthorized"}
            start = {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "selected"}}
            terminal = {"type": "event_msg", "payload": {
                "type": "task_complete", "turn_id": "selected", "error": error,
            }}
            rows = [start, terminal]
            if case == "adjacent":
                rows = [start, {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "other"}},
                        {"type": "event_msg", "payload": {**terminal["payload"], "turn_id": "other"}}]
            elif case == "wrong_boundary":
                rows.insert(1, {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "other"}})
            elif case == "missing_start":
                rows = [terminal]
            path = tmp_path / "rollout.jsonl"
            text = "".join(json.dumps(row) + "\n" for row in rows)
            path.write_text(text.rstrip("\n") if case == "partial" else text, encoding="utf-8")
            assert read_codex_turn_error(path, "selected") == (error if case == "exact" else None)


@pytest.mark.usefixtures("no_continue_pause")
def test_recovery_budget_survives_requeues_and_only_new_work_resets_it(subtests, isolated_portal_case):
    """恢复预算跨重新排队保留且仅完成新工作后重置"""
    for failure_kind in ["system_error", "capacity", "interrupted"]:
        for progress_call in [None, 4]:
            with subtests.test(failure_kind=failure_kind, progress_call=progress_call), isolated_portal_case() as case_path:
                invocation = _invocation()
                checkpoint: dict = {}
                calls = []
                claims = 0
                client = mock.Mock()

                def run_turn(thread_id, _prompt, **kwargs):
                    number = len(calls) + 1
                    turn_id = f"budget-turn-{number}"
                    calls.append(turn_id)
                    checkpoint["turn_id"] = turn_id
                    kwargs["turn_started_callback"](turn_id)
                    kwargs["event_callback"]({
                        "method": "turn/started", "params": {"turn": {"id": turn_id}},
                    })
                    if number == progress_call:
                        kwargs["event_callback"]({
                            "method": "item/completed",
                            "params": {
                                "turnId": turn_id,
                                "item": {"id": "work-result", "type": "commandExecution", "status": "completed", "exitCode": 0},
                            },
                        })
                    if failure_kind == "capacity":
                        raise CodexTurnRetryableFailure(thread_id, turn_id, "Selected model is at capacity", "capacity")
                    if failure_kind == "interrupted":
                        raise CodexTurnInterrupted(thread_id, turn_id, "isolated interruption")
                    raise CodexTurnContinuationRequired(thread_id, turn_id, "provider_system_error")

                def save(state):
                    checkpoint["provider_recovery_state"] = copy.deepcopy(state)

                client.run_turn.side_effect = run_turn
                for _ in range(8):
                    claims += 1
                    # A fresh adapter instance receives only the persisted checkpoint.
                    adapter = CodexBackend(client_factory=lambda _socket: client)
                    try:
                        adapter.execute(
                            replace(invocation, recovery_checkpoint=copy.deepcopy(checkpoint)),
                            BackendContext(
                                emit_event=lambda *_args: None, cancellation_requested=lambda: False,
                                set_interrupt_target=lambda *_args: None, set_provider_recovery_state=save,
                            ),
                        )
                    except ExecutionRecoveryDeferred:
                        continue
                    except RuntimeError as error:
                        assert getattr(error, "error_code", "") == "provider_recovery_exhausted", str(error)
                        break
                    pytest.fail("provider unexpectedly succeeded")
                else:
                    pytest.fail("no-progress recovery budget was renewed on every queue claim")

                assert claims >= 2  # Not merely a single-call continuation-limit test.
                assert len(calls) == (7 if progress_call else 4)
                assert checkpoint["provider_recovery_state"]["recovery_budget"]["attempts"] == 3

    with subtests.test(failure_kind="writer_released"), isolated_portal_case():
        invocation = _invocation()
        client = mock.Mock()
        thread_id = invocation.session.backend_session_id
        client.run_turn.side_effect = [
            CodexThreadWriterBusy(f"thread {thread_id} already has an active writer"),
            CodexTurnResult(thread_id=thread_id, turn_id="released", response="finished",
                            turn={"id": "released", "status": "completed"}, token_usage={}),
        ]
        checkpoint = {}
        context = BackendContext(
            emit_event=lambda *_args: None, cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
            set_provider_recovery_state=lambda state: checkpoint.update(provider_recovery_state=copy.deepcopy(state)),
        )
        with pytest.raises(ExecutionRecoveryDeferred):
            CodexBackend(client_factory=lambda _socket: client).execute(invocation, context)
        result = CodexBackend(client_factory=lambda _socket: client).execute(
            replace(invocation, recovery_checkpoint=checkpoint), context,
        )
        assert result.output["message"] == "finished"
        assert client.run_turn.call_count == 2
        assert [call.args[1] for call in client.run_turn.call_args_list] == [invocation.user_prompt] * 2
        client.interrupt_turn.assert_not_called()
        client.resume_turn.assert_not_called()


def _completed(item: dict, turn_id: str = "current") -> dict:
    return {"method": "item/completed", "params": {"turnId": turn_id, "item": {"id": "work", **item}}}


def test_non_work_events_never_replenish_budget(subtests, isolated_portal_case):
    """非工作事件与失败结果不补充恢复预算"""
    for event in [
    {"method": "turn/started", "params": {"turn": {"id": "current"}}},
    {"method": "thread/status/changed", "params": {"status": "active"}},
    {"method": "item/agentMessage/delta", "params": {"delta": "not complete"}},
    _completed({"type": "reasoning", "text": "thinking"}),
    _completed({"type": "contextCompaction", "status": "completed"}),
    _completed({"type": "agentMessage", "text": "   "}),
    _completed({"type": "agentMessage", "text": "partial", "status": "failed"}),
    _completed({"type": "commandExecution", "status": "inProgress"}),
    _completed({"type": "commandExecution", "status": "completed", "exitCode": 1}),
    _completed({"type": "dynamicToolCall", "status": "completed", "success": False}),
    _completed({"type": "mcpToolCall", "status": "completed", "result": {"isError": True}}),
    _completed({"type": "agentMessage", "text": "old result"}, "old-turn"),
    _completed({"type": "agentMessage", "text": "missing turn"}, ""),
    _completed({"id": "", "type": "agentMessage", "text": "missing item"}),
    _completed({"type": [], "status": {}}),
    _completed({"type": "commandExecution", "status": "completed", "exitCode": {}}),
    {"method": "item/completed", "params": None},
]:
        with subtests.test(event=event), isolated_portal_case() as case_path:
            budget = CodexRecoveryBudget({})
            budget.turn_started("current")
            for _ in range(3):
                budget.reserve_retry("outage")
            assert budget.observe(event) is False
            with pytest.raises(CodexRecoveryExhausted):
                budget.reserve_retry("still down")


def test_new_work_resets_once_and_dedup_survives_restore(subtests, isolated_portal_case):
    """完成的新工作仅重置一次预算且恢复后仍去重"""
    for item in [
    {"type": "agentMessage", "text": "completed useful output"},
    {"type": "commandExecution", "status": "completed", "exitCode": 0},
    {"type": "fileChange", "status": "completed"},
    {"type": "mcpToolCall", "status": "completed", "result": {"isError": False}},
    {"type": "dynamicToolCall", "status": "completed", "success": True},
]:
        with subtests.test(item=item), isolated_portal_case() as case_path:
            budget = CodexRecoveryBudget({})
            budget.turn_started("current")
            budget.reserve_retry("outage")
            assert budget.observe(_completed(item)) is True
            assert budget.snapshot()["attempts"] == 0
            budget.reserve_retry("new outage")
            restored = CodexRecoveryBudget({"recovery_budget": budget.snapshot()})
            restored.turn_started("current")
            assert restored.observe(_completed(item)) is False
            assert restored.snapshot()["attempts"] == 1
            restored.turn_started("new-turn")
            assert restored.snapshot()["attempts"] == 1  # Started is not success.
            assert restored.observe(_completed(item)) is False  # Previous turn replay.
            assert restored.observe(_completed(item, "new-turn")) is True


def test_budget_checkpoint_contract(subtests, isolated_portal_case):
    """恢复预算检查点保留有效次数并将异常值视为耗尽"""
    for checkpoint, expected in [
    ({}, 0),
    ({"recovery_budget": {"attempts": 2, "completed_item_ids": None}}, 2),
    ({"recovery_budget": {"attempts": "invalid"}}, 3),
    ({"recovery_budget": {"attempts": -1}}, 3),
]:
        with subtests.test(checkpoint=checkpoint, expected=expected), isolated_portal_case() as case_path:
            assert CodexRecoveryBudget(checkpoint).snapshot()["attempts"] == expected


def test_healthy_work_is_deduplicated_without_requesting_extra_checkpoints_contracts(subtests, isolated_portal_case):
    """正常工作去重且不额外请求检查点

    已观察过的正常工作事件在恢复预算中去重，恢复后不会额外消耗尝试次数或请求检查点。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_healthy_work_is_deduplicated_without_requesting_extra_checkpoints'), isolated_portal_case() as case_path:
        budget = CodexRecoveryBudget({})
        budget.turn_started("current")
        event = _completed({"type": "commandExecution", "status": "completed", "exitCode": 0})
        assert budget.observe(event) is False
        budget.reserve_retry("new failure")
        restored = CodexRecoveryBudget({"recovery_budget": budget.snapshot()})
        assert restored.observe(event) is False
        assert restored.snapshot()["attempts"] == 1


def test_reconnect_loop_is_bounded(subtests, isolated_portal_case):
    """codex 重连达到时限后延后恢复并保留预算"""
    for failure in [TimeoutError("no response"), CodexAppServerUnavailable("offline")]:
        with subtests.test(failure=failure), isolated_portal_case() as case_path:
            invocation = _invocation()
            client = mock.Mock()
            client.run_turn.side_effect = CodexTurnConnectionLost(invocation.session.backend_session_id, "current")
            client.resume_turn.side_effect = failure
            states = []
            with mock.patch("management_portal.execution.backends.time.sleep"), mock.patch("management_portal.execution.backends.time.monotonic", side_effect=range(0, 10000, 30)), pytest.raises(ExecutionRecoveryDeferred):
                CodexBackend(client_factory=lambda _socket: client).execute(
                    invocation,
                    BackendContext(
                        emit_event=lambda *_args: None, cancellation_requested=lambda: False,
                        set_interrupt_target=lambda *_args: None, set_provider_recovery_state=lambda state: states.append(copy.deepcopy(state)),
                    ),
                )
            assert client.run_turn.call_count == 1
            assert client.resume_turn.call_count >= 3
            assert states[-1]["recovery_budget"]["attempts"] == 1




@pytest.mark.parametrize(("failure_kind", "progress_call"), [
    (kind, progress) for kind in ["system_error", "capacity"] for progress in [None, 4]
] + [("writer_busy", None)])
@pytest.mark.usefixtures("no_continue_pause")
def test_worker_requeue_budget(tmp_path: Path, failure_kind: str, progress_call: int | None) -> None:
    """Worker 重新排队保留恢复预算并在耗尽后终止

    Real Harness/Runtime/Worker/Scheduler + PostgreSQL, fake remote provider only.

    Dispatch is in-process to avoid a network dependency. The session fixture
    identifies an already-bound thread; each new claim gets a fresh adapter.
    """
    invocation = _invocation()
    request = replace(invocation.request, runtime_options={"agent_harness": {
        "mode": "manual", "features": {"provider_error_continue": {"enabled": True}},
    }})
    scheduler_options = {"dispatch_interval_min_seconds": 0, "dispatch_interval_max_seconds": 0}
    scheduler = Scheduler(**scheduler_options)
    provider_client = mock.Mock()
    calls = []

    def run_turn(thread_id, _prompt, **kwargs):
        number = len(calls) + 1
        turn_id = f"work-{number}"
        calls.append(turn_id)
        if failure_kind == "writer_busy":
            raise CodexThreadWriterBusy(f"thread {thread_id} already has an active writer")
        kwargs["turn_started_callback"](turn_id)
        kwargs["event_callback"]({"method": "turn/started", "params": {
            "threadId": thread_id, "turn": {"id": turn_id, "status": "inProgress"},
        }})
        if number == progress_call:
            kwargs["event_callback"](_completed({"type": "commandExecution", "status": "completed", "exitCode": 0}, turn_id))
        if failure_kind == "capacity":
            raise CodexTurnRetryableFailure(thread_id, turn_id, "Selected model is at capacity", "capacity")
        raise CodexTurnContinuationRequired(thread_id, turn_id, "provider_system_error")

    provider_client.run_turn.side_effect = run_turn
    with ExecutionServiceServer(tmp_path / "s", scheduler) as server:
        transport = mock.Mock()
        transport.request.side_effect = lambda method, params, **_kwargs: server.dispatch(method, params)
        scheduler_client = SchedulerClient(
            worker_id="dev:budget-test", build_id="test", capabilities=["agent_session:codex"], service_client=transport,
        )
        scheduler_client.register()
        scheduler.submit(request)
        observed_budgets = []
        for claim_number in range(8):
            # Reopen the database, so an in-memory counter cannot pass this test.
            scheduler = Scheduler(**scheduler_options)
            server.scheduler = scheduler
            with mock.patch("management_portal.execution.scheduler.time.time", return_value=time.time() + 31):
                claim = scheduler.claim(worker_id="dev:budget-test", capabilities=["agent_session:codex"])
            assert claim["claimed"], scheduler.snapshot(request.execution_id)
            checkpoint = claim["execution"].get("checkpoint") or {}
            observed_budgets.append(checkpoint.get("provider_recovery_state", {}).get("recovery_budget", {}).get("attempts", 0))
            sessions = mock.Mock()
            sessions.prepare.return_value = invocation.session
            runtime = ExecutionRuntime(
                agent_providers={"codex": CodexBackend(client_factory=lambda _socket: provider_client)},
                agent_harness=AgentHarness((ProviderErrorContinueFeature(),)),
                session_manager=sessions, agent_data_projector=mock.Mock(), state_root=tmp_path / "runtime",
            )
            outcome = ExecutionWorker(
                build_id="test", worker_id="dev:budget-test", scheduler=scheduler_client, runtime=runtime,
            ).execute_claim(claim)
            if outcome["status"] == "requeued":
                assert scheduler.snapshot(request.execution_id)["status"] == "queued"
                continue
            assert outcome["status"] == "failed", outcome
            assert outcome["result"]["error_code"] == "provider_recovery_exhausted", outcome
            break
        else:
            pytest.fail("real Worker kept resetting the recovery budget")
        assert claim_number >= 1
        assert len(calls) == (7 if progress_call else 4)
        assert max(observed_budgets) == 3
        assert scheduler.snapshot(request.execution_id)["status"] == "failed"
        assert scheduler.claim(worker_id="dev:budget-test", capabilities=["agent_session:codex"])["claimed"] is False
