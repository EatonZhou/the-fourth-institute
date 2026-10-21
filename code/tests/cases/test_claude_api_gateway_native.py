"""Claude Code on a declared Anthropic gateway, with the real CLI and a recording Messages endpoint.

Set ``PORTAL_CLAUDE_NATIVE_TEST_BINARY`` to a Claude Code executable (the
SDK's bundled ``claude``) to run it.

:module: execution.providers
:importance: P1
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from management_portal.execution import isa
from tests.cases.claude_sdk_support import load_claude_module

BINARY = os.environ.get("PORTAL_CLAUDE_NATIVE_TEST_BINARY", "")
KEY = "sk-claude-native"
daemon_module = load_claude_module("claude_code_daemon")


def _call(handler, **arguments):
    return handler(SimpleNamespace(caller="alice", argument=lambda name, default=None: arguments.get(name, default),
                                   expected_version_for=lambda object_id: None))


class RecordingMessages(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), MessagesHandler)
        self.requests: list[dict] = []


class MessagesHandler(BaseHTTPRequestHandler):
    server: RecordingMessages
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.server.requests.append({"method": "GET", "path": self.path})
        self._json({"data": [], "has_more": False})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        self.server.requests.append({
            "method": "POST", "path": self.path.split("?")[0], "model": body.get("model"),
            "x_api_key": self.headers.get("x-api-key"), "authorization": self.headers.get("Authorization"),
            "team": self.headers.get("X-Team")})
        if self.path.split("?")[0].endswith("/count_tokens"):
            self._json({"input_tokens": 10})
            return
        message = {"id": "msg_native", "type": "message", "role": "assistant", "model": body.get("model"),
                   "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0}}
        if not body.get("stream"):
            self._json({**message, "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": "NATIVE_CLAUDE_OK"}],
                        "usage": {"input_tokens": 10, "output_tokens": 2}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for kind, payload in (
            ("message_start", {"message": {**message, "content": []}}),
            ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "NATIVE_CLAUDE_OK"}}),
            ("content_block_stop", {"index": 0}),
            ("message_delta", {"delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}}),
            ("message_stop", {}),
        ):
            self.wfile.write(f"event: {kind}\ndata: {json.dumps({'type': kind, **payload})}\n\n".encode())
        self.wfile.flush()
        self.close_connection = True


class ProxyTrap:
    def __init__(self) -> None:
        self.socket = socket.create_server(("127.0.0.1", 0))
        self.targets: list[str] = []
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.socket.getsockname()[1]}"

    def _serve(self) -> None:
        while True:
            try:
                connection, _address = self.socket.accept()
            except OSError:
                return
            with connection:
                self.targets.append(connection.recv(256).split(b"\r\n")[0].decode(errors="replace"))


@pytest.mark.skipif(not BINARY, reason="Set PORTAL_CLAUDE_NATIVE_TEST_BINARY for the native Claude Code test")
@pytest.mark.parametrize("auth", ["x-api-key", "bearer"])
def test_claude_code_sends_only_the_selected_model_to_a_declared_gateway(auth, tmp_path):
    """真实的 Claude Code 只向声明的网关发送所选模型，并按声明的认证方式带密钥

    用 Portal 为网关路线生成的环境运行 Claude Code：所有 Messages 请求（含后台请求）都发往网关、使用所选模型而不是 Claude 的模型名，认证头符合声明（x-api-key 或 Bearer），带附加请求头，没有请求经代理发往其他主机。
    """
    server = RecordingMessages()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    trap = ProxyTrap()
    key_file = tmp_path / "gateway.key"
    key_file.write_text(KEY + "\n")
    _call(isa.create_model_api, api_id="gateway", base_url=f"http://127.0.0.1:{server.server_port}/v1",
          default_model="gpt-test", secret_file=str(key_file), headers={"X-Team": "lab"},
          models=[{"id": "gpt-test"}, {"id": "gpt-mini"}],
          protocols={"anthropic_messages": {"base_url": f"http://127.0.0.1:{server.server_port}", "auth": auth}})
    _call(isa.register_provider_account, account_id="gateway-claude", provider="claude", kind="api_profile",
          config={"api_id": "gateway"})
    provider_env, _profile = daemon_module._provider_environment("gateway-claude")
    (tmp_path / "home").mkdir()
    environment = {"HOME": str(tmp_path / "home"), "PATH": os.defpath, "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
                   "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", **provider_env,
                   **dict.fromkeys(daemon_module.GATEWAY_MODEL_VARIABLES, "gpt-mini"),
                   **{name: trap.url for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")},
                   "NO_PROXY": "127.0.0.1,localhost"}
    try:
        result = subprocess.run([BINARY, "-p", "Say hello.", "--model", "gpt-mini", "--output-format", "json"],
                                cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=120)
    finally:
        server.shutdown()
        trap.socket.close()
    assert result.returncode == 0, result.stderr[-2000:] + result.stdout[-2000:]
    assert "NATIVE_CLAUDE_OK" in result.stdout
    calls = [request for request in server.requests if request["method"] == "POST"]
    assert calls and {call["path"] for call in calls} <= {"/v1/messages", "/v1/messages/count_tokens"}
    assert {call["model"] for call in calls if call["path"] == "/v1/messages"} == {"gpt-mini"}
    expected = (KEY, None) if auth == "x-api-key" else (None, f"Bearer {KEY}")
    assert {(call["x_api_key"], call["authorization"], call["team"]) for call in calls} == {(*expected, "lab")}
    assert trap.targets == [], "no request may leave for another host"
