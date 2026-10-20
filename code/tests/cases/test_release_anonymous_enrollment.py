"""Released first-admin UI must boot through the real anonymous HTTP routes."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.parse import urlsplit
import subprocess


def test_first_admin_page_loads_and_submits_without_a_session(monkeypatch):
    from management_portal import serve_management_portal as portal, personal_ama

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

    monkeypatch.setattr(portal, "_USER_ENROLLMENT", SimpleNamespace(
        preview_invitation=preview, accept_invitation=accept))
    monkeypatch.setattr(portal, "_PORTAL_ACCESS", SimpleNamespace(audit=lambda *a, **k: None))
    monkeypatch.setattr(portal, "_get_session_username", lambda request: "")
    monkeypatch.setattr(portal, "_check_session", lambda request: False)
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
