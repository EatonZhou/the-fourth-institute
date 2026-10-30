"""Codex Adapter turns: final answers, resuming after a disconnect or on another Worker, input media,
usage, native events, steer and goals.

:module: execution.service
:importance: P2
"""
from __future__ import annotations

import json
from tests.cases import capability_contracts
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.agent_prompt_context import (
    portal_system_instructions,
)
from management_portal.codex_app_server_protocol import CodexTurnConnectionLost
from management_portal.execution import backend_contracts, codex_backend, codex_turn_run
from management_portal.execution.backend_contracts import BackendInvocation
from management_portal.execution.contracts import (
    ExecutionRecoveryDeferred,
    ExecutionRequest,
    InputMedia,
    SessionOptions,
)
from management_portal.execution.harness import AgentLoopProviderEventProcessor
from management_portal.execution.session_manager import RuntimeSession


def test_codex_backend_uses_client_final_answer() -> None:
    """codex 轮次结果采用客户端选定的最终回答，不取事件流中的最后一条"""
    thread_id = "019f0000-0000-7000-8000-000000000489"
    turn_id = "019f0000-0000-7000-8000-000000000490"
    client = mock.Mock()

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"](turn_id)
        callback = kwargs["event_callback"]
        for text in ("complete answer", "trailing summary"):
            callback({
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "item": {
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": text,
                    },
                },
            })
        return mock.Mock(
            thread_id=thread_id,
            turn_id=turn_id,
            response="selected answer",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:canonical-final-answer",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
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
        ),
        backend_contracts.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert result.output["message"] == "selected answer"


def test_codex_backend_resumes_an_accepted_turn_after_disconnect() -> None:
    """codex 轮次被接受后连接断开时按原线程和轮次恢复，不重新发起"""
    thread_id = "019f0000-0000-7000-8000-000000000491"
    turn_id = "019f0000-0000-7000-8000-000000000492"
    client = mock.Mock()

    def disconnect_after_accept(*_args, **kwargs):
        kwargs["turn_started_callback"](turn_id)
        raise CodexTurnConnectionLost(thread_id, turn_id)

    client.run_turn.side_effect = disconnect_after_accept
    client.resume_turn.return_value = mock.Mock(
        thread_id=thread_id,
        turn_id=turn_id,
        response="recovered response",
        token_usage={},
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:disconnect-recovery",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
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
        ),
        backend_contracts.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert result.output["message"] == "recovered response"
    client.resume_turn.assert_called_once_with(
        thread_id,
        turn_id,
        cwd="/tmp",
        config={
            "show_raw_agent_reasoning": True,
            "model_reasoning_summary": "auto",
            "agents": {"enabled": False, "interrupt_message": False},
            "compact_prompt": codex_backend._CODEX_COMPACTION_PROMPT,
            "project_doc_max_bytes": 0,
            "tools": {"update_plan": {"enabled": True}},
            "shell_environment_policy.set": {"PORTAL_EXECUTION_CONTEXT_FILE": str(codex_backend.CodexBackend._execution_context_path(request))},
        },
        event_callback=mock.ANY,
        turn_started_callback=mock.ANY,
        turn_snapshot_transform=mock.ANY,
        detach_requested=mock.ANY,
        cancellation_requested=mock.ANY,
        dynamic_tool_handler=None,
        turn_control_provider=mock.ANY,
        turn_control_result_callback=mock.ANY,
        timeout_seconds=None,
    )


def test_codex_backend_keeps_waiting_for_an_unavailable_accepted_turn() -> None:
    """恢复已接受的 codex 轮次暂时超时时继续重试等待，不重发轮次"""
    thread_id = "019f0000-0000-7000-8000-000000000493"
    turn_id = "019f0000-0000-7000-8000-000000000494"
    client = mock.Mock()

    def disconnect_after_accept(*_args, **kwargs):
        kwargs["turn_started_callback"](turn_id)
        raise CodexTurnConnectionLost(thread_id, turn_id)

    client.run_turn.side_effect = disconnect_after_accept
    client.resume_turn.side_effect = [
        TimeoutError("temporary reconciliation timeout"),
        mock.Mock(
            thread_id=thread_id,
            turn_id=turn_id,
            response="recovered response",
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
            binding_id="resident:deferred-recovery",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )

    with mock.patch.object(codex_turn_run.time, "sleep"):
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
            ),
            backend_contracts.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )

    assert result.output["message"] == "recovered response"
    assert client.run_turn.call_count == 1
    assert client.resume_turn.call_count == 2
    assert all(
        call.kwargs["timeout_seconds"] is None
        for call in client.resume_turn.call_args_list
    )


