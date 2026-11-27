"""Roll a validated Codex or Claude runtime onto independent account services."""
import time

from management_common.code_environment import account_provider_link
from management_common.instance_manifest import load
from management_portal.claude_code_client import ClaudeCodeClient, ClaudeCodeUnavailable, account_socket
from management_portal.codex_app_server_client import CodexAppServerClient
from management_portal.codex_app_server_protocol import CodexAppServerUnavailable
from scripts.instance_services import unit_name
from scripts.portal_service_units import claude_account_service, provider_runtime_services
from .account_hosting import codex_socket_path
from .account_registrations import registrations
from .provider_activation import activation_window, publish_activation_ready


def _codex_busy(account):
    client = CodexAppServerClient(codex_socket_path(account), request_timeout_seconds=5)
    try:
        cursor = None
        while True:
            page = client.request("thread/loaded/list", {"cursor": cursor, "limit": 100})
            for thread_id in page["data"]:
                thread = client.request("thread/read", {"threadId": thread_id, "includeTurns": False})["thread"]
                status = thread["status"]["type"]
                if status == "systemError" and not client.active_turn_id(thread_id):
                    # Codex keeps a failed terminal turn loaded until its next request.
                    continue
                if status not in {"idle", "notLoaded"}:
                    return True
            cursor = page["nextCursor"]
            if not cursor:
                return False
    finally:
        client.close()


def _wait_ready(provider, instance, account, route, root):
    deadline = time.monotonic() + 45
    while True:
        try:
            if provider == "codex":
                _codex_busy(account)  # Native RPC handshake and a readable thread list.
            else:
                health = ClaudeCodeClient(account_socket(instance, route)).health()
                if health["provider_profile"] != route or health["provider_version"] != root.name.removeprefix("claude-provider-"):
                    raise RuntimeError("Claude account service loaded an unexpected route or version")
            return
        except (CodexAppServerUnavailable, ClaudeCodeUnavailable, OSError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(.15)


def switch_account(provider, runtime, root, row):
    from . import provider_updates as updates
    instance = load()
    account, route = row["account_id"], row["route_id"]
    component = "codex-app-server@" + account if provider == "codex" else "claude-account@" + route
    with activation_window(provider + "@" + route) as reserved:
        if not reserved or updates._active_executions(provider, route, account_id=account):
            return False
        configured = component in instance.document["services"]["components"]
        control = runtime.service_control()
        unit = unit_name(instance, component) if configured else None
        state = control.status(unit)["active"] if unit else "inactive"
        if state not in {"active", "inactive", "failed"}:
            return False
        running = state == "active"
        hold = account_socket(instance, route).with_suffix(".migrating") if provider == "claude" else None
        provision = provider == "claude" and row["enabled"] and (not configured or hold.exists())
        if running:
            if provider == "codex" and _codex_busy(account):
                return False
            if provider == "claude" and ClaudeCodeClient(account_socket(instance, route)).health()["active"]:
                return False
        pin = account_provider_link(instance, component)
        previous = pin.resolve() if pin.exists() else runtime.activation_link().resolve()
        target = runtime.link_target(root)
        if provision:
            hold.parent.mkdir(parents=True, exist_ok=True)
            hold.touch()  # Existing routes stay on the shared socket until verified.
        updates._link(pin, target)
        try:
            if running:
                control.restart(unit)
            elif provision:
                claude_account_service(route, action="start")
            if running or provision:
                _wait_ready(provider, instance, account, route, root)
            if provision:
                hold.unlink()
        except Exception:
            updates._link(pin, previous)
            if running:
                control.restart(unit)
            raise
        publish_activation_ready(provider + "@" + route, root.name)
        return True


def roll_accounts(provider, runtime, root, completed, save, errors):
    """A busy route is skipped, including while another route is being admitted."""
    from . import provider_updates as updates
    if not runtime.link_target(root).exists():
        raise FileNotFoundError("Validated account runtime is missing")
    pending = False
    for row in registrations(provider):
        key = row["account_id"]
        if key in errors or completed.get(key) == [row["route_id"], str(root)]:
            continue
        try:
            if switch_account(provider, runtime, root, row):
                completed[key] = [row["route_id"], str(root)]
                save()
            else:
                pending = True
        except Exception as exc:
            updates.LOGGER.exception("Could not activate %s account %s", provider, key)
            errors[key] = exc
    if pending:
        return False
    if errors:
        raise next(iter(errors.values()))
    # All registered accounts now have their own selection. The default is for
    # future registrations and Claude's legacy socket only.
    with activation_window(provider) as reserved:
        if not reserved:
            return False
        shared = provider_runtime_services(runtime.unit_files[0]) if provider == "claude" else ()
        if shared and ClaudeCodeClient().health()["active"]:
            return False
        previous = runtime.activation_link().resolve()
        updates._link(runtime.activation_link(), runtime.link_target(root))
        try:
            for unit in shared:
                runtime.service_control().restart(unit)
            if provider == "claude":
                runtime.ensure_ready()
        except Exception:
            updates._link(runtime.activation_link(), previous)
            for unit in shared:
                runtime.service_control().restart(unit)
            raise
        publish_activation_ready(provider, root.name)
        return True
