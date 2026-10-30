"""No Backend sends a turn's input twice.

A Worker handover leaves the turn of Claude Code, DSH and Kimi Code running,
and the next Worker attaches to it.  A turn that is no longer running is
continued in the same session with "continue" once the Provider had taken its
input; only an input the Provider never took is sent (for the first time).

:module: execution.service
"""
from __future__ import annotations
from contextlib import ExitStack

import json
import socket
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal import kimi_portal_tools
from management_portal.claude_code_client import ClaudeCodeClient
from management_portal.execution import claude_backend, dsh_backend, kimi_backend
from management_portal.execution.contracts import ExecutionRecoveryDeferred, ExecutionRequest, SessionOptions
from management_portal.execution.kimi_tool_gateway import KimiToolGateway
from management_portal.execution.native_harness_tools import prepare_tools
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.execution.scheduler import Scheduler
from management_portal.kimi_code_client import KimiCodeClient, KimiCodeDetached
from management_portal.native_agent_transport import NativeAgentClient, NativeAgentDetached, ToolCallHandedOver

TURN_ID = "job-recovered"


def test_no_provider_resends_a_turns_input() -> None:
    """没有 Provider 会重发一轮的输入

    Provider 目录不再有 interrupted_turn_recovery，Claude Code、DSH 和 Kimi Code 都声明交接时保留这一轮。
    """
    assert not any(hasattr(provider, "interrupted_turn_recovery") for provider in AGENT_PROVIDERS.values())
    # A Provider's process keeps the turn its retiring Worker leaves (each Adapter's tests show how).
    assert all(provider.handover_keeps_turn for provider in AGENT_PROVIDERS.values())


@pytest.mark.parametrize("provider", ["kimi", "dsh"])
def test_a_kimi_or_dsh_handover_does_not_interrupt_the_turn(provider) -> None:
    """Kimi 与 DSH 交接不中断这一轮

    Kimi Code 和 DSH 执行的计划内交接准备结果为无需中断，交接标记保留这一轮。
    """
    from tests.cases.model_api_test_support import DEEPSEEK_PRIMARY, register_apis

    register_apis({**DEEPSEEK_PRIMARY, "routes": [{"backend": "dsh", "route_id": "deepseek"}]})
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    capability = f"agent_session:{provider}"
    scheduler.register_worker(worker_id="dev:old", build_id="old", capabilities=[capability], pid=1)
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="the original task", service_type="agent_session", provider=provider,
        operation="turn", agent_id="resident:test", cwd="/workspace",
        session=SessionOptions(mode="persistent", binding_id="resident:test"),
    )
    scheduler.submit(request)
    claim = scheduler.claim(worker_id="dev:old", capabilities=[capability])
    owner = {"worker_id": "dev:old", "claim_token": claim["claim_token"]}
    scheduler.start(request.execution_id, **owner)
    scheduler.checkpoint(request.execution_id, **owner, checkpoint={
        "provider": provider, "operation": "turn", "resumable": True, "backend_session_id": "native", "turn_id": TURN_ID,
    })

    prepared = scheduler.prepare_handover(request.execution_id, **owner)

    assert prepared["ready"] is True and prepared["interrupt_required"] is False


# --- Claude Code and DSH: the daemon attaches, continues, or delivers --------


def _invocation(provider: str, checkpoint: dict):
    request = ExecutionRequest.new(
        service_type="agent_session", provider=provider, operation="turn", actor="test",
        agent_id="resident:test", cwd="/workspace", prompt="the original task",
        session=SessionOptions(mode="persistent", binding_id="resident:test", backend_route_id="deepseek_primary"),
        runtime_options={"turn_id": TURN_ID, "session_key": "resident:test", "is_owner": True},
    )
    return SimpleNamespace(
        request=request, session=SimpleNamespace(backend_route_id="deepseek_primary", backend_session_id="native"),
        user_prompt="the original task", system_context="", attach_system_context=False,
        recovery_checkpoint=checkpoint,
    )


