"""A Worker handover leaves a running Claude turn running.

The retiring Worker detaches from the Claude daemon without interrupting the
turn; the next Worker attaches to the same turn (same session key and turn
id).  The turn is neither interrupted nor asked again.

:module: execution.service
"""
from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from management_common.json_columns import json_value
from management_portal.execution import backends
from management_portal.execution.contracts import ExecutionRecoveryDeferred, ExecutionRequest, SessionOptions
from management_portal.execution.provider_turn_usage import ObservedTurnUsage, RequestUsage
from management_portal.execution.transport import ExecutionClaimOwnershipLost
from management_portal.execution.scheduler import Scheduler
from management_portal.native_agent_transport import NativeAgentClient, NativeAgentDetached
from tests.cases.model_api_test_support import claude_subscription, register_apis

TURN_ID = "job-kept"


@pytest.fixture
def claude_claim(tmp_path):
    register_apis(claude_subscription("claude_one", str(tmp_path / "claude-one")))
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    scheduler.register_worker(worker_id="dev:old", build_id="old", capabilities=["agent_session:claude"], pid=1)
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="the original task", service_type="agent_session", provider="claude",
        operation="turn", agent_id="resident:test", cwd="/workspace",
        session=SessionOptions(mode="persistent", binding_id="resident:test", backend_route_id="claude_one"),
    )
    scheduler.submit(request)
    claim = scheduler.claim(worker_id="dev:old", capabilities=["agent_session:claude"])
    owner = {"worker_id": "dev:old", "claim_token": claim["claim_token"]}
    scheduler.start(request.execution_id, **owner)
    scheduler.checkpoint(request.execution_id, **owner, checkpoint={
        "provider": "claude", "operation": "turn", "resumable": True,
        "backend_session_id": "native", "backend_route_id": "claude_one", "turn_id": TURN_ID,
    })
    return scheduler, request, owner


def _checkpoint(scheduler, execution_id):
    with scheduler.changes.connect() as connection:
        row = connection.execute(
            'SELECT checkpoint_json FROM executions WHERE execution_id = %s', (execution_id,),
        ).fetchone()
    return json_value(row[0] or "{}")


def test_claude_handover_does_not_interrupt_the_turn(claude_claim):
    """Claude 交接时不打断正在运行的 turn

    调度器为 Claude 的 Worker 交接不要求打断 Provider，并在检查点里记下 turn 仍在运行；执行重新排队等下一个 Worker 接回。
    """
    scheduler, request, owner = claude_claim
    prepared = scheduler.prepare_handover(request.execution_id, **owner)
    assert prepared["ready"] is True
    assert prepared["interrupt_required"] is False
    handover = _checkpoint(scheduler, request.execution_id)["worker_handover"]
    assert handover["turn_id"] == TURN_ID and handover["turn_kept_running"] is True
    scheduler.requeue_claim(request.execution_id, **owner, reason="worker generation handover")
    assert scheduler.snapshot(request.execution_id)["status"] == "queued"


@pytest.mark.parametrize("stop_while", ["releasing", "queued"])
def test_stop_during_handover_interrupts_the_kept_turn_before_ending(claude_claim, stop_while):
    """交接空档里的停止先打断仍在运行的 turn

    交接时保留运行的 Claude turn，在释放认领或排队期间被停止，执行不会直接结束，而是由下一个 Worker 先打断该 turn 再以取消结束。
    """
    scheduler, request, owner = claude_claim
    scheduler.prepare_handover(request.execution_id, **owner)
    if stop_while == "releasing":
        scheduler.cancel(request.execution_id, actor=request.actor)
        scheduler.requeue_claim(request.execution_id, **owner, reason="worker generation handover")
    else:
        scheduler.requeue_claim(request.execution_id, **owner, reason="worker generation handover")
        scheduler.cancel(request.execution_id, actor=request.actor)
    # Still active: the next Worker interrupts the Claude turn, then ends it cancelled.
    assert scheduler.snapshot(request.execution_id)["status"] == "queued"
    checkpoint = _checkpoint(scheduler, request.execution_id)
    assert checkpoint["abandoned"]["status"] == "cancelled"
    assert checkpoint["resumable"] is False


