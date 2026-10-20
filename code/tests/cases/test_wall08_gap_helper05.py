from __future__ import annotations

import asyncio
import copy
import importlib.util
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from management_portal.codex_app_server_client import (
    CodexAppServerClient,
    CodexAppServerError,
    CodexTurnAuthenticationError,
    CodexTurnContinuationRequired,
    CodexTurnInterrupted,
)
from tests.cases.claude_sdk_support import synthetic_sdk
from tests.cases.test_codex_app_server_client import FakeAppServer


CLAUDE_POINT = "decision_3948ce9b9443#71a46fc94507de17"
CODEX_POINT = "decision_3e8c2a7f91d4#c93207b552a75f1f"


@pytest.fixture
def claude_owner(monkeypatch):
    sdk = synthetic_sdk()
    sdk.project_key_for_directory = lambda path: "synthetic-workspace"
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    source = Path(__file__).resolve().parents[2] / "management_portal/claude_code_daemon.py"
    spec = importlib.util.spec_from_file_location("wall08_helper05_claude_owner", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


class SyntheticSdkClient:
    def __init__(self, module, *, fail=False):
        self.module = module
        self.fail = fail
        self.queries = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def query(self, prompt):
        self.queries.append(prompt)
        # The daemon sends its prompt as a stream of tagged user messages.
        self.prompt_uuid = None
        if hasattr(prompt, "__aiter__"):
            async for item in prompt:
                self.prompt_uuid = item.get("uuid")
        self.started.set()

    async def receive_response(self):
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        # The CLI echoes the prompt; the daemon takes only the result after it.
        yield self.module.UserMessage(content="fixture input", uuid=self.prompt_uuid)
        yield self.module.AssistantMessage(
            content=[self.module.TextBlock("synthetic SDK answer")], model="fixture-only", session_id="fixture-session")
        yield self.module.ResultMessage(subtype="error" if self.fail else "success", is_error=self.fail,
            # A successful ResultMessage carries the final answer, which the daemon returns.
            session_id="fixture-session", result="synthetic failure" if self.fail else "synthetic SDK answer", usage={},
            model_usage={}, duration_ms=1, duration_api_ms=1, num_turns=1, errors=["synthetic failure"] if self.fail else [])


def daemon_fixture(module, monkeypatch, *, fail=False):
    daemon = module.ClaudeCodeDaemon()
    sdk = SyntheticSdkClient(module, fail=fail)
    handle = module.SessionHandle(key="fixture-session-key", signature=(), client=sdk)
    monkeypatch.setattr(daemon, "_handle_for", AsyncMock(return_value=handle))
    events = []

    async def emit(event):
        events.append(event)

    return daemon, sdk, emit, events


@pytest.mark.parametrize("peer_waiter", [False, True])
def test_claude_cancelled_waiter_leaves_real_shared_run_alive(claude_owner, monkeypatch, record_property, peer_waiter):
    """取消等待中的 Claude 轮次调用不中断共享运行，同一轮只查询并完成一次"""
    record_property("point_id", CLAUDE_POINT)
    record_property("fixture_kind", "synthetic SDK; real daemon run_turn/_run_turn_once; no daemon socket or Provider")

    async def scenario():
        daemon, sdk, emit, events = daemon_fixture(claude_owner, monkeypatch)
        params = {"session_key": "fixture-session-key", "turn_id": "fixture-turn", "prompt": "fixture input"}
        first_waiter = asyncio.create_task(daemon.run_turn(params, emit))
        await asyncio.wait_for(sdk.started.wait(), 1)
        shared = daemon._turn_tasks[(params["session_key"], params["turn_id"])]
        second_waiter = asyncio.create_task(daemon.run_turn(dict(params), emit)) if peer_waiter else None
        await asyncio.sleep(0)
        assert len(daemon._turn_tasks) == 1 and len(sdk.queries) == 1
        first_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_waiter
        assert not shared.done() and not shared.cancelled() and not sdk.cancelled
        sdk.release.set()
        result = await asyncio.wait_for(second_waiter if second_waiter is not None else shared, 1)
        assert result["response"] == "synthetic SDK answer"
        repeated = await daemon.run_turn(dict(params), emit)
        assert repeated == result and len(sdk.queries) == 1
        assert daemon._turn_tasks[(params["session_key"], params["turn_id"])] is shared
        assert [event["method"] for event in events].count("turn/started") == 1
        assert [event["method"] for event in events].count("turn/completed") == 1
        assert not sdk.cancelled

    asyncio.run(scenario())


@pytest.mark.parametrize("dimension", ["session_key", "turn_id"])
def test_claude_task_identity_uses_both_tuple_dimensions(claude_owner, monkeypatch, record_property, dimension):
    """Claude 轮次任务按 session_key 与 turn_id 共同区分，任一不同即独立运行"""
    record_property("point_id", CLAUDE_POINT)
    record_property("fixture_kind", "synthetic SDK; real shared-task registry and run control")

    async def scenario():
        daemon, sdk, emit, events = daemon_fixture(claude_owner, monkeypatch)
        params = {"session_key": "fixture-session-key", "turn_id": "fixture-turn", "prompt": "fixture input"}
        different = {**params, dimension: params[dimension] + "-different"}
        first = asyncio.create_task(daemon.run_turn(params, emit))
        await asyncio.wait_for(sdk.started.wait(), 1)
        second = asyncio.create_task(daemon.run_turn(different, emit))
        await asyncio.sleep(0)
        assert len(daemon._turn_tasks) == 2
        tasks = list(daemon._turn_tasks.values())
        assert tasks[0] is not tasks[1]
        sdk.release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert len(sdk.queries) == 2 and len(results) == 2
        assert [result["turn_id"] for result in results] == [params["turn_id"], different["turn_id"]]
        assert [event["method"] for event in events].count("turn/started") == 2

    asyncio.run(scenario())


def test_claude_duplicate_failed_run_keeps_same_task_and_error(claude_owner, monkeypatch, record_property):
    """重复调用失败的 Claude 轮次复用同一任务并得到同一错误，不发出 turn/completed"""
    record_property("point_id", CLAUDE_POINT)
    record_property("fixture_kind", "synthetic SDK error; real daemon error propagation and task deduplication")

    async def scenario():
        daemon, sdk, emit, events = daemon_fixture(claude_owner, monkeypatch, fail=True)
        params = {"session_key": "fixture-session-key", "turn_id": "fixture-turn", "prompt": "fixture input"}
        first = asyncio.create_task(daemon.run_turn(params, emit))
        await asyncio.wait_for(sdk.started.wait(), 1)
        second = asyncio.create_task(daemon.run_turn(params, emit))
        await asyncio.sleep(0)
        shared = daemon._turn_tasks[(params["session_key"], params["turn_id"])]
        sdk.release.set()
        errors = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 1)
        assert all(isinstance(error, RuntimeError) and "synthetic failure" in str(error) for error in errors)
        with pytest.raises(RuntimeError, match="synthetic failure"):
            await daemon.run_turn(params, emit)
        assert daemon._turn_tasks[(params["session_key"], params["turn_id"])] is shared
        assert len(sdk.queries) == 1
        assert not any(event["method"] == "turn/completed" for event in events)

    asyncio.run(scenario())


