"""Antigravity subscription accounts: native OAuth and discovered model catalogs."""
from __future__ import annotations

import json
import queue
import re
import sys
import threading
import time
from datetime import datetime, timezone
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


def quota_windows(report, observed_at):
    """Normalize the official ACP /usage report, not session token totals.

    ACP 1.4.5 exposes quota as a Markdown command response, not structured
    metadata. Unknown formats stay unknown; absolute units are not percentages.
    """
    section = report.partition("### Quota Limits\n")[2]
    group, windows = "", []
    for line in section.splitlines():
        if line.startswith("### "):
            break
        heading = re.fullmatch(r"- \*\*(.+)\*\*", line)
        if heading:
            group = heading[1]
            continue
        bucket = re.fullmatch(r"  - \*\*(.+):\*\* (.+)", line)
        if not group or not bucket or bucket[2].startswith("Disabled"):
            continue
        label, value = bucket.groups()
        percent = re.fullmatch(r"(\d+(?:\.\d+)?)% remaining(?: \(resets (.+)\))?", value)
        remaining = float(percent[1]) if percent else None
        if remaining is not None and not 0 <= remaining <= 100:
            remaining = None
        reset = None
        if percent and percent[2]:
            try:
                reset = datetime.strptime(percent[2], "%b %d, %Y, %H:%M UTC").replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                pass
        windows.append({"id": f"{group}/{label}", "label": f"{group} · {label}",
                        "remaining_percent": remaining, "resets_at": reset,
                        "observed_at": observed_at, "stale": False})
    return windows


def observe_account(account_id):
    """Read native auth and quota without an LLM turn or a Resident session."""
    session_id, commands, chunks = "", {}, []
    advertised = threading.Condition()
    def update(params):
        item = params.get("update") or {}
        if item.get("sessionUpdate") == "available_commands_update":
            with advertised:
                commands[params.get("sessionId")] = {c.get("name") for c in item.get("availableCommands", [])}
                advertised.notify_all()
        if params.get("sessionId") == session_id and item.get("sessionUpdate") == "agent_message_chunk":
            content = item.get("content") or {}
            if content.get("type") == "text":
                chunks.append(content.get("text", ""))

    sample = {"status": "unknown", "rate_limits": []}
    try:
        with AcpClient(command(), env=environment(account_id), on_update=update) as client:
            client.initialize()
            client.call("authenticate", {"methodId": "oauth-personal"})
            sample.update(login_observation="logged_in", login_observed_at=datetime.now(timezone.utc).isoformat())
            try:
                result = client.call("session/new", {"cwd": str(account_home("antigravity", account_id)),
                    "mcpServers": [], "_meta": {"antigravity": {"enabledTools": []}}})
                session_id = result["sessionId"]
                # Do not turn a missing native slash command into an LLM prompt.
                # Google advertises commands asynchronously AFTER session/new.
                with advertised:
                    advertised.wait_for(lambda: session_id in commands, timeout=5)
                if "usage" not in commands.get(session_id, set()):
                    sample.update(error_code="quota_unsupported", error="当前 Antigravity 运行时未提供额度查询")
                    return sample
                result = client.call("session/prompt", {"sessionId": session_id,
                    "prompt": [{"type": "text", "text": "/usage"}]}, timeout=30)
                if result.get("stopReason") != "end_turn":
                    raise ValueError("Incomplete quota response")
                windows = quota_windows("".join(chunks), time.time())
                if not windows:
                    raise ValueError("No quota windows reported")
                sample.update(status="ok", rate_limits=windows)
            except (AcpError, TimeoutError, ValueError, KeyError):
                # Quota failure is not evidence of logout. Raw native errors and
                # the report must not cross the account-observation boundary.
                sample.update(error_code="quota_unavailable", error="Antigravity 额度暂不可读取")
            return sample
    except AuthenticationRequired:
        return {"status": "unknown", "rate_limits": [], "login_observation": "logged_out",
                "login_observed_at": datetime.now(timezone.utc).isoformat()}
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
    client = None
    try:
        # LoginManager already supplied the isolated account and approved network route.
        import os
        with AcpClient(sys.argv[1:] or command(), env=dict(os.environ), on_login_url=got_url) as client:
            client.initialize()
            client.call("authenticate", {"methodId": "oauth-personal"}, timeout=900)
    except Exception:
        # A fixed diagnostic code is the only failure output crossing the
        # subprocess boundary; native text can contain tokens and account data.
        print("ANTIGRAVITY_LOGIN_ERROR:" + ((client.login_failure if client else "") or "native_failed"), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
