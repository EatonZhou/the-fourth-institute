#!/usr/bin/env python3
"""Interactive subscription login; never extract or copy account tokens."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_portal.claude_provider_catalog import NATIVE_KIND, login_environment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, required=True,
                        help="Absolute directory dedicated to this Claude account")
    parser.add_argument("command", choices=("login", "status"))
    args = parser.parse_args()
    profile = {"kind": NATIVE_KIND, "provider": "claude",
               "config_dir": str(args.config_dir)}
    overrides = login_environment(profile)
    binary = shutil.which("claude")
    if not binary:
        parser.error("Claude Code is not installed; use the Portal installer first")
    if args.command == "login":
        args.config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    command = [binary, "auth", args.command]
    if args.command == "login":
        command.append("--claudeai")
    return subprocess.call(command, env={**os.environ, **overrides})


if __name__ == "__main__":
    raise SystemExit(main())