class CompletionBodyFixtureServer(FakeAppServer):
    def __init__(self, socket_path, *, missing_notice_body, stored_items):
        super().__init__(socket_path)
        self.missing_notice_body = missing_notice_body
        self.stored_items = stored_items
        self.completion_request_count = None

    def _write(self, connection, payload):
        method = payload.get("method")
        if method == "item/completed" and (payload.get("params", {}).get("item") or {}).get("type") == "agentMessage":
            return
        if method == "turn/completed":
            self.last_turn = {**self.last_turn, "items": copy.deepcopy(self.stored_items), "itemsView": "full"}
            payload = copy.deepcopy(payload)
            payload["params"]["turn"] = {**self.last_turn, "items": []} if self.missing_notice_body else self.last_turn
            self.completion_request_count = len(self.requests)
        return super()._write(connection, payload)


def final_items():
    return [{"id": "first-final", "type": "agentMessage", "phase": "final_answer", "text": "first canonical answer"},
            {"id": "second-final", "type": "agentMessage", "phase": "final_answer", "text": "later acknowledgement"}]


def after_completion_reads(server):
    assert server.completion_request_count is not None
    return [request for request in server.requests[server.completion_request_count:]
            if request["method"] == "thread/turns/list"
            and request["params"].get("itemsView") == "full"]


