from __future__ import annotations

import logging
import threading
from collections.abc import Callable


_LOCK = threading.Lock()
_WAKE: Callable[[str], None] | None = None
_CANCEL: Callable[[str, str], None] | None = None
LOGGER = logging.getLogger(__name__)


def set_execution_cancellation_handler(handler: Callable[[str, str], None] | None) -> None:
    global _CANCEL
    with _LOCK:
        _CANCEL = handler


def notify_execution_cancellation(execution_id: str, actor: str) -> None:
    from . import invariant_locks

    execution_id = str(execution_id or "").strip()
    actor = str(actor or "").strip()
    if not execution_id or not actor:
        return

    def notify() -> None:
        with _LOCK:
            handler = _CANCEL
        if handler is not None:
            try:
                handler(execution_id, actor)
            except Exception:
                LOGGER.exception("Execution cancellation notification failed for %s", execution_id)

    invariant_locks.defer_until_state_unlock(notify)


def set_execution_wake_handler(
    handler: Callable[[str], None] | None,
) -> None:
    global _WAKE
    with _LOCK:
        _WAKE = handler


def notify_execution_binding(resident_id: object) -> None:
    from . import invariant_locks

    clean_resident_id = str(resident_id or "").strip()
    if not clean_resident_id:
        return

    def notify() -> None:
        with _LOCK:
            handler = _WAKE
        if handler is not None:
            handler(clean_resident_id)
            return
        from .config import society_service_mode

        if society_service_mode() == "client":
            from . import get_society_service

            try:
                get_society_service().wake_resident_execution(clean_resident_id)
            except Exception:
                # The durable Job remains recoverable by the coordinator's
                # periodic scan if its immediate wake cannot be delivered.
                LOGGER.exception("Resident execution wake failed for %s", clean_resident_id)

    invariant_locks.defer_until_state_unlock(notify)
