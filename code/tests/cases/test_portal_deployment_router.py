""":importance: P1"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import ipaddress
from management_portal.identity_store import IdentityState

import json
import subprocess
import sys
import threading
import time
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from management_portal.portal_deployment_router import (
    Backend,
    DEPLOYMENT_PREFERENCE_COOKIE,
    PortalRouterServer,
    RUNTIME_STATUS_PATH,
    RouterPolicy,
    PortalSessionAuthority,
    _encode_deployment_preference,
)
from management_portal.portal_access_control import PortalAccessStore
from management_portal.portal_deployment_registry import PortalDeploymentRegistry


def _credentials() -> bytes:
    raw = json.dumps({"CASE-A": "hash", "yijun": "hash"}, sort_keys=True).encode("utf-8")
    IdentityState("credentials").replace(json.loads(raw))
    return raw


def _signing_key(path: Path) -> bytes:
    key = b"portal-router-test-signing-key!!"
    path.write_bytes(key)
    return key


def _access(tmp_path: Path) -> PortalAccessStore:
    store = PortalAccessStore()
    store.upsert_user("CASE-A", actor="CASE-A")
    store.upsert_user("yijun", actor="CASE-A")
    return store


def _token(signing_key: bytes, actor: str, *, expires_at: int | None = None) -> str:
    payload = {
        "v": 1,
        "u": actor,
        "iat": int(time.time()),
        "exp": int(expires_at if expires_at is not None else time.time() + 3600),
        "sv": 0,
        "nonce": "test",
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = base64.urlsafe_b64encode(
        hmac.new(signing_key, payload_b64.encode("ascii"), hashlib.sha256).digest()
    ).decode("ascii").rstrip("=")
    return f"{payload_b64}.{signature}"


def test_session_verifier_and_user_policy_contracts(tmp_path, subtests, isolated_portal_case):
    """Router 按会话和偏好选择后端，登录与账户安全请求固定走 Dev，热加载失败时保留旧快照

    :importance: P2"""
    with subtests.test(contract='test_session_verifier_and_user_policy'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _credentials()
        signing_key = _signing_key(tmp_path / 'session_signing.key')
        verifier = PortalSessionAuthority(tmp_path / 'session_signing.key', access_store=_access(tmp_path))
        policy = RouterPolicy(verifier, {'CASE-A'})
        et_token = _token(signing_key, 'CASE-A')
        release_token = _token(signing_key, 'yijun')
        issued_token = verifier.issue('CASE-A', max_age=3600)
        assert verifier.verify(et_token) == 'CASE-A'
        assert verifier.verify(issued_token) == 'CASE-A'
        assert verifier.verify(_token(signing_key, 'CASE-A', expires_at=1), now=2) == ''
        assert verifier.verify(et_token + 'x') == ''
        assert policy.select('/', f'db_session={et_token}') == 'dev'
        assert policy.select('/lineage/', f'db_session={release_token}') == 'release'
        et_release = _encode_deployment_preference('CASE-A', 'release')
        yijun_dev = _encode_deployment_preference('yijun', 'dev')
        assert policy.select('/', f'db_session={et_token}; {DEPLOYMENT_PREFERENCE_COOKIE}={et_release}') == 'release'
        assert policy.select('/', f'db_session={release_token}; {DEPLOYMENT_PREFERENCE_COOKIE}={yijun_dev}') == 'dev'
        assert policy.preference('CASE-A', f'{DEPLOYMENT_PREFERENCE_COOKIE}={yijun_dev}') == ''
        assert policy.select('/login', '') == 'dev'
        assert policy.select('/login/verify', '') == 'dev'
        assert policy.select('/api/auth/passkeys/options', '') == 'dev'
        assert policy.select('/api/auth/password-reset/request', '') == 'dev'
        assert policy.select('/forgot-password', '') == 'dev'
        assert policy.select('/reset-password?token=secret', '') == 'dev'
        assert policy.select('/static/portal_passkeys.js', '') == 'dev'
        assert policy.select('/static/portal_ui_primitives.js', '') == 'dev'
        release_cookie = f'db_session={release_token}'
        assert policy.select('/settings.html', release_cookie) == 'dev'
        assert policy.select('/static/settings.css', release_cookie) == 'dev'
        assert policy.select('/static/settings.js?v=mobile', release_cookie) == 'dev'
        assert policy.select('/api/account/security', release_cookie) == 'dev'
        assert policy.select('/api/account/two-factor/setup', release_cookie) == 'dev'
        assert policy.select('/api/account/passkeys/registration/options', release_cookie) == 'dev'
        assert policy.select('/api/health', '') == 'release'
        time.sleep(0.002)
        IdentityState("credentials").replace({'CASE-A': 'new-hash', 'yijun': 'new-hash'})
        assert verifier.verify(et_token) == 'CASE-A'
    with subtests.test(contract='test_router_serves_runtime_status_without_contacting_selected_backend'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _credentials()
        signing_key_path = tmp_path / 'session_signing.key'
        signing_key = _signing_key(signing_key_path)
        runtime_reads = []
        router = PortalRouterServer(('127.0.0.1', 0), RouterPolicy(PortalSessionAuthority(signing_key_path, access_store=_access(tmp_path)), {'CASE-A'}), {'dev': Backend('dev', '127.0.0.1', 1), 'release': Backend('release', '127.0.0.1', 2)}, [ipaddress.ip_network('127.0.0.0/8')], 0.1, runtime_status_reader=lambda: runtime_reads.append(True) or {'schema': 'tfi.portal-runtime-status.v1', 'ok': False, 'ready': False, 'state': 'blocked', 'code': 'runtime_policy_blocked', 'terminal': True, 'retryable': False, 'title': 'Portal 运行受阻', 'message': '请检查运行环境后重试。'})
        router_thread = threading.Thread(target=router.serve_forever, daemon=True)
        router_thread.start()
        try:
            request = urllib.request.Request(f'http://127.0.0.1:{router.server_address[1]}{RUNTIME_STATUS_PATH}', headers={'Cookie': f'db_session={_token(signing_key, 'CASE-A')}'})
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read())
            assert runtime_reads == [True]
            assert 'instance' not in payload
            assert payload['code'] == 'runtime_policy_blocked'
            assert payload['terminal'] is True
        finally:
            router.shutdown()
            router.server_close()
            router_thread.join(timeout=2)
    with subtests.test(contract='test_registry_keeps_last_good_snapshot_when_a_hot_reload_is_invalid'), isolated_portal_case() as case_path:
        tmp_path = case_path
        registry_path = tmp_path / 'deployments.json'
        registry_path.write_text(json.dumps({'schema': 'tfi.portal-deployments.v1', 'instances': [{'id': 'dev', 'label': 'Dev', 'kind': 'dev', 'origin': 'http://127.0.0.1:18086', 'browser_origin': 'http://localhost:18086', 'order': 100}]}), encoding='utf-8')
        registry = PortalDeploymentRegistry(registry_path)
        assert [item.instance_id for item in registry.deployments()] == ['dev']
        assert registry.deployments()[0].browser_origin == 'http://localhost:18086'
        time.sleep(0.002)
        registry_path.write_text(json.dumps({'schema': 'tfi.portal-deployments.v1', 'instances': [{'id': 'dev', 'label': 'Dev', 'kind': 'dev', 'origin': 'http://127.0.0.1:18086', 'browser_origin': 'http://audit.invalid:18086', 'order': 100}]}), encoding='utf-8')
        assert registry.deployments()[0].browser_origin == 'http://localhost:18086'
        assert 'browser origin' in registry.error()
        time.sleep(0.002)
        registry_path.write_text('{', encoding='utf-8')
        assert [item.instance_id for item in registry.deployments()] == ['dev']
        assert registry.error()


class _EchoHandler(BaseHTTPRequestHandler):
    label = ""

    def log_message(self, _format: str, *_args) -> None:
        return

    def _respond(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        payload = json.dumps({"label": self.label, "path": self.path, "body": body}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_GET = _respond
    do_HEAD = _respond
    do_POST = _respond


def _backend(label: str) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = type(f"{label.title()}Handler", (_EchoHandler,), {"label": label})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_router_proxies_to_selected_backend(tmp_path: Path) -> None:
    """Router 按会话和部署偏好把请求转发到所选后端，偏好 cookie 为 HttpOnly 和 Secure

    :importance: P2"""
    _credentials()
    signing_key_path = tmp_path / "session_signing.key"
    signing_key = _signing_key(signing_key_path)
    dev, dev_thread = _backend("dev")
    release, release_thread = _backend("release")
    router = PortalRouterServer(
        ("127.0.0.1", 0),
        RouterPolicy(
            PortalSessionAuthority(
                signing_key_path,
                access_store=_access(tmp_path),
            ),
            {"CASE-A"},
        ),
        {
            "dev": Backend("dev", "127.0.0.1", dev.server_address[1]),
            "release": Backend("release", "127.0.0.1", release.server_address[1]),
        },
        [ipaddress.ip_network("127.0.0.0/8")],
        5,
    )
    router_thread = threading.Thread(target=router.serve_forever, daemon=True)
    router_thread.start()
    try:
        base = f"http://127.0.0.1:{router.server_address[1]}"
        with urllib.request.urlopen(base + "/api/health", timeout=5) as response:
            assert response.headers["X-Portal-Backend"] == "release"
            payload = json.loads(response.read())
            assert payload["label"] == "release"
            assert payload["path"] == "/api/health"

        request = urllib.request.Request(
            base + "/lineage/?q=1",
            data=b"payload",
            method="POST",
            headers={"Cookie": f"db_session={_token(signing_key, 'CASE-A')}"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.headers["X-Portal-Backend"] == "dev"
            assert json.loads(response.read())["body"] == "payload"

        request = urllib.request.Request(
            base + "/",
            headers={"Cookie": f"db_session={_token(signing_key, 'yijun')}"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.headers["X-Portal-Backend"] == "release"
            assert json.loads(response.read())["label"] == "release"

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            router.server_address[1],
            timeout=5,
        )
        try:
            connection.request(
                "HEAD",
                "/",
                headers={"Cookie": f"db_session={_token(signing_key, 'yijun')}"},
            )
            response = connection.getresponse()
            assert response.status == 200
            assert response.headers["X-Portal-Backend"] == "release"
            assert response.read() == b""
        finally:
            connection.close()

        session_cookie = f"db_session={_token(signing_key, 'CASE-A')}"
        request = urllib.request.Request(
            base + "/__portal_router/preference",
            data=json.dumps({"instance": "release"}).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Cookie": session_cookie,
                "X-Forwarded-Proto": "https",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
            assert payload["effective_instance"] == "release"
            assert payload["default_instance"] == "dev"
            set_cookie = response.headers["Set-Cookie"]

        preference_cookie = SimpleCookie()
        preference_cookie.load(set_cookie)
        preference = preference_cookie[DEPLOYMENT_PREFERENCE_COOKIE]
        assert preference["httponly"]
        assert preference["secure"]
        assert preference["samesite"] == "Lax"

        request = urllib.request.Request(
            base + "/",
            headers={
                "Cookie": f"{session_cookie}; {DEPLOYMENT_PREFERENCE_COOKIE}={preference.value}",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.headers["X-Portal-Backend"] == "release"
            assert json.loads(response.read())["label"] == "release"
    finally:
        router.shutdown()
        router.server_close()
        dev.shutdown()
        dev.server_close()
        release.shutdown()
        release.server_close()
        router_thread.join(timeout=2)
        dev_thread.join(timeout=2)
        release_thread.join(timeout=2)


def test_registry_hot_loads_an_exploration_for_every_authenticated_user(
    tmp_path: Path,
) -> None:
    """部署注册表热加载探索实例，并向所有登录用户公开

    :importance: P2"""
    registry_path = tmp_path / "deployments.json"
    registry_path.write_text(
        json.dumps({
            "schema": "tfi.portal-deployments.v1",
            "instances": [
                {
                    "id": "explore-1",
                    "label": "探索 1",
                    "kind": "exploration",
                    "origin": "http://127.0.0.1:18087",
                    "order": 1,
                    "build_id": "example",
                    "enabled": True,
                },
                {
                    "id": "dev",
                    "label": "Dev",
                    "kind": "dev",
                    "origin": "http://127.0.0.1:18086",
                    "order": 100,
                    "enabled": True,
                },
                {
                    "id": "release",
                    "label": "Release",
                    "kind": "release",
                    "origin": "http://127.0.0.1:18085",
                    "order": 110,
                    "enabled": True,
                },
            ],
        }),
        encoding="utf-8",
    )
    registry = PortalDeploymentRegistry(registry_path)

    assert [item.instance_id for item in registry.deployments()] == [
        "explore-1",
        "dev",
        "release",
    ]
    assert registry.public_instances()[0] == {
        "id": "explore-1",
        "label": "探索 1",
        "kind": "exploration",
        "build_id": "example",
    }
    assert registry.error() == ""


def test_router_switches_a_release_default_user_to_a_registered_exploration(
    tmp_path: Path,
) -> None:
    """默认使用 Release 的用户可经 Router 切换到已注册的探索实例

    :importance: P2"""
    _credentials()
    signing_key_path = tmp_path / "session_signing.key"
    signing_key = _signing_key(signing_key_path)
    dev, dev_thread = _backend("dev")
    release, release_thread = _backend("release")
    exploration, exploration_thread = _backend("explore-1")
    registry_path = tmp_path / "deployments.json"
    registry_path.write_text(
        json.dumps({
            "schema": "tfi.portal-deployments.v1",
            "instances": [
                {
                    "id": "explore-1",
                    "label": "探索 1",
                    "kind": "exploration",
                    "origin": f"http://127.0.0.1:{exploration.server_address[1]}",
                    "order": 1,
                    "enabled": True,
                },
                {
                    "id": "dev",
                    "label": "Dev",
                    "kind": "dev",
                    "origin": f"http://127.0.0.1:{dev.server_address[1]}",
                    "order": 100,
                    "enabled": True,
                },
                {
                    "id": "release",
                    "label": "Release",
                    "kind": "release",
                    "origin": f"http://127.0.0.1:{release.server_address[1]}",
                    "order": 110,
                    "enabled": True,
                },
            ],
        }),
        encoding="utf-8",
    )
    registry = PortalDeploymentRegistry(registry_path)
    router = PortalRouterServer(
        ("127.0.0.1", 0),
        RouterPolicy(
            PortalSessionAuthority(
                signing_key_path,
                access_store=_access(tmp_path),
            ),
            {"CASE-A"},
        ),
        {},
        [ipaddress.ip_network("127.0.0.0/8")],
        5,
        registry,
    )
    router_thread = threading.Thread(target=router.serve_forever, daemon=True)
    router_thread.start()
    try:
        base = f"http://127.0.0.1:{router.server_address[1]}"
        session_cookie = f"db_session={_token(signing_key, 'yijun')}"
        request = urllib.request.Request(
            base + "/__portal_router/preference",
            data=json.dumps({"instance": "explore-1"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Cookie": session_cookie},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
            assert payload["effective_instance"] == "explore-1"
            assert [item["id"] for item in payload["instances"]] == [
                "explore-1",
                "dev",
                "release",
            ]
            set_cookie = response.headers["Set-Cookie"]
        preference_cookie = SimpleCookie()
        preference_cookie.load(set_cookie)
        request = urllib.request.Request(
            base + "/",
            headers={
                "Cookie": (
                    f"{session_cookie}; "
                    f"{DEPLOYMENT_PREFERENCE_COOKIE}="
                    f"{preference_cookie[DEPLOYMENT_PREFERENCE_COOKIE].value}"
                ),
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.headers.get("X-Portal-Backend") == "explore-1"
            assert json.loads(response.read()) == {
                "label": "explore-1",
                "path": "/",
                "body": "",
            }
    finally:
        router.shutdown()
        router.server_close()
        dev.shutdown()
        dev.server_close()
        release.shutdown()
        release.server_close()
        exploration.shutdown()
        exploration.server_close()
        router_thread.join(timeout=2)
        dev_thread.join(timeout=2)
        release_thread.join(timeout=2)
        exploration_thread.join(timeout=2)


def test_only_failed_requests_are_logged():
    """HTTP 访问日志只记录失败的请求

    Portal 路由器和 Portal 服务对 2xx/3xx 请求不写访问日志，4xx/5xx 请求照常记录。

    :importance: P2
    """
    from http import HTTPStatus

    from management_portal.portal_deployment_router import PortalRouterHandler
    from management_portal.serve_management_portal import Handler

    for handler_class in (PortalRouterHandler, Handler):
        handler = object.__new__(handler_class)
        handler.requestline = "GET /api/events HTTP/1.1"
        logged = []
        handler.log_message = lambda format, *args, logged=logged: logged.append(format % args)
        for code in (HTTPStatus.OK, 204, 304, HTTPStatus.NOT_FOUND, 502):
            handler.log_request(code)
        assert logged == ['"GET /api/events HTTP/1.1" 404 -', '"GET /api/events HTTP/1.1" 502 -']


def _restart_router(tmp_path: Path, port: int, restart_wait: float) -> tuple[PortalRouterServer, threading.Thread]:
    _credentials()
    signing_key_path = tmp_path / "session_signing.key"
    _signing_key(signing_key_path)
    router = PortalRouterServer(
        ("127.0.0.1", 0),
        RouterPolicy(PortalSessionAuthority(signing_key_path, access_store=_access(tmp_path)), {"CASE-A"}),
        {"release": Backend("release", "127.0.0.1", port)},
        [ipaddress.ip_network("127.0.0.0/8")],
        5,
        restart_wait=restart_wait,
    )
    thread = threading.Thread(target=router.serve_forever, daemon=True)
    thread.start()
    return router, thread


def _closed_port():
    """A port that refuses connections: bound, not listening (so no other socket takes it)."""
    import socket

    held = socket.socket()
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", 0))
    return held, held.getsockname()[1]


def test_a_request_waits_out_its_backends_restart_and_reaches_it_once(tmp_path: Path) -> None:
    """路由器等待重启中的 Web，请求只送达一次

    目标 Web 重启期间拒绝连接时，路由器在等待时限内重试连接，新进程上线后转发；请求在连接建立前没有送出，所以不会重复执行，用户不会看到 502。
    """
    # Restarting Web refuses connections for a few seconds; the request has not
    # reached it, so the router waits for the new process instead of a 502.
    held, port = _closed_port()
    received: list[bytes] = []

    class Recording(_EchoHandler):
        label = "release"

        def do_POST(self):
            received.append(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    servers = []

    def restarted() -> None:
        time.sleep(1.0)
        held.close()
        server = ThreadingHTTPServer(("127.0.0.1", port), Recording)
        servers.append(server)
        server.serve_forever()

    router, thread = _restart_router(tmp_path, port, restart_wait=10)
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{router.server_address[1]}/api/instructions/execute",
                                         data=b"once", method="POST")
        started = time.monotonic()
        threading.Thread(target=restarted, daemon=True).start()
        with urllib.request.urlopen(request, timeout=15) as response:
            assert response.status == 200 and json.loads(response.read()) == {"ok": True}
        assert time.monotonic() - started >= 0.9
        assert received == [b"once"]
    finally:
        router.shutdown()
        router.server_close()
        for server in servers:
            server.shutdown()
            server.server_close()
        thread.join(timeout=2)


def test_a_backend_that_stays_away_gets_a_page_that_retries_by_itself(tmp_path: Path) -> None:
    """后端超过等待时限时，页面导航得到自动重试页

    Web 超过等待时限仍不可用时，浏览器的页面请求得到 502 和一张 5 秒后自动重试的页面（带 Retry-After）；API 请求仍得到 backend_unavailable JSON。
    """
    import urllib.error

    held, port = _closed_port()
    router, thread = _restart_router(tmp_path, port, restart_wait=0.5)
    base = f"http://127.0.0.1:{router.server_address[1]}"
    try:
        try:
            urllib.request.urlopen(urllib.request.Request(base + "/lineage/", headers={"Accept": "text/html"}), timeout=5)
        except urllib.error.HTTPError as error:
            page = error
        assert page.code == 502 and page.headers["Retry-After"] == "5"
        assert '<meta http-equiv="refresh" content="5">' in page.read().decode("utf-8")
        # API calls keep the error code the pages handle.
        try:
            urllib.request.urlopen(base + "/api/health", timeout=5)
        except urllib.error.HTTPError as error:
            api = error
        assert api.code == 502 and json.loads(api.read())["code"] == "backend_unavailable"
    finally:
        held.close()
        router.shutdown()
        router.server_close()
        thread.join(timeout=2)


def test_a_request_whose_client_left_during_the_wait_never_reaches_the_backend(tmp_path: Path) -> None:
    """等待期间离开的请求不送达后端

    客户端在路由器等待重启时断开（刷新或关闭页面），路由器放弃这次请求，后端上线后不会再收到它。
    """
    import socket

    held, port = _closed_port()
    received: list[str] = []

    class Recording(_EchoHandler):
        label = "release"

        def do_POST(self):
            received.append(self.path)
            self.send_response(204)
            self.end_headers()

    router, thread = _restart_router(tmp_path, port, restart_wait=10)
    server = None
    try:
        with socket.create_connection(("127.0.0.1", router.server_address[1]), timeout=5) as client:
            client.sendall(b"POST /api/instructions/execute HTTP/1.1\r\nHost: portal\r\nContent-Length: 2\r\n\r\nok")
            time.sleep(0.5)
        time.sleep(0.5)
        held.close()
        server = ThreadingHTTPServer(("127.0.0.1", port), Recording)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        time.sleep(1.0)
        assert received == []
    finally:
        router.shutdown()
        router.server_close()
        if server is not None:
            server.shutdown()
            server.server_close()
        thread.join(timeout=2)


def test_a_router_checking_sessions_loads_neither_resources_nor_execution(isolated_portal_case) -> None:
    """路由器校验登录时不加载资源层和执行层

    用户路由器校验会话 cookie 并读取运行状态后，已加载的源文件中没有 management_portal/resources 与 management_portal/execution；发布只在 scripts、management_common 和 HATG 改动时重启路由器，这两层改动后路由器也不会运行旧代码。

    :design: decision_08bb2d9141f9
    """
    # A router restart cuts the connections it carries: the browser sees
    # "Failed to fetch".  Checking a session and reading the runtime status
    # stay out of the resource and execution packages, which change in most
    # publications and do not restart the router.
    with isolated_portal_case() as case_path:
        _credentials()
        signing_key_path = case_path / "session_signing.key"
        token = _token(_signing_key(signing_key_path), "CASE-A")
        _access(case_path)
        probe = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "from management_portal import portal_deployment_router as router\n"
            "actor = router.PortalSessionAuthority(sys.argv[1]).verify(sys.argv[2])\n"
            "router.portal_runtime_status()\n"
            "root = Path(router.__file__).resolve().parents[1]\n"
            "files = [Path(m.__file__).resolve() for m in list(sys.modules.values()) if getattr(m, '__file__', None)]\n"
            "sources = sorted(str(f.relative_to(root)) for f in files if root in f.parents)\n"
            "print(json.dumps({'actor': actor, 'sources': sources}))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe, str(signing_key_path), token],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
    loaded = json.loads(result.stdout.strip().splitlines()[-1])
    assert loaded["actor"] == "CASE-A"
    assert "management_portal/portal_session_auth.py" in loaded["sources"]
    assert [
        name for name in loaded["sources"]
        if name.startswith(("management_portal/resources/", "management_portal/execution/"))
    ] == []
