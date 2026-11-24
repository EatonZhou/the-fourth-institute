#!/usr/bin/env python3
"""Run one fixed backup operation under Lineage's RunScript lifecycle.

The backup service owns credentials, restic commands, status and its repository
lock. This entry does not schedule work or start a detached systemd process.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts import resource_backup_drive


OPERATIONS = {
    "critical": ("run-once", "--profile", "critical", "--apply"),
    "content": ("run-once", "--profile", "content", "--apply"),
    "check": ("maintain", "--operation", "check", "--apply"),
    "deep-check": ("maintain", "--operation", "deep-check", "--apply"),
    "retention": ("maintain", "--operation", "retention", "--apply"),
    "prune": ("maintain", "--operation", "retention", "--apply", "--prune"),
}


def run_operation(operation: str, *, service_root: Path | None = None, network_route: str | None = None) -> dict[str, Any]:
    if operation not in OPERATIONS and operation not in resource_backup_drive.OPERATIONS:
        raise ValueError("Unsupported backup operation")
    if service_root is None:
        from management_common.instance_manifest import load
        configured = load().document['dependencies'].get('backup')
        if not configured:
            raise ValueError("This instance has no backup program in its manifest dependencies")
        program = Path(configured)
        service_root = program.parent.parent
    else:
        program = service_root / 'src/main.py'
    if operation in resource_backup_drive.OPERATIONS:
        try:
            return resource_backup_drive.run(operation, service_root, network_route)
        except (KeyError, RuntimeError, OSError, ValueError):
            return {"operation": operation, "status": "failed", "succeeded": False,
                    "error": "Check the selected network route, scoped Drive connection and local backup repository."}
    completed = subprocess.run(
        ["/usr/bin/nice", "-n", "10", "/usr/bin/ionice", "-c", "2", "-n", "7",
         "/usr/bin/python3", str(program), *OPERATIONS[operation]],
        cwd=service_root, capture_output=True, text=True, check=False, umask=0o077,
    )
    # Never relay raw command output: it may contain repository or credential
    # diagnostics. Full domain records remain in the backup service's state.
    try:
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            result = {}
    except (TypeError, ValueError):
        result = {}
    status = str(result.get("status") or "failed")
    succeeded = completed.returncode == 0 and status in {"success", "no_op", "skipped_locked"}
    return {
        "operation": operation,
        "status": status if status in {"success", "no_op", "skipped_locked", "blocked", "failed"} else "failed",
        "succeeded": succeeded,
        "run_id": str(result.get("run_id") or "")[:160],
        "started_at": str(result.get("started_at") or "")[:80],
        "finished_at": str(result.get("finished_at") or "")[:80],
    }


def main() -> int:
    from portal_script import set_output

    operation, *routes = sys.argv[1:]
    if len(routes) > 1 or (routes and operation not in resource_backup_drive.OPERATIONS):
        raise ValueError("Only Google Drive backup accepts a transfer route")
    result = run_operation(operation, network_route=routes[0] if routes else None)
    set_output(result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["succeeded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
