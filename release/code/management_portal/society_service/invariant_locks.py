from __future__ import annotations
from management_common.portal_paths import path as portal_path

import copy
from management_common.file_lock import lock_stream, try_lock_descriptor, unlock_descriptor
import json
import os
import re
import sys
import threading
import time
import traceback
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator


_DEFAULT_ROOT = (portal_path("staging_root") / "shared" / "portal_lineages")
_ROOT_PROVIDER: Callable[[], Path] = lambda: Path(
    os.getenv("PORTAL_LINEAGE_ROOT", str(_DEFAULT_ROOT))
).expanduser()
_SCOPE_LOCAL = threading.local()
_LOCKS_GUARD = threading.Lock()
_METRICS_GUARD = threading.Lock()
_SLOW_SECONDS = max(
    0.1,
    float(os.getenv("PORTAL_LINEAGE_SLOW_LOCK_SECONDS", "1.0")),
)
_METRICS: dict[str, Any] = {
    "acquisition_count": 0,
    "contended_count": 0,
    "slow_wait_count": 0,
    "slow_hold_count": 0,
    "max_wait_seconds": 0.0,
    "max_hold_seconds": 0.0,
    "recent_slow_events": [],
}


class _LockRecord:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.owner_thread_id = 0
        self.owner_operation = ""
        self.acquired_at = 0.0


_LOCKS: dict[str, _LockRecord] = {}


def configure_root_provider(provider: Callable[[], Path]) -> None:
    global _ROOT_PROVIDER
    _ROOT_PROVIDER = provider


def _lineage_scope_id(lineage_id: object) -> str:
    clean_id = str(lineage_id or "").strip()
    if not clean_id:
        raise ValueError("lineage_id is required for a Lineage scope")
    return f"lineage:{clean_id}"


def _catalog_scope_id(catalog_name: object) -> str:
    clean_name = re.sub(
        r"[^a-zA-Z0-9_.-]+",
        "-",
        str(catalog_name or "").strip().lower(),
    )[:80]
    if not clean_name:
        raise ValueError("catalog_name is required for catalog mutation locking")
    return f"catalog:{clean_name}"


def _resident_continuation_scope_id(resident_id: object) -> str:
    clean_id = str(resident_id or "").strip()
    if not clean_id:
        raise ValueError("resident_id is required for continuation locking")
    return f"resident-continuation:{clean_id}"


def _resident_scope_id(resident_id: object) -> str:
    clean_id = str(resident_id or "").strip()
    if not clean_id:
        raise ValueError("resident_id is required for Resident mutation locking")
    return f"resident:{clean_id}"


def _resident_conversation_scope_id(resident_id: object) -> str:
    clean_id = str(resident_id or "").strip()
    if not clean_id:
        raise ValueError("resident_id is required for Conversation mutation locking")
    return f"resident-conversation:{clean_id}"


def _record(scope_id: str) -> _LockRecord:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(scope_id, _LockRecord())


def _depths() -> dict[str, int]:
    depths = getattr(_SCOPE_LOCAL, "lock_depths", None)
    if not isinstance(depths, dict):
        depths = {}
        _SCOPE_LOCAL.lock_depths = depths
    return depths


def lock_held() -> bool:
    return any(depth > 0 for depth in _depths().values())


@contextmanager
def diagnostic_scope(scope_id: str, operation: str = "") -> Iterator[None]:
    previous_scope = str(getattr(_SCOPE_LOCAL, "scope_id", "") or "")
    previous_operation = str(getattr(_SCOPE_LOCAL, "operation", "") or "")
    _SCOPE_LOCAL.scope_id = str(scope_id or "").strip()
    _SCOPE_LOCAL.operation = str(operation or "").strip()
    try:
        yield
    finally:
        _SCOPE_LOCAL.scope_id = previous_scope
        _SCOPE_LOCAL.operation = previous_operation


def _lock_path(scope_id: str) -> Path:
    safe_scope = re.sub(r"[^a-zA-Z0-9_.-]+", "_", scope_id)[:120]
    return Path(_ROOT_PROVIDER()).expanduser() / ".locks" / f"{safe_scope}.lock"


def _thread_stack(thread_id: int) -> str:
    frame = sys._current_frames().get(thread_id)
    return "" if frame is None else "".join(traceback.format_stack(frame, limit=24))[-12000:]


