from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from management_common import portal_agent_http
from scripts import resident_hatg_instruction as cli


COMMITTED = json.dumps({"schema": "archive.hatg-instruction-result.v2", "instruction_id": "inst_lin_000000000000",
                        "instruction": "GetResident", "status": "committed", "replayed": False,
                        "result": {"ok": True}}).encode()


@contextmanager
def http_server(status=200, location=None, body=b'{"ok":true,"items":[]}'):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, dict(self.headers)))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.do_GET()

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", calls
        finally:
            server.shutdown()
            thread.join(timeout=3)


@contextmanager
def stalled_server(mode):
    received, release, disconnected = threading.Event(), threading.Event(), threading.Event()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append(self.path)
            received.set()
            try:
                if mode == "headers":
                    release.wait(5)
                self.send_response(200)
                self.end_headers()
                if mode == "body":
                    release.wait(5)
                while not release.is_set():
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    release.wait(.02)
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", calls, received, disconnected
        finally:
            release.set()
            server.shutdown()
            thread.join(timeout=3)


def bounded_cli_command(tmp_path, url):
    resident = "resident_aaaaaaaaaaaa"
    (tmp_path / (resident + ".token")).write_text("test-only-token")
    script = Path(__file__).resolve().parents[2] / "scripts/resident_hatg_instruction.py"
    env = dict(os.environ, PORTAL_URL=url, PORTAL_RESIDENT_HATG_TOKEN_ROOT=str(tmp_path),
               PORTAL_RESIDENT_ID=resident)
    command = [sys.executable, str(script), "--resident-id", resident, "call", "GetConversation",
               "--arguments-json", json.dumps({"resident_id": resident, "history_limit": 1}),
               "--request-timeout", ".15", "--max-attempts", "1"]
    return command, env


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_control_read_stalls_return_failure_once_without_retry(tmp_path, phase):
    with stalled_server(phase) as (url, calls, received, disconnected):
        command, env = bounded_cli_command(tmp_path, url)
        start = time.monotonic()
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=5)
        elapsed = time.monotonic() - start
    assert result.returncode == 1, result.stderr
    output = json.loads(result.stdout)
    assert output["error"]["code"] == "instruction.timeout"
    assert output["error"]["attempts"] == 1
    assert len(calls) == 1 and elapsed < 3


def test_wall_deadline_cancels_and_reaps_trickling_read_without_retry(tmp_path):
    # Socket inactivity is not a wall deadline: preserve the controller's
    # subprocess kill-and-reap boundary even when bytes keep arriving.
    with stalled_server("trickle") as (url, calls, received, disconnected):
        command, env = bounded_cli_command(tmp_path, url)
        with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            try:
                assert received.wait(3)
                with pytest.raises(subprocess.TimeoutExpired):
                    process.communicate(timeout=.4)
            finally:
                process.kill()
                stdout, stderr = process.communicate(timeout=3)
            assert process.returncode != 0 and stdout == b""
            assert disconnected.wait(2)
    assert len(calls) == 1