def test_codex_full_completion_keeps_first_canonical_final(tmp_path, record_property):
    """codex 完成通知带完整正文时取第一条最终回答且不再补读"""
    record_property("point_id", CODEX_POINT)
    record_property("fixture_kind", "local synthetic WebSocket App Server; real native client; no Provider")
    server = CompletionBodyFixtureServer(tmp_path / "fixture.sock", missing_notice_body=False, stored_items=final_items())
    server.start()
    client = CodexAppServerClient(server.socket_path)
    try:
        result = client.run_turn("fixture-thread", "synthetic request", cwd=str(tmp_path))
        assert result.response == "first canonical answer"
        assert not after_completion_reads(server)
    finally:
        client.close()
        server.thread.join(timeout=2)


def test_codex_bodyless_completion_reads_exact_turn_and_recovers_first_final(tmp_path, record_property):
    """codex 完成通知不带正文时按 turn 读回完整记录并取第一条最终回答"""
    record_property("point_id", CODEX_POINT)
    record_property("fixture_kind", "bodyless completion notification; full persisted turn in local protocol fixture")
    server = CompletionBodyFixtureServer(tmp_path / "fixture.sock", missing_notice_body=True, stored_items=final_items())
    server.start()
    client = CodexAppServerClient(server.socket_path)
    try:
        result = client.run_turn("fixture-thread", "synthetic request", cwd=str(tmp_path))
        assert result.response == "first canonical answer"
        reads = after_completion_reads(server)
        assert reads and reads[-1]["params"]["threadId"] == "fixture-thread"
        assert reads[-1]["params"]["itemsView"] == "full"
        assert result.turn_id == server.last_turn["id"]
    finally:
        client.close()
        server.thread.join(timeout=2)


def test_codex_bodyless_completion_reads_then_explicitly_rejects_empty_turn(tmp_path, record_property):
    """codex 完成通知不带正文且读回的 turn 为空时报告缺少最终回答，不重发 turn"""
    record_property("point_id", CODEX_POINT)
    record_property("fixture_kind", "bodyless completion and empty persisted turn; synthetic local protocol only")
    server = CompletionBodyFixtureServer(tmp_path / "fixture.sock", missing_notice_body=True, stored_items=[])
    server.start()
    client = CodexAppServerClient(server.socket_path)
    try:
        with pytest.raises(CodexTurnContinuationRequired) as error:
            client.run_turn("fixture-thread", "synthetic request", cwd=str(tmp_path))
        assert error.value.reason == "missing_final_response"
        assert error.value.thread_id == "fixture-thread" and error.value.turn_id == server.last_turn["id"]
        reads = after_completion_reads(server)
        assert reads and reads[-1]["params"]["itemsView"] == "full"
        assert server.methods.count("turn/start") == 1
    finally:
        client.close()
        server.thread.join(timeout=2)


def test_codex_protocol_fixture_exposes_saved_exact_turn_without_generation(tmp_path, record_property):
    """codex 客户端 read_turn 只读取指定 thread 中已保存的 turn，不生成新 turn"""
    record_property("point_id", CODEX_POINT)
    record_property("fixture_kind", "positive fixture capability probe; real read_turn RPC, no turn generation")
    server = CompletionBodyFixtureServer(tmp_path / "fixture.sock", missing_notice_body=True, stored_items=final_items())
    server.last_turn = {"id": "saved-fixture-turn", "status": "completed", "itemsView": "full", "items": final_items()}
    server.start()
    client = CodexAppServerClient(server.socket_path)
    try:
        saved = client.read_turn("fixture-thread", "saved-fixture-turn")
        assert saved["id"] == "saved-fixture-turn"
        assert saved["items"][0]["text"] == "first canonical answer" and saved["itemsView"] == "full"
        assert client.read_turn("fixture-thread", "different-turn") is None
        assert server.methods.count("thread/turns/list") == 2
        assert "turn/start" not in server.methods and "thread/resume" not in server.methods
        assert all(request["params"]["threadId"] == "fixture-thread"
                   for request in server.requests if request["method"] == "thread/turns/list")
    finally:
        client.close()
        server.thread.join(timeout=2)


