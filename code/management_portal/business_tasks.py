from __future__ import annotations

import threading
import os
import socket
import sys
from pathlib import Path
from collections.abc import Callable
from typing import Any

from management_common import process_runtime

_LOCK = threading.RLock()
_ACTIVE: set[str] = set()


def process_identity() -> dict:
    """Identify this task host across restarts and PID reuse."""
    if sys.platform != "linux":
        # Observation only: process creation and termination belong to runtime.
        try:
            import psutil
        except ImportError as error:
            raise RuntimeError("业务任务进程身份需要 psutil；请安装平台运行依赖") from error
        return {"host": socket.gethostname(), "pid": os.getpid(),
                "identity_kind": "process-create-time-v1",
                "created_at": psutil.Process(os.getpid()).create_time()}
    # Retain Linux's boot/namespace identity and persisted receipt compatibility.
    stat = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()
    return {
        "host": socket.gethostname(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "pid_namespace": os.readlink('/proc/self/ns/pid'),
        "pid": os.getpid(),
        "start_ticks": stat[19],
    }


def process_is_alive(owner: dict) -> bool:
    # A different host or unreadable process is not proof of interruption.
    if not owner or owner.get("host") != socket.gethostname():
        return True
    if owner.get("identity_kind") == "process-create-time-v1":
        try:
            import psutil
        except ImportError:
            return True  # Missing observations do not prove interruption.
        try:
            pid = int(owner["pid"])
            if pid <= 0 or "created_at" not in owner:
                return True
            if not process_runtime.process_alive(pid):
                return False
            return psutil.Process(pid).create_time() == float(owner["created_at"])
        except psutil.NoSuchProcess:
            return False
        except (psutil.AccessDenied, OSError, KeyError, TypeError, ValueError):
            return True
    if sys.platform != "linux":
        return True  # A Linux receipt cannot be disproved using another OS.
    try:
        if owner["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
            return False
        namespace = os.readlink('/proc/self/ns/pid')
        if owner.get("pid_namespace", namespace) != namespace:
            return True
        if not process_runtime.process_alive(int(owner["pid"])):
            return False
        stat = Path(f"/proc/{int(owner['pid'])}/stat").read_text().rsplit(")", 1)[1].split()
        return stat[0] != "Z" and stat[19] == str(owner["start_ticks"])
    except FileNotFoundError:
        return False
    except (OSError, KeyError, ValueError, IndexError):
        return True


def start(
    task_id: str,
    target: Callable[..., Any],
    *args: Any,
    on_error: Callable[[BaseException], None] | None = None,
    daemon: bool = True,
    **kwargs: Any,
) -> bool:
    """Run Portal-owned business code without creating an Execution."""

    clean_id = str(task_id or "").strip()
    if not clean_id:
        raise ValueError("Portal business task id required")
    with _LOCK:
        if clean_id in _ACTIVE:
            return False
        _ACTIVE.add(clean_id)

    def run() -> None:
        try:
            target(*args, **kwargs)
        except BaseException as exc:
            if on_error is not None:
                on_error(exc)
        finally:
            with _LOCK:
                _ACTIVE.discard(clean_id)

    try:
        threading.Thread(
            target=run,
            name=f"portal-business-{clean_id[:80]}",
            daemon=daemon,
        ).start()
    except BaseException:
        with _LOCK:
            _ACTIVE.discard(clean_id)
        raise
    return True


def is_active(task_id: str) -> bool:
    with _LOCK:
        return str(task_id or "").strip() in _ACTIVE
