"""Opt-in, expiring aggregate memory evidence for the Society service owner.

The operator's runtime lease enables a short capture at service load. No object
values, repr, locals, request data or heap dumps are recorded. Normal starts do
not enable tracing, scan the heap, or create a sampler thread.
"""
from __future__ import annotations

from collections import Counter
import ctypes
import gc
from itertools import islice
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


def heap_summary(*, object_limit: int = 500_000) -> dict:
    """Shallow size by type, bounded inspection with its reference-list cost shown.

    Untracked strings/bytes and native allocations are not a complete GC heap;
    allocation_summary and OS counters are independent evidence for those.
    """
    objects = gc.get_objects()
    counts, sizes = Counter(), Counter()
    for item in islice(objects, object_limit):
        kind = type(item)
        module, name = kind.__module__, kind.__qualname__
        # Some extension metatypes expose descriptors instead of these strings.
        # Do not stringify a descriptor or inspect an instance to name its type.
        name = (module if isinstance(module, str) else "<native>") + "." + (
            name if isinstance(name, str) else "<type>")
        counts[name] += 1
        # Do not dispatch arbitrary application __sizeof__ implementations.
        if kind in (dict, list, tuple, set, frozenset, str, bytes, bytearray):
            sizes[name] += sys.getsizeof(item)
        else:
            # object.__sizeof__ is not valid for every variable-size extension
            # layout (frames, for example, define their own sizing). Report a
            # static lower bound without invoking application methods.
            basic = kind.__basicsize__
            sizes[name] += basic if isinstance(basic, int) else 0
    pointer_modules = Counter()
    for kind in list(getattr(ctypes, "_pointer_type_cache", {})):
        module = getattr(kind, "__module__", "<native>")
        pointer_modules[module if isinstance(module, str) else "<native>"] += 1
    return {"tracked_objects": len(objects), "inspected_objects": min(len(objects), object_limit),
            "reference_list_bytes": sys.getsizeof(objects), "truncated": len(objects) > object_limit,
            "ctypes_pointer_types_by_module": dict(pointer_modules.most_common(20)),
            "top_types": [{"type": kind, "count": counts[kind], "shallow_bytes": size}
                          for kind, size in sizes.most_common(30)],
            "non_builtin_sizes": "static type basicsize lower bounds",
            "gc_counts": gc.get_count(), "gc_thresholds": gc.get_threshold(), "gc_stats": gc.get_stats()}


def sample() -> dict:
    started = time.monotonic()
    allocations = allocation_summary()
    heap = heap_summary()
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
            "allocations": allocations, "heap": heap, "process": native,
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