def test_codex_backend_recovers_terminal_event_without_resuming_thread() -> None:
    """codex 发出终止事件后 worker 交接，恢复时直接用记录的最终回答，不再续接线程"""
    thread_id = "019f0000-0000-7000-8000-000000000495"
    turn_id = "019f0000-0000-7000-8000-000000000496"
    client = mock.Mock()
    recovery_states: list[dict] = []

    def complete_before_transport_returns(*_args, **kwargs):
        kwargs["turn_started_callback"](turn_id)
        callback = kwargs["event_callback"]
        callback({
            "method": "item/completed",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {
                    "type": "agentMessage",
                    "phase": "commentary",
                    "text": "still working",
                },
            },
        })
        callback({
            "method": "item/completed",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "durable final answer",
                },
            },
        })
        callback({
            "method": "item/completed",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "trailing final summary",
                },
            },
        })
        callback({
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {
                    "id": turn_id,
                    "status": "completed",
                    "items": [{"type": "agentMessage", "phase": "final_answer", "text": "durable final answer"}],
                },
            },
        })
        # Reproduce the production failure boundary: the Provider has emitted
        # its terminal event, but the transport call never gets to return a
        # BackendResult before this worker is handed over.
        raise ExecutionRecoveryDeferred("worker retired after provider completion")

    client.run_turn.side_effect = complete_before_transport_returns
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:terminal-event-recovery",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id=thread_id,
        backend_route_id="one",
        is_new_backend_session=False,
    )
    backend = codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
    )

    with pytest.raises(ExecutionRecoveryDeferred):
        backend.execute(
            BackendInvocation(
                request=request,
                session=session,
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

    terminal_state = recovery_states[-1]
    assert terminal_state == {
        "kind": "completed_turn",
        "turn_id": turn_id,
        "response": "durable final answer",

        "usage": {},
        "provider_error_continue_count": 0,
        "original_input_recorded": False,
        "recovery_budget": {"attempts": 0, "turn_id": turn_id, "completed_item_ids": []},
    }

    client.reset_mock()
    result = backend.execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
            recovery_checkpoint={
                "turn_id": turn_id,
                "provider_recovery_state": terminal_state,
            },
        ),
        backend_contracts.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
            set_provider_recovery_state=recovery_states.append,
        ),
    )

    assert result.output == {
        "message": "durable final answer",
        "provider": "codex",
        "turn_id": turn_id,
    }
    assert result.backend_session_id == thread_id
    assert result.backend_route_id == "one"
    client.run_turn.assert_not_called()
    client.resume_turn.assert_not_called()


def test_codex_backend_maps_input_media_to_native_app_server_images() -> None:
    """codex 后端把本地路径和 URL 图片分别映射为 app-server 的 images 和 image_urls"""
    client = mock.Mock()

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"](
            "019f0000-0000-7000-8000-000000000412"
        )
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000411",
            turn_id="019f0000-0000-7000-8000-000000000412",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="inspect the image",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        input_media=(
            InputMedia("image", "local_path", "/tmp/probe.png"),
            InputMedia("image", "url", "https://example.test/probe.png"),
        ),
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )

    codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id="019f0000-0000-7000-8000-000000000411",
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

    assert client.run_turn.call_args.kwargs["images"] == ["/tmp/probe.png"]
    assert client.run_turn.call_args.kwargs["image_urls"] == [
        "https://example.test/probe.png",
    ]