def _context():
    return mock.Mock(is_cancelled=mock.Mock(return_value=False), is_handover=mock.Mock(return_value=False))


@pytest.mark.parametrize("provider, backend", [("claude", claude_backend.ClaudeBackend), ("dsh", dsh_backend.DshBackend)])
@pytest.mark.parametrize("checkpoint, turn_id, prompt, continue_prompt", [
    ({}, TURN_ID, "the original task", None),
    # A Worker that died: no handover marker, the turn had started.
    ({"turn_id": TURN_ID}, TURN_ID, "the original task", "continue"),
    # The turn running then was a continuation after a Provider error.
    ({"turn_id": TURN_ID, "provider_recovery_state": {"provider_error_continue_count": 1}},
     f"{TURN_ID}:continue1", "continue", "continue"),
])
def test_a_recovered_native_turn_is_attached_to_or_continued(provider, backend, checkpoint, turn_id, prompt, continue_prompt):
    """恢复的原生轮次接回或续接

    Claude 和 DSH 恢复已开始的一轮时以同一 turn_id 接回并附带 continue_prompt；Provider 错误续接那一轮的输入也是 continue。
    """
    client = mock.Mock()
    client.run_turn.return_value = SimpleNamespace(response="done", session_id="native", turn_id=turn_id, usage={})
    with mock.patch.object(backend, "client_factory", staticmethod(lambda: client)), \
            mock.patch.object(backend, "prepare_native_session", lambda *_args, **_kwargs: None), \
            mock.patch.object(backend, "observe_turn_usage", lambda *_args, **_kwargs: None), \
            mock.patch.object(backend, "record_provider_compaction", lambda *_args, **_kwargs: None), \
            mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)):
        backend().execute(_invocation(provider, checkpoint), _context())
    call = client.run_turn.call_args.kwargs
    assert (call["turn_id"], call["prompt"], call.get("continue_prompt")) == (turn_id, prompt, continue_prompt)


@pytest.mark.parametrize("delivery, prompt", [("continued", "continue"), ("first_delivery", "the original task")])
def test_what_the_daemon_sent_for_a_recovered_turn_is_recorded(delivery, prompt):
    """守护进程为恢复发出的输入被记录

    守护进程报告 turn/recovered 后，Adapter 记录 recovery_input_sent，带实际发出的文本和 continued 或 first_delivery。
    """
    def run_turn(**kwargs):
        kwargs["event_callback"]({"method": "turn/recovered", "params": {
            "threadId": "native", "turnId": TURN_ID, "delivery": delivery, "prompt": prompt}})
        return SimpleNamespace(response="done", session_id="native", turn_id=TURN_ID, usage={})

    client = mock.Mock(run_turn=mock.Mock(side_effect=run_turn))
    context = _context()
    with mock.patch.object(claude_backend.ClaudeBackend, "client_factory", staticmethod(lambda: client)), \
            mock.patch.object(claude_backend.ClaudeBackend, "prepare_native_session", lambda *_args, **_kwargs: None), \
            mock.patch.object(claude_backend.ClaudeBackend, "observe_turn_usage", lambda *_args, **_kwargs: None), \
            mock.patch.object(claude_backend.ClaudeBackend, "record_provider_compaction", lambda *_args, **_kwargs: None), \
            mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)):
        claude_backend.ClaudeBackend().execute(_invocation("claude", {"turn_id": TURN_ID}), context)
    context.emit.assert_any_call("recovery_input_sent", {
        "provider": "claude", "reason": "turn_interrupted", "prompt": prompt,
        "input_delivery": delivery, "turn_id": TURN_ID,
    })


