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
import signal
import sys
import re
import math
import weakref
from collections import Counter
import threading
import time
import tracemalloc

from management_common.file_permissions import atomic_write_private_bytes, create_private_file, ensure_private_directory
from management_common.instance_manifest import instance_runtime_root

_running = threading.Lock()
_role = 'main'
_runtime_provider = None


def configure_runtime(provider) -> None:
    global _runtime_provider
    _runtime_provider = weakref.WeakMethod(provider)


def thread_summary() -> dict:
    """Known live threads and source positions, never locals or object values."""
    groups = Counter()
    frames = sys._current_frames()
    threads = threading.enumerate()
    try:
        for thread in threads:
            name = re.sub(r'[0-9]+', '#', thread.name)
            frame = frames.get(thread.ident)
            stack = []
            for _ in range(5):
                if frame is None:
                    break
                stack.append((_location(frame.f_code.co_filename), frame.f_code.co_name, frame.f_lineno))
                frame = frame.f_back
            groups[(name, tuple(stack))] += 1
    finally:
        frame = None
        frames.clear()
    return {'python_threads': len(threads), 'groups': [
        {'name': key[0], 'count': count,
         'stack': [{'file': file, 'function': function, 'line': line} for file, function, line in key[1]]}
        for key, count in groups.most_common(64)]}


def holdings_summary() -> dict:
    owners = {}
    for label, module_name, method in [
        ('database_pools', 'management_common.postgres_database', 'pool_diagnostics'),
        ('lineage_read_cache', 'management_portal.society_service.lineage_state', 'read_cache_diagnostics'),
        ('compiled_plans', 'management_portal.society_service.lineage_program_compilation', 'plan_cache_diagnostics')]:
        module = sys.modules.get(module_name)
        provider = getattr(module, method, None) if module is not None else None
        if provider is not None:
            try:
                owners[label] = provider()
            except Exception as error:
                owners[label] = {'diagnostic_error_type': type(error).__name__}
    provider = _runtime_provider() if _runtime_provider is not None else None
    if provider is not None:
        try:
            owners['runtime'] = provider()
        except Exception as error:
            owners['runtime'] = {'diagnostic_error_type': type(error).__name__}
    return owners


def install(*, role: str = 'main') -> bool:
    """POSIX operator trigger: sample an existing generation under a new lease.

    SIGUSR1 is installed only when unused. It does no work without a current
    private operator lease; Windows retains the startup-lease entry.
    """
    global _role
    if role not in {'main', 'read', 'index'}:
        raise ValueError('unknown diagnostic role')
    _role = role
    if not hasattr(signal, 'SIGUSR1') or signal.getsignal(signal.SIGUSR1) != signal.SIG_DFL:
        return False
    signal.signal(signal.SIGUSR1, lambda *_: start())
    return True


def _location(filename: str) -> str:
    path = Path(filename)
    for package in ("management_portal", "management_common", "knowledge_board_lan"):
        if package in path.parts:
            return "/".join(path.parts[path.parts.index(package):])
    return path.name


def allocation_summary(limit: int = 30, *, detailed: bool = True) -> dict:
    """Only source locations and allocation totals; never serialize trace values."""
    if not tracemalloc.is_tracing():
        return {"enabled": False}
    current, peak = tracemalloc.get_traced_memory()
    overhead = tracemalloc.get_tracemalloc_memory()
    totals = {"enabled": True, "current_bytes": current, "peak_bytes": peak,
              "tracer_metadata_bytes": overhead, "detailed": detailed}
    if not detailed:
        return totals
    snapshot = tracemalloc.take_snapshot()
    rows = [{"file": _location(stat.traceback[0].filename), "line": stat.traceback[0].lineno,
             "bytes": stat.size, "count": stat.count}
            for stat in snapshot.statistics("lineno")[:limit]]
    stacks = [{"bytes": stat.size, "count": stat.count,
               "frames": [{"file": _location(frame.filename), "line": frame.lineno} for frame in stat.traceback]}
              for stat in snapshot.statistics("traceback")[:12]]
    return {**totals, "top_lines": rows, "top_stacks": stacks}