class CompletionReadBoundaryServer(CompletionBodyFixtureServer):
    def __init__(self, socket_path, *, snapshot_patch=None, omit_snapshot=False,
                 unrelated_first=False, read_error=False, cancel_at=""):
        super().__init__(socket_path, missing_notice_body=True, stored_items=final_items())
        self.snapshot_patch = snapshot_patch or {}
        self.omit_snapshot = omit_snapshot
        self.unrelated_first = unrelated_first
        self.read_error = read_error
        self.cancel_at = cancel_at
        self.cancelled = threading.Event()

    def _write(self, connection, payload):
        if payload.get("method") == "turn/completed" and self.cancel_at == "notice":
            self.cancelled.set()
        if self.completion_request_count is not None and isinstance(payload.get("result", {}).get("data"), list):
            payload = copy.deepcopy(payload)
            if self.read_error:
                payload = {"id": payload["id"], "error": {"code": -32001, "message": "fixture read denied"}}
            else:
                turns = [{**turn, **copy.deepcopy(self.snapshot_patch)} for turn in payload["result"]["data"]]
                if self.omit_snapshot:
                    turns = []
                if self.unrelated_first:
                    turns.insert(0, {**self.last_turn, "id": "unrelated-newer-turn", "items": [
                        {"type": "agentMessage", "phase": "final_answer", "text": "unrelated answer"}
                    ]})
                payload["result"]["data"] = turns
            if self.cancel_at == "read":
                self.cancelled.set()
        return super()._write(connection, payload)


@pytest.mark.parametrize("server_options,expected_error,match", [
    pytest.param({"snapshot_patch": {"id": "other-turn"}}, CodexTurnContinuationRequired,
                 "final response", id="wrong-turn"),
    pytest.param({"snapshot_patch": {"status": "inProgress"}}, CodexTurnContinuationRequired,
                 "final response", id="in-progress"),
    pytest.param({"snapshot_patch": {"status": ""}}, CodexAppServerError,
                 "Codex turn failed", id="missing-status"),
    pytest.param({"snapshot_patch": {"status": "failed", "error": {"message": "fixture failure"}}},
                 CodexAppServerError, "fixture failure", id="failed-with-body"),
    pytest.param({"snapshot_patch": {"status": "interrupted"}}, CodexTurnInterrupted,
                 None, id="interrupted-with-body"),
    pytest.param({"snapshot_patch": {"status": "cancelled"}}, CodexAppServerError,
                 "Codex turn cancelled", id="cancelled-with-body"),
    pytest.param({"snapshot_patch": {"error": {"codexErrorInfo": "unauthorized", "message": "fixture auth failure"}}},
                 CodexTurnAuthenticationError, None, id="completed-with-error"),
    pytest.param({"omit_snapshot": True}, CodexTurnContinuationRequired,
                 "final response", id="missing-snapshot"),
    pytest.param({"snapshot_patch": {"items": [
        {"type": "agentMessage", "phase": "final_answer", "text": "  \n "}
    ]}}, CodexTurnContinuationRequired, "final response", id="whitespace-final"),
    pytest.param({"read_error": True}, CodexAppServerError,
                 "fixture read denied", id="read-rpc-error"),
])
def test_codex_completion_read_rejects_invalid_saved_turn(tmp_path, record_property, server_options,
                                                        expected_error, match):
    """codex 补读的 turn 编号不符、未完成、失败、回答为空或读取出错时抛出对应错误"""
    record_property("point_id", CODEX_POINT)
    record_property("fixture_kind", "synthetic local protocol; actual run/read/error owner; no Provider")
    server = CompletionReadBoundaryServer(tmp_path / "fixture.sock", **server_options)
    server.start()
    client = CodexAppServerClient(server.socket_path)
    try:
        with pytest.raises(expected_error, match=match) as raised:
            client.run_turn("fixture-thread", "synthetic request", cwd=str(tmp_path))
        assert type(raised.value) is expected_error
        reads = after_completion_reads(server)
        assert len(reads) == 1 and reads[0]["params"]["threadId"] == "fixture-thread"
        assert reads[0]["params"]["itemsView"] == "full"
        assert server.methods.count("turn/start") == 1
        assert not client._turn_waiters and not client._busy_threads
        if isinstance(raised.value, CodexTurnContinuationRequired):
            assert raised.value.reason == "missing_final_response"
            assert raised.value.turn_id == server.last_turn["id"]
            assert raised.value.thread_id == "fixture-thread"
    finally:
        client.close()
        server.thread.join(timeout=2)


