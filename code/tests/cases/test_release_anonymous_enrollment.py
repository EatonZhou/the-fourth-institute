"""Released first-admin UI must boot through the real anonymous HTTP routes."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.parse import urlsplit
import subprocess


def test_desktop_setup_status_reads_current_authority_without_session(monkeypatch):
    import json
    from urllib.request import ProxyHandler, build_opener
    from management_portal import serve_management_portal as portal
    from management_portal import http_auth, http_instance, web_accounts
    from scripts.tfi_windows_launcher import DefaultStartup

    configured = False
    monkeypatch.setattr(web_accounts, '_USER_ENROLLMENT', SimpleNamespace(has_accounts=lambda: configured))
    monkeypatch.setattr(http_instance, '_PORTAL_INSTANCE', 'setup-fixture')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            parsed = urlsplit(self.path)
            result = portal._PORTAL_HTTP_ROUTES.dispatch(
                'GET', parsed.path, self, parsed, '', phase='public')
            if result is portal.ROUTE_NOT_FOUND:
                self.send_error(404)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        flow = DefaultStartup('/unused')
        state = {'target': {'service_prefix': 'setup-fixture'}}
        for configured in (False, True, False):
            with build_opener(ProxyHandler({})).open(base + '/api/auth/setup-status') as response:
                assert 'no-store' in response.headers['Cache-Control']
                assert json.load(response) == {'instance': 'setup-fixture', 'configured': configured}
            assert flow.account_configured(base, state) is configured
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_first_admin_page_loads_and_submits_without_a_session(monkeypatch):
    from management_portal import serve_management_portal as portal, personal_ama
    from management_portal import http_auth, http_instance, web_accounts

    calls = []

    def preview(secret):
        assert secret == "fixture-invitation"
        calls.append("preview")
        return dict(username="fixture-admin", display_name="Fixture admin",
                    first_admin=True, expires_at=4102444800)

    def accept(**payload):
        assert payload == {"invitation_secret": "fixture-invitation",
                           "password": "Fixture-only-password-123!"}
        calls.append("accept")
        return dict(user={"username": "fixture-admin"}, credential={}, first_admin=True)

    monkeypatch.setattr(web_accounts, "_USER_ENROLLMENT", SimpleNamespace(
        preview_invitation=preview, accept_invitation=accept))
    monkeypatch.setattr(web_accounts, "_PORTAL_ACCESS", SimpleNamespace(audit=lambda *a, **k: None))
    monkeypatch.setattr(http_auth, "_get_session_username", lambda request: "")
    monkeypatch.setattr(http_auth, "_check_session", lambda request: False)
    monkeypatch.setattr(personal_ama, "provision_account", lambda username: {})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def dispatch(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            result = portal._PORTAL_HTTP_ROUTES.dispatch(
                self.command, parsed.path, self, parsed, "", phase="public")
            if result is portal.ROUTE_NOT_FOUND:
                self.send_error(404)

        do_GET = do_POST = dispatch

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        script = Path(__file__).with_name("javascript") / "release_anonymous_enrollment.cjs"
        subprocess.run(["node", str(script), f"http://127.0.0.1:{server.server_port}"],
                       check=True, timeout=45)
        assert calls == ["preview", "accept"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_lan_http_enrollment_hashes_the_secret_without_web_crypto():
    """局域网 HTTP 来源的用户邀请界面在无 Web Crypto 时仍只提交密钥摘要

    Chromium 打开命名的 HTTP 来源（非安全上下文、无 crypto.subtle），portal_user_enrollment.js 的 SHA-256 与 Node 对空串、中文与 emoji 及多种长度一致；
    生成的随机密钥不重复且为 43 位 URL 安全字符；提交只带 secret_sha256 和 user_id，原密钥不离开浏览器；复制走后备路径。

    :module: security.device-credentials
    :design: decision_30528aff7035
    """
    script = Path(__file__).with_name("javascript") / "release_http_enrollment.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
