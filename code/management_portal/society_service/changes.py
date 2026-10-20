from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from management_portal.notifications import (
    get_notification_publisher,
    notification_subject,
)


Change = tuple[str, str, str, dict[str, Any]]
LOGGER = logging.getLogger(__name__)
_COLLECTED_CHANGES: ContextVar[list[Change] | None] = ContextVar(
    "portal_lineage_collected_changes",
    default=None,
)
_LOCAL_CHANGE_HANDLER: Callable[[str, str, str], None] | None = None


def set_local_change_handler(
    handler: Callable[[str, str, str], None] | None,
) -> None:
    global _LOCAL_CHANGE_HANDLER
    _LOCAL_CHANGE_HANDLER = handler


def _enabled() -> bool:
    return str(os.getenv("PORTAL_SOCIETY_SERVICE_PROCESS", "") or "").strip() == "1"


@contextmanager
def collect_changes() -> Iterator[list[Change]]:
    changes: list[Change] = []
    token = _COLLECTED_CHANGES.set(changes)
    try:
        yield changes
    finally:
        _COLLECTED_CHANGES.reset(token)


def publish_collected_changes(publisher: object, changes: list[Change]) -> None:
    for domain, resource_id, scope, payload in changes:
        publisher.publish(
            notification_subject(domain, resource_id, scope=scope),
            payload,
        )


def _record_or_publish(
    domain: str,
    resource_id: str,
    scope: str,
    payload: dict[str, Any] | None = None,
) -> None:
    event = dict(payload or {"kind": "changed"})
    from management_portal.society_service import invariant_locks

    # A request collector is not a commit boundary. Register inside the state
    # scope first so a rolled-back transition cannot leak into the collector.
    if invariant_locks.lock_held():
        invariant_locks.defer_until_state_unlock(
            lambda: _record_or_publish(domain, resource_id, scope, event)
        )
        return
    collected = _COLLECTED_CHANGES.get()
    if collected is not None:
        change = (domain, resource_id, scope, event)
        if event.get("kind") != "changed" or not any(
            existing[:3] == change[:3]
            and existing[3].get("kind") == "changed"
            for existing in collected
        ):
            collected.append(change)
        return
    handler = _LOCAL_CHANGE_HANDLER
    if handler is not None:
        try:
            handler(domain, resource_id, scope)
        except Exception:
            LOGGER.exception(
                "Lineage local change handler failed for %s:%s:%s",
                domain,
                resource_id,
                scope,
            )
    get_notification_publisher().publish(
        notification_subject(domain, resource_id, scope=scope),
        event,
    )


def resident_changed(resident_id: object, *, scope: str = "runtime") -> None:
    clean_id = str(resident_id or "").strip()
    if not clean_id.startswith("resident_"):
        return
    if _COLLECTED_CHANGES.get() is None and not _enabled():
        return
    _record_or_publish("resident", clean_id, str(scope or "runtime"))


def lineage_changed(lineage_id: object, *, runtime: bool = False) -> None:
    clean_id = str(lineage_id or "").strip()
    if not clean_id.startswith("lineage_"):
        return
    if _COLLECTED_CHANGES.get() is None and not _enabled():
        return
    _record_or_publish("lineage", clean_id, "runtime" if runtime else "")
