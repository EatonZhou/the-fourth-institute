"""A rejected lifecycle load may wait; an accepted model input is never replayed.

:module: execution.providers
:design: decision_3e8c2a7f91d4
"""
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import pytest

from management_portal import codex_app_server_threads as threads
from management_portal.codex_app_server_client import CodexAppServerClient
from management_portal.codex_app_server_protocol import CodexAppServerError, CodexThreadClosing
from management_portal.society_service.resident_record_domain import runtime_failure_summary
from tests.cases.test_codex_app_server_client import FakeAppServer


def closing(thread_id="same"):
    return CodexAppServerError(
        f"Codex app-server request failed: thread {thread_id} is closing; "
        "retry thread/resume after the thread is closed"
    )


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(threads, "time", SimpleNamespace(
        monotonic=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    ))
    return now


def test_closing_retries_the_same_load_with_a_bounded_budget(clock):
    rpc = Mock(side_effect=[closing(), closing(), {"thread": {"id": "same"}}])
    params = {"threadId": "same", "modelProvider": "original", "config": {"sandbox": "preserve"}}
    assert threads.resume_thread(rpc, params, timeout=2) == {"thread": {"id": "same"}}
    assert [c.args for c in rpc.call_args_list] == [("thread/resume", params)] * 3
    assert 0 < clock[0] < 2
    assert 0 < rpc.call_args.kwargs["timeout"] < 2


def test_persistent_closing_expires_without_turn_or_account_changes(clock):
    rpc = Mock(side_effect=closing())
    with pytest.raises(CodexThreadClosing) as caught:
        threads.resume_thread(rpc, {"threadId": "same"}, timeout=0.25)
    assert clock[0] == pytest.approx(0.25)
    assert caught.value.error_code == "provider_thread_closing"
    assert caught.value.error_details["input_replayed"] is False
    assert all(c.args == ("thread/resume", {"threadId": "same"}) for c in rpc.call_args_list)
    assert runtime_failure_summary(str(caught.value)) == "Codex 会话关闭尚未完成，请稍后重试"


def test_cancellation_interrupts_close_wait_before_another_request(clock):
    rpc = Mock(side_effect=closing())
    with pytest.raises(InterruptedError):
        threads.resume_thread(rpc, {"threadId": "same"}, cancellation_requested=lambda: clock[0] >= 0.05)
    assert rpc.call_count == 1
    assert clock[0] == pytest.approx(0.05)


@pytest.mark.parametrize("error", [closing("other"), CodexAppServerError("connection closed"),
                                  CodexAppServerError("thread same already has an active writer"),
                                  TimeoutError("request outcome unknown")])
def test_only_explicit_closing_of_this_thread_is_retried(error, clock):
    rpc = Mock(side_effect=error)
    with pytest.raises(type(error)):
        threads.resume_thread(rpc, {"threadId": "same"})
    assert rpc.call_count == 1
    assert clock[0] == 0


def test_closing_after_provider_identity_recovery_keeps_that_identity(clock):
    mismatch = CodexAppServerError("This chat uses openai, but your current provider is openai_http. Switch to openai to continue.")
    rpc = Mock(side_effect=[mismatch, {"thread": {"id": "same", "modelProvider": "openai"}},
                            closing(), {"thread": {"id": "same"}}])
    threads.resume_thread(rpc, {"threadId": "same"})
    assert [c.args[0] for c in rpc.call_args_list] == ["thread/resume", "thread/read", "thread/resume", "thread/resume"]
    assert rpc.call_args.args[1] == {"threadId": "same", "modelProvider": "openai"}


def test_real_disconnect_keeps_its_distinct_user_message():
    assert runtime_failure_summary("Codex app-server connection lost") == "Codex app-server 连接中断"


class ClosingServer(FakeAppServer):
    def _write(self, connection, payload):
        request = self.requests[-1] if self.requests else {}
        if (request.get("method") == "thread/resume" and self.methods.count("thread/resume") == 1
                and payload.get("id") == request.get("id") and "result" in payload):
            payload = {"id": payload["id"], "error": {"message": str(closing(request["params"]["threadId"])).removeprefix("Codex app-server request failed: ")}}
        return super()._write(connection, payload)


def test_socket_closing_then_resume_submits_user_input_exactly_once(tmp_path):
    socket_dir = TemporaryDirectory(prefix="codex-close-", dir="/tmp")
    server = ClosingServer(Path(socket_dir.name) / "server.sock")
    server.start()
    client = CodexAppServerClient(server.socket_path)
    try:
        result = client.run_turn("original-thread", "original input", cwd=str(tmp_path))
        assert result.response == "finished"
        assert server.methods.count("thread/resume") == 2
        assert server.methods.count("turn/start") == 1
        assert "thread/start" not in server.methods
        assert "thread/fork" not in server.methods
        start = next(r for r in server.requests if r["method"] == "turn/start")
        assert start["params"]["threadId"] == "original-thread"
    finally:
        client.close()
        socket_dir.cleanup()
