""":importance: P2"""
from __future__ import annotations

import asyncio
from datetime import date
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from management_portal import agent_status_backend as monitor
from management_portal import ai_socket_relay as relay
from management_portal.execution import worker_manager
from tests.cases.model_api_test_support import register_relay_resources
from tests.cases.test_execution_worker_manager import _worker, process_boundaries
from tests.cases.test_wall08_gap_helper05 import claude_owner
from tests.cases.test_wall08_round4_session_gaps import active_request, isolated_rpc


def test_claude_signature_reuse_idle_recreate_active_reject(claude_owner, monkeypatch, tmp_path):
    """Claude 会话参数不变时复用，参数变化时空闲则重建、有活动轮次则拒绝"""
    owner = claude_owner
    profile = {"id": "fixture-route", "default_model": "fixture", "models": [
        {"id": name, "enabled": True} for name in ("fixture", "other-model")
    ]}
    monkeypatch.setattr(owner, "_provider_profile", lambda *_args: profile)
    monkeypatch.setattr(owner, "_provider_environment", lambda *_args: ({}, profile))
    monkeypatch.setattr(owner, "ClaudeAgentOptions", lambda **kwargs: SimpleNamespace(**kwargs))
    clients = []

    def sdk_client(options):
        client = SimpleNamespace(options=options, connect=AsyncMock(), disconnect=AsyncMock())
        clients.append(client)
        return client

    monkeypatch.setattr(owner, "ClaudeSDKClient", sdk_client)

    async def check():
        daemon = owner.ClaudeCodeDaemon()
        params = {"session_key": "fixture", "cwd": str(tmp_path / "cwd"), "config_dir": str(tmp_path / "config")}
        changed_fields = {"cwd": str(tmp_path / "other-cwd"), "config_dir": str(tmp_path / "other-config"),
            "provider_profile": "other-profile", "model": "other-model", "effort": "high", "is_owner": True,
            "permission_profile": "synthetic", "system_prompt_append": "new instructions", "add_dirs": [str(tmp_path / "extra")]}
        for field, value in changed_fields.items():
            handle = await daemon._handle_for(params)
            assert await daemon._handle_for(params) is handle
            modified = {**params, field: value}
            await handle.lock.acquire()
            try:
                with pytest.raises(RuntimeError, match="active turn"):
                    await daemon._handle_for(modified)
                handle.client.disconnect.assert_not_awaited()
            finally:
                handle.lock.release()
            replacement = await daemon._handle_for(modified)
            assert replacement is not handle
            handle.client.disconnect.assert_awaited_once()
            assert replacement.signature != handle.signature
            await daemon.close_session({"session_key": "fixture"})
        assert all(client.connect.await_count == 1 for client in clients)

    asyncio.run(check())


def test_claude_fork_transcript_ambiguity_and_existing_target_preserved(claude_owner, tmp_path):
    """Claude 分叉会话迁移转录时源文件不唯一或目标已存在就报错且不改动文件"""
    owner = claude_owner
    projects = tmp_path / "projects"
    first = projects / "source-a" / "fixture.jsonl"
    second = projects / "source-b" / "fixture.jsonl"
    target = projects / "synthetic-workspace" / "fixture.jsonl"
    for transcript in [first, second, target]:
        transcript.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("first")
    second.write_text("second")
    with pytest.raises(RuntimeError, match="2 files"):
        owner.ClaudeCodeDaemon._relocate_forked_session_file("fixture", tmp_path, tmp_path)
    assert first.read_text() == "first" and second.read_text() == "second" and not target.exists()
    second.unlink()
    target.touch()
    with pytest.raises(RuntimeError, match="already exists"):
        owner.ClaudeCodeDaemon._relocate_forked_session_file("fixture", tmp_path, tmp_path)
    assert first.read_text() == "first" and target.read_bytes() == b""
    target.unlink()
    assert owner.ClaudeCodeDaemon._relocate_forked_session_file("fixture", tmp_path, tmp_path) == target
    assert target.read_text() == "first" and not first.exists()


