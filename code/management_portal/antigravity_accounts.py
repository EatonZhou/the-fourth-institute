"""Antigravity subscription accounts: native OAuth and discovered model catalogs."""
from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from management_common.file_permissions import atomic_write_private_bytes
from management_portal.antigravity_client import AcpClient, AcpError, AuthenticationRequired
from management_portal.antigravity_runtime import command
from management_portal.execution.account_hosting import account_home
from management_common.provider_account_paths import account_runtime


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


def subscription_metadata(session, authenticated=None):
    """Extract only the tier already returned by ACP; never make a request."""
    for method, response in (('session', session), ('authenticate', authenticated)):
        if not isinstance(response, dict):
            continue
        meta = response.get('_meta')
        vendor = meta.get('antigravity') if isinstance(meta, dict) else None
        tier = vendor.get('tier') if isinstance(vendor, dict) else None
        if not isinstance(tier, dict):
            continue
        fields = {key: tier[key] for key in ('id', 'name') if isinstance(tier.get(key), str) and tier[key].strip()}
        if fields:
            return {'status': 'observed', 'source': 'acp.'+method, 'tier': fields,
                    'observed_at': time.time()}
    return {'status': 'unknown', 'source': 'acp', 'tier': {}, 'observed_at': None}


def save_catalog(account_id, session, agent_capabilities, *, authenticated=None):
    selection = session.get("models") or {}
    choices = {str(item["modelId"]): item.get("name", "") for item in selection.get("availableModels", [])}
    default = selection.get("currentModelId", "")
    if not choices:
        option = next((v for v in session.get("configOptions", []) if v.get("category") == "model"), {})
        for item in option.get("options", []):
            choices.update((str(v["value"]), v.get("name", "")) for v in item.get("options", [item]) if "value" in v)
        default = option.get("currentValue", "")
    if default not in choices:
        raise ValueError("Antigravity returned no usable model catalog")
    prompt_capabilities = agent_capabilities.get("promptCapabilities") or {}
    value = {"models": list(choices), "model_labels": {key: label for key, label in choices.items() if isinstance(label, str) and label},
             "default_model": default, "efforts_by_model": {},
             "default_efforts_by_model": {}, "service_tiers_by_model": {},
             "capabilities": {"media": prompt_capabilities.get("image") is True},
             "prompt_capabilities": prompt_capabilities, "observed_at": time.time(),
             "subscription": subscription_metadata(session, authenticated)}
    atomic_write_private_bytes(account_runtime("antigravity", account_id) / "catalog.json", json.dumps(value).encode())
    return value


def catalog_snapshot(route=""):
    """Read observed capabilities only; page reads must never start ACP or OAuth.

    Capabilities remain the last native observation beyond the model-list TTL.
    Legacy, absent or unreadable observations offer no media capability.
    Execution still validates the current initialize response before sending.
    """
    row = account_for_route(route)
    cached = account_runtime("antigravity", row["account_id"]) / "catalog.json"
    try:
        value = json.loads(cached.read_text())
        return value if isinstance(value, dict) and isinstance(value.get("prompt_capabilities"), dict) else {}
    except (OSError, ValueError):
        return {}


def catalog(route=""):
    """Discovery is carried by execution or an admitted quota read, never a page read."""
    return catalog_snapshot(route)


def routes():
    from management_portal.execution.account_registrations import registrations
    return [{"backend_route_id": row["route_id"], "label": row["account_id"]}
            for row in registrations("antigravity") if row["enabled"]]


def read_subscription(route):
    """Read native account tier without creating or loading a model session."""
    from management_portal.execution.account_observation import cancel_resource
    row = account_for_route(route)
    env = environment(row['account_id'])
    with AcpClient(command(env), env=env) as client, \
            cancel_resource(lambda: client.close(timeout=1)):
        client.initialize()
        authenticated = client.call('authenticate', {'methodId': 'oauth-personal'})
        return subscription_metadata({}, authenticated)


def logged_in(account_id):
    try:
        env = environment(account_id)
        with AcpClient(command(env), env=env) as client:
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
        from management_portal.execution.quota_reset_time import reset_epoch_seconds
        reset = reset_epoch_seconds(percent[2]) if percent and percent[2] else None
        windows.append({"id": f"{group}/{label}", "label": f"{group} · {label}", "quota_group": group,
                        "remaining_percent": remaining, "resets_at": reset,
                        "reset_time_precision_seconds": 60 if percent and percent[2] and percent[2].endswith(' UTC') else 1,
                        "observed_at": observed_at, "stale": False})
    return windows


def _cli_quota_summary(env):
    from management_portal.execution.account_observation import login_status

    token = Path(env["GEMINI_HOME"]) / "antigravity-acp" / "antigravity-oauth-token"
    if not token.is_file():
        return None
    with tempfile.TemporaryDirectory(prefix="agy-quota-") as home:
        profile = Path(home) / ".gemini" / "antigravity-cli"
        profile.mkdir(parents=True)
        (profile / token.name).symlink_to(token.resolve())
        isolated = {**env, "HOME": home, "GEMINI_HOME": str(profile.parent),
                    "XDG_CONFIG_HOME": home + "/.config", "XDG_DATA_HOME": home + "/.local/share"}
        args = [command(env)[0], "-p", "/usage", "--output-format", "json",
                "--print-timeout", "20s", "--log-file", "/dev/null"]
        try:
            result = login_status(args, env=isolated, cwd=home)
            if result.returncode:
                return None
            groups = json.loads(result.stdout)["command"]["data"]["groups"]
            return {"groups": [{"displayName": group["name"], "buckets": [
                {"displayName": bucket["name"], "remainingFraction": bucket.get("remaining_fraction"),
                 "resetTime": bucket.get("reset_time")} for bucket in group["buckets"]]}
                for group in groups]}
        except (ValueError, TypeError, KeyError, OSError, subprocess.TimeoutExpired):
            return None


