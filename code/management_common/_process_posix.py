"""Linux/macOS process groups. Never imported by the Windows runtime."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def spawn_options():
    return {"start_new_session": True}


def spawn_process(*args, tree_id=None, kill_on_owner_exit=True, **kwargs):
    if kwargs.get("start_new_session") is False or kwargs.get("process_group") not in (None, -1):
        raise ValueError("Owned process trees require a new session")
    kwargs.update(spawn_options())
    process = subprocess.Popen(*args, **kwargs)
    process._portal_tree_stopped = False
    return process


def _linux_stat(pid):
    try:
        # comm may itself contain spaces and closing parentheses.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if sys.platform == "linux":
        try:
            fields = _linux_stat(pid)
            return bool(fields and fields[0] not in {"Z", "X"})
        except PermissionError:
            return True
    result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True,
                            text=True, timeout=15)
    if result.returncode not in (0, 1):
        result.check_returncode()
    return bool(result.stdout.strip() and not result.stdout.strip().startswith("Z"))


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    # killpg(0) includes zombies. Do not report a dead group as an active tree
    # while waiting for an external reaper (notably container PID 1).
    if sys.platform == "linux":
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = _linux_stat(entry.name)
            except PermissionError:
                return True  # Cannot prove the group empty.
            if fields and int(fields[2]) == pgid and fields[0] not in {"Z", "X"}:
                return True
        return False
    result = subprocess.run(["ps", "-axo", "pgid=,stat="], capture_output=True, text=True,
                            check=True, timeout=15)
    return any(int(parts[0]) == pgid and not parts[1].startswith("Z")
               for line in result.stdout.splitlines() if len(parts := line.split()) >= 2)


def process_tree_alive(process, *, tree_id=None):
    pid = process if isinstance(process, int) else getattr(process, "pid", None)
    if type(pid) is not int or pid <= 1:
        raise ValueError("An owned process-group identity is required")
    return _group_alive(pid)


def terminate_process_tree(process, timeout=15, force=False, *, tree_id=None):
    if process is None:
        return
    managed = not isinstance(process, int)
    if managed and getattr(process, "_portal_tree_stopped", False):
        return
    pid = process.pid if managed else process
    if not isinstance(pid, int) or pid <= 1 or pid == os.getpgrp():
        raise ValueError("Refusing to signal an unowned process group")
    # If the leader still exists, reject a caller that did not isolate it.
    try:
        if os.getpgid(pid) != pid:
            raise ValueError("Process is not the leader of an owned process group")
    except ProcessLookupError:
        pass
    def send(signum):
        try:
            os.killpg(pid, signum)
        except ProcessLookupError:
            pass
    def wait_empty(seconds):
        deadline = time.monotonic() + seconds
        while True:
            if managed:
                process.poll()  # Reap our leader as soon as it exits.
            if not _group_alive(pid):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(.02)
    send(signal.SIGKILL if force else signal.SIGTERM)
    if not wait_empty(timeout):
        if not force:
            send(signal.SIGKILL)
        if not wait_empty(max(timeout, .1)):
            raise TimeoutError(f"Process group {pid} did not exit")
    if managed:
        process.wait(timeout=max(timeout, .1))
        process._portal_tree_stopped = True