def test_codex_backend_resumes_a_thread_created_by_another_worker() -> None:
    """codex 后端在另一个 worker 上续用已有线程，不新建线程"""
    old_thread_id = "019f0000-0000-7000-8000-000000000451"
    route = mock.Mock(
        account_id="one",
        socket_path=Path("/tmp/codex-one.sock"),
    )
    registry = mock.Mock()
    registry.get.return_value = route
    registry.find_thread_rollout.return_value = None
    client = mock.Mock()
    client.run_turn.return_value = mock.Mock(
        thread_id=old_thread_id,
        turn_id="019f0000-0000-7000-8000-000000000452",
        response="cross-worker resumed",
        token_usage={},
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:recovery-main",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id=old_thread_id,
        backend_route_id="one",
        is_new_backend_session=False,
    )
    result = codex_backend.CodexBackend(
        account_registry=registry,
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="resident rules",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        backend_contracts.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert result.output["message"] == "cross-worker resumed"
    assert client.run_turn.call_count == 1

    client.start_thread.assert_not_called()


def test_codex_subagent_requests_count_toward_the_turn_but_not_its_context() -> None:
    """Codex 子 agent 的请求计入本轮用量但不计入上下文

    Codex 子 agent 在自己的 thread 中发出的模型请求作为本轮用量增量上报（事件 ID 带子 thread 前缀、重复通知只计一次），当前上下文大小只取主 thread 的请求。

    A Codex subagent works in its own thread; its requests are the turn's usage too.

    :importance: P1
    :design: decision_f1331d57be89
    """
    client = mock.Mock()
    parent, child = "019f0000-0000-7000-8000-000000000431", "019f0000-0000-7000-8000-000000000439"

    def token_usage(thread_id, tokens):
        return {"method": "thread/tokenUsage/updated", "params": {"threadId": thread_id, "tokenUsage": {
            "last": {"inputTokens": tokens, "outputTokens": 100, "totalTokens": tokens + 100},
            "total": {"totalTokens": tokens + 100}, "modelContextWindow": 258_400,
        }}}

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"]("019f0000-0000-7000-8000-000000000432")
        kwargs["event_callback"](token_usage(parent, 150_000))
        child_usage = {"method": "subagent/event", "params": {
            "traceKey": child, "agentId": child, "ancestorTraceKeys": [], "parentToolUseId": "call-1",
            "event": token_usage(child, 40_000),
        }}
        kwargs["event_callback"](child_usage)
        kwargs["event_callback"](child_usage)  # The same notification again counts once.
        return mock.Mock(thread_id=parent, turn_id="019f0000-0000-7000-8000-000000000432", response="done", token_usage={})

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="continue", service_type="agent_session", provider="codex",
        operation="turn", cwd="/tmp",
        session=SessionOptions(mode="persistent", binding_id="provider:test-session", backend_route_id="one"),
    )
    observed: list[tuple[dict[str, int], int]] = []
    events: list[tuple[str, dict]] = []
    context = backend_contracts.BackendContext(
        emit_event=lambda name, details: events.append((name, details)),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
        update_context_usage=lambda usage, window: observed.append((usage, window)),
    )
    codex_backend.CodexBackend(client_factory=lambda _socket_path: client).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent", binding_id="", backend_session_id=parent,
                backend_route_id="one", is_new_backend_session=False,
            ),
            user_prompt=request.prompt, system_context="", attach_system_context=False,
            is_new_backend_session=False,
        ),
        context,
    )

    deltas = [details for name, details in events if name == "token_usage_delta"]
    assert [delta["usage"]["last"]["inputTokens"] for delta in deltas] == [150_000, 40_000]
    assert deltas[1]["event_id"].startswith(f"{child}:")
    assert [usage["input_tokens"] for usage, _window in observed] == [150_000]