def _record_slow_event(event: dict[str, Any]) -> None:
    with _METRICS_GUARD:
        recent = list(_METRICS["recent_slow_events"])
        recent.append(event)
        _METRICS["recent_slow_events"] = recent[-20:]
    print(
        "SocietyService slow invariant lock: "
        + json.dumps(event, ensure_ascii=False, separators=(",", ":")),
        file=sys.stderr,
    )


def health() -> dict[str, Any]:
    with _METRICS_GUARD:
        snapshot = copy.deepcopy(_METRICS)
    now = time.monotonic()
    with _LOCKS_GUARD:
        records = list(_LOCKS.items())
    snapshot["active_holds"] = [
        {
            "scope_id": scope_id,
            "operation": record.owner_operation,
            "owner_thread_id": record.owner_thread_id,
            "held_seconds": round(now - record.acquired_at, 6),
            **({"owner_stack": _thread_stack(record.owner_thread_id)}
               if now - record.acquired_at >= _SLOW_SECONDS else {}),
        }
        for scope_id, record in records
        if record.owner_thread_id and record.acquired_at
    ]
    snapshot["scope_count"] = len(records)
    snapshot["slow_threshold_seconds"] = _SLOW_SECONDS
    return snapshot


def _callback_frames() -> list[list[Callable[[], Any]]]:
    frames = getattr(_SCOPE_LOCAL, "callback_frames", None)
    if frames is None:
        frames = []
        _SCOPE_LOCAL.callback_frames = frames
    return frames


def defer_until_state_unlock(callback: Callable[[], Any]) -> None:
    """Run an effect after every enclosing state scope has released its locks."""

    frames = _callback_frames()
    if not frames:
        callback()
        return
    frames[-1].append(callback)


@contextmanager
def _invariant_scope(scope_id: str, *, operation: str = "") -> Iterator[None]:
    operation = str(
        operation or getattr(_SCOPE_LOCAL, "operation", "") or ""
    ).strip()
    record = _record(scope_id)
    depths = _depths()
    nested = int(depths.get(scope_id) or 0) > 0
    wait_started = time.monotonic()
    warned = False
    while not record.lock.acquire(timeout=_SLOW_SECONDS):
        if warned:
            continue
        warned = True
        _record_slow_event({
            "phase": "wait",
            "scope_id": scope_id,
            "operation": operation,
            "wait_seconds": round(time.monotonic() - wait_started, 6),
            "waiting_thread_id": threading.get_ident(),
            "owner_thread_id": record.owner_thread_id,
            "owner_operation": record.owner_operation,
            "owner_stack": _thread_stack(record.owner_thread_id),
        })
    wait_seconds = time.monotonic() - wait_started
    with _METRICS_GUARD:
        _METRICS["acquisition_count"] += 1
        if wait_seconds >= 0.001:
            _METRICS["contended_count"] += 1
        if wait_seconds >= _SLOW_SECONDS:
            _METRICS["slow_wait_count"] += 1
        _METRICS["max_wait_seconds"] = max(
            float(_METRICS["max_wait_seconds"]), wait_seconds
        )

    file_handles = ExitStack()
    outer_started = time.monotonic()
    frames = _callback_frames()
    frames.append([])
    succeeded = False
    slow_hold_event: dict[str, Any] | None = None
    try:
        if not nested:
            lock_path = _lock_path(scope_id)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = file_handles.enter_context(lock_path.open("a+b"))
            flock_started = time.monotonic()
            file_handles.enter_context(lock_stream(lock_file))
            flock_wait = time.monotonic() - flock_started
            if flock_wait >= _SLOW_SECONDS:
                _record_slow_event({
                    "phase": "file_wait",
                    "scope_id": scope_id,
                    "operation": operation,
                    "wait_seconds": round(flock_wait, 6),
                    "waiting_thread_id": threading.get_ident(),
                    "waiting_stack": "".join(
                        traceback.format_stack(limit=24)
                    )[-12000:],
                })
            record.owner_thread_id = threading.get_ident()
            record.owner_operation = operation
            record.acquired_at = time.monotonic()
            outer_started = record.acquired_at
        depths[scope_id] = int(depths.get(scope_id) or 0) + 1
        yield
        succeeded = True
    finally:
        callbacks = frames.pop()
        if not succeeded:
            callbacks = []
        current_depth = max(0, int(depths.get(scope_id) or 0) - 1)
        if current_depth:
            depths[scope_id] = current_depth
        else:
            depths.pop(scope_id, None)
            hold_seconds = time.monotonic() - outer_started
            with _METRICS_GUARD:
                if hold_seconds >= _SLOW_SECONDS:
                    _METRICS["slow_hold_count"] += 1
                _METRICS["max_hold_seconds"] = max(
                    float(_METRICS["max_hold_seconds"]), hold_seconds
                )
            if hold_seconds >= _SLOW_SECONDS:
                slow_hold_event = {
                    "phase": "hold",
                    "scope_id": scope_id,
                    "operation": operation or record.owner_operation,
                    "hold_seconds": round(hold_seconds, 6),
                    "owner_thread_id": threading.get_ident(),
                }
            record.owner_thread_id = 0
            record.owner_operation = ""
            record.acquired_at = 0.0
        try:
            file_handles.close()
        finally:
            record.lock.release()
        if slow_hold_event is not None:
            slow_hold_event["owner_stack"] = "".join(
                traceback.format_stack(limit=24)
            )[-12000:]
            _record_slow_event(slow_hold_event)
        if frames:
            frames[-1].extend(callbacks)
            callbacks = []
        _run_callbacks(callbacks)


