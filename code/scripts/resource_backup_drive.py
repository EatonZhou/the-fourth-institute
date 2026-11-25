"""Copy Portal's encrypted backup snapshots to the configured Drive folder."""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

import yaml


OPERATIONS = {"drive-probe", "drive-critical", "drive-content"}
DESTINATION = "colab_drive:Backups/Portal"


def run(operation: str, service_root: Path, network_route: str | None = None) -> dict:
    from management_common.transfer_network import drive_transfer, resolve_route
    from management_common.site_config import read_site_config

    route = resolve_route(network_route)
    env = {key: value for key, value in os.environ.items() if not key.startswith(("RCLONE_", "RESTIC_"))}
    binary = read_site_config()["deployment"]["google_cloud_devices"]["rclone"]
    with drive_transfer(env, route, binary) as (rclone, scoped):
        return {**_run(operation, service_root, scoped, rclone), "network_route": route}


def _run(operation: str, service_root: Path, env: dict, rclone: str) -> dict:
    from management_common.portal_paths import path as portal_path

    if operation not in OPERATIONS:
        raise ValueError("Unsupported Google Drive backup operation")
    config_path = portal_path("secrets") / "colab-drive.conf"
    config = configparser.ConfigParser(interpolation=None)
    config.read(config_path)
    remote = config["colab_drive"]
    if (remote.get("type") != "drive" or remote.get("scope") != "drive.file"
            or not remote.get("root_folder_id") or remote.get("team_drive")):
        raise ValueError("Google Drive backup requires a folder-scoped personal Drive remote")
    settings = yaml.safe_load((service_root / "config/config.yaml").read_text())["restic"]
    password_file = Path(settings["password_file"])
    if password_file.stat().st_mode & 0o077:
        raise ValueError("The backup password file must be private")
    env["RCLONE_CONFIG"] = str(config_path)
    dest = DESTINATION
    prefix = [settings["binary"], "--password-file", str(password_file)]

    def execute(args, *, accepted=(0,)):
        result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL, capture_output=True, check=False)
        if result.returncode not in accepted:
            raise RuntimeError(f"Backup tool exited with code {result.returncode}")
        return result

    def restic(repository, *args):
        return execute([*prefix, "-o", f"rclone.program={rclone}", "-r", str(repository), *args]).stdout

    def copy(source, target, snapshot):
        listing = execute([rclone, "lsf", target], accepted=(0, 3))
        if not listing.stdout.strip():
            restic(f"rclone:{target}", "init", "--from-repo", str(source),
                   "--from-password-file", str(password_file), "--copy-chunker-params")
        elif b"config" not in listing.stdout.splitlines():
            raise ValueError("The Drive backup directory is occupied by non-repository files")
        restic(f"rclone:{target}", "copy", "--from-repo", str(source),
               "--from-password-file", str(password_file), snapshot)
        snapshots = json.loads(restic(f"rclone:{target}", "snapshots", "--json"))
        match = next((item for item in snapshots if snapshot in (item.get("id"), item.get("original"))), None)
        if match is None:
            raise RuntimeError("Copied snapshot is missing from the cloud repository")
        return match["id"]

    if operation == "drive-probe":
        dest = f"colab_drive:Backups/.probes/{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory(prefix="tfi-drive-backup-") as temp:
            folder = Path(temp)
            source = folder / "restic"
            sample = folder / "probe.bin"
            payload = os.urandom(64 * 1024)
            sample.write_bytes(payload)
            restic(source, "init")
            restic(source, "backup", "--", str(sample))
            snapshot = json.loads(restic(source, "snapshots", "--json"))[0]["id"]
            try:
                copied = copy(source, dest, snapshot)
                restored = restic(f"rclone:{dest}", "dump", copied, str(sample))
                if restored != payload:
                    raise RuntimeError("Restored probe payload does not match")
            finally:
                execute([rclone, "purge", dest], accepted=(0, 3))
        return {"operation": operation, "status": "success", "succeeded": True, "verified_bytes": len(payload)}

    profile = operation.removeprefix("drive-")
    source = settings["repository"]
    snapshots = json.loads(restic(source, "snapshots", "--tag", f"profile-{profile}", "--json"))
    if not snapshots:
        raise ValueError("No completed local snapshot for the requested profile")
    snapshot = max(snapshots, key=lambda item: item["time"])["id"]
    copied = copy(source, dest, snapshot)
    return {"operation": operation, "status": "success", "succeeded": True,
            "source_snapshot": snapshot, "drive_snapshot": copied}
