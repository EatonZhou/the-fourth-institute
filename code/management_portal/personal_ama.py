"""Every User gets an Ask Me Anything with the account (decision_bd5217d08c09).

SocietyService creates the personal Lineage and its leader Resident in the
User's name; Portal then points the User's personal entry at that leader
unless the User already has an entry.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .resident_sidebar_backend import PERSONAL_AMA_UI_NAMESPACE as NAMESPACE, invalidate_snapshot_cache

logger = logging.getLogger(__name__)

KEY = "primary"
TITLE = "Ask Me Anything"
SESSION_RETRY_SECONDS = 600

_session_lock = threading.Lock()
_session_next_attempt: dict[str, float] = {}


def provision(username: str) -> dict[str, Any]:
    """Ensure the User's Ask Me Anything and entry; repeated calls change nothing."""
    from .society_service import get_society_service
    from .portal_ui_state import get_portal_ui_state_store

    service = get_society_service().lineage
    result = dict(service.ensure_personal_ama(username, username))
    store = get_portal_ui_state_store()
    with store.association_lock(username, NAMESPACE, KEY):
        entries = store.list(username, NAMESPACE)
        entry = next((item for item in entries if item["key"] == KEY), entries[0] if entries else None)
        associated = entry is None
        if associated:
            store.put(username, NAMESPACE, KEY, {
                "resident_id": result["resident_id"], "lineage_id": result["lineage_id"],
                "presentation": {"kind": "personal-ama", "title": TITLE},
            })
            entry = {"value": {"resident_id": result["resident_id"]}}
    assistant = dict(service.ensure_personal_assistant(username, entry["value"]["resident_id"], username))
    from .society_service.personal_assistant import NAMESPACE as assistant_namespace
    with store.association_lock(username, assistant_namespace, KEY):
        value = {**assistant, "presentation": {"kind": "personal-assistant"}}
        previous = store.get(username, assistant_namespace, KEY)
        assistant_associated = previous is None or previous["value"] != value
        if assistant_associated:
            store.put(username, assistant_namespace, KEY, value)
    if associated or assistant_associated:
        invalidate_snapshot_cache()
    return {**result, "associated": associated, "personal_assistant": assistant}


def provision_account(username: str) -> dict[str, Any]:
    """Account creation or login committed; retry provision without blocking access."""
    try:
        return provision(username)
    except Exception as exc:  # noqa: BLE001 - the next successful login retries the same operation
        logger.exception("Ask Me Anything provisioning failed for %s", username)
        return {"error": str(exc) or type(exc).__name__}


def provision_session(username: str) -> threading.Thread | None:
    """A page opened with an existing session finishes the setup in the background.

    Sessions last up to 30 days, so waiting for the next login would leave
    users without their personal assistant for weeks. Each process checks a
    User once; a failure is retried on a later page after SESSION_RETRY_SECONDS.
    """
    now = time.monotonic()
    with _session_lock:
        if not username or now < _session_next_attempt.get(username, 0.0):
            return None
        _session_next_attempt[username] = float("inf")
    thread = threading.Thread(target=_provision_session, args=(username,), name="personal-assistant-session", daemon=True)
    thread.start()
    return thread


def _provision_session(username: str) -> None:
    from .society_service.personal_assistant import NAMESPACE as assistant_namespace
    from .portal_ui_state import get_portal_ui_state_store

    try:
        # The entry is written last, so its presence means setup already finished.
        if get_portal_ui_state_store().get(username, assistant_namespace, KEY) is not None:
            return
    except Exception:  # noqa: BLE001 - provisioning reads the same store and reports its own failure
        pass
    if "error" in provision_account(username):
        with _session_lock:
            _session_next_attempt[username] = time.monotonic() + SESSION_RETRY_SECONDS