def _run_callbacks(callbacks: list[Callable[[], Any]]) -> None:
    callback_error: BaseException | None = None
    for callback in callbacks:
        try:
            callback()
        except BaseException as error:
            if callback_error is None:
                callback_error = error
    if callback_error is not None:
        raise callback_error


@contextmanager
def lineage_scope(lineage_id: object, *, operation: str = "") -> Iterator[None]:
    """Group one Lineage operation's deferred effects; it takes no lock.

    Instructions on the same Lineage run concurrently: a definition write
    merges its own changes into the committed definition at commit
    (``lineage_definition_merge``).  Effects deferred inside the scope run
    once the outermost scope ends, after the operation's transactions
    committed; a failed operation drops them.
    """

    with _effect_scope(_lineage_scope_id(lineage_id), operation):
        yield


@contextmanager
def lineage_scopes(lineage_ids: Iterable[object], *, operation: str = "") -> Iterator[None]:
    """One effect scope for an operation that spans several Lineages."""

    clean_ids = sorted({str(item or "").strip() for item in lineage_ids} - {""})
    with _effect_scope("lineage:" + ",".join(clean_ids), operation):
        yield


@contextmanager
def _effect_scope(scope_id: str, operation: str) -> Iterator[None]:
    frames = _callback_frames()
    frames.append([])
    try:
        with diagnostic_scope(scope_id, operation):
            yield
    except BaseException:
        frames.pop()
        raise
    callbacks = frames.pop()
    if frames:
        frames[-1].extend(callbacks)
        return
    _run_callbacks(callbacks)


@contextmanager
def catalog_lock(catalog_name: str, *, operation: str = "") -> Iterator[None]:
    with _invariant_scope(_catalog_scope_id(catalog_name), operation=operation):
        yield


@contextmanager
def resident_lock(resident_id: object, *, operation: str = "") -> Iterator[None]:
    """Serialize short commands for one Resident, independently of its graph."""

    with _invariant_scope(_resident_scope_id(resident_id), operation=operation):
        yield


@contextmanager
def resident_conversation_lock(
    resident_id: object,
    *,
    operation: str = "",
) -> Iterator[None]:
    """Serialize short Conversation event commits without blocking Resident jobs."""

    with _invariant_scope(
        _resident_conversation_scope_id(resident_id),
        operation=operation,
    ):
        yield


@contextmanager
def resident_locks(resident_ids: Iterable[object], *, operation: str = "") -> Iterator[None]:
    """Enter Resident participants of a graph transition in stable ID order."""

    with ExitStack() as stack:
        for resident_id in sorted({str(item).strip() for item in resident_ids}):
            stack.enter_context(resident_lock(resident_id, operation=operation))
        yield


@contextmanager
def resident_continuation_lock(resident_id: object) -> Iterator[bool]:
    """Try to own one Resident waiter across Portal processes."""

    with _try_ownership_scope(_resident_continuation_scope_id(resident_id)) as owned:
        yield owned


@contextmanager
def resident_lifecycle_lock(operation_id: str) -> Iterator[bool]:
    """Own an external lifecycle effect without locking Resident commands."""

    if not operation_id:
        raise ValueError("operation_id required")
    with _try_ownership_scope(f"resident-lifecycle:{operation_id}") as owned:
        yield owned


@contextmanager
def _try_ownership_scope(scope_id: str) -> Iterator[bool]:
    lock_path = _lock_path(scope_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    try:
        if not try_lock_descriptor(lock_file.fileno()):
            yield False
            return
        try:
            yield True
        finally:
            unlock_descriptor(lock_file.fileno())
    finally:
        lock_file.close()
