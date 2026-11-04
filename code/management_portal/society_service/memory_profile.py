"""Opt-in, expiring aggregate memory evidence for the Society service owner.

The operator's runtime lease enables a short capture at service load. No object
values, repr, locals, request data or heap dumps are recorded. Live diagnostics
never enumerate GC objects. Normal starts do not enable tracing or create a
sampler thread.
"""
from __future__ import annotations

import gc
import json
import os
from pathlib import Path
import sys
import threading
import time
import tracemalloc

from management_common.file_permissions import atomic_write_private_bytes, create_private_file, ensure_private_directory
from management_common.instance_manifest import instance_runtime_root


def _location(filename: str) -> str:
    path = Path(filename)
    for package in ("management_portal", "management_common", "knowledge_board_lan"):
        if package in path.parts:
            return "/".join(path.parts[path.parts.index(package):])
    return path.name


def allocation_summary(limit: int = 30) -> dict:
    """Only source locations and allocation totals; never serialize trace values."""
    if not tracemalloc.is_tracing():
        return {"enabled": False}
    current, peak = tracemalloc.get_traced_memory()
    overhead = tracemalloc.get_tracemalloc_memory()
    snapshot = tracemalloc.take_snapshot()
    rows = [{"file": _location(stat.traceback[0].filename), "line": stat.traceback[0].lineno,
             "bytes": stat.size, "count": stat.count}
            for stat in snapshot.statistics("lineno")[:limit]]
    stacks = [{"bytes": stat.size, "count": stat.count,
               "frames": [{"file": _location(frame.filename), "line": frame.lineno} for frame in stat.traceback]}
              for stat in snapshot.statistics("traceback")[:12]]
    return {"enabled": True, "current_bytes": current, "peak_bytes": peak,
            "tracer_metadata_bytes": overhead, "top_lines": rows, "top_stacks": stacks}


def gc_summary() -> dict:
    """Read counters without acquiring references to live application objects.

    gc.get_objects() can retain a tuple still being built in another thread;
    CPython's subsequent _PyTuple_Resize then fails its exclusive-reference
    check. Filtering the snapshot afterwards cannot prevent that interference.
    """
    return {"counts": gc.get_count(), "thresholds": gc.get_threshold(), "stats": gc.get_stats()}


def sample() -> dict:
    started = time.monotonic()
    allocations = allocation_summary()
    garbage_collection = gc_summary()
    try:
        import psutil
        process = psutil.Process()
        memory = process.memory_info()
        native = {"rss_bytes": memory.rss, "vms_bytes": memory.vms,
                  "private_bytes": getattr(memory, "private", None),
                  "created_at": process.create_time(), "threads": process.num_threads()}
    except ImportError:
        native = {"available": False}
    return {"pid": os.getpid(), "sample_epoch": time.time(), "allocated_blocks": sys.getallocatedblocks(),
            "allocations": allocations, "gc": garbage_collection, "process": native,
            "sampling_seconds": time.monotonic() - started}


def start() -> threading.Thread | None:
    """Honor a local operator lease for at most four minutes, then stop tracing."""
    folder = instance_runtime_root() / "diagnostics"
    try:
        lease = json.loads((folder / "society-memory-profile.json").read_text(encoding="utf-8"))
        remaining = float(lease["expires_at"]) - time.time()
        if not 0 < remaining <= 240 or tracemalloc.is_tracing():
            return None
        interval = max(20.0, min(float(lease.get("interval_seconds", 30)), 60.0))
    except (OSError, ValueError, TypeError, KeyError):
        return None
    try:
        ensure_private_directory(folder)
        target = folder / f"society-memory-profile-{os.getpid()}-{time.time_ns()}.jsonl"
        tracemalloc.start(4)
    except Exception:
        # Optional observation must not prevent the business owner from starting.
        return None

    def run() -> None:
        deadline = time.monotonic() + remaining
        try:
            with create_private_file(target) as stream:
                for _ in range(8):
                    try:
                        row = sample()
                    except Exception as error:
                        # The exception's message/locals may contain business data.
                        row = {"diagnostic_error_type": type(error).__name__}
                    stream.write((json.dumps(row, separators=(",", ":")) + "\n").encode())
                    stream.flush()
                    delay = min(interval, deadline - time.monotonic())
                    if delay <= 0:
                        break
                    time.sleep(delay)
                    if time.monotonic() >= deadline:
                        break
        finally:
            tracemalloc.stop()
            atomic_write_private_bytes(target.with_suffix(".done.json"), json.dumps({
                "pid": os.getpid(), "stopped_epoch": time.time(), "tracing": tracemalloc.is_tracing(),
            }).encode())
    thread = threading.Thread(target=run, name="society-memory-profile", daemon=True)
    try:
        thread.start()
    except Exception:
        tracemalloc.stop()
        return None
    return thread
