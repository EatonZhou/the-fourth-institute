"""Google device observations, shared Colab sessions and remote execution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

from management_common.file_lock import lock_stream
from management_common.portal_paths import path as portal_path
from management_common.site_config import read_site_config


def settings() -> dict:
    return read_site_config().get("deployment", {}).get("google_cloud_devices", {})


def _executable(config: dict, key: str) -> str:
    path = Path(config.get(key) or "")
    if not path.is_absolute() or not path.is_file():
        raise ValueError("Google device CLI is not configured")
    return str(path)


def _colab_command(config: dict) -> list[str]:
    root = portal_path("secrets") / "colab-cli"
    return [
        "/usr/bin/env", f"XDG_DATA_HOME={root}",
        "PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring",
        _executable(config, "colab_cli"), "--auth", "oauth2",
        "--config", str(root / "sessions.json"),
    ]


def colab_session(device_id: str) -> str:
    config = settings()
    if not device_id or device_id != config.get("compute_device_id"):
        return ""
    name = config.get("colab_session", "portal-colab")
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
        raise ValueError("Invalid Colab session name")
    return name


def colab_ssh_command(device_id: str, *remote_command: str) -> list[str]:
    session = colab_session(device_id)
    if not session or not remote_command:
        raise ValueError("Colab SSH requires a configured device and an existing session")
    key = portal_path("secrets") / "colab-cli" / "ssh-ed25519"
    return [*_colab_command(settings()), "ssh", "-s", session, "-i", str(key), "--", *remote_command]


def change_colab_session(device_id: str, action: str, payload: dict, *, actor: str) -> dict:
    from management_portal import permission_system

    if not permission_system.allows(actor, "RunResourceAction", {"resource_id": device_id, "action": action}):
        raise PermissionError("Colab requires action permission on the target device")
    session = colab_session(device_id)
    if not session or action not in {"colab.start", "colab.stop"}:
        raise ValueError("Expected the configured Colab device and a session action")
    start = action == "colab.start"
    hardware = payload.get("hardware")
    if start and hardware not in ("CPU", "T4", "L4", "G4", "A100", "H100"):
        raise ValueError("Choose a supported Colab accelerator or CPU")

    cli = _colab_command(settings())
    lock = portal_path("secrets") / "colab-cli" / "session-action.lock"
    with lock.open("a+b") as stream, lock_stream(stream):
        status = _read([*cli, "status", "-s", session])
        absent = f"[colab] Session '{session}' not found." in status.splitlines()
        current = re.search(rf"^\[{re.escape(session)}\] [^\n|]+ \| Hardware: (\w+) \|", status, re.MULTILINE)
        if not absent and not current:
            raise ValueError("Cannot determine the shared Colab session state")
        changed = False
        if start and current and current[1] != hardware:
            raise ValueError(f"Shared Colab session already uses {current[1]}")
        if start and absent:
            args = [] if hardware == "CPU" else ["--gpu", hardware]
            output = _read([*cli, "new", "-s", session, *args], timeout=90)
            if "[colab] Session READY." not in output.splitlines():
                raise ValueError("Colab session allocation was not confirmed")
            changed = True
        elif not start and current:
            output = _read([*cli, "stop", "-s", session], timeout=90)
            if "[colab] Session terminated." not in output.splitlines():
                raise ValueError("Colab session release was not confirmed")
            changed = True
        return {"session": session, "running": start, "changed": changed}


def _read(command: list[str], *, env: dict | None = None, timeout: int = 12) -> str:
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode:
        raise ValueError("Google cloud operation failed. Check authorization, availability and quota.")
    return result.stdout


def read_status(config: dict, kind: str) -> dict:
    if kind == "google_colab":
        output = _read([*_colab_command(config), "usage"])
        values = {}
        for field, pattern, cast in (
            ("compute_units", r"Current balance:\s*([\d.]+) compute units", float),
            ("compute_units_per_hour", r"Usage rate:\s*([\d.]+)/hr", float),
            ("active_assignments", r"Active assignments:\s*(\d+)", int),
        ):
            match = re.search(pattern, output)
            if not match:
                raise ValueError("Colab did not return all usage fields")
            values[field] = cast(match[1])
        return values
    from management_common.transfer_network import drive_transfer, resolve_route

    route = resolve_route()
    args = [
        "--config", str(portal_path("secrets") / "colab-drive.conf"),
        "--retries", "1", "--low-level-retries", "1", "about", "colab_drive:", "--json",
    ]
    with drive_transfer(dict(os.environ), route, _executable(config, "rclone")) as (binary, env):
        data = json.loads(_read([binary, *args], env=env))
    return {
        field: data[key]
        for field, key in (("storage_total_bytes", "total"), ("storage_used_bytes", "used"),
                           ("storage_free_bytes", "free"), ("storage_trashed_bytes", "trashed"))
        if type(data.get(key)) is int and data[key] >= 0
    }
