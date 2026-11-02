"""Observe host process identity; unavailable evidence never proves death.

True from process_is_alive includes unknown. These observations neither own nor
terminate processes; allocation owners persist their own explicit end receipts.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import socket
import sys

from . import process_runtime


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
    """Return False only for a complete identity whose process is proven gone."""
    if not isinstance(owner, dict) or owner.get('host') != socket.gethostname():
        return True
    try:
        pid = int(owner['pid'])
        if isinstance(owner['pid'], bool) or pid <= 0 or str(pid) != str(owner['pid']):
            return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return True
    if owner.get('identity_kind') == 'process-create-time-v1':
        try:
            created_at = float(owner['created_at'])
            if not math.isfinite(created_at) or created_at <= 0:
                return True
        except (KeyError, TypeError, ValueError, OverflowError):
            return True
        try:
            import psutil
        except ImportError:
            return True
        try:
            if not process_runtime.process_alive(pid):
                return False
            return psutil.Process(pid).create_time() == created_at
        except psutil.NoSuchProcess:
            return False
        except (psutil.AccessDenied, OSError, ValueError):
            return True
    if sys.platform != 'linux' or owner.get('identity_kind'):
        return True
    # Validate the entire receipt before using even a boot change as proof.
    if not all(isinstance(owner.get(key), str) and owner[key]
               for key in ('boot_id', 'pid_namespace', 'start_ticks')):
        return True
    try:
        int(owner['start_ticks'])
    except ValueError:
        return True
    try:
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        namespace = os.readlink('/proc/self/ns/pid')
    except OSError:
        return True
    if not boot or not namespace:
        return True
    if owner['boot_id'] != boot:
        return False
    if owner['pid_namespace'] != namespace:
        return True
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return stat[0] not in {'Z', 'X'} and stat[19] == owner['start_ticks']
    except (FileNotFoundError, ProcessLookupError):
        # A hidden/unmounted /proc entry is not evidence of process death.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            pass
        return True
    except (OSError, IndexError, ValueError):
        return True
