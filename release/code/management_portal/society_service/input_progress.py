"""Where one unfinished input is in its Resident, and which inputs a Conversation event moves.

A Family facade shows this progress under each request it relayed to a member
(decision_44cf587c1721); ``GetInputState`` returns it on request, so a
Chatroom shows it under each message that called a Resident
(decision_a6bebf4ac283).  The progress is the input's place in the queue
while it waits, and the Resident's live work on it - work phase and
activities - while it runs.  Intermediate replies are not part of it.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import resident_conversation_stream

# A queue change moves the place of every waiting input; only the first ones are reported.
MAX_QUEUED_INPUTS = 20


def input_progress(
    repository: Any,
    resident_id: str,
    message_id: str,
    state: str,
    projections: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The queue place of a waiting input, or the live work on a running one; empty otherwise.

    ``projections`` caches Resident projections across several inputs.
    """

    if state not in {"queued", "running"}:
        return {}
    cache = projections if projections is not None else {}
    if resident_id not in cache:
        try:
            cache[resident_id] = repository.current_projection(resident_id)
        except KeyError:
            cache[resident_id] = {}
    projection = cache[resident_id]
    resident_state = projection.get("state") or {}
    active = str(resident_state.get("active_message_id") or "")
    if active == message_id:
        return {
            "work_state": str(resident_state.get("work_state") or state),
            "work_phase": str(resident_state.get("work_phase") or ""),
            "activities": [
                dict(item) for item in projection.get("activities") or []
                if isinstance(item, Mapping) and (not item.get("message_id") or item.get("message_id") == message_id)
            ],
        }
    if state != "queued":
        return {}
    queued = [str(item.get("message_id") or "") for item in (projection.get("queue") or {}).get("items") or []]
    if message_id not in queued:
        return {}
    # The Resident's current turn on another message runs first.
    return {"ahead": queued.index(message_id) + (1 if active else 0)}


def event_input_ids(event: Mapping[str, Any]) -> list[str]:
    """The inputs whose state or progress one Resident Conversation event changes."""

    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    event_type = str(event.get("event_type") or "")
    if event_type == resident_conversation_stream.INPUT_STATE_CHANGED:
        input_state = payload.get("input_state") if isinstance(payload.get("input_state"), Mapping) else {}
        message_id = str(input_state.get("message_id") or "")
    elif event_type in {resident_conversation_stream.MESSAGE_APPENDED, resident_conversation_stream.MESSAGE_UPDATED}:
        message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
        if message.get("role") != "agent":
            return []
        message_id = str(message.get("reply_to_message_id") or "")
    elif event_type == resident_conversation_stream.STATE_CHANGED:
        state = payload.get("state") if isinstance(payload.get("state"), Mapping) else {}
        message_id = str(state.get("active_message_id") or "")
    elif event_type == resident_conversation_stream.ACTIVITY_CHANGED:
        message_id = next((
            str(item.get("message_id") or "")
            for item in payload.get("activities") or []
            if isinstance(item, Mapping) and item.get("message_id")
        ), "")
    elif event_type == resident_conversation_stream.QUEUE_CHANGED:
        # A waiting input's place moves with the queue.
        queue = payload.get("queue") if isinstance(payload.get("queue"), Mapping) else {}
        ids = [
            str(item.get("message_id") or "") if isinstance(item, Mapping) else ""
            for item in list(queue.get("items") or [])[:MAX_QUEUED_INPUTS]
        ]
        return list(dict.fromkeys(item for item in ids if item))
    else:
        return []
    return [message_id] if message_id else []