def test_the_claude_client_never_lets_a_daemon_send_a_started_turns_input_again() -> None:
    """Claude 客户端不让旧守护进程重发输入

    守护进程支持 turn_continue 时由它核对会话记录；不支持时客户端直接发送 continue。
    """
    client = ClaudeCodeClient("/nonexistent.sock")
    with mock.patch.object(client, "_request", return_value={"response": "done"}) as request:
        client.run_turn(
            session_key="resident:test", session_id="native", prompt="the original task", cwd="/workspace",
            config_dir="/config", provider_profile="claude_one", model="m", is_owner=True,
            turn_id=TURN_ID, continue_prompt="continue",
        )
    # The daemon sends the original input only if the CLI never took it; otherwise it continues.
    params = request.call_args.args[1]
    assert (params["prompt"], params["continue_prompt"]) == ("the original task", "continue")


# --- A tool call that reaches a retiring Worker goes to the next one ---------


def _tool_invocation(provider: str):
    tool = SimpleNamespace(expose_schema=True, name="lookup", description="Look up", input_schema={"type": "object"},
                           handler=mock.Mock(return_value="42"))
    return SimpleNamespace(harness_tools=[tool], tool_batch_processor=None,
                           request=SimpleNamespace(provider=provider)), tool


@pytest.mark.parametrize("provider, handover, raised", [
    ("claude", True, ToolCallHandedOver), ("kimi", True, ToolCallHandedOver),
    ("claude", False, InterruptedError), ("codex", True, ToolCallHandedOver),
])
def test_a_fenced_worker_hands_a_new_tool_call_over_instead_of_failing_it(provider, handover, raised) -> None:
    """交接中的 Worker 把新工具调用交给下一个 Worker

    保留轮次的 Provider 在交接时，旧 Worker 不执行新到的工具调用而交给下一个 Worker；其他情况仍按取消处理。
    """
    invocation, tool = _tool_invocation(provider)
    context = mock.Mock(is_cancelled=mock.Mock(return_value=True), is_handover=mock.Mock(return_value=handover))
    _catalog, dispatch = prepare_tools(invocation, context)
    with pytest.raises(raised):
        dispatch({"tool": "lookup", "arguments": {}, "call_id": "call-1"})
    tool.handler.assert_not_called()


def test_the_native_client_leaves_a_handed_over_tool_call_unanswered(tmp_path) -> None:
    """原生客户端不回答交出的工具调用

    工具调用交给下一个 Worker 时客户端不回传结果直接离开，Agent 把调用留给接回的 Worker。
    """
    path = tmp_path / "agent.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    received: list[bytes] = []

    def agent() -> None:
        connection, _ = server.accept()
        with connection, connection.makefile("rb") as stream:
            request = json.loads(stream.readline())
            connection.sendall(json.dumps({"id": request["id"], "type": "tool_call", "call_id": "call-1",
                                           "tool": "lookup", "arguments": {}}).encode() + b"\n")
            received.append(stream.readline())

    thread = threading.Thread(target=agent)
    thread.start()

    def handed_over(_call):
        raise ToolCallHandedOver("next Worker")

    with pytest.raises(NativeAgentDetached):
        NativeAgentClient(path)._request("turn/run", {}, tool_callback=handed_over, response_timeout=5)
    thread.join(5)
    server.close()
    # The agent saw the Worker leave without an answer, so it keeps the call for the next one.
    assert received == [b""]


# --- Kimi Code: the server runs the prompt; Workers only follow it -----------


