"""Where a service's CPU goes: SIGUSR2 samples its busy threads and logs the result.

py-spy cannot attach to the services (ptrace_scope=1), and slow instruction
profiles only cover instruction handlers. On SIGUSR2 a sampler thread reads
every thread's CPU time from /proc/self/task/<tid>/stat each ``INTERVAL``
seconds. A thread whose CPU time advanced since the previous read has its stack
recorded, weighted by the clock ticks it used, so waiting threads add nothing.
After ``DURATION`` seconds one line goes to stderr (the journal):
"Service CPU profile: {...}" with CPU per thread name and the frames and stacks
that held it.
"""
from __future__ import annotations

import collections
import json
import os
import re
import signal
import sys
import threading
import time
import traceback
from typing import Any

DURATION = 30.0
INTERVAL = 0.02
# Portal code by package, whichever tree it was loaded from (Release imports
# shared packages from the Dev tree).
_PORTAL = re.compile(r"/((?:management_\w+|scripts|tests)/[^/].*)$")
_running = threading.Lock()


def _portal(entry: traceback.FrameSummary) -> bool:
    path = entry.filename.replace("\\", "/")
    return "/site-packages/" not in path and _PORTAL.search(path) is not None


def _frame(entry: traceback.FrameSummary) -> str:
    path = entry.filename.replace("\\", "/")
    portal = _PORTAL.search(path)
    if "/site-packages/" in path:
        path = path.split("/site-packages/", 1)[1]
    elif portal:
        path = portal.group(1)
    else:
        path = path.rsplit("/", 1)[-1]
    return f"{path}:{entry.lineno} {entry.name}"


def _cpu_ticks(native_id: int) -> int | None:
    try:
        with open(f"/proc/self/task/{native_id}/stat", "rb") as stat:
            fields = stat.read().rsplit(b")", 1)[1].split()
    except OSError:
        return None
    return int(fields[11]) + int(fields[12])  # utime + stime


def profile(duration: float = DURATION, interval: float = INTERVAL) -> dict[str, Any]:
    """Sample for ``duration`` seconds and return the CPU each thread, frame and stack held."""
    own = threading.get_native_id()
    previous: dict[int, int] = {}
    by_thread: collections.Counter[str] = collections.Counter()
    inclusive: collections.Counter[str] = collections.Counter()
    innermost: collections.Counter[str] = collections.Counter()
    stacks: collections.Counter[str] = collections.Counter()
    samples = 0
    deadline = time.monotonic() + duration
    clock_ticks = os.sysconf("SC_CLK_TCK") if sys.platform == "linux" else 1_000_000
    observer = None
    if sys.platform != "linux":
        import psutil
        observer = psutil.Process()
    while time.monotonic() < deadline:
        time.sleep(interval)
        samples += 1
        frames = sys._current_frames()
        cpu = {item.id: int((item.user_time + item.system_time) * clock_ticks)
               for item in observer.threads()} if observer else None
        for thread in threading.enumerate():
            native_id = thread.native_id
            if native_id is None or native_id == own:
                continue
            ticks = _cpu_ticks(native_id) if cpu is None else cpu.get(native_id)
            if ticks is None:
                continue
            used = ticks - previous.get(native_id, ticks)
            previous[native_id] = ticks
            frame = frames.get(thread.ident)
            if used <= 0 or frame is None:
                continue
            stack = traceback.extract_stack(frame)
            portal = [_frame(entry) for entry in stack if _portal(entry)]
            by_thread[re.sub(r"\d+", "N", thread.name)] += used
            inclusive.update(dict.fromkeys(portal, used))
            innermost[" < ".join([_frame(stack[-1]), *reversed(portal[-2:])])] += used
            stacks[" > ".join(portal[-10:])] += used
    total = sum(by_thread.values())
    return {
        "pid": os.getpid(), "seconds": duration, "interval": interval, "samples": samples,
        "clock_ticks_per_second": clock_ticks, "sampled_ticks": total,
        "threads": by_thread.most_common(15),
        "frames": inclusive.most_common(40),
        "innermost": innermost.most_common(20),
        "stacks": stacks.most_common(12),
    }


def _run() -> None:
    try:
        result = profile(DURATION, INTERVAL)
        os.write(2, ("Service CPU profile: " + json.dumps(
            result, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
    finally:
        _running.release()


def _start(*_) -> None:
    # A second signal while a profile runs is ignored.
    if _running.acquire(blocking=False):
        threading.Thread(target=_run, name="cpu-profile", daemon=True).start()


def install() -> None:
    """Register the optional POSIX trigger; profile() is portable on Windows."""
    if hasattr(signal, "SIGUSR2"):
        signal.signal(signal.SIGUSR2, _start)
