"""A Codex API account against the real app-server and a recording Responses endpoint.

Set ``PORTAL_CODEX_NATIVE_TEST_BINARIES`` to the Codex executable(s) to run it.

:module: execution.providers
:importance: P1
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal.codex_app_server_client import CodexAppServerClient
from management_portal.execution import account_hosting, account_login, isa
from management_portal.execution.account_hosting import account_home
from management_portal.execution.codex_backend import CodexBackend

NATIVE_BINARIES = sorted(set(filter(None, os.environ.get("PORTAL_CODEX_NATIVE_TEST_BINARIES", "").split(os.pathsep))))
KEY = "sk-native-test"


def _call(handler, **arguments):
    return handler(SimpleNamespace(caller="alice", argument=lambda name, default=None: arguments.get(name, default),
                                   expected_version_for=lambda object_id: None))


class RecordingResponses(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), RecordingHandler)
        self.requests: list[dict] = []


class RecordingHandler(BaseHTTPRequestHandler):
    server: RecordingResponses

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _record(self, body: dict | None = None) -> None:
        self.server.requests.append({"method": self.command, "path": self.path, "body": body or {},
                                     "authorization": self.headers.get("Authorization"),
                                     "team": self.headers.get("X-Team")})

    def do_GET(self) -> None:
        self._record()
        payload = b'{"data": [], "models": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        self._record(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        number = len(self.server.requests)
        for kind, payload in (
            ("response.created", {"response": {"id": f"response-{number}"}}),
            ("response.output_item.done", {"item": {"id": f"message-{number}", "type": "message", "role": "assistant",
                                                    "content": [{"type": "output_text", "text": "NATIVE_API_KEY_OK"}]}}),
            ("response.completed", {"response": {"id": f"response-{number}", "status": "completed",
                                                 "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}}),
        ):
            self.wfile.write(f"event: {kind}\ndata: {json.dumps({'type': kind, **payload})}\n\n".encode())
            self.wfile.flush()


class ProxyTrap:
    """A proxy that refuses every connection and remembers where each one wanted to go."""

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


@pytest.mark.parametrize("binary_path", NATIVE_BINARIES)
def test_a_codex_api_account_sends_its_key_only_to_its_model_api(binary_path, tmp_path, monkeypatch):
    """真实的 Codex app-server 用 API 账号的密钥只访问模型 API 的 Responses 地址

    Portal 为 Codex API 账号准备的目录（API Key 登录与 config.toml）让真实 app-server 以 API Key 登录，对话和压缩都以 Bearer 密钥和附加请求头发往模型 API 的 /responses，所选模型与推理强度随请求发送，没有任何请求经代理发往其他主机。
    """
    server = RecordingResponses()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    trap = ProxyTrap()
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.setenv(name, trap.url)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(account_login, "provider_binary", lambda provider: binary_path)
    for name in ("start_codex_instance", "stop_codex_instance"):
        monkeypatch.setattr(account_hosting, name, lambda account_id: {})
    monkeypatch.setattr("management_portal.execution.provider_updates.runtime_available", lambda provider: True)
    key_file = tmp_path / "gateway.key"
    key_file.write_text(KEY + "\n")
    key_file.chmod(0o600)
    _call(isa.create_model_api, api_id="gateway", base_url=f"http://127.0.0.1:{server.server_port}/v1",
          default_model="gpt-test", secret_file=str(key_file), headers={"X-Team": "lab"},
          models=[{"id": "gpt-test", "reasoning_efforts": ["low", "high"]}], protocols={"openai_responses": {}})
    _call(isa.register_provider_account, account_id="gateway-codex", provider="codex", kind="api_profile",
          config={"api_id": "gateway"})
    home = account_home("codex", "gateway-codex")
    sockets = Path(tempfile.mkdtemp(prefix="cx", dir="/tmp"))
    socket_path = sockets / "codex.sock"
    process = None
    client = CodexAppServerClient(socket_path, request_timeout_seconds=10)
    try:
        (tmp_path / "home").mkdir()
        environment = {"HOME": str(tmp_path / "home"), "CODEX_HOME": str(home), "PATH": os.defpath,
                       **{name: os.environ[name] for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy",
                                                             "http_proxy", "all_proxy", "NO_PROXY")}}
        with (tmp_path / "native.log").open("a") as log:
            process = subprocess.Popen([binary_path, "app-server", "--listen", f"unix://{socket_path}"],
                                       cwd=tmp_path, env=environment, stdout=log, stderr=log)
        deadline = time.monotonic() + 30
        while not socket_path.exists():
            assert process.poll() is None, (tmp_path / "native.log").read_text()[-2000:]
            assert time.monotonic() < deadline, "Native app-server startup timed out"
            time.sleep(0.02)
        assert "apiKey" in json.dumps(client.request("account/read", {}, timeout=10))
        config = CodexBackend._thread_config({})
        thread = client.start_thread({"cwd": str(tmp_path), "config": config, "model": "gpt-test",
                                      "approvalPolicy": "never", "sandbox": "danger-full-access"})["thread"]["id"]
        client.run_turn(thread, "Say hello.", cwd=str(tmp_path), model="gpt-test", effort="high", config=config)
        client.compact_thread(thread, timeout_seconds=15, no_progress_timeout_seconds=10, status_poll_interval_seconds=0.05)
    finally:
        client.close()
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        server.shutdown()
        trap.socket.close()
        shutil.rmtree(sockets, ignore_errors=True)
    calls = [request for request in server.requests if request["method"] == "POST"]
    assert len(calls) >= 2, server.requests
    assert {(call["path"], call["authorization"], call["team"]) for call in calls} == {
        ("/v1/responses", f"Bearer {KEY}", "lab")}
    assert calls[0]["body"]["model"] == "gpt-test"
    assert calls[0]["body"]["reasoning"]["effort"] == "high"
    assert trap.targets == [], "no request may leave for another host"