class ScriptedKimi(KimiCodeClient):
    def __init__(self, snapshots: list[dict], tmp_path: Path) -> None:
        super().__init__("http://127.0.0.1:1/api/v1", poll_seconds=0.01, events_root=tmp_path)
        self.snapshots = deque(snapshots)
        self.requests: list[tuple[str, str]] = []

    def _open_event_stream(self, session_id):
        frames = [
            {"type": "turn.step.started", "payload": {"agentId": "main", "turnId": 1, "step": 1}},
            {"type": "assistant.delta", "payload": {"agentId": "main", "turnId": 1, "delta": "done"}},
            {"type": "turn.ended", "payload": {"agentId": "main", "turnId": 1, "reason": "completed"}},
        ]
        def drain(observe):
            while frames:
                observe({"session_id": session_id, "epoch": "test", **frames.pop(0)})
        return SimpleNamespace(drain=drain, close=lambda: None)

    def _request(self, method, path, payload=None, **_kwargs):
        self.requests.append((method, path))
        if path.endswith("/profile"):
            return {"usage": {}}
        if path.endswith("/snapshot"):
            return self.snapshots.popleft() if len(self.snapshots) > 1 else self.snapshots[0]
        if path.endswith("/prompts"):
            return {"prompt_id": "prompt-1", "user_message_id": "user-1"}
        if path.endswith("/messages"):
            return {"items": [
                {"id": "user-1", "role": "user", "prompt_id": "prompt-1"},
                {"id": "answer-1", "role": "assistant", "prompt_id": "prompt-1", "content": [{"type": "text", "text": "done"}]},
            ], "has_more": False}
        if "/approvals/" in path:
            return {"resolved": True}
        raise AssertionError(f"unexpected request: {method} {path}")


def _kimi_snapshot(*, busy: bool, approvals: list[dict] | None = None) -> dict:
    return {
        "as_of_seq": 1, "session": {"busy": busy, "last_turn_reason": "" if busy else "completed", "usage": {}},
        "messages": {"items": [], "has_more": False},
        "in_flight_turn": {"prompt_id": "prompt-1", "assistant_text": "working"} if busy else {},
        "pending_approvals": approvals or [],
    }


def test_a_retiring_worker_stops_following_the_kimi_prompt_without_aborting_it(tmp_path) -> None:
    """退役 Worker 停止跟随 Kimi prompt 而不中止

    交接时 Kimi 客户端停止轮询并报告离开，不向 Kimi Code 发送中止。
    """
    client = ScriptedKimi([_kimi_snapshot(busy=False), _kimi_snapshot(busy=True)], tmp_path)
    polls = iter([False, False, True])
    with pytest.raises(KimiCodeDetached):
        client.run_turn(session_id="session_kimi", prompt="the task", tools=[], detach_requested=lambda: next(polls))
    assert not any(":abort" in path for _method, path in client.requests)


def test_the_next_worker_follows_the_running_kimi_prompt_and_answers_its_approvals(tmp_path) -> None:
    """新 Worker 跟随运行中的 Kimi prompt 并处理审批

    接回的 Worker 跟随同一个 prompt 到结束，照常回答审批，不提交新的 prompt。
    """
    approval = {"approval_id": "approval-1", "tool_name": "Shell"}
    client = ScriptedKimi([_kimi_snapshot(busy=True, approvals=[approval]), _kimi_snapshot(busy=False)], tmp_path)
    events: list[dict] = []
    result = client.resume_turn(session_id="session_kimi", prompt_id="prompt-1", event_callback=events.append, tools=["Shell"])
    assert result.response == "done"
    assert any("/approvals/approval-1" in path for _method, path in client.requests)
    assert not any(path.endswith("/prompts") for _method, path in client.requests)
    assert events[-1]["method"] == "turn/completed"


def _kimi_invocation(checkpoint: dict):
    return SimpleNamespace(
        request=SimpleNamespace(session=SimpleNamespace(binding_id="resident:test"), runtime_options={}, permission_profile="", cwd="/work", operation="turn",
                                execution_id="execution-1"),
        session=SimpleNamespace(backend_route_id="kimi_primary", backend_session_id="session_kimi"), recovery_checkpoint=checkpoint,
        system_context="", user_prompt="the original task", attach_system_context=False, is_new_backend_session=False,
    )


KIMI_HOME = Path("/nonexistent/kimi-account")


