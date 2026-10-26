"""One wall-clock deadline for stopping a Linux Portal instance.

Stop jobs retain systemd's dependency ordering. The deadline belongs to the
whole transaction, including queued jobs, rather than to each individual unit.
Code activation and rolling Worker replacement do not use this operation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SHUTDOWN_SECONDS = 20.0
GRACE_SECONDS = 15.0


def stop_instance(root, *, application_only=False, timeout=SHUTDOWN_SECONDS):
    started = time.monotonic()
    from scripts.tfi_instance_manager import supervisor_environment
    from scripts.tfi_release_provider_services import RESTART_RECORD
    target = json.loads((Path(root) / "installation.json").read_text())["target"]
    prefix = target["service_prefix"]
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", prefix):
        raise ValueError("Invalid instance service prefix")
    env = supervisor_environment()
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError("Instance shutdown deadline elapsed during configuration")
    return stop_units(prefix, application_only=application_only,
                      timeout=remaining, env=env,
                      restart_record=None if application_only else Path(root) / RESTART_RECORD)


def stop_units(prefix, *, application_only=False, timeout=SHUTDOWN_SECONDS,
               env=None, runner=subprocess.run, restart_record=None, extra_units=()):
    if not sys.platform.startswith("linux"):
        raise NotImplementedError("Bounded instance shutdown currently requires systemd")
    if not 0 < timeout <= SHUTDOWN_SECONDS:
        raise ValueError("Shutdown deadline must be between 0 and 20 seconds")
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", prefix):
        raise ValueError("Invalid instance service prefix")
    if any(not isinstance(unit, str) or not re.fullmatch(r"[A-Za-z0-9_@.:-]+\.service", unit)
           for unit in extra_units):
        raise ValueError("Extra shutdown services must be explicit service unit names")
    started = time.monotonic()
    deadline = started + timeout
    grace_deadline = started + min(GRACE_SECONDS, timeout * 0.75)

    def control(*args, check=True):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Portal shutdown exceeded its overall deadline")
        try:
            result = runner(["systemctl", "--user", *args], env=env,
                            capture_output=True, text=True, timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("Portal shutdown control exceeded its overall deadline") from exc
        if check and result.returncode:
            raise RuntimeError("Portal shutdown control failed: " + result.stderr.strip())
        return result.stdout

    rows = json.loads(control("list-units", "--all", "--output=json", "--plain",
                              prefix + "-*.service", *extra_units))
    units = sorted({row["unit"] for row in rows
                    if (row["unit"].startswith(prefix + "-") and row["unit"].endswith(".service"))
                    or row["unit"] in extra_units})
    if application_only:
        units = [unit for unit in units if unit not in
                 {prefix + "-postgres.service", prefix + "-nats.service"}]
    if not units:
        return {"ok": True, "services": [], "forced": [], "elapsed_seconds": time.monotonic() - started}

    # Capture the running account set before stopping it. The normal instance
    # start path restores this record; repeated stops must not erase it.
    # Callers managing storage can preserve database/NATS while still stopping
    # application and Provider consumers. Prefix selection includes paired Webs.
    running_accounts = {row["unit"] for row in rows if row["unit"] in units
                        and row["unit"].startswith(prefix + "-codex-app-server@")
                        and row.get("active") in {"active", "activating", "reloading"}}
    accounts = [unit for unit in units if unit.startswith(prefix + "-codex-app-server@")]
    if restart_record is not None:
        from scripts.tfi_release_provider_services import _record, _recorded
        _record(restart_record, running_accounts | set(_recorded(restart_record)))

    control("stop", "--no-block", *units)
    forced = []
    while True:
        output = control("show", *units, "--property=Id,ActiveState,MainPID,ControlPID")
        states = {}
        for block in output.strip().split("\n\n"):
            values = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            if values.get("Id"):
                states[values["Id"]] = values
        if set(states) != set(units):
            raise RuntimeError("Incomplete shutdown observation; refusing to report success")
        pending = [unit for unit in units if states[unit].get("ActiveState") not in {"inactive", "failed"}
                   or states[unit].get("MainPID", "0") != "0"
                   or states[unit].get("ControlPID", "0") != "0"]
        if not pending:
            if restart_record is not None and accounts:
                control("disable", *accounts)
            return {"ok": True, "services": units, "forced": forced,
                    "elapsed_seconds": time.monotonic() - started}
        if not forced and time.monotonic() >= grace_deadline:
            # Include live units whose stop jobs are queued behind another unit.
            # Merely timing out systemctl leaves those processes running.
            forced = pending
            control("kill", "--kill-whom=all", "--signal=SIGKILL", *pending, check=False)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Portal processes remain after shutdown deadline: " + ", ".join(pending))
        time.sleep(min(0.1, remaining))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--application-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(stop_instance(args.root, application_only=args.application_only), ensure_ascii=False))


if __name__ == "__main__":
    main()