def test_codex_completion_read_preserves_cancellation(tmp_path, record_property, subtests, isolated_portal_case):
    """codex 在完成通知或补读阶段收到取消时中断本轮并清理等待状态"""
    for cancel_at in ["notice", "read"]:
        with subtests.test(cancel_at=cancel_at), isolated_portal_case() as case_path:
            tmp_path = case_path
            record_property("point_id", CODEX_POINT)
            record_property("fixture_kind", "protocol cancellation before/after actual hydration; no Provider")
            server = CompletionReadBoundaryServer(tmp_path / "fixture.sock", cancel_at=cancel_at)
            server.start()
            client = CodexAppServerClient(server.socket_path)
            try:
                with pytest.raises(InterruptedError):
                    client.run_turn("fixture-thread", "synthetic request", cwd=str(tmp_path),
                                    cancellation_requested=server.cancelled.is_set)
                assert len(after_completion_reads(server)) == (1 if cancel_at == "read" else 0)
                assert server.methods.count("turn/start") == 1
                assert server.methods.count("turn/interrupt") <= 1
                assert not client._turn_waiters and not client._busy_threads
            finally:
                client.close()
                server.thread.join(timeout=2)


def test_codex_completion_read_ignores_newer_unrelated_turn(tmp_path, record_property):
    """codex 补读完成轮次时忽略更新的无关 turn，返回本轮回答"""
    record_property("point_id", CODEX_POINT)
    record_property("fixture_kind", "two synthetic turns returned by real exact-turn read owner")
    server = CompletionReadBoundaryServer(tmp_path / "fixture.sock", unrelated_first=True)
    server.start()
    client = CodexAppServerClient(server.socket_path)
    try:
        result = client.run_turn("fixture-thread", "synthetic request", cwd=str(tmp_path))
        assert result.response == "first canonical answer"
        assert result.turn_id == server.last_turn["id"] and result.thread_id == "fixture-thread"
        assert len(after_completion_reads(server)) == 1
        assert server.methods.count("turn/start") == 1
    finally:
        client.close()
        server.thread.join(timeout=2)


def test_codex_completion_read_rejects_transformed_turn_identity(tmp_path, record_property):
    """turn_snapshot_transform 改变 turn 编号时 codex 客户端拒绝该结果"""
    record_property("point_id", CODEX_POINT)
    record_property("fixture_kind", "real snapshot transformation owner cannot change result turn identity")
    server = CompletionReadBoundaryServer(tmp_path / "fixture.sock")
    server.start()
    client = CodexAppServerClient(server.socket_path)
    try:
        with pytest.raises(CodexAppServerError, match="does not match the requested turn"):
            client.run_turn("fixture-thread", "synthetic request", cwd=str(tmp_path),
                            turn_snapshot_transform=lambda turn: {**turn, "id": "different-turn"})
        assert len(after_completion_reads(server)) == 1
        assert server.methods.count("turn/start") == 1
    finally:
        client.close()
        server.thread.join(timeout=2)
