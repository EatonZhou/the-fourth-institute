"""An OpenCode API-key route against the real OpenCode and a recording Chat Completions endpoint.

Set ``PORTAL_OPENCODE_NATIVE_TEST_BINARY`` to an ``opencode`` executable to run it.

:module: execution.providers
:importance: P1
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from management_portal import opencode_client, opencode_provider_catalog
from management_portal.execution import account_registrations
from tests.cases.model_api_test_support import register_apis

BINARY = os.environ.get("PORTAL_OPENCODE_NATIVE_TEST_BINARY", "")
KEY = "sk-opencode-native"


class RecordingChat(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), ChatHandler)
        self.requests: list[dict] = []


class ChatHandler(BaseHTTPRequestHandler):
    server: RecordingChat
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.server.requests.append({"method": "GET", "path": self.path})
        self._json({"object": "list", "data": [{"id": "gpt-test", "object": "model"}]})

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append({"method": "POST", "path": self.path, "model": body.get("model"),
                                     "stream": body.get("stream"), "authorization": self.headers.get("Authorization"),
                                     "team": self.headers.get("X-Team")})
        usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
        if not body.get("stream"):
            self._json({"id": "chat", "object": "chat.completion", "created": 0, "model": body.get("model"),
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": "A title"}}], "usage": usage})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        chunk = {"id": "chat", "object": "chat.completion.chunk", "created": 0, "model": body.get("model")}
        for choice, extra in (({"index": 0, "delta": {"role": "assistant", "content": "NATIVE_OPENCODE_OK"},
                                "finish_reason": None}, {}),
                              ({"index": 0, "delta": {}, "finish_reason": "stop"}, {"usage": usage})):
            self.wfile.write(f"data: {json.dumps({**chunk, 'choices': [choice], **extra})}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


@pytest.mark.skipif(not BINARY, reason="Set PORTAL_OPENCODE_NATIVE_TEST_BINARY for the native OpenCode test")
def test_an_opencode_api_route_runs_on_the_real_opencode_with_its_key_kept_out_of_tools(tmp_path, monkeypatch):
    """真实的 OpenCode 以 API 路线的模型 API 作为 OpenAI 兼容 Provider 完成一轮对话

    Portal 生成的 Provider 定义被真实 OpenCode 接受；请求以 Bearer 密钥和附加请求头发往模型 API 的 /chat/completions，使用所选模型；密钥不在 launch.json 中。
    """
    server = RecordingChat()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/opencode").symlink_to(Path(BINARY).resolve())
    sockets = Path(tempfile.mkdtemp(prefix="oc", dir="/tmp"))
    monkeypatch.setattr(opencode_provider_catalog, "RUNTIME", runtime)
    monkeypatch.setattr(opencode_client, "STATE", tmp_path / "state")
    monkeypatch.setattr(opencode_client, "SOCKETS", sockets)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    key_file = tmp_path / "gateway.key"
    key_file.write_text(KEY + "\n")
    register_apis({"id": "gateway", "kind": "openai_compatible_chat", "provider": "example",
                   "base_url": f"http://127.0.0.1:{server.server_port}/v1", "default_model": "gpt-test",
                   "api_key_file": str(key_file), "default_headers": {"X-Team": "lab"},
                   "models": [{"id": "gpt-test", "context_window": 128000}]})
    account_registrations.create_registration({"account_id": "gateway-opencode", "provider": "opencode",
                                               "kind": "api_profile", "config": {"api_id": "gateway"}}, "test")
    work = tmp_path / "work"
    work.mkdir()
    client = opencode_client.OpenCodeClient()
    try:
        result = client.run_turn(
            session_key="k", session_id="", prompt="Say hello.", cwd=str(work), provider_profile="gateway-opencode",
            model="gpt-test", effort="", is_owner=True, permission_profile="owner", system_prompt_append="rules",
            turn_id="turn-native", tools=[], resident_environment={}, inherit_portal_database=False,
            allow_subagents=False, event_callback=lambda event: None, tool_callback=None)
    finally:
        for root in (tmp_path / "state").glob("opencode-*"):
            try:
                client.close_session("", root.name)
            except Exception:  # noqa: BLE001 - the session may already be gone
                pass
        server.shutdown()
        shutil.rmtree(sockets, ignore_errors=True)
    assert "NATIVE_OPENCODE_OK" in result.response
    calls = [request for request in server.requests if request["method"] == "POST"]
    assert calls and {(call["path"], call["authorization"], call["team"]) for call in calls} == {
        ("/v1/chat/completions", f"Bearer {KEY}", "lab")}
    assert {call["model"] for call in calls} == {"gpt-test"}
    assert KEY not in (tmp_path / "state" / result.session_id / "launch.json").read_text()
