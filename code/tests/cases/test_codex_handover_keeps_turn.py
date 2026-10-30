"""Plan handover follows the native turn; interrupting its waiter is unsafe.

:importance: P0
:design: decision_075f66430606, decision_3e8c2a7f91d4
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
from unittest import mock

import pytest

from management_portal.codex_app_server_client import CodexAppServerClient
from management_portal.codex_app_server_protocol import CodexTurnDetached, TurnWaiter
from tests.cases.test_codex_interrupt_semantics import (
    NATIVE_BINARIES, OfflineResponsesServer, native_test_session,
)
from tests.cases.test_execution_planned_handover import (
    accepted_checkpoint, handover_claim, invoke_backend, stored_checkpoint,
)
from tests.cases.test_codex_message_replay import _request_session
from management_portal.execution.codex_backend import CodexBackend
from management_portal.execution.contracts import ExecutionRecoveryDeferred
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore


def test_codex_handover_prepares_without_native_interrupt(handover_claim):
    """Codex计划移交保留原生Turn

    验证计划移交不调用原生中断，恢复跟随原thread和turn且不重新发送输入。

    :design-point:
    """
    scheduler, request, owner = handover_claim
    checkpoint = {**accepted_checkpoint(), "operation": "turn"}
    scheduler.checkpoint(request.execution_id, **owner, checkpoint=checkpoint)
    prepared = scheduler.prepare_handover(request.execution_id, **owner)
    assert prepared["interrupt_required"] is False
    retained = stored_checkpoint(scheduler, request.execution_id)
    assert retained["worker_handover"]["turn_kept_running"] is True
    result, client, _, _ = invoke_backend(retained, completed=True)
    assert result.output["message"] == "finished"
    assert client.resume_turn.call_args.args == (checkpoint["backend_session_id"], checkpoint["turn_id"])
    client.run_turn.assert_not_called()
    client.controls.interrupt_turn.assert_not_called()


def test_detach_drains_started_dynamic_call_and_leaves_new_request_unanswered(tmp_path):
    """移交等待已开始的动态工具完成

    验证已启动的动态调用先返回真实结果，解除跟随后的新请求留给下一跟随者处理。

    :design-point:
    """
    client = CodexAppServerClient(tmp_path / "unused.sock")
    client.rpc.send = mock.Mock()
    thread, turn = "thread", "turn"
    started, release = threading.Event(), threading.Event()
    def handle(_params):
        started.set()
        assert release.wait(3)
        return {"content": [{"type": "text", "text": "committed result"}], "isError": False}
    client.work.turns[thread] = TurnWaiter(thread_id=thread, turn_id=turn)
    client.work.dynamic_tool_handlers[thread] = handle
    client.events.answer_server_request(1, "item/tool/call", {"threadId": thread})
    assert started.wait(2)
    try:
        client.work.detach_if_requested(thread, turn, lambda: True)
        client.events.answer_server_request(2, "item/tool/call", {"threadId": thread})
        client.rpc.send.assert_not_called()
    finally:
        release.set()
    # Join only this isolated dispatch thread via the client's tracked count.
    import time
    deadline = time.monotonic() + 3
    while client.work.active_dynamic_calls and time.monotonic() < deadline:
        time.sleep(.01)
    with pytest.raises(CodexTurnDetached):
        client.work.detach_if_requested(thread, turn, lambda: True)
    assert client.rpc.send.call_count == 1
    assert client.rpc.send.call_args.args[0]["id"] == 1
    assert "result" in client.rpc.send.call_args.args[0]


def test_native_command_replay_keeps_real_call_identity_in_one_trace(isolated_portal_case):
    """原生命令接回保留独立事件身份

    验证同文不同command ID仍产生独立段，同一原生事件的重放只引用一次。

    :design-point:
    """
    with isolated_portal_case():
        request, session = _request_session()
        store = AgentDataStore()
        items = [{"id": "exec-one", "type": "commandExecution", "command": "sleep 25",
                  "status": "completed", "exitCode": 0, "aggregatedOutput": ""},
                 {"id": "exec-two", "type": "commandExecution", "command": "sleep 25",
                  "status": "completed", "exitCode": 0, "aggregatedOutput": ""}]
        for _ in range(2):
            projector = AgentDataProjector(store)
            projector.begin(request, session, created_at="2026-10-09T03:52:43Z")
            client = CodexAppServerClient(Path("/tmp/unused-native-handover.sock"))
            waiter = TurnWaiter(thread_id="thread", turn_id="same-turn", event_callback=lambda event: projector.observe(
                provider="codex", backend_route_id="zero", backend_session_id="thread",
                execution_id=request.execution_id, event=event))
            client.follower.replay_completed_items(waiter, {"id": "same-turn", "status": "completed", "items": items})
        segments = [store.segment(identity) for identity in store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))]
        tools = [segment for segment in segments if segment["segment_type"] == "tool"]
        assert len(tools) == 2
        assert {segment["content"]["call_id"] for segment in tools} == {"exec-one", "exec-two"}


@pytest.mark.parametrize("binary_path", NATIVE_BINARIES)
def test_native_handover_reconnects_running_command_without_extra_input(binary_path):
    """原生工具运行中接回不重复执行

    使用真实原生app-server和固定模型验证同turn接回、单次命令和成功结果交付。

    :design-point:
    """
    # AF_UNIX paths stay within the native socket limit independently of pytest's long case name.
    with tempfile.TemporaryDirectory(prefix="cnh-") as directory:
        root = Path(directory)
        model = OfflineResponsesServer(interrupt_during_tool=True)
        with native_test_session(binary_path, root, model, {}) as (old, thread, codex_home):
            detach = threading.Event()
            turns = []
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(old.run_turn, thread, "Run the single offline native tool.",
                    cwd=str(root), turn_started_callback=turns.append,
                    event_callback=model.observe_event, detach_requested=detach.is_set,
                    cancellation_requested=detach.is_set)
                assert model.tool_started.wait(10)
                detach.set()
                with pytest.raises(CodexTurnDetached):
                    pending.result(timeout=8)
            old.close()  # Only the follower disconnects, not the native Provider process.
            new = CodexAppServerClient(old.socket_path, request_timeout_seconds=10)
            new_events = []
            try:
                result = new.resume_turn(thread, turns[0], cwd=str(root),
                    event_callback=new_events.append, timeout_seconds=25)
                assert result.turn_id == turns[0]
                assert result.response == "NATIVE_TEST_DONE"
                commands = [event["params"]["item"] for event in new_events
                            if event.get("method") == "item/completed"
                            and event["params"]["item"].get("type") == "commandExecution"]
                assert len({command["id"] for command in commands}) == 1
                assert all(command["status"] == "completed" and command["exitCode"] == 0 for command in commands)
                assert len(model.requests) == 2
                inputs = json.dumps(model.requests[1:])
                assert "aborted by user" not in inputs
                # Native tool-result wording varies by Provider version. The
                # completed command above and its matching model input are the
                # protocol contract, including across a detached follower.
                tool_results = [item for request in model.requests[1:] for item in request.get("input", [])
                                if item.get("type") == "function_call_output" and item.get("call_id") == "tool-sleep"]
                assert len(tool_results) == 1
                history = [json.loads(line) for file in codex_home.glob(f"sessions/**/*{thread}.jsonl")
                           for line in file.read_text().splitlines()]
                assert not any(record.get("payload", {}).get("type") == "turn_aborted" for record in history)
                assert len([record for record in history if record.get("payload", {}).get("type") == "task_started"]) == 1
            finally:
                new.close()


def test_handover_detaches_goal_without_pausing_or_recreating_it(tmp_path):
    """Goal移交只解除跟随

    验证Goal移交不暂停或重建原生目标，保留既有执行。

    :design-point:
    """
    client = CodexAppServerClient(tmp_path / "unused.sock")
    goal = {"objective": "original objective", "status": "active"}
    client.rpc.request = mock.Mock(return_value={"goal": goal})
    with pytest.raises(CodexTurnDetached):
        client.run_goal("thread", action="observe", detach_requested=lambda: True)
    methods = [call.args[0] for call in client.rpc.request.call_args_list]
    assert "thread/goal/get" in methods
    assert "thread/goal/set" not in methods
    assert "turn/interrupt" not in methods
    assert not client.work.goals
    assert "thread" not in client.threads._busy


def test_resume_detaches_while_native_user_input_is_pending(tmp_path):
    """等待用户输入期间仍可移交

    验证恢复跟随在原生用户输入待答期间可以解除连接。

    :design-point:
    """
    client = CodexAppServerClient(tmp_path / "unused.sock")
    client.rpc.request = mock.Mock(return_value={})
    client.reads.read_active_turn = mock.Mock(return_value={"id": "accepted-turn", "status": "inProgress"})
    client.user_inputs.blocking = mock.Mock(return_value=True)
    with pytest.raises(CodexTurnDetached):
        client.resume_turn("thread", "accepted-turn", cwd=str(tmp_path),
                           detach_requested=lambda: True, timeout_seconds=1)
    assert [call.args[0] for call in client.rpc.request.call_args_list] == ["thread/resume"]
    assert not client.work.turns




@pytest.mark.parametrize("binary_path", NATIVE_BINARIES)
def test_native_pending_dynamic_request_survives_follower_gap(binary_path):
    """跟随断档中的动态工具只执行一次

    使用真实原生app-server验证断档期间的待处理工具由新连接接回一次并返回结果。

    :design-point:
    """
    from tests.cases.test_codex_interrupt_semantics import OfflineResponsesHandler
    class Handler(OfflineResponsesHandler):
        def do_POST(self):
            if len(self.server.requests) != 1:
                return super().do_POST()
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.requests.append(request)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.send_event("response.created", {"response": {"id": "pending-tool-response"}})
            self.send_event("response.output_item.done", {"item": {
                "id": "dynamic-once", "call_id": "dynamic-once", "type": "function_call",
                "name": "handover_proof", "arguments": "{}"}})
            self.send_event("response.completed", {"response": {
                "id": "pending-tool-response", "status": "completed",
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}})
            self.server.pending_emitted.set()
    with tempfile.TemporaryDirectory(prefix="cng-") as directory:
        root = Path(directory)
        model = OfflineResponsesServer(interrupt_during_tool=True)
        model.RequestHandlerClass = Handler
        model.pending_emitted = threading.Event()
        with native_test_session(binary_path, root, model, {}) as (old, _unused, _home):
            thread = old.start_thread({"cwd": str(root), "approvalPolicy": "never", "sandbox": "danger-full-access",
                "dynamicTools": [{"name": "handover_proof", "description": "Isolated no-op proof",
                                  "inputSchema": {"type": "object", "properties": {}}}]})["thread"]["id"]
            detach, turns = threading.Event(), []
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(old.run_turn, thread, "Use the offline proof tools.", cwd=str(root),
                    turn_started_callback=turns.append, event_callback=model.observe_event,
                    detach_requested=detach.is_set)
                assert model.tool_started.wait(10)
                detach.set()
                with pytest.raises(CodexTurnDetached):
                    pending.result(timeout=8)
            old.close()
            assert model.pending_emitted.wait(15)
            calls = []
            new = CodexAppServerClient(old.socket_path, request_timeout_seconds=10)
            try:
                def handle(params):
                    calls.append(params)
                    return {"success": True, "contentItems": [{"type": "inputText", "text": "HANDOVER_TOOL_ACK"}]}
                result = new.resume_turn(thread, turns[0], cwd=str(root), dynamic_tool_handler=handle,
                                         timeout_seconds=15)
                assert result.response == "NATIVE_TEST_DONE"
                assert result.turn_id == turns[0]
                assert len(calls) == 1
                assert calls[0]["tool"] == "handover_proof"
                assert len(model.requests) == 3
                assert "HANDOVER_TOOL_ACK" in json.dumps(model.requests[-1])
                assert "aborted by user" not in json.dumps(model.requests)
            finally:
                new.close()