@pytest.fixture
def rejecting_proxy(monkeypatch):
    with http_server(403) as (url, calls):
        for name in ("http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.setenv(name, url)
            monkeypatch.setenv(name.upper(), url)
        monkeypatch.setenv("no_proxy", "")
        monkeypatch.setenv("NO_PROXY", "")
        yield calls


@pytest.mark.parametrize("url", [
    "http://localhost:18086", "http://LOCALHOST.:18085", "http://test.localhost",
    "http://127.0.0.2", "http://192.168.115.204", "http://192.168.205.69",
    "http://192.168.205.62", "http://10.77.0.2", "http://10.138.30.36",
    "https://172.16.2.3", "http://169.254.10.1", "http://[::1]:18086",
    "http://[fd00::1]", "http://[fe80::1]", "http://[::ffff:192.168.205.69]",
])
def test_local_addresses_never_enter_proxy_handler(url):
    """本机、内网、链路本地和 IPv6 本地地址的请求从不进入代理"""
    request = urllib.request.Request(url)
    handler = portal_agent_http._PortalProxyHandler({})
    with patch.object(urllib.request.ProxyHandler, "proxy_open") as upstream:
        assert handler.proxy_open(request, "http://proxy.invalid", "http") is None
    upstream.assert_not_called()


@pytest.mark.parametrize("url", [
    "https://176.122.178.237", "https://example.com", "https://localhost.evil.example",
    "http://172.32.0.1", "http://[2606:4700:4700::1111]",
])
def test_public_addresses_keep_configured_proxy_policy(url):
    """公网地址和伪装成 localhost 的域名仍按配置走代理"""
    handler = portal_agent_http._PortalProxyHandler({})
    with patch.object(urllib.request.ProxyHandler, "proxy_open", return_value="proxied") as upstream:
        assert handler.proxy_open(urllib.request.Request(url), "http://proxy.invalid", "http") == "proxied"
    upstream.assert_called_once()


def test_cli_works_with_a_broken_proxy_and_preserves_identity(monkeypatch, rejecting_proxy, capsys):
    """坏代理下 ISA CLI 仍直连 Portal 并带上令牌和所在执行的 ID，不改动环境变量和全局 opener"""
    resident = "resident_aaaaaaaaaaaa"
    with http_server(body=COMMITTED) as (url, calls):
        monkeypatch.setattr(cli, "DEFAULT_PORTAL_URL", url)
        monkeypatch.setattr(cli, "_token", lambda _: "test-only-token")
        monkeypatch.setattr(cli, "execution_context", lambda: {"execution_id": "execution_test"})
        monkeypatch.delenv("PORTAL_RESIDENT_ID", raising=False)
        env_before = dict(os.environ)
        opener_before = urllib.request._opener
        code = cli.main(["--resident-id", resident, "call", "GetResident", "--arguments-json", json.dumps({"resident_id": resident})])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["result"] == {"ok": True}
    assert rejecting_proxy == []
    path, headers = calls[0]
    headers = {key.lower(): value for key, value in headers.items()}
    assert path == "/api/instructions/execute"
    assert headers["authorization"] == "Bearer test-only-token"
    assert headers["x-portal-execution-id"] == "execution_test"
    assert "x-archive-execution-id" not in headers
    assert "x-archive-authorization" not in headers
    assert dict(os.environ) == env_before
    assert urllib.request._opener is opener_before


@pytest.mark.parametrize("injected_identity", [False, True])
def test_cli_from_arbitrary_cwd_with_proxy(tmp_path, rejecting_proxy, injected_identity):
    """ISA CLI 在任意工作目录和坏代理下直连 Portal，身份可来自参数或注入的环境变量"""
    resident = "resident_aaaaaaaaaaaa"
    (tmp_path / f"{resident}.token").write_text("test-only-token", encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "scripts" / "resident_hatg_instruction.py"
    with http_server(body=COMMITTED) as (url, calls):
        env = dict(os.environ, PORTAL_URL=url, PORTAL_RESIDENT_HATG_TOKEN_ROOT=str(tmp_path))
        for name in ("PORTAL_RESIDENT_ID", "PORTAL_LINEAGE_ID", "PORTAL_EXECUTION_CONTEXT_FILE"):
            env.pop(name, None)
        identity = ["--resident-id", resident]
        if injected_identity:
            env.update(PORTAL_RESIDENT_ID=resident)
            identity = []
        result = subprocess.run([sys.executable, str(script), *identity, "call", "GetResident",
                                 "--arguments-json", json.dumps({"resident_id": resident})],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["result"] == {"ok": True}
    assert len(calls) == 1
    assert rejecting_proxy == []


def test_public_request_still_uses_proxy(rejecting_proxy):
    """Portal HTTP 客户端访问公网地址时仍经过代理"""
    with pytest.raises(urllib.error.HTTPError) as error:
        portal_agent_http.urlopen(urllib.request.Request("http://example.invalid/api"), timeout=3)
    assert error.value.code == 403
    error.value.close()
    assert len(rejecting_proxy) == 1


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_never_forwards_credentials(rejecting_proxy, status):
    """Portal HTTP 客户端遇到 3xx 重定向时不跟随，Authorization 不会转发给目标"""
    with http_server() as (target, target_calls), http_server(status, target) as (url, calls):
        request = urllib.request.Request(url, headers={"Authorization": "Bearer test-only-token"})
        with pytest.raises(urllib.error.HTTPError) as error:
            portal_agent_http.urlopen(request, timeout=3)
        assert error.value.code == status
        error.value.close()
    assert len(calls) == 1
    assert target_calls == []
    assert rejecting_proxy == []


def test_actual_portal_denial_is_not_bypassed(monkeypatch, rejecting_proxy, capsys):
    """Portal 真实返回 403 时 CLI 报告失败，不改走代理重试"""
    with http_server(403) as (url, calls):
        monkeypatch.setattr(cli, "DEFAULT_PORTAL_URL", url)
        monkeypatch.setattr(cli, "_token", lambda _: "test-only-token")
        monkeypatch.delenv("PORTAL_RESIDENT_ID", raising=False)
        assert cli.main(["--resident-id", "resident_aaaaaaaaaaaa", "call", "GetResident",
                         "--arguments-json", '{"resident_id":"resident_aaaaaaaaaaaa"}']) == 1
    assert json.loads(capsys.readouterr().out)["http_status"] == 403
    assert len(calls) == 1
    assert rejecting_proxy == []


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
