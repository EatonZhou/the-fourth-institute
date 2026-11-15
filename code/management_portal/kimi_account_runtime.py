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
import time

from management_common.file_lock import lock_stream
from management_common.file_permissions import atomic_write_private_bytes, ensure_private_directory
from management_common.provider_account_paths import account_runtime, runtime_root
from management_common.provider_account_paths import registered_home
from management_common.process_runtime import terminate_process_tree
from management_portal import kimi_code_server, kimi_portal_tools, model_api_store
from management_portal.execution.account_registrations import registrations


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
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
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
    return any(_session_busy(client, row) for row in rows or [])


def _session_busy(client, row):
    # The session listing owns this flag in newer native runtimes, whose
    # per-session /status route was removed. Use its explicit boolean so an
    # incompatible installed runtime can still be safely retired. Older
    # servers retain their status probe; an absent flag is never assumed idle.
    if isinstance(row.get("busy"), bool):
        return row["busy"]
    return client.get_status(row["id"]).get("busy")


def _stop_server(account, target, state):
    import psutil
    process = _owned_process(target, state)
    if process is None:
        return
    if _server_busy(_client(target, state["port"])):
        raise RuntimeError("Kimi account has an active turn; activation deferred")
    descendants = process.children(recursive=True)
    if state.get('supervised'):
        from scripts.portal_service_units import kimi_account_service
        kimi_account_service(account['account_id'], action='stop')
    else:
        terminate_process_tree(state["pid"], tree_id=process_tree_id(account))
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
    for remaining in alive:
        try:
            if remaining.is_running() and remaining.status() != psutil.STATUS_ZOMBIE:
                raise TimeoutError("Owned Kimi process did not stop")
        except psutil.NoSuchProcess:
            pass
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


def process_tree_id(account):
    # A Windows Job belongs to this instance's control state, not to the
    # shared Provider directory or an account ID reused by another instance.
    return "kimi-account-" + hashlib.sha256(os.fsencode(control(account))).hexdigest()


def restart_account(account_id):
    account = account_for_route(account_id)
    target = home(account)
    with (control(account) / "launch.lock").open("a+") as stream, lock_stream(stream):
        _stop_server(account, target, _read(control(account) / "server.json"))
    client_for_route(account_id)


def _version(account):
    return [account['version'], model_api_store.api_version(account['config']['api_id'])
            if account['kind'] == 'api_profile' else None]


def record_service(account, binary, port):
    """The supervised host refreshes its identity on every native restart."""
    import psutil
    atomic_write_private_bytes(control(account) / 'server.json', json.dumps({
        'pid': os.getpid(), 'port': port, 'version': _version(account),
        'created': psutil.Process().create_time(), 'runtime': str(binary.resolve()),
        'host_contract': 1, 'supervised': True,
    }).encode())


def client_for_route(route, *, for_control=False, probe_timeout=3):
    from .kimi_code_client import shared_client, KimiCodeUnavailable
    account = account_for_route(route)
    if account["kind"] == "service":
        return shared_client()
    target = home(account)
    if account["kind"] == "subscription_login" and not target.is_dir():
        raise FileNotFoundError("Registered Kimi account directory is missing")
    ensure_private_directory(target)
    with (control(account) / "launch.lock").open("a+") as stream, lock_stream(stream):
        marker = control(account) / "server.json"
        state = _read(marker)
        version = _version(account)
        binary = Path(os.getenv("PORTAL_KIMI_CODE_BINARY", str(kimi_code_server.DEFAULT_BINARY)))
        runtime = str(binary.resolve())
        if _owned_process(target, state) is not None:
            client = _client(target, state["port"])
            # Never start a second daemon or kill a live turn after a failed probe.
            sessions = client._request("GET", "sessions", timeout=probe_timeout)
            current = (state.get('version') == version and state.get('runtime') == runtime
                       and state.get('host_contract') == 1)
            if state.get('supervised') and (for_control or current):
                return client if for_control else _subscription_tools(account, client)
            rows = sessions.get("items", sessions.get("sessions", [])) if isinstance(sessions, dict) else sessions
            for row in rows or []:
                if _session_busy(client, row):
                    if for_control or current:
                        # A legacy host may finish its live turn before migration.
                        return client if for_control else _subscription_tools(account, client)
                    raise RuntimeError("Kimi account configuration changed while a turn is running; retry after it finishes")
            _stop_server(account, target, state)
        if not binary.is_file():
            raise KimiCodeUnavailable("Kimi Code runtime is missing")
        from scripts.portal_service_units import kimi_account_service
        service = kimi_account_service(account['account_id'], action='start')
        client = _client(target, service['port'])
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = _read(marker)
            if state.get('supervised') and state.get('port') == service['port'] and _owned_process(target, state):
                try:
                    client._request('GET', 'sessions', timeout=1)
                    return client if for_control else _subscription_tools(account, client)
                except KimiCodeUnavailable:
                    pass
            time.sleep(.2)
        # The supervisor owns recovery; never kill a host after a failed probe.
        raise KimiCodeUnavailable('Kimi account service did not become ready')


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
    with (control(account) / "launch.lock").open("a+") as stream, lock_stream(stream):
        state = _read(control(account) / "server.json")
        _stop_server(account, target, state)
        from scripts.portal_service_units import kimi_account_service
        kimi_account_service(account['account_id'], action='remove')
    shutil.rmtree(control(account))
