#!/usr/bin/env python3
"""Run one provider update check; scheduled by the System Lineage Resident."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_portal.execution.provider_updates import activate_pending_once, run_update_pass
from management_common.portal_paths import resolve_directory
from management_common.site_config import read_site_config
from scripts.portal_service_units import _isolated_provider_context


def _watch_pending() -> int:
    deadline = time.monotonic() + 72 * 60 * 60
    while True:
        result = activate_pending_once()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        if not result.get("pending") and not result.get("busy"):
            return 0 if not result.get("errors") else 1
        if time.monotonic() >= deadline:
            return 2
        time.sleep(30)


def _start_activation_watch() -> None:
    isolated = _isolated_provider_context()
    prefix = isolated["service_prefix"] if isolated else "portal"
    unit = f"{prefix}-provider-pending-activation"
    active = subprocess.run(
        ("systemctl", "--user", "is-active", "--quiet", f"{unit}.service"),
        check=False,
    )
    if active.returncode == 0:
        return
    configuration_environment = tuple(
        f"--setenv={name}={resolve_directory(os.environ[name])}"
        for name in ("PORTAL_SITE_CONFIG", "PORTAL_PATHS_CONFIG") if name in os.environ
    )
    subprocess.run(
        ("systemd-run", "--user", "--collect", f"--unit={unit}",
         "--property=EnvironmentFile=" + str(resolve_directory(os.getenv(
             "PORTAL_POSTGRES_ENV_FILE",
             read_site_config().get("deployment", {}).get("postgres_env_file", "~/.config/tfi/portal-postgres.env"),
         ))),
         *configuration_environment,
         sys.executable, str(Path(__file__).resolve()), "--activate-pending"),
        check=True, capture_output=True, text=True,
    )


if __name__ == "__main__":
    if sys.argv[1:] == ["--activate-pending"]:
        raise SystemExit(_watch_pending())
    if sys.argv[1:]:
        raise SystemExit("Usage: update_provider_runtimes.py [--activate-pending]")
    result = run_update_pass()
    if result.get("pending"):
        try:
            _start_activation_watch()
            result["activation_watch"] = "started"
        except (OSError, subprocess.CalledProcessError) as exc:
            result["errors"]["activation_watch"] = str(exc)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(1 if result.get("busy") or result.get("errors") else 0)
