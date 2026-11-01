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
from management_common.instance_manifest import load
from management_common.code_environment import runtime_python


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
    """Run the watcher as the instance's own job; stopping the instance stops it."""
    from scripts.instance_services import job_unit
    from scripts.tfi_shutdown import verify_job
    instance = load()
    unit = job_unit(instance, "provider-activation")
    verify_job(unit, instance.root)
    active = subprocess.run(("systemctl", "--user", "is-active", "--quiet", unit), check=False)
    if active.returncode == 0:
        return
    subprocess.run(
        ("systemd-run", "--user", "--collect", f"--unit={unit}",
         '--setenv=PORTAL_INSTANCE_ROOT=' + str(instance.root),
         str(runtime_python(instance.code_root())), str(instance.code_root() / 'scripts/update_provider_runtimes.py'), '--activate-pending'),
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
