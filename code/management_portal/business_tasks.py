from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any

from management_common.process_observation import process_identity, process_is_alive

_LOCK = threading.RLock()
_ACTIVE: set[str] = set()


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
    from management_common.resource_lifecycle import current_lifecycle, LifecycleStore, lifecycle_scope
    from management_common.operation_identity import current_operation_identity, operation_identity_scope
    lifecycle_id = current_lifecycle()
    lifecycle = LifecycleStore().scope(lifecycle_id) if lifecycle_id else None
    identity = current_operation_identity()
    ready = threading.Event()
    admission_errors = []
    with _LOCK:
        if clean_id in _ACTIVE:
            return False
        _ACTIVE.add(clean_id)

    def run() -> None:
        # Carry only the authenticated identity, not request-local transactions
        # or locks. Stored job authors cannot manufacture this context later.
        identity_scope = operation_identity_scope(identity) if identity is not None else nullcontext()
        with identity_scope:
            try:
                scope = lifecycle_scope(lifecycle_id, actor=lifecycle['actor']) if lifecycle else nullcontext()
                # Acquire the background request's own shared lease before returning
                # to the caller. Sealing a run cannot slip between parent and child.
                with scope:
                    ready.set()
                    target(*args, **kwargs)
            except BaseException as exc:
                if not ready.is_set():
                    admission_errors.append(exc)
                if on_error is not None:
                    on_error(exc)
            finally:
                ready.set()
                with _LOCK:
                    _ACTIVE.discard(clean_id)

    try:
        threading.Thread(
            target=run,
            name=f"portal-business-{clean_id[:80]}",
            daemon=daemon,
        ).start()
        if lifecycle:
            ready.wait()
            if admission_errors:
                raise admission_errors[0]
    except BaseException:
        with _LOCK:
            _ACTIVE.discard(clean_id)
        raise
    return True


def is_active(task_id: str) -> bool:
    with _LOCK:
        return str(task_id or "").strip() in _ACTIVE
