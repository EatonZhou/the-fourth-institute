"""Cross-process authority for Lineage Program ownership and enqueue decisions."""

from __future__ import annotations

import hashlib
import logging
import sys
import threading
import traceback
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

from management_portal.shared_state import exclusive_file_lock


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}
_LOCAL = threading.local()
_OWNERS: dict[str, int] = {}
_LOGGER = logging.getLogger(__name__)
_WAIT_DIAGNOSTIC_SECONDS = 1.0


def _thread_lock(lineage_id: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(lineage_id, threading.RLock())


@contextmanager
def _thread_authority(lineage_id: str) -> Iterator[None]:
    lock = _thread_lock(lineage_id)
    if not lock.acquire(timeout=_WAIT_DIAGNOSTIC_SECONDS):
        with _LOCKS_GUARD:
            owner = _OWNERS.get(lineage_id)
        frame = sys._current_frames().get(owner)
        _LOGGER.warning(
            "Lineage execution authority waiting: lineage_id=%s owner_thread_id=%s owner_stack=%s",
            lineage_id, owner, "".join(traceback.format_stack(frame, limit=32)) if frame else "unavailable",
        )
        lock.acquire()
    try:
        with _LOCKS_GUARD:
            previous = _OWNERS.get(lineage_id)
            _OWNERS[lineage_id] = threading.get_ident()
        try:
            yield
        finally:
            with _LOCKS_GUARD:
                if previous is None:
                    _OWNERS.pop(lineage_id, None)
                else:
                    _OWNERS[lineage_id] = previous
    finally:
        lock.release()


@contextmanager
def execution_authority(lineage_id: str, *, root: Path) -> Iterator[None]:
    """Keep typed Program takeover serialized with external enqueue.

    The authority lock is separate from the aggregate state lock so an
    in-flight delivery can still be cancelled by a generation CAS.
    """
    normalized_id = str(lineage_id or "").strip()
    if not normalized_id:
        yield
        return
    with _thread_authority(normalized_id):
        states = getattr(_LOCAL, "states", None)
        if not isinstance(states, dict):
            states = {}
            _LOCAL.states = states
        state = states.get(normalized_id)
        if not isinstance(state, dict):
            lock_root = root / ".execution-authority"
            lock_root.mkdir(parents=True, exist_ok=True)
            lock_name = hashlib.sha256(normalized_id.encode("utf-8")).hexdigest()[:24]
            stack = ExitStack()
            stack.enter_context(exclusive_file_lock(lock_root / f"{lock_name}.lock"))
            state = {"depth": 0, "stack": stack}
            states[normalized_id] = state
        state["depth"] = int(state.get("depth") or 0) + 1
        try:
            yield
        finally:
            state["depth"] = int(state.get("depth") or 1) - 1
            if state["depth"] == 0:
                try:
                    state["stack"].close()
                finally:
                    states.pop(normalized_id, None)


@contextmanager
def execution_authorities(
    lineage_ids: list[str] | set[str] | tuple[str, ...], *, root: Path,
) -> Iterator[None]:
    with ExitStack() as stack:
        for lineage_id in sorted({
            str(item or "").strip() for item in lineage_ids if str(item or "").strip()
        }):
            stack.enter_context(execution_authority(lineage_id, root=root))
        yield
