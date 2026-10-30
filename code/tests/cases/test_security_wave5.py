from __future__ import annotations

from http.server import ThreadingHTTPServer
import http.client
import json

from tests.cases.security_http_support import server as _server

import pytest

from management_portal import serve_management_portal as portal
from management_portal import http_auth


class _QuietHandler(portal.Handler):
    def log_message(self, _format: str, *_args: object) -> None:
        return




def _post(
    server: ThreadingHTTPServer,
    path: str,
    *,
    body: bytes,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=5
    )
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    try:
        connection.request("POST", path, body=body, headers=request_headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _authenticated_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[object, ...]]:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(http_auth, "_check_session", lambda _handler: True)
    monkeypatch.setattr(http_auth, "_get_session_username", lambda _handler: "tester")
    # Opening a Terminal is OpenTerminal, which the Portal 管理员 category may run.
    from tests.cases.permission_test_support import portal_admin

    portal_admin("tester")

    def _start(*args: object) -> dict[str, bool]:
        calls.append(args)
        return {"ok": True}

    monkeypatch.setattr(portal.local_terminal_backend, "start_device_terminal", _start)
    return calls


def _terminal_body() -> bytes:
    return json.dumps(
        {
            "device_id": "[SECURITY-TEST:run_20260825_first_batch50_extension]-device",
            "session_id": "[SECURITY-TEST:run_20260825_first_batch50_extension]-session",
        }
    ).encode("utf-8")


def test_foreign_origin_session_post_is_rejected_before_business_logic_contracts(monkeypatch, subtests, isolated_portal_case):
    """来自外部来源的登录会话 POST 请求返回 403，不进入业务处理"""
    with subtests.test(contract='test_foreign_origin_session_post_is_rejected_before_business_logic'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            calls = _authenticated_terminal(monkeypatch)
            with _server(_QuietHandler) as server:
                status, _body = _post(server, '/api/terminal/start', body=_terminal_body(), headers={'Origin': 'https://foreign.example'})
            assert status == 403
            assert calls == []



def test_session_post_accepts_same_origin_and_requests_without_origin(monkeypatch, subtests, isolated_portal_case):
    """同源或不带 Origin 头的登录会话 POST 请求正常处理"""
    for source_header in ["same-origin", "missing"]:
        with subtests.test(source_header=source_header), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                calls = _authenticated_terminal(monkeypatch)

                with _server(_QuietHandler) as server:
                    headers = (
                        {"Origin": f"http://127.0.0.1:{server.server_address[1]}"}
                        if source_header == "same-origin"
                        else {}
                    )
                    status, body = _post(
                        server,
                        "/api/terminal/start",
                        body=_terminal_body(),
                        headers=headers,
                    )

                assert status == 200
                assert json.loads(body) == {"ok": True}
                assert len(calls) == 1
