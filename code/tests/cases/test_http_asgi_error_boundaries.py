"""Errors emitted before or around the synchronous Portal HTTP handler."""

import asyncio
import json
from unittest import mock

from management_portal import asgi


def _response(path: str, method: str = "GET"):
    async def call():
        events = []
        received = False

        async def receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(event):
            events.append(event)

        await asgi.app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode("ascii"),
                "root_path": "",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("127.0.0.1", 18086),
            },
            receive,
            send,
        )
        start = next(event for event in events if event["type"] == "http.response.start")
        body = b"".join(event.get("body", b"") for event in events if event["type"] == "http.response.body")
        return start["status"], dict(start["headers"]), body

    return asyncio.run(call())


def test_unsupported_api_method_has_json_error_and_non_api_keeps_text():
    """不支持的请求方法在 API 路径返回 JSON 405 错误，非 API 路径仍返回纯文本"""
    status, headers, body = _response("/api/unknown", method="PUT")
    payload = json.loads(body)
    assert status == payload["http_status"] == 405
    assert headers[b"content-type"] == b"application/json"
    assert b"allow" in headers
    assert payload["code"] == "operation.method_not_allowed"
    assert payload["commit_state"] == "not_committed"

    status, headers, body = _response("/some-page", method="PUT")
    assert status == 405
    assert headers[b"content-type"].startswith(b"text/plain")
    assert body == b"Method Not Allowed"


def test_api_bridge_failure_is_private_json_error():
    """同步处理桥接失败时 API 返回提交状态未知的 JSON 500，不泄露内部信息"""
    with mock.patch.object(asgi, "_run_sync_handler", side_effect=RuntimeError("private marker")):
        status, headers, body = _response("/api/unknown")
    payload = json.loads(body)
    assert status == payload["http_status"] == 500
    assert headers[b"content-type"] == b"application/json"
    assert payload["code"] == "operation.failed"
    assert payload["commit_state"] == "unknown"
    assert "private marker" not in body.decode()


def test_independent_api_failures_use_private_json_error():
    """events 与内部重启、排空接口出错时同样返回不含内部信息的 JSON 500"""
    cases = (
        ("/api/events", "GET", asgi.portal, "_get_session_username"),
        ("/api/internal/restart-notice", "POST", asgi, "_is_direct_local_request"),
        ("/api/internal/drain-realtime", "POST", asgi, "_is_direct_local_request"),
    )
    for path, method, owner, attribute in cases:
        with mock.patch.object(owner, attribute, side_effect=RuntimeError("private marker")):
            status, headers, body = _response(path, method)
        payload = json.loads(body)
        assert status == payload["http_status"] == 500
        assert headers[b"content-type"] == b"application/json"
        assert payload["code"] == "operation.failed"
        assert payload["commit_state"] == "unknown"
        assert "private marker" not in body.decode()
