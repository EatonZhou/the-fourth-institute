"""Launch a one-off Portal maintenance job outside the Web process lifetime."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from management_common.process_runtime import process_alive, spawn_process, terminate_process_tree


def launch_job(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    environment: dict[str, str] | None = None,
    inherit_environment: bool = True,
    platform: str | None = None,
) -> int | None:
    """Return a native detached child's PID, or None for a systemd unit.

    The caller owns persistent job state.  This process only owns the child
    handle long enough to launch it; no Web process needs to stay alive.
    """
    env = os.environ.copy() if inherit_environment else {}
    platform = platform or sys.platform
    if environment:
        env.update(environment)
    if platform.startswith("linux"):
        subprocess.run(
            ["systemd-run", "--user", f"--unit={name}", "--collect",
             "--property=Type=exec", f"--working-directory={cwd}",
             *(f"--setenv={key}={value}" for key, value in (environment or {}).items()),
             *command],
            check=True, capture_output=True, text=True,
        )
        return None
    if platform not in {"darwin", "win32"}:
        raise RuntimeError(f"Unsupported maintenance job platform: {platform}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as output:
        child = spawn_process(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                              tree_id=name, kill_on_owner_exit=False)
    return child.pid


def job_alive(name: str, pid: int | None, *, platform: str | None = None) -> bool:
    platform = platform or sys.platform
    if platform.startswith("linux"):
        result = subprocess.run(
            ["systemctl", "--user", "is-active", f"{name}.service"],
            capture_output=True, text=True, check=False,
        )
        return result.returncode == 0 and result.stdout.strip() in {"active", "activating"}
    if not pid or pid <= 0:
        return False
    return process_alive(pid)
