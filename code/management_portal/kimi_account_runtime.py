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
from management_common.instance_paths import accounts_root
from management_common.process_runtime import spawn_process, terminate_process_tree, process_alive
from management_portal import kimi_provider_catalog, kimi_code_server, kimi_portal_tools, model_api_store
from management_portal.ai_provider_registry import AIProviderRegistry
from management_portal.execution.account_registrations import registrations
from management_portal.resident_shell_environment import resident_process_environment


def root():
    return accounts_root() / "kimi"


def account_for_route(route):
    row = next((r for r in registrations("kimi") if route in {r["route_id"], r["account_id"]}), None)
    if row is None:
        raise ValueError("Unknown Kimi account route")
    return row


def home(account):
    path = root() / account["account_id"]
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Kimi account directories must not be symbolic links")
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


def client_for_route(route, *, for_control=False):
    from .kimi_code_client import shared_client, KimiCodeUnavailable
    account = account_for_route(route)
    if account["kind"] == "service":
        return shared_client()
    target = home(account)
    ensure_private_directory(target)
    tree_id = f"kimi-account-{account['account_id']}"
    with (target / "launch.lock").open("a+") as stream, lock_stream(stream):
        marker = target / "server.json"
        state = _read(marker)
        version = [account["version"], model_api_store.api_version(account["config"]["api_id"])
                   if account["kind"] == "api_profile" else None]
        if state and process_alive(state["pid"]):
            client = _client(target, state["port"])
            # Never start a second daemon or kill a live turn after a failed probe.
            sessions = client._request("GET", "sessions", timeout=3)
            if for_control or state["version"] == version:
                return client if for_control else _subscription_tools(account, client)
            rows = sessions.get("items", sessions.get("sessions", [])) if isinstance(sessions, dict) else sessions
            for row in rows or []:
                if client.get_status(row["id"]).get("busy"):
                    raise RuntimeError("Kimi account configuration changed while a turn is running; retry after it finishes")
            terminate_process_tree(state["pid"], tree_id=tree_id)
        if for_control:
            raise KimiCodeUnavailable("Kimi account server is not running")
        binary = Path(os.getenv("PORTAL_KIMI_CODE_BINARY", str(kimi_code_server.DEFAULT_BINARY)))
        if not binary.is_file():
            raise KimiCodeUnavailable("Kimi Code runtime is missing")
        _write_config(account, target)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        env, _ = resident_process_environment({})
        env["KIMI_CODE_HOME"] = str(target)
        with (target / "server.log").open("ab") as log:
            process = spawn_process([str(binary), "web", "--no-open", "--host", "127.0.0.1", "--port", str(port),
                                     "--dangerous-bypass-auth"], cwd=target, env=env, stdout=log, stderr=log,
                                    tree_id=tree_id, kill_on_owner_exit=False)
        client = _client(target, port)
        try:
            deadline = time.monotonic() + 45
            while process.poll() is None:
                try:
                    client._request("GET", "sessions", timeout=1)
                    atomic_write_private_bytes(marker, json.dumps({"pid": process.pid, "port": port, "version": version}).encode())
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
    with (target / "launch.lock").open("a+") as stream, lock_stream(stream):
        state = _read(target / "server.json")
        if state and process_alive(state["pid"]):
            _client(target, state["port"])._request("GET", "sessions", timeout=3)
            terminate_process_tree(state["pid"], tree_id=f"kimi-account-{account['account_id']}")
    shutil.rmtree(target)