def test_claude_release_and_daemon_exit_preserve_files_and_remove_socket(claude_owner, monkeypatch, tmp_path):
    """Claude 会话忙时拒绝释放，空闲释放与守护进程退出保留转录文件并删除 socket"""
    owner = claude_owner

    async def check():
        daemon = owner.ClaudeCodeDaemon()
        handle = owner.SessionHandle(key="fixture", signature=(), client=SimpleNamespace(disconnect=AsyncMock()), session_id="native-fixture")
        daemon._sessions[handle.key] = handle
        daemon._session_ids[handle.session_id] = handle.key
        daemon._session_setup_locks[handle.key] = asyncio.Lock()
        transcript = tmp_path / "transcript.jsonl"
        binding = tmp_path / "binding.json"
        transcript.write_text("synthetic historical message")
        binding.write_text(json.dumps({"backend_session_id": handle.session_id}))
        before = {path: path.read_bytes() for path in [transcript, binding]}
        handle.active_turn_id = "busy"
        assert not (await daemon.close_session({"session_id": handle.session_id}))["closed"]
        handle.client.disconnect.assert_not_awaited()
        handle.active_turn_id = ""
        assert (await daemon.close_session({"session_id": handle.session_id}))["closed"]
        handle.client.disconnect.assert_awaited_once()
        assert not daemon._sessions and not daemon._session_ids
        daemon._sessions[handle.key] = handle
        handle.client.disconnect.reset_mock()
        monkeypatch.setattr(owner, "ClaudeCodeDaemon", lambda: daemon)
        callbacks = []
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", lambda _signal, callback: callbacks.append(callback))
        socket_path = tmp_path / "daemon.sock"
        task = asyncio.create_task(owner._serve(socket_path))
        try:
            for _attempt in range(100):
                if callbacks:
                    break
                await asyncio.sleep(0.01)
            assert callbacks and socket_path.exists()
            callbacks[0]()
            await asyncio.wait_for(task, 2)
            assert not socket_path.exists() and not daemon._sessions
            handle.client.disconnect.assert_awaited_once()
            assert {path: path.read_bytes() for path in before} == before
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(check())


def test_worker_manager_health_identity_and_failed_replacement_keep_old(tmp_path, monkeypatch):
    """新 Worker 的健康检查 worker_id 与 build_id 都匹配才算就绪，替换失败时保留旧 Worker"""
    manager = worker_manager.ExecutionWorkerManager()
    old = _worker(tmp_path, "old", 101)
    replacement = _worker(tmp_path, "new", 102)
    manager._current = old
    monkeypatch.setattr(manager, "_spawn", lambda: replacement)
    activate = Mock()
    monkeypatch.setattr(manager, "_activate_replacement", activate)
    states = iter([
        {"ok": True, "worker_id": "wrong", "build_id": "new"},
        {"ok": True, "worker_id": "new", "build_id": "wrong"},
        {"ok": False, "worker_id": "new", "build_id": "new"},
        {"ok": True, "worker_id": "new", "build_id": "new"},
    ])
    # Afterwards the exited Worker reports nothing (it was not retired).
    health = Mock(side_effect=lambda _worker: next(states, {}))
    monkeypatch.setattr(manager, "_health", health)
    monkeypatch.setattr(worker_manager.time, "sleep", lambda _seconds: None)
    manager._wait_ready(replacement)
    assert health.call_count == 4
    replacement.process.returncode = 7
    with pytest.raises(RuntimeError, match="exited with code 7"):
        manager._replace()
    assert manager._current is old and not old.process.terminated and not manager._draining
    activate.assert_not_called()
    monkeypatch.setattr(manager, "_replace", lambda: None)
    monkeypatch.setattr(worker_manager.process_runtime, "install_shutdown_handlers", lambda *a, **k: {})
    monkeypatch.setattr(worker_manager.process_runtime, "restore_shutdown_handlers", lambda previous: None)
    monkeypatch.setattr(manager, "_write_health_safely", lambda: True)
    old.process.returncode = 8
    replacement_attempts = []

    def replace_exited_worker():
        replacement_attempts.append("replace")
        manager._stopping.set()

    monkeypatch.setattr(manager, "_replace", replace_exited_worker)
    assert manager.run() == 0
    assert replacement_attempts == ["replace"]


def test_relay_reloads_resource_admission_and_pins_model(tmp_path, monkeypatch):
    """中继每次请求重新读取资源准入并固定模型，资源停用、缺失或改模型时返回 403"""
    resource = {"id": "fixture", "provider": "synthetic", "model": "fixed", "enabled": True}
    register_relay_resources(resource)
    forward = Mock(return_value=(200, {"synthetic": True}, "application/json"))
    monkeypatch.setattr(relay, "_forward_chat_completion", forward)
    frame = {"version": 1, "resource": "fixture", "payload": {"messages": []}}
    assert relay._handle_request(frame)["status"] == 200
    assert forward.call_args.args == ("synthetic", {"messages": [], "model": "fixed"})
    forward.reset_mock()
    assert relay._handle_request({**frame, "payload": {"model": "other"}})["status"] == 403
    register_relay_resources({**resource, "enabled": False})
    assert relay._handle_request(frame)["status"] == 403
    assert relay._handle_request({**frame, "resource": "absent"})["status"] == 403
    forward.assert_not_called()
    networks = relay._parse_allowed_networks("127.0.0.1,10.8.0.0/24,::1")
    assert relay._peer_allowed("10.8.0.4", networks) and relay._peer_allowed("::1", networks)
    assert not relay._peer_allowed("10.8.1.4", networks) and not relay._peer_allowed("invalid", networks)