def test_codex_requests_no_worker_heard_are_reported_from_the_rollouts(tmp_path: Path, monkeypatch) -> None:
    """Worker 没收到的 Codex 请求从 rollout 补记且只记一次

    Worker 交接期间结束的请求和子 agent 的请求在执行尝试结束时从 rollout 读出补报（事件 ID 与实时通知一致，已实时上报的不重报），执行开始前开始的轮次和子 agent 复制的父历史不计入。

    A request that ends during a Worker handover still counts, from the rollout, once.

    :importance: P1
    :design: decision_f1331d57be89
    """
    from tests.cases.account_registration_test_support import register_codex
    registry = register_codex(monkeypatch, tmp_path, names=("one",))
    parent, child = "019f0000-0000-7000-8000-000000000441", "019f0000-0000-7000-8000-000000000449"
    now = datetime.now(timezone.utc)

    def at(seconds: int) -> str:
        return (now + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def token_count(seconds: int, tokens: int, total: int) -> dict:
        return {"timestamp": at(seconds), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "last_token_usage": {"input_tokens": tokens, "output_tokens": 100, "total_tokens": tokens + 100},
            "total_token_usage": {"input_tokens": total, "output_tokens": 300, "total_tokens": total + 300},
            "model_context_window": 258_400,
        }}}

    def task_started(seconds: int) -> dict:
        return {"timestamp": at(seconds), "type": "event_msg", "payload": {"type": "task_started"}}

    day = registry.get("one").codex_home / "sessions" / f"{datetime.now():%Y/%m/%d}"
    day.mkdir(parents=True)
    (day / f"rollout-x-{parent}.jsonl").write_text("".join(json.dumps(line) + "\n" for line in [
        {"timestamp": at(-600), "type": "session_meta", "payload": {"id": parent}},
        task_started(-120), token_count(-110, 70_000, 70_000),  # An earlier Execution's turn
        task_started(-30), token_count(-20, 80_000, 150_000),  # reported live
        token_count(-10, 90_000, 240_000),  # ended while no Worker listened
    ]))
    (day / f"rollout-y-{child}.jsonl").write_text("".join(json.dumps(line) + "\n" for line in [
        {"timestamp": at(-15), "ordinal": 0, "type": "session_meta", "payload": {
            "id": child, "subagent_history_start_ordinal": 2,
            "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent}}},
        }},
        {**token_count(-15, 70_000, 70_000), "ordinal": 1},  # copied from the parent
        {**task_started(-14), "ordinal": 2}, {**token_count(-5, 40_000, 40_000), "ordinal": 3},
    ]))
    client = mock.Mock()

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"]("019f0000-0000-7000-8000-000000000442")
        kwargs["event_callback"]({"method": "thread/tokenUsage/updated", "params": {"threadId": parent, "tokenUsage": {
            "last": {"inputTokens": 80_000, "outputTokens": 100, "totalTokens": 80_100},
            "total": {"inputTokens": 150_000, "outputTokens": 300, "totalTokens": 150_300},
            "modelContextWindow": 258_400,
        }}})
        return mock.Mock(thread_id=parent, turn_id="019f0000-0000-7000-8000-000000000442", response="done", token_usage={})

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="continue", service_type="agent_session", provider="codex",
        operation="turn", cwd="/tmp",
        session=SessionOptions(mode="persistent", binding_id="provider:test-session", backend_route_id="one"),
    )
    events: list[tuple[str, dict]] = []
    context = backend_contracts.BackendContext(
        emit_event=lambda name, details: events.append((name, details)),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
        execution_started_at=at(-60),
    )
    codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
        account_registry=registry,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent", binding_id="", backend_session_id=parent,
                backend_route_id="one", is_new_backend_session=False,
            ),
            user_prompt=request.prompt, system_context="", attach_system_context=False,
            is_new_backend_session=False,
        ),
        context,
    )

    deltas = [details for name, details in events if name == "token_usage_delta"]
    assert [delta["usage"]["last"]["inputTokens"] for delta in deltas] == [80_000, 90_000, 40_000]
    assert deltas[1]["occurred_at"] == at(-10)
    assert deltas[2]["event_id"].startswith(f"{child}:")


def test_codex_backend_projects_current_context_usage_from_token_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """codex 后端从 token 用量事件得出当前上下文大小，并上报一次用量增量"""
    client = mock.Mock()

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"](
            "019f0000-0000-7000-8000-000000000422"
        )
        kwargs["event_callback"]({
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "019f0000-0000-7000-8000-000000000421",
                "turnId": "019f0000-0000-7000-8000-000000000422",
                "tokenUsage": {
                    "last": {
                        "inputTokens": 153_000,
                        "cachedInputTokens": 149_000,
                        "outputTokens": 700,
                        "reasoningOutputTokens": 400,
                        "totalTokens": 153_700,
                    },
                    "total": {"totalTokens": 600_000},
                    "modelContextWindow": 258_400,
                },
            },
        })
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000421",
            turn_id="019f0000-0000-7000-8000-000000000422",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
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
    session = RuntimeSession(
        mode="persistent",
        binding_id="",
        backend_session_id="019f0000-0000-7000-8000-000000000421",
        backend_route_id=request.session.backend_route_id,
        is_new_backend_session=False,
    )
    observed: list[tuple[dict[str, int], int]] = []
    events: list[tuple[str, dict]] = []
    context = backend_contracts.BackendContext(
        emit_event=lambda name, details: events.append((name, details)),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
        update_context_usage=lambda usage, window: observed.append(
            (usage, window)
        ),
    )

    codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        context,
    )

    assert observed == [(
        {
            "input_tokens": 153_000,
            "cached_input_tokens": 149_000,
            "output_tokens": 700,
            "reasoning_output_tokens": 400,
            "total_tokens": 153_700,
        },
        258_400,
    )]
    usage_events = [
        details
        for name, details in events
        if name == "token_usage_delta"
    ]
    assert len(usage_events) == 1
    assert usage_events[0]["event_id"]
    assert usage_events[0]["usage"] == {
        "last": {
            "inputTokens": 153_000,
            "cachedInputTokens": 149_000,
            "outputTokens": 700,
            "reasoningOutputTokens": 400,
            "totalTokens": 153_700,
        }
    }
    assert not any(name == "provider_event" for name, _details in events)


