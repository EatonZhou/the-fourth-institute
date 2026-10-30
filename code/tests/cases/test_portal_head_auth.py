from __future__ import annotations

import http.client
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from unittest import mock

from management_portal import serve_management_portal as portal
from management_portal import http_auth


class _QuietHandler(portal.Handler):
    def log_message(self, _format: str, *_args) -> None:
        return


@contextmanager
def _portal_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _head(server: ThreadingHTTPServer, path: str) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        server.server_address[1],
        timeout=5,
    )
    try:
        connection.request("HEAD", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_unauthenticated_head_uses_the_get_authentication_dispatch_contracts(subtests, isolated_portal_case):
    """未登录的 HEAD 请求与 GET 一样跳转登录页，公开路由返回 200 且无响应体"""
    with subtests.test(contract='test_unauthenticated_head_uses_the_get_authentication_dispatch'), isolated_portal_case() as case_path:
        with mock.patch.object(http_auth, '_check_session', return_value=False), _portal_server() as server:
            for path in ('/', '/index.html', '/settings'):
                status, headers, body = _head(server, path)
                assert status == 302, path
                assert headers['Location'] == '/login', path
                assert 'Last-Modified' not in headers, path
                assert body == b'', path
    with subtests.test(contract='test_public_head_routes_keep_their_get_visibility_without_a_body'), isolated_portal_case() as case_path:
        with mock.patch.object(http_auth, '_check_session', return_value=False), _portal_server() as server:
            for path in ('/login', '/join', '/api/health', '/static/portal_theme.js'):
                status, _headers, body = _head(server, path)
                assert status == 200, path
                assert body == b'', path
                if path == '/join':
                    assert 'no-store' in _headers['Cache-Control']
                    assert _headers['Referrer-Policy'] == 'no-referrer'