def gc_summary() -> dict:
    """Read counters without acquiring references to live application objects.

    gc.get_objects() can retain a tuple still being built in another thread;
    CPython's subsequent _PyTuple_Resize then fails its exclusive-reference
    check. Filtering the snapshot afterwards cannot prevent that interference.
    """
    return {"counts": gc.get_count(), "thresholds": gc.get_threshold(), "stats": gc.get_stats()}


def sample(*, detailed: bool = True) -> dict:
    started = time.monotonic()
    allocations = allocation_summary(detailed=detailed)
    garbage_collection = gc_summary()
    from .allocator_memory import summary as allocator_summary
    allocators = allocator_summary()
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
            "monotonic": time.monotonic(), 'role': _role,
            "allocations": allocations, "gc": garbage_collection, "process": native,
            "allocators": allocators,
            'threads': thread_summary(), 'holdings': holdings_summary(),
            "sampling_seconds": time.monotonic() - started}


def start() -> threading.Thread | None:
    """Honor a local operator lease for at most four minutes, then stop tracing."""
    folder = instance_runtime_root() / "diagnostics"
    try:
        lease = json.loads((folder / "society-memory-profile.json").read_text(encoding="utf-8"))
        remaining = float(lease["expires_at"]) - time.time()
        trace_allocations = lease.get("trace_allocations", True)
        if not isinstance(trace_allocations, bool):
            return None
        if not 0 < remaining <= (240 if trace_allocations else 1800) or tracemalloc.is_tracing():
            return None
        requested_interval = float(lease.get('interval_seconds', 30))
        if not math.isfinite(requested_interval):
            return None
        interval = max(20.0, min(requested_interval, 60.0 if trace_allocations else 300.0))
        limit = lease.get('sample_limit', 8)
        if type(limit) is not int or not 1 <= limit <= (8 if trace_allocations else 24):
            return None
        capture_id = lease.get('capture_id')
        if capture_id is not None and (not isinstance(capture_id, str) or
                re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', capture_id) is None):
            return None
        if lease.get('include_workers') is True and (capture_id is None or 'created_after' not in lease):
            return None
        targets = lease.get('targets')
        if targets is not None:
            import psutil
            expected = targets.get(str(os.getpid())) if isinstance(targets, dict) else None
            if type(expected) is not int or abs(int(psutil.Process().create_time() * 1000) - expected) > 10:
                return None
        elif _role != 'main' and lease.get('include_workers') is not True:
            return None
        if 'created_after' in lease:
            import psutil
            created_after = float(lease['created_after'])
            if not math.isfinite(created_after) or psutil.Process().create_time() < created_after:
                return None
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not _running.acquire(blocking=False):
        return None
    try:
        ensure_private_directory(folder)
        target = folder / f"society-memory-profile-{os.getpid()}-{time.time_ns()}.jsonl"
        if trace_allocations:
            tracemalloc.start(4)
    except Exception:
        # Optional observation must not prevent the business owner from starting.
        _running.release()
        return None

    def run() -> None:
        deadline = time.monotonic() + remaining
        try:
            with create_private_file(target) as stream:
                for index in range(limit):
                    try:
                        # Snapshot aggregation allocates temporary objects too.
                        # Use cheap live counters between the first/last detail.
                        row = sample(detailed=index in (0, limit - 1) or deadline - time.monotonic() <= interval)
                    except Exception as error:
                        # The exception's message/locals may contain business data.
                        row = {"diagnostic_error_type": type(error).__name__}
                    if capture_id is not None:
                        row['capture_id'] = capture_id
                    stream.write((json.dumps(row, separators=(",", ":")) + "\n").encode())
                    stream.flush()
                    delay = min(interval, deadline - time.monotonic())
                    if delay <= 0:
                        break
                    time.sleep(delay)
                    if time.monotonic() >= deadline:
                        break
        finally:
            try:
                if trace_allocations:
                    tracemalloc.stop()
                atomic_write_private_bytes(target.with_suffix(".done.json"), json.dumps({
                    "pid": os.getpid(), "stopped_epoch": time.time(), "tracing": tracemalloc.is_tracing(),
                    **({'capture_id': capture_id} if capture_id is not None else {}),
                }).encode())
            finally:
                _running.release()
    thread = threading.Thread(target=run, name="society-memory-profile", daemon=True)
    try:
        thread.start()
    except Exception:
        if trace_allocations:
            tracemalloc.stop()
        _running.release()
        return None
    return thread
