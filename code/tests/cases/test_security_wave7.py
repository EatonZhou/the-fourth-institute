from __future__ import annotations

import http.client
import json
from types import SimpleNamespace

from tests.cases.security_http_support import server as _server

import pytest

from management_portal import http_terminal
from management_portal import serve_management_portal as portal
from management_portal import http_auth


class _QuietHandler(portal.Handler):
    def log_message(self, _format: str, *_args: object) -> None:
        return




@pytest.mark.parametrize("terminal_prefix", ["/api/agent-workbench/local-terminal", "/api/terminal"])
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            "/api/agent-workbench/local-terminal/start",
            {"session_id": "[SECURITY-TEST:run_20260825_first_batch50_extension]terminal", "device_id": "device_aaaaaaaaaaaa"},
        ),
        (
            "GET",
            "/api/agent-workbench/local-terminal/read?session_id=test",
            None,
        ),
        (
            "POST",
            "/api/agent-workbench/local-terminal/input",
            {"session_id": "test", "input": "fixture"},
        ),
        (
            "POST",
            "/api/agent-workbench/local-terminal/control",
            {"session_id": "test", "action": "close"},
        ),
    ],
)
def test_terminal_routes_use_selected_policy_for_scoped_users(
    terminal_prefix: str,
    method: str,
    path: str,
    body: dict | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已登录用户经两组终端路由到达终端后端的启动、读取、输入和控制；能否打开由后端按 OpenTerminal 判定"""
    calls: list[str] = []
    path = path.replace("/api/agent-workbench/local-terminal", terminal_prefix)
    def backend(actor: str, *_args: object, **_kwargs: object) -> dict:
        assert actor == "bob"
        calls.append("backend")
        return {}

    monkeypatch.setattr(http_auth, "_check_session", lambda _handler: True)
    monkeypatch.setattr(http_auth, "_get_session_username", lambda _handler: "bob")
    fake_backend = SimpleNamespace(
        open_terminal=backend,
        read_terminal=backend,
        write_terminal=backend,
        control_terminal=backend,
    )
    # /api/agent-workbench/local-terminal and /api/terminal each reach the backend through their own adapter.
    monkeypatch.setattr(portal, "local_terminal_backend", fake_backend)
    monkeypatch.setattr(http_terminal, "local_terminal_backend", fake_backend)
    encoded = json.dumps(body or {}).encode("utf-8") if body is not None else b""
    with _server(_QuietHandler) as server:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_address[1],
            timeout=5,
        )
        try:
            connection.request(
                method,
                path,
                body=encoded,
                headers={"Content-Type": "application/json"} if body is not None else {},
            )
            response = connection.getresponse()
            response_body = response.read()
        finally:
            connection.close()

    assert response.status == 200, response_body
    assert calls == ["backend"]








@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("terminal_prefix", ["/api/agent-workbench/local-terminal", "/api/terminal"])
def test_terminal_a_still_requires_login(method, terminal_prefix, monkeypatch):
    """Terminal 统一授权与会话隔离

    验证 A 下所有已登录 User 可使用本机与设备终端，B 保留入口限制，未登录访问、错误配置及跨用户会话访问不获得新增权限。
    """
    monkeypatch.setattr(http_auth, "_check_session", lambda _handler: False)
    with _server(_QuietHandler) as server:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            connection.request(method, terminal_prefix + ("/read" if method == "GET" else "/start"))
            response = connection.getresponse()
            response.read()
        finally:
            connection.close()
    assert response.status == 401  # API routes answer 401; only pages redirect to login


@pytest.mark.parametrize("operation", ["read", "input", "control"])
def test_terminal_a_does_not_share_other_users_sessions(operation, monkeypatch):
    """Terminal 统一授权与会话隔离

    验证 A 下所有已登录 User 可使用本机与设备终端，B 保留入口限制，未登录访问、错误配置及跨用户会话访问不获得新增权限。
    """
    from management_portal import local_terminal_backend

    monkeypatch.setattr(local_terminal_backend, "_cleanup_locked", lambda: None)
    monkeypatch.setattr(local_terminal_backend, "_SESSIONS", {"other-user": SimpleNamespace(actor="sample_admin")})
    with pytest.raises(KeyError, match="terminal session not found"):
        if operation == "read":
            local_terminal_backend.read_terminal("sample_reader", "other-user")
        elif operation == "input":
            local_terminal_backend.write_terminal("sample_reader", "other-user", "whoami")
        else:
            local_terminal_backend.control_terminal("sample_reader", "other-user", "close")
