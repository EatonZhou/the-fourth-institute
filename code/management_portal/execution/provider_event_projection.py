"""The interpreter of each wire form a Provider event comes in.

An Execution's Provider events do not name their Provider; their form names
the protocol.  A JSON-RPC notification names its ``method``
(``app_server_event_projection``); a Codex rollout record names its ``type``
(``codex_rollout_event_projection``).  Portal's own items come first, in any
form (``portal_item_projection``).  Adding a wire form means adding its
interpreter's table here; an event no table names has no card.
"""
from __future__ import annotations

from typing import Any

from management_common.provider_subagent_runs import PROGRESS_METHOD

from . import app_server_event_projection, codex_rollout_event_projection
from .portal_item_projection import project_portal_item, project_subagent_progress
from .runtime_details import EventContext, RuntimeDetails, mapping

# The interpreters of notifications by method, and of records by type.
NOTIFICATIONS = {**app_server_event_projection.NOTIFICATIONS}
RECORDS = {**codex_rollout_event_projection.RECORDS}


def project_provider_event(event: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    """Add the runtime details of one Provider event."""

    method = str(event.get("method") or "")
    params = mapping(event.get("params"))
    if method == PROGRESS_METHOD:
        project_subagent_progress(params, context, details)
        return
    if project_portal_item(mapping(params.get("item")), context, details):
        return
    if method:
        notification = NOTIFICATIONS.get(method)
        if notification is not None:
            notification(event, params, context, details)
        return
    record = RECORDS.get(str(event.get("type") or ""))
    if record is not None:
        record(event, context, details)
