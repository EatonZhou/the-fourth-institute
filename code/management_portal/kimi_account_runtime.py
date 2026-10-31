"""On-demand Kimi servers; each account owns its credentials, config and sessions.

The installed service account keeps its existing server. API accounts never
rewrite that service's credentials. Workers may reconnect after a handover.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import time

from management_common.file_lock import lock_stream
from management_common.file_permissions import atomic_write_private_bytes, ensure_private_directory
from management_common.provider_account_paths import account_runtime, runtime_root
from management_portal.execution.account_hosting import registered_home
from management_common.process_runtime import spawn_process, terminate_process_tree
from management_portal import kimi_provider_catalog, kimi_code_server, kimi_portal_tools, model_api_store, kimi_windows_runtime
from management_portal.ai_provider_registry import AIProviderRegistry
from management_portal.execution.account_registrations import registrations
from management_portal.resident_shell_environment import resident_process_environment


def root():
    return runtime_root() / "kimi"


def account_for_route(route):
    row = next((r for r in registrations("kimi") if route in {r["route_id"], r["account_id"]}), None)
    if row is None:
        raise ValueError("Unknown Kimi account route")
    return row


def home(account):
    return registered_home(account)


def control(account):
    path = account_runtime("kimi", account["account_id"])
    ensure_private_directory(path)
    return path


def _write_config(account, target):
    if account["kind"] == "subscription_login":
        # Kimi owns OAuth configuration. Never replace it with an API profile.
        kimi_portal_tools.install(target)
        return
    entry = kimi_provider_catalog.profile(account)
    key = AIProviderRegistry.read_secret(AIProviderRegistry.secret_path(entry))
    config = kimi_provider_catalog.runtime_config(account, key)
    lines = []
    def table(value, parents=()):
        if parents:
            lines.append("[" + ".".join(json.dumps(p) for p in parents) + "]")
        for name, child in value.items():
            if not isinstance(child, dict):
                lines.append(json.dumps(name) + " = " + json.dumps(child, ensure_ascii=False))
        for name, child in value.items():
            if isinstance(child, dict):
                table(child, (*parents, name))
    table(config)
    content = "\n".join(lines) + "\n" + kimi_portal_tools.hook_config()
    atomic_write_private_bytes(target / "config.toml", content.encode())
    kimi_portal_tools.install(target)


def _client(target, port):
    from .kimi_code_client import KimiCodeClient
    return KimiCodeClient(f"http://127.0.0.1:{port}/api/v1", events_root=target / "server" / "events", home=target)


def _subscription_tools(account, client):
    if account["kind"] != "subscription_login":
        return client
    if not kimi_portal_tools.service_ready(client.home):
        kimi_portal_tools.install(client.home)
    import tomllib
    hook = tomllib.loads(kimi_portal_tools.hook_config())["hooks"][0]
    config = client._request("GET", "config")
    hooks = config.get("hooks", [])
    if hook not in hooks:
        # Merge only the hook list; a redacted config must never be written back.
        client._request("POST", "config", {"hooks": [*hooks, hook]})
    return client


def _read(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def _owned_process(target, state):
    """A PID alone never proves ownership, including after an owner restart."""
    import psutil
    if not state:
        return None
    try:
        process = psutil.Process(state["pid"])
        if not process.is_running():
            return None
        if state.get("created") is not None and process.create_time() != state["created"]:
            return None
        if Path(process.environ().get("KIMI_CODE_HOME", "")).resolve() != target.resolve():
            raise RuntimeError("Kimi server process ownership mismatch")
        return process
    except psutil.NoSuchProcess:
        return None


def _server_busy(client):
    sessions = client._request("GET", "sessions", timeout=3)
    rows = sessions.get("items", sessions.get("sessions", [])) if isinstance(sessions, dict) else sessions
    return any(client.get_status(row["id"]).get("busy") for row in rows or [])


def _stop_server(account, target, state):
    import psutil
    process = _owned_process(target, state)
    if process is None:
        return
    if _server_busy(_client(target, state["port"])):
        raise RuntimeError("Kimi account has an active turn; activation deferred")
    descendants = process.children(recursive=True)
    terminate_process_tree(state["pid"], tree_id=f"kimi-account-{account['account_id']}")
    # A detached Windows Job may cease to be reopenable after its original
    # owner exits. Use the verified psutil handles (PID + creation time), never
    # an unverified process ID or a blanket taskkill by executable name.
    for child in reversed([process, *descendants]):
        try:
            if child.is_running():
                child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs([process, *descendants], timeout=15)
    if alive:
        raise TimeoutError("Owned Kimi process did not stop")
    (control(account) / "server.json").unlink(missing_ok=True)


def running_accounts():
    result = []
    for account in registrations("kimi"):
        if account["kind"] == "service":
            continue
        target = home(account)
        state = _read(control(account) / "server.json")
        if _owned_process(target, state) is not None:
            result.append((account, target, state))
    return result


def runtime_busy():
    if any(_server_busy(_client(target, state["port"])) for _, target, state in running_accounts()):
        return True
    if any(account["kind"] == "service" for account in registrations("kimi")):
        from scripts.portal_service_units import provider_runtime_services
        if provider_runtime_services("portal-kimi-code.service"):
            from .kimi_code_client import shared_client
            return _server_busy(shared_client())
    return False


def restart_account(account_id):
    account = account_for_route(account_id)
    target = home(account)
    with (control(account) / "launch.lock").open("a+") as stream, lock_stream(stream):
        _stop_server(account, target, _read(control(account) / "server.json"))
    client_for_route(account_id)


def client_for_route(route, *, for_control=False):
    from .kimi_code_client import shared_client, KimiCodeUnavailable
    account = account_for_route(route)
    if account["kind"] == "service":
        return shared_client()
    target = home(account)
    if account["kind"] == "subscription_login" and not target.is_dir():
        raise FileNotFoundError("Registered Kimi account directory is missing")
    ensure_private_directory(target)
    tree_id = f"kimi-account-{account['account_id']}"
    with (control(account) / "launch.lock").open("a+") as stream, lock_stream(stream):
        marker = control(account) / "server.json"
        state = _read(marker)
        version = [account["version"], model_api_store.api_version(account["config"]["api_id"])
                   if account["kind"] == "api_profile" else None]
        binary = Path(os.getenv("PORTAL_KIMI_CODE_BINARY", str(kimi_code_server.DEFAULT_BINARY)))
        runtime = str(binary.resolve())
        if _owned_process(target, state) is not None:
            client = _client(target, state["port"])
            # Never start a second daemon or kill a live turn after a failed probe.
            sessions = client._request("GET", "sessions", timeout=3)
            if for_control or (state["version"] == version and state.get("runtime") == runtime):
                return client if for_control else _subscription_tools(account, client)
            rows = sessions.get("items", sessions.get("sessions", [])) if isinstance(sessions, dict) else sessions
            for row in rows or []:
                if client.get_status(row["id"]).get("busy"):
                    raise RuntimeError("Kimi account configuration changed while a turn is running; retry after it finishes")
            _stop_server(account, target, state)
        if for_control:
            raise KimiCodeUnavailable("Kimi account server is not running")
        if not binary.is_file():
            raise KimiCodeUnavailable("Kimi Code runtime is missing")
        _write_config(account, target)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        env, _ = resident_process_environment({})
        env["KIMI_CODE_HOME"] = str(target)
        env.update(kimi_windows_runtime.environment())
        with (control(account) / "server.log").open("ab") as log:
            process = spawn_process([os.sys.executable, str(Path(__file__).with_name("provider_daemon_host.py")),
                                     "--account-dir", str(target), "--", str(binary), "web", "--no-open",
                                     "--host", "127.0.0.1", "--port", str(port), "--dangerous-bypass-auth"], cwd=target, env=env, stdout=log, stderr=log,
                                    tree_id=tree_id, kill_on_owner_exit=False)
        client = _client(target, port)
        try:
            deadline = time.monotonic() + 45
            while process.poll() is None:
                try:
                    client._request("GET", "sessions", timeout=1)
                    import psutil
                    atomic_write_private_bytes(marker, json.dumps({"pid": process.pid, "port": port, "version": version,
                        "created": psutil.Process(process.pid).create_time(), "runtime": runtime}).encode())
                    return _subscription_tools(account, client)
                except KimiCodeUnavailable:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(.2)
            raise KimiCodeUnavailable("Kimi API account server did not become ready")
        except BaseException:
            terminate_process_tree(process, tree_id=tree_id)
            raise


def remember_session(session_id, route):
    if not session_id:
        return
    folder = root() / "session-routes"
    ensure_private_directory(folder)
    atomic_write_private_bytes(folder / (hashlib.sha256(session_id.encode()).hexdigest() + ".json"),
                               json.dumps({"route": route}).encode())


def session_route(session_id):
    return _read(root() / "session-routes" / (hashlib.sha256(session_id.encode()).hexdigest() + ".json")).get("route", "")


def remove_account(account):
    if account["kind"] == "service":
        return
    target = home(account)
    if not target.exists():
        return
    with (control(account) / "launch.lock").open("a+") as stream, lock_stream(stream):
        state = _read(control(account) / "server.json")
        _stop_server(account, target, state)
    shutil.rmtree(control(account))