def _patched_kimi(client):
    from management_portal import kimi_account_runtime

    # The invocation's route names a Kimi account; its client is the fake.
    return (
        mock.patch.object(kimi_account_runtime, "client_for_route", return_value=client),
        mock.patch.object(kimi_account_runtime, "remember_session"),
        mock.patch.object(kimi_backend.KimiBackend, "record_provider_compaction"),
        mock.patch.object(kimi_backend.KimiBackend, "settle_portal_tools", return_value=[]),
        mock.patch.object(kimi_backend.KimiBackend, "observe_turn_usage", return_value=None),
        mock.patch.object(kimi_backend.KimiBackend, "offer_portal_tools", return_value=([], None)),
    )


def test_the_next_kimi_worker_follows_the_kept_prompt() -> None:
    """新 Kimi Worker 接回保留的 prompt

    交接保留的 Kimi prompt 由新 Worker 接回，不发送新输入，并记录 worker_handover_continuing。
    """
    client = SimpleNamespace(
        home=KIMI_HOME,
        run_turn=mock.Mock(return_value=SimpleNamespace(response="done", turn_id="prompt-7", session_id="session_kimi", usage={})),
    )
    context = _context()
    checkpoint = {"turn_id": "prompt-7", "worker_handover": {"turn_id": "prompt-7", "turn_kept_running": True}}
    patches = _patched_kimi(client)
    with ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        result = kimi_backend.KimiBackend().execute(_kimi_invocation(checkpoint), context)
    assert result.output["message"] == "done"
    assert client.run_turn.call_args.kwargs["turn_id"] == "prompt-7"
    assert client.run_turn.call_args.kwargs["detach_requested"] == context.is_handover
    context.emit.assert_any_call("worker_handover_continuing", {
        "provider": "kimi", "previous_turn_id": "prompt-7", "input_delivery": "kept_running",
    })


def test_a_kimi_worker_that_stops_following_requeues_the_execution() -> None:
    """停止跟随的 Kimi Worker 让执行重新排队

    Kimi 客户端因交接离开后，Adapter 报告可恢复的延期，执行交回队列。
    """
    client = SimpleNamespace(home=KIMI_HOME, run_turn=mock.Mock(side_effect=KimiCodeDetached("left")))
    patches = _patched_kimi(client)
    with ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        with pytest.raises(ExecutionRecoveryDeferred):
            kimi_backend.KimiBackend().execute(_kimi_invocation({"turn_id": "prompt-7"}), _context())
    client.run_turn.assert_called_once()


def test_a_kimi_tool_call_waits_for_the_next_workers_gateway(tmp_path, monkeypatch) -> None:
    """Kimi 工具调用等待下一个 Worker 的网关

    退役 Worker 交出的 Portal 工具调用重试到下一个 Worker 的网关，旧网关退出不删除新 Worker 的套接字。
    """
    monkeypatch.setattr(kimi_portal_tools, "gateway_socket_path", lambda session_id: tmp_path / f"{session_id}.sock")
    monkeypatch.setattr(kimi_portal_tools, "_take_note", lambda _name, _arguments: {"session_id": "s1", "tool_call_id": "c1"})
    retiring = KimiToolGateway("s1", mock.Mock(side_effect=ToolCallHandedOver("next Worker")))
    retiring.__enter__()
    next_worker = KimiToolGateway("s1", lambda call: {"content": [{"type": "text", "text": "42"}]})

    def hand_over() -> None:
        time.sleep(0.3)
        # The next Worker listens before the retiring one has closed its gateway.
        next_worker.__enter__()
        retiring.__exit__(None, None, None)

    thread = threading.Thread(target=hand_over)
    thread.start()
    result = kimi_portal_tools.call_portal_tool("lookup", {})
    thread.join(5)
    try:
        assert result == {"content": [{"type": "text", "text": "42"}]}
        # The retiring gateway's exit left the next Worker's socket in place.
        assert next_worker.path.exists()
    finally:
        next_worker.__exit__(None, None, None)
