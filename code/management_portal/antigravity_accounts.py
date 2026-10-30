"""Antigravity subscription accounts: native OAuth and discovered model catalogs."""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, build_opener

from management_common.file_permissions import atomic_write_private_bytes
from management_portal.antigravity_client import AcpClient, AcpError, AuthenticationRequired
from management_portal.antigravity_runtime import command
from management_portal.execution.account_hosting import account_home


def account_for_route(route):
    from management_portal.execution.account_registrations import registrations
    rows = registrations("antigravity")
    if not route and len(rows) == 1:
        return rows[0]
    for row in rows:
        if route in {row["account_id"], row["route_id"]}:
            return row
    raise ValueError("Unknown Antigravity account route")


def environment(account_id):
    from management_portal.execution.account_login import login_environment
    return login_environment("antigravity", account_id)


def save_catalog(account_id, session):
    selection = session.get("models") or {}
    models = [str(item["modelId"]) for item in selection.get("availableModels", [])]
    default = selection.get("currentModelId", "")
    if not models:
        option = next((v for v in session.get("configOptions", []) if v.get("category") == "model"), {})
        for item in option.get("options", []):
            models.extend(str(v["value"]) for v in item.get("options", [item]) if "value" in v)
        default = option.get("currentValue", "")
    if not models or default not in models:
        raise ValueError("Antigravity returned no usable model catalog")
    value = {"models": models, "default_model": default, "efforts_by_model": {},
             "default_efforts_by_model": {}, "service_tiers_by_model": {},
             "capabilities": {"media": False}, "observed_at": time.time()}
    atomic_write_private_bytes(account_home("antigravity", account_id) / "catalog.json", json.dumps(value).encode())
    return value


def catalog(route=""):
    row = account_for_route(route)
    home = account_home("antigravity", row["account_id"])
    cached = home / "catalog.json"
    if cached.is_file():
        value = json.loads(cached.read_text())
        if time.time() - value.get("observed_at", 0) < 300:
            return value
    with AcpClient(command(), env=environment(row["account_id"]), cwd=str(home)) as client:
        client.initialize()
        client.call("authenticate", {"methodId": "oauth-personal"})
        # An empty native session lists models, without invoking one.
        result = client.call("session/new", {"cwd": str(home), "mcpServers": [],
                             "_meta": {"antigravity": {"enabledTools": []}}})
        return save_catalog(row["account_id"], result)


def routes():
    from management_portal.execution.account_registrations import registrations
    return [{"backend_route_id": row["route_id"], "label": row["account_id"]}
            for row in registrations("antigravity") if row["enabled"]]


def logged_in(account_id):
    try:
        with AcpClient(command(), env=environment(account_id)) as client:
            client.initialize()
            client.call("authenticate", {"methodId": "oauth-personal"})
        return True
    except AuthenticationRequired:
        return False
    except AcpError:
        raise ValueError("Antigravity authentication status is unavailable") from None


def complete_loopback_login(auth_url, callback):
    """Relay only this OAuth flow's loopback redirect, supplied by its user.

    The native Google process validates state and exchanges the code. Portal
    never handles tokens. No arbitrary URL or redirected network request is accepted.
    """
    auth = parse_qs(urlsplit(auth_url).query)
    expected = urlsplit(auth.get("redirect_uri", [""])[0])
    returned = urlsplit(callback.strip())
    state = parse_qs(returned.query).get("state")
    if (expected.scheme != "http" or expected.hostname not in {"localhost", "127.0.0.1"}
            or returned.scheme != expected.scheme or returned.netloc != expected.netloc
            or returned.path != expected.path or returned.fragment or returned.username
            or not state or state != auth.get("state")):
        raise ValueError("Paste the complete Google callback URL for this login")
    # Redirect responses may lead to Google onboarding. The browser, not this relay,
    # owns that UI; the native loopback reply is normally a local success page.
    from urllib.request import HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    from urllib.error import HTTPError
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(callback.strip(), timeout=10) as response:
            response.read(4096)
    except HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise


def main():
    """LoginManager subprocess. Only the browser authorization URL reaches stdout."""
    auth_urls = queue.Queue()
    def got_url(url):
        auth_urls.put(url)
        print(url, flush=True)

    def receive_callback():
        url = auth_urls.get()
        for line in sys.stdin:
            try:
                complete_loopback_login(url, line)
                return
            except (ValueError, OSError):
                print("Invalid callback URL; paste the full address from this login.", flush=True)

    threading.Thread(target=receive_callback, daemon=True).start()
    try:
        # LoginManager already supplied the isolated account and approved network route.
        import os
        with AcpClient(sys.argv[1:] or command(), env=dict(os.environ), on_login_url=got_url) as client:
            client.initialize()
            client.call("authenticate", {"methodId": "oauth-personal"}, timeout=900)
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