def test_resume_checks_current_rpc_identity_without_creating_replacement(isolated_rpc):
    """恢复执行时关联标识、服务类型、Provider 或调用者不符即拒绝且不新建执行"""
    request, owner = active_request(isolated_rpc)
    service = isolated_rpc.service
    before = service.status(request.execution_id, actor=request.actor)
    assert service.resume(request.execution_id, actor=request.actor, service_type="agent_session", provider="codex")["execution_id"] == request.execution_id
    for expected in [{"correlation_id": "different"}, {"service_type": "chat_completion"}, {"service_type": "agent_session", "provider": "claude"}]:
        with pytest.raises(ValueError, match=r"execution (correlation|capability) does not match the request"):
            service.resume(request.execution_id, actor=request.actor, **expected)
    with pytest.raises(KeyError):
        service.resume(request.execution_id, actor="different-fixture-actor")
    assert service.status(request.execution_id, actor=request.actor) == before


def test_usage_current_calendar_windows_ledger_boundaries(monkeypatch):
    """用量的今日、本周、本月、本年窗口按日历边界汇总分派与其他 token"""
    monkeypatch.setattr(monitor, "_date_today", lambda: date(2026, 9, 19))
    resident = {"daily_assigned": {"2026-08-31": 1000, "2026-09-01": 40, "2026-09-14": 10,
                "2026-09-17": 100, "2026-09-18": 100, "2026-09-19": 30, "2026-09-20": 9999},
                "daily_others": {"2026-09-18": 5, "2026-09-19": 7}}
    windows = monitor._make_token_windows(resident)
    assert {key: value["total_tokens"] for key, value in windows.items()} == {
        "today": 37, "week": 252, "month": 292, "year": 1292}
    assert windows["today"] == {"total_tokens": 37, "assigned_tokens": 30, "other_tokens": 7}
    for value in windows.values():
        assert value["total_tokens"] == value["assigned_tokens"] + value["other_tokens"]


def test_kimi_release_calls_abort_without_archive_or_replacement(tmp_path, monkeypatch):
    """kimi 释放会话只调用 abort 接口，不归档也不替换原会话"""
    from management_portal.execution import backends
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    from management_portal.execution.session_manager import RuntimeSession
    from management_portal.kimi_code_client import KimiCodeClient

    client = KimiCodeClient(base_url="http://synthetic.invalid", events_root=tmp_path)
    transport = Mock(return_value={"accepted": True})
    monkeypatch.setattr(client, "_request", transport)
    monkeypatch.setattr(backends, "shared_kimi_client", lambda: client)
    request = ExecutionRequest.new(actor="fixture", prompt="release existing synthetic session", service_type="agent_session", provider="kimi", operation="release",
        session=SessionOptions(mode="persistent", binding_id="fixture-binding"))
    invocation = backends.BackendInvocation(request=request,
        session=RuntimeSession(mode="persistent", binding_id="fixture-binding", backend_session_id="native/fixture", is_new_backend_session=False),
        user_prompt="", system_context="", attach_system_context=False, is_new_backend_session=False)
    context = backends.BackendContext(emit_event=lambda *_args: None, cancellation_requested=lambda: False, set_interrupt_target=lambda *_args: None)
    result = backends.KimiBackend().execute(invocation, context)
    assert result.output["operation"] == "release"
    transport.assert_called_once_with("POST", "sessions/native%2Ffixture:abort", {})
    assert invocation.session.backend_session_id == "native/fixture"


def test_codex_client_cache_is_per_socket_and_controls_keep_exact_turn(tmp_path, monkeypatch):
    """codex 客户端按 socket 分别缓存，turn/steer 必须携带指定 turn 编号"""
    from management_portal import codex_app_server_client as owner

    monkeypatch.setattr(owner, "_CLIENTS", {})
    first = owner.client_for_socket(tmp_path / "first.sock")
    second = owner.client_for_socket(tmp_path / "second.sock")
    try:
        assert owner.client_for_socket(tmp_path / "first.sock") is first
        assert second is not first and first._state_lock is not second._state_lock
        assert first._thread_lock("same-native-id") is not second._thread_lock("same-native-id")
        transport = Mock(return_value={"accepted": True})
        monkeypatch.setattr(first, "request", transport)
        inputs = [{"type": "text", "text": "synthetic"}, {"type": "image", "url": "data:image/png;base64,Zml4dHVyZQ=="}]
        first.steer_turn("native", "exact-turn", inputs)
        assert transport.call_args.args == ("turn/steer", {"threadId": "native", "expectedTurnId": "exact-turn", "input": inputs})
        with pytest.raises(ValueError, match="required"):
            first.steer_turn("native", "", inputs)
        assert transport.call_count == 1
        assert not second._thread_activity
    finally:
        first.close()
        second.close()
