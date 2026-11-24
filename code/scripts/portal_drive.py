#!/usr/bin/env python3
"""Transfer files between the Portal host and its configured Google Drive folder."""
from __future__ import annotations

import argparse
import configparser
import os
from pathlib import Path
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.portal_paths import path as portal_path
from management_common.site_config import read_site_config
from management_common.transfer_network import GOOGLE_DRIVE_HOSTS, ROUTES, resolve_route, transfer_environment


REMOTE = "colab_drive:"


def _remote_path(value: str) -> str:
    if not value.startswith(REMOTE) or ".." in value.removeprefix(REMOTE).split("/"):
        raise ValueError(f"Drive paths must be within {REMOTE}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(prog="portal-drive", description=__doc__)
    parser.add_argument("--network-route", choices=ROUTES, help="Override the Portal site's default route")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("copy", "copyto"):
        transfer = commands.add_parser(command)
        transfer.add_argument("source")
        transfer.add_argument("destination")
        transfer.add_argument("--dry-run", action="store_true")
        transfer.add_argument("--progress", "-P", action="store_true")
    commands.add_parser("about").add_argument("--json", action="store_true")
    commands.add_parser("lsf").add_argument("path", nargs="?", default=REMOTE)
    args = parser.parse_args()

    try:
        options = []
        if args.command in ("copy", "copyto"):
            endpoints = (args.source, args.destination)
            is_remote = [endpoint.startswith(REMOTE) for endpoint in endpoints]
            if sum(is_remote) != 1:
                raise ValueError(f"Specify one local path and one {REMOTE} path")
            operands = [_remote_path(path) if remote else os.path.abspath(path)
                        for path, remote in zip(endpoints, is_remote)]
            if args.dry_run:
                options.append("--dry-run")
            if args.progress:
                options.append("--progress")
        elif args.command == "about":
            operands = [REMOTE]
            if args.json:
                options.append("--json")
        else:
            operands = [_remote_path(args.path)]

        deployment = read_site_config().get("deployment", {})
        binary = Path(deployment.get("google_cloud_devices", {}).get("rclone") or "")
        if not binary.is_absolute() or not binary.is_file():
            raise ValueError("Portal's rclone executable is not configured")
        config_path = portal_path("secrets") / "colab-drive.conf"
        config = configparser.ConfigParser(interpolation=None)
        try:
            config.read(config_path)
        except configparser.Error:
            raise ValueError("Portal's Google Drive configuration cannot be parsed") from None
        remote = config[REMOTE.rstrip(":")] if REMOTE.rstrip(":") in config else {}
        if (remote.get("type") != "drive" or remote.get("scope") != "drive.file"
                or not remote.get("root_folder_id") or remote.get("team_drive")):
            raise ValueError("Portal's folder-scoped Google Drive remote is unavailable")

        route = resolve_route(args.network_route)
        env = {key: value for key, value in os.environ.items() if not key.startswith("RCLONE_")}
        command = [str(binary), "--config", str(config_path), args.command, *options, "--", *operands]
        with transfer_environment(env, route, GOOGLE_DRIVE_HOSTS) as scoped:
            print(f"portal-drive: network_route={route}", file=sys.stderr, flush=True)
            return subprocess.run(command, env=scoped, check=False).returncode
    except (ValueError, OSError) as exc:
        parser.exit(1, f"portal-drive: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