def _quota_summary(env, version):
    """Read the same Cloud Code quota summary used by the signed-in CLI."""
    from management_portal.execution.account_observation import urlopen

    token_file = Path(env["GEMINI_HOME"]) / "antigravity-acp" / "antigravity-oauth-token"
    if not token_file.is_file():
        return None
    bearer = json.loads(token_file.read_bytes())["token"]["access_token"]
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + bearer,
               "User-Agent": "antigravity/" + version}

    def post(method, payload):
        url = "https://daily-cloudcode-pa.googleapis.com/v1internal:" + method
        request = Request(url, json.dumps(payload).encode(), headers)
        with urlopen(request, timeout=8, follow_redirects=False) as response:
            return json.load(response)

    # The scope for quota is the project Google assigns to this subscription.
    account = post("loadCodeAssist", {"metadata": {"ideType": "ANTIGRAVITY"}})
    project = account.get("cloudaicompanionProject")
    if isinstance(project, dict):
        project = project.get("id")
    if not isinstance(project, str) or not project:
        raise ValueError("Missing Antigravity subscription project")
    return post("retrieveUserQuotaSummary", {"project": project})


def _summary_windows(summary, observed_at):
    from management_portal.execution.quota_reset_time import reset_epoch_seconds

    windows = []
    for group in summary.get("groups") or []:
        name = group.get("displayName")
        for bucket in group.get("buckets") or []:
            label, fraction = bucket.get("displayName"), bucket.get("remainingFraction")
            if not name or not label or type(fraction) not in (int, float) or not 0 <= fraction <= 1:
                continue
            windows.append({"id": f"{name}/{label}", "label": f"{name} · {label}", "quota_group": name,
                            "remaining_percent": round(fraction * 100, 6),
                            "resets_at": reset_epoch_seconds(bucket.get("resetTime")),
                            "reset_time_precision_seconds": 1, "observed_at": observed_at, "stale": False})
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
    from management_portal.execution.account_observation import cancel_resource, check_cancelled
    check_cancelled()
    try:
        env = environment(account_id)
        with AcpClient(command(env), env=env, on_update=update) as client, \
                cancel_resource(lambda: client.close(timeout=1)):
            handshake = client.initialize()
            authenticated = client.call("authenticate", {"methodId": "oauth-personal"})
            sample.update(login_observation="logged_in", login_observed_at=datetime.now(timezone.utc).isoformat())
            try:
                # Keep quota traffic separate from business history, but reuse
                # its own native session across queries and process restarts.
                saved = account_runtime("antigravity", account_id) / "usage-session.json"
                try:
                    prior = json.loads(saved.read_text()).get("session_id", "")
                except (OSError, ValueError):
                    prior = ""
                params = {"cwd": str(account_home("antigravity", account_id)),
                          "mcpServers": []}
                if prior:
                    try:
                        result = client.call("session/load", {**params, "sessionId": prior})
                    except AcpError as error:
                        if error.code != -32002:  # ACP resource not found
                            raise
                        # The saved ID belongs only to the quota observer.
                        prior = ""
                if not prior:
                    result = client.call("session/new", params)
                # ACP load responses need not echo sessionId. The requested
                # identity remains authoritative for a resumed quota session.
                session_id = prior or result.get("sessionId", "")
                if not isinstance(session_id, str) or not session_id:
                    raise ValueError("Missing quota session identity")
                atomic_write_private_bytes(saved, json.dumps({"session_id": session_id}).encode())
                # Model configuration is optional on load; missing catalog
                # metadata must not prevent the independent /usage command.
                if result.get("models") or result.get("configOptions"):
                    save_catalog(account_id, result, handshake.get("agentCapabilities") or {}, authenticated=authenticated)
                # Do not turn a missing native slash command into an LLM prompt.
                # Google advertises commands asynchronously AFTER session/new.
                with advertised:
                    deadline = time.monotonic() + 5
                    while session_id not in commands and time.monotonic() < deadline:
                        check_cancelled()
                        advertised.wait(timeout=.1)
                check_cancelled()
                if "usage" not in commands.get(session_id, set()):
                    summary = _cli_quota_summary(env)
                    source = "antigravity.cli/usage"
                    if not summary or not summary["groups"]:
                        source = "antigravity.retrieveUserQuotaSummary"
                        summary = _quota_summary(env, handshake.get("agentInfo", {}).get("version", ""))
                    if summary is None:
                        sample.update(error_code="quota_unsupported", error="当前 Antigravity 运行时未提供额度查询")
                        return sample
                    windows = _summary_windows(summary, time.time())
                else:
                    result = client.call("session/prompt", {"sessionId": session_id,
                        "prompt": [{"type": "text", "text": "/usage"}]}, timeout=30)
                    if result.get("stopReason") != "end_turn":
                        raise ValueError("Incomplete quota response")
                    windows = quota_windows("".join(chunks), time.time())
                    source = "antigravity.acp/usage"
                if not windows:
                    raise ValueError("No quota windows reported")
                sample.update(status="ok", rate_limits=windows)
                if source == "antigravity.acp/usage":
                    sample["refresh_cards"] = {"status": "observed", "available_count": 0, "cards": [],
                        "source": source, "basis": "native_field_absent_as_zero",
                        "observed_at": datetime.fromtimestamp(windows[0]['observed_at'], timezone.utc).isoformat(),
                        "stale": False}
            except (AcpError, OSError, ValueError, KeyError):
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
        with AcpClient(command(os.environ), env=dict(os.environ), on_login_url=got_url) as client:
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