def _serve_once(path: Path, received: list):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)

    def run():
        connection, _ = server.accept()
        stream = connection.makefile("rb")
        request = json.loads(stream.readline())
        send = lambda body: connection.sendall(json.dumps({"id": request["id"], **body}).encode() + b"\n")
        send({"type": "event", "event": {"method": "item/completed"}})
        send({"type": "tool_call", "call_id": "call-1", "tool": "lookup", "arguments": {}})
        received.append(json.loads(stream.readline()))
        received.append(stream.readline())  # b"" once the client leaves
        connection.close()
        server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_a_retiring_worker_answers_taken_tool_calls_then_leaves(tmp_path):
    """退役 Worker 先答完已收到的工具调用再离开

    Native Agent 客户端在交接时只在两条消息之间断开：已收到的事件照常处理，已收到的工具调用执行完并回传结果后才离开。
    """
    path = tmp_path / "agent.sock"
    received: list = []
    thread = _serve_once(path, received)
    events = []
    tool_callback = mock.Mock(return_value={"content": [{"type": "text", "text": "ok"}]})
    with pytest.raises(NativeAgentDetached):
        NativeAgentClient(path)._request(
            "turn/run", {}, event_callback=events.append, tool_callback=tool_callback,
            detach_requested=lambda: True,
        )
    thread.join(5)
    assert events == [{"method": "item/completed"}]
    tool_callback.assert_called_once()
    assert received[0]["type"] == "tool_result" and received[0]["call_id"] == "call-1"
    assert received[1] == b""


def _invocation(checkpoint):
    request = ExecutionRequest.new(
        service_type="agent_session", provider="claude", operation="turn", actor="test",
        agent_id="resident:test", cwd="/workspace", prompt="the original task",
        session=SessionOptions(mode="persistent", binding_id="resident:test", backend_route_id="claude_one"),
        runtime_options={"turn_id": TURN_ID, "session_key": "resident:test"},
    )
    return SimpleNamespace(
        request=request, session=SimpleNamespace(backend_route_id="claude_one", backend_session_id="native"),
        user_prompt="the original task", system_context="", attach_system_context=False,
        recovery_checkpoint=checkpoint,
    )


def _context():
    return mock.Mock(is_cancelled=mock.Mock(return_value=False), is_handover=mock.Mock(return_value=False))


KEPT = {"turn_id": TURN_ID, "worker_handover": {"worker_id": "dev:old", "turn_id": TURN_ID, "turn_kept_running": True}}


@pytest.mark.parametrize("checkpoint, running_turn", [
    (KEPT, TURN_ID),
    ({**KEPT, "provider_recovery_state": {"provider_error_continue_count": 2}}, f"{TURN_ID}:continue2"),
])
def test_next_worker_attaches_to_the_running_turn(checkpoint, running_turn):
    """下一个 Worker 用同一个 turn_id 接回运行中的 turn

    带有 turn 仍在运行标记的恢复检查点让 Claude Adapter 以原 turn_id（Provider 错误续接时为续接 turn）接回，不重发原消息。
    """
    client = mock.Mock()
    client.run_turn.return_value = SimpleNamespace(response="done", session_id="native", turn_id=running_turn, usage={})
    context = _context()
    with mock.patch.object(backends, "shared_claude_client", return_value=client), \
            mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)):
        result = backends.ClaudeBackend().execute(_invocation(checkpoint), context)
    assert result.output["message"] == "done"
    client.run_turn.assert_called_once()
    assert client.run_turn.call_args.kwargs["turn_id"] == running_turn
    assert client.run_turn.call_args.kwargs["detach_requested"] == context.is_handover
    context.emit.assert_any_call("worker_handover_continuing", {
        "provider": "claude", "previous_turn_id": running_turn, "input_delivery": "kept_running",
    })


def test_a_stop_before_attaching_does_not_start_the_turn_anew():
    """接回前已停止的执行不重新开始 turn

    接回仍在运行的 turn 之前执行已被停止时，Adapter 不再调用守护进程，避免已被打断的 turn 以原消息重新开始。
    """
    client = mock.Mock()
    context = _context()
    context.is_cancelled.return_value = True
    with mock.patch.object(backends, "shared_claude_client", return_value=client), \
            mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)):
        with pytest.raises(InterruptedError):
            backends.ClaudeBackend().execute(_invocation(KEPT), context)
    client.run_turn.assert_not_called()