def test_codex_backend_bridges_native_events_and_turn_controls() -> None:
    """codex 后端把原生工具事件交给事件处理器，并回报轮内控制输入的应用结果"""
    client = mock.Mock()
    observed_events: list[dict] = []
    controls = [{
        "control_id": "checkpoint-1",
        "input": [{"type": "text", "text": "consolidate"}],
    }]
    control_results: list[tuple[dict, bool, str]] = []
    processor = AgentLoopProviderEventProcessor(
        feature_id="native_tool_loop_governor",
        process_event=observed_events.append,
        next_control=lambda: controls.pop(0) if controls else None,
        record_control_result=(
            lambda control, applied, error: control_results.append(
                (control, applied, error)
            )
        ),
        report=lambda: {},
    )

    def run_turn(*_args, **kwargs):
        item = {"id": "command-native-1", "type": "commandExecution", "command": "rg target"}
        kwargs["event_callback"]({"method": "item/started", "params": {
            "turnId": "turn-native-1", "item": {**item, "status": "inProgress"}}})
        kwargs["event_callback"]({"method": "item/completed", "params": {
            "turnId": "turn-native-1", "item": {**item, "status": "completed", "exitCode": 0}}})
        control = kwargs["turn_control_provider"]()
        kwargs["turn_control_result_callback"](control, True, "")
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000471",
            turn_id="turn-native-1",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
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
    codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id="019f0000-0000-7000-8000-000000000471",
                backend_route_id="one",
                is_new_backend_session=False,
            ),
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
            provider_event_processor=processor,
        ),
        backend_contracts.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert [event["method"] for event in observed_events] == ["item/started", "item/completed"]
    capability_contracts.check_tool_events(observed_events)
    assert control_results == [(
        {
            "control_id": "checkpoint-1",
            "input": [{"type": "text", "text": "consolidate"}],
        },
        True,
        "",
    )]


def test_codex_backend_maps_execution_input_control_to_native_steer() -> None:
    """codex 后端把执行输入控制作为原生 steer 送入当前轮次并回报结果"""
    client = mock.Mock()
    controls = [{
        "schema": "tfi.execution-input-control.v1",
        "control_id": "control-333333333333333333333333",
        "execution_id": "execution-444444444444444444444444",
        "actor": "sample_admin",
        "expected_turn_id": "turn-active",
        "input": [{"type": "text", "text": "adjust the active task"}],
    }]
    results: list[tuple[dict, bool, str]] = []

    def run_turn(*_args, **kwargs):
        control = kwargs["turn_control_provider"]()
        kwargs["turn_control_result_callback"](control, True, "")
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000472",
            turn_id="turn-active",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
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

    codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="",
                backend_session_id="019f0000-0000-7000-8000-000000000472",
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
            turn_control_provider=(
                lambda: controls.pop(0) if controls else None
            ),
            turn_control_result_callback=(
                lambda control, delivered, error: results.append(
                    (control, delivered, error)
                )
            ),
        ),
    )

    assert results == [(
        {
            "schema": "tfi.execution-input-control.v1",
            "control_id": "control-333333333333333333333333",
            "execution_id": "execution-444444444444444444444444",
            "actor": "sample_admin",
            "expected_turn_id": "turn-active",
            "input": [{"type": "text", "text": "adjust the active task"}],
        },
        True,
        "",
    )]


def test_codex_backend_maps_goal_to_native_protocol() -> None:
    """codex 的 goal 请求走原生 run_goal 协议，返回 Goal 状态和用量"""
    thread_id = "019f0000-0000-7000-8000-000000000480"
    turn_id = "019f0000-0000-7000-8000-000000000481"
    route = mock.Mock(
        account_id="one",
        socket_path=Path("/tmp/codex-one.sock"),
    )
    registry = mock.Mock()
    registry.get.return_value = route
    client = mock.Mock()
    client.run_goal.return_value = mock.Mock(
        thread_id=thread_id,
        turn_id=turn_id,
        response="goal finished",
        goal={
            "objective": "finish the portal goal integration",
            "status": "complete",
            "tokensUsed": 21,
        },
        token_usage={"last": {"totalTokens": 21}},
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="/goal finish the portal goal integration",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        runtime_options={
            "goal": {
                "action": "set",
                "objective": "finish the portal goal integration",
            },
        },
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
    )

    result = codex_backend.CodexBackend(
        account_registry=registry,
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

    client.run_turn.assert_not_called()
    self_call = client.run_goal.call_args
    assert self_call.args == (thread_id,)
    assert self_call.kwargs["action"] == "set"
    assert self_call.kwargs["objective"] == (
        "finish the portal goal integration"
    )
    assert self_call.kwargs["config"]["features"] == {"goals": True}
    assert result.output["message"] == "goal finished"
    assert result.output["goal"]["status"] == "complete"
    assert result.usage["last"]["totalTokens"] == 21


from tests.cases.compatibility_test_support import setUpModule, tearDownModule
