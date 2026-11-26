"""Copy a configured backup snapshot within the current authenticated ScriptRun.

The backup Lineage owns admission and the resource-backup mutex. The existing
Drive executor owns private configuration, copy/probe execution and cleanup.
No second ScriptRun or detached job is created by this entry point.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from management_common.instance_manifest import load
from management_common.principal import actor_principal
from management_portal import backup_lineage
from scripts import resource_backup_drive


def script_context() -> dict:
    from portal_script import get_current_run, inputs

    hints = inputs()
    identifier = hints.get("script_run_id") if isinstance(hints, dict) else None
    if not isinstance(identifier, str) or not re.fullmatch(r"script_run_[0-9a-f]{24}", identifier):
        raise PermissionError("Google Drive backup requires a current ScriptRun")
    expected_input = load().directory("runtime") / "script_runs" / identifier / "io/input.json"
    if Path(os.environ.get("PORTAL_SCRIPT_INPUT", "")) != expected_input:
        raise PermissionError("Backup input does not belong to this ScriptRun")
    target = backup_lineage.lineage_id()
    # The private relay matches identifier to its launch-bound run before
    # GetScriptRun reads the record under the original caller's live lease.
    run = get_current_run(lineage_id=target, script_run_id=identifier)
    if (not isinstance(run, dict) or run.get("lineage_id") != target
            or run.get("script_run_id") != identifier or run.get("status") != "running"
            or run.get("path") != backup_lineage.google_drive_script_path()
            or run.get("exclusive_key") != "resource-backup"
            or not isinstance(run.get("arguments"), dict)):
        raise PermissionError("Google Drive backup requires its bound, locked ScriptRun")
    caller = run.get("caller_id")
    try:
        principal = actor_principal(caller) if isinstance(caller, str) else None
    except ValueError as exc:
        raise PermissionError("Backup requires an authenticated User or Resident") from exc
    if principal is None or principal.id != caller or principal.kind not in {"user", "resident"}:
        raise PermissionError("Backup requires an authenticated User or Resident")
    return run


def main() -> int:
    from portal_script import set_output

    run = script_context()
    arguments = backup_lineage.google_drive_arguments(run["arguments"])
    configured = load().document["dependencies"].get("backup")
    if not configured:
        raise ValueError("This instance has no backup program in its manifest dependencies")
    operation = "drive-" + arguments["profile"]
    try:
        result = resource_backup_drive.run(
            operation, Path(configured).parent.parent, arguments.get("network_route"))
    except (KeyError, RuntimeError, OSError, ValueError):
        # Preserve the former executor's safe failure receipt. Tool output
        # and private repository/credential diagnostics never leave the domain.
        result = {"operation": operation, "status": "failed", "succeeded": False,
                  "error": "Check the selected network route, scoped Drive connection and local backup repository."}
    set_output(result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["succeeded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
