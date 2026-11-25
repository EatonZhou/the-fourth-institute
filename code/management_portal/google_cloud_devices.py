"""Read-only observations and existing-session SSH for the configured Google devices."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

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


def _read(command: list[str], *, env: dict | None = None) -> str:
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=12, env=env)
    if result.returncode:
        raise ValueError("Cloud status unavailable. Check the CLI configuration and Google authorization.")
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
