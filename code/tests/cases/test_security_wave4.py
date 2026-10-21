from __future__ import annotations

import http.client
import io
from types import SimpleNamespace

from tests.cases.security_http_support import server as _server

import pytest

from management_portal import serve_management_portal as portal


class _QuietHandler(portal.Handler):
    def log_message(self, _format: str, *_args: object) -> None:
        return




def _request(
    server: ThreadingHTTPServer,
    path: str,
    *,
    body: bytes,
    content_type: str,
) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=5
    )
    try:
        connection.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": content_type},
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_legacy_json_parser_rejects_invalid_non_object_requests(subtests, isolated_portal_case):
    """旧版 JSON 请求解析拒绝空体、长度不符、非对象、非法编码和错误内容类型"""
    for body, declared_length, content_type in [
        (b"", 0, "application/json"),
        (b'{"value": 1}', 18, "application/json"),
        (b"[]", 2, "application/json"),
        (b"1", 1, "application/json"),
        (b"\xff", 1, "application/json"),
        (b"{}", 2, "text/plain"),
    ]:
        with subtests.test(body=body, declared_length=declared_length, content_type=content_type), isolated_portal_case() as case_path:
            handler = SimpleNamespace(
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(declared_length),
                },
                rfile=io.BytesIO(body),
            )

            with pytest.raises(ValueError):
                portal._read_request_json(handler)


def test_malformed_json_never_reaches_business_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """格式不合法的 JSON 请求返回 400，不进入终端启动等业务处理"""
    invoked = False

    def _unexpected(*_args: object, **_kwargs: object) -> None:
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(portal, "_check_session", lambda _handler: True)
    monkeypatch.setattr(portal, "_get_session_username", lambda _handler: "tester")
    monkeypatch.setattr(portal, "_same_origin_request", lambda _handler: True)
    monkeypatch.setattr(portal.local_terminal_backend, "start_device_terminal", _unexpected)

    with _server(_QuietHandler) as server:
        status, _body = _request(
            server,
            "/api/terminal/start",
            body=b"[]",
            content_type="application/json",
        )

    assert status == 400
    assert not invoked


@pytest.mark.parametrize(
    ("body", "content_type", "expected_status"),
    [
        (b"username=tester&password=" + b"x" * 16_385, "application/x-www-form-urlencoded", 413),
        (b"username=tester&password=valid", "application/json", 400),
    ],
    ids=("oversized", "wrong-content-type"),
)
def test_login_rejects_invalid_body_before_credential_work(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    content_type: str,
    expected_status: int,
) -> None:
    """登录请求体过大或内容类型错误时，在校验密码前就被拒绝"""
    invoked = False

    def _unexpected(*_args: object) -> bool:
        nonlocal invoked
        invoked = True
        return True

    monkeypatch.setattr(portal, "_same_origin_request", lambda _handler: True)
    monkeypatch.setattr(portal, "_verify_credentials", _unexpected)

    with _server(_QuietHandler) as server:
        status, _response_body = _request(
            server,
            "/login",
            body=body,
            content_type=content_type,
        )

    assert status == expected_status
    assert not invoked