def test_a_detached_worker_requeues_the_execution():
    """离开 turn 的 Worker 让执行重新排队

    退役 Worker 离开仍在运行的 Claude turn 后，Adapter 报告可恢复的延期，Worker 把执行交回队列而不记为失败。
    """
    client = mock.Mock()
    client.run_turn.side_effect = NativeAgentDetached("left")
    context = _context()
    usage = ObservedTurnUsage(turn={}, requests=(RequestUsage("claude:msg_1", {"input_tokens": 1}, ""),))
    with mock.patch.object(backends, "shared_claude_client", return_value=client), \
            mock.patch.object(backends.ClaudeBackend, "observe_turn_usage", return_value=usage), \
            mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)):
        with pytest.raises(ExecutionRecoveryDeferred):
            backends.ClaudeBackend().execute(_invocation({}), context)
    # The turn has not ended and this Worker's claim is released: the
    # Worker that attaches records the turn's usage and compactions.
    assert "token_usage_delta" not in [call.args[0] for call in context.emit.call_args_list]


def test_an_event_execution_service_did_not_take_leaves_the_turn_to_the_next_attempt():
    """ExecutionService 未接收轮次事件时把轮次留给下一次尝试

    发布重启 ExecutionService 时，Worker 写入一条 Provider 事件遇到连接重置；这是 Portal 的失败而非 Provider 的失败。Adapter 不在同一会话里发 continue（原轮次仍在运行，守护进程会拒绝），执行延期重排，下一次尝试用同一 turn_id 接回。
    """
    from management_portal.execution.harness import ProviderErrorRecoveryPolicy
    from management_portal.execution.transport import ExecutionResponseUnknown

    turn_ids: list[str] = []

    def run_turn(**options):
        turn_ids.append(options["turn_id"])
        if len(turn_ids) > 1:
            return SimpleNamespace(response="continued", session_id="native", turn_id=options["turn_id"], usage={})
        options["event_callback"]({"method": "turn/started", "params": {"turn": {"id": TURN_ID}}})
        options["event_callback"]({"method": "item/started", "params": {"item": {"type": "reasoning", "id": "r1"}}})
        raise AssertionError("the second event is not taken")

    client = mock.Mock()
    client.run_turn.side_effect = run_turn
    context = _context()
    context.observe_provider_event.side_effect = [
        None, ExecutionResponseUnknown("ExecutionService request failed at execution-service.sock: ConnectionResetError"),
    ]
    invocation = _invocation({"turn_id": TURN_ID})
    invocation.provider_error_recovery = ProviderErrorRecoveryPolicy(max_continue_attempts=1)
    usage = ObservedTurnUsage(turn={}, requests=(RequestUsage("claude:msg_1", {"input_tokens": 1}, ""),))
    with mock.patch.object(backends, "shared_claude_client", return_value=client), \
            mock.patch.object(backends.ClaudeBackend, "observe_turn_usage", return_value=usage), \
            mock.patch.object(backends, "wait_before_continuation", return_value=True), \
            mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)):
        with pytest.raises(ExecutionRecoveryDeferred):
            backends.ClaudeBackend().execute(invocation, context)
    assert turn_ids == [TURN_ID]
    emitted = [call.args[0] for call in context.emit.call_args_list]
    assert "agent_harness_provider_error_continue" not in emitted
    assert "token_usage_delta" not in emitted

    # The next attempt asks for the same turn, which the daemon attaches to while it runs.
    client.run_turn.side_effect = None
    client.run_turn.return_value = SimpleNamespace(response="done", session_id="native", turn_id=TURN_ID, usage={})
    with mock.patch.object(backends, "shared_claude_client", return_value=client), \
            mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)):
        result = backends.ClaudeBackend().execute(_invocation({"turn_id": TURN_ID}), _context())
    assert result.output["message"] == "done"
    assert client.run_turn.call_args.kwargs["turn_id"] == TURN_ID


def test_a_worker_that_lost_its_claim_mid_turn_leaves_the_records_to_the_next_worker():
    """轮次中途失去 claim 的 Worker 不再记录这一轮

    交接时一次检查点写入先发现 claim 已交出，轮次以 ExecutionClaimOwnershipLost 结束；退役 Worker 不再尝试写入用量。
    """
    client = mock.Mock()
    client.run_turn.side_effect = ExecutionClaimOwnershipLost("execution claim ownership changed")
    context = _context()
    context.is_handover.return_value = True
    usage = ObservedTurnUsage(turn={}, requests=(RequestUsage("claude:msg_1", {"input_tokens": 1}, ""),))
    with mock.patch.object(backends, "shared_claude_client", return_value=client), \
            mock.patch.object(backends.ClaudeBackend, "observe_turn_usage", return_value=usage), \
            mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)):
        with pytest.raises(ExecutionClaimOwnershipLost):
            backends.ClaudeBackend().execute(_invocation({}), context)
    assert "token_usage_delta" not in [call.args[0] for call in context.emit.call_args_list]
