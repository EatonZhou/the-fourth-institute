"""A Resident Conversation's event names and limits, its notification shapes, and the protocol and storage forms of its messages, activities and queue."""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from management_common.database_time import iso_time, instant
from management_common.format_tags import schema_matches
from management_common.json_columns import json_text, json_value
from management_common.principal import message_attribution, message_quote, message_recipients
from management_common.site_compatibility import compatibility_value

from management_portal.execution.agent_data_store import utc_now


RESIDENT_CONVERSATION_SCHEMA = "tfi.resident-messages-state.v1"


MESSAGE_APPENDED = "conversation.message.appended"


MESSAGE_UPDATED = "conversation.message.updated"


CONTEXT_CLEARED = "conversation.context.cleared"


STATE_CHANGED = "resident.state.changed"


INPUT_STATE_CHANGED = "resident.input.state.changed"


ACTIVITY_CHANGED = "resident.activity.changed"


QUEUE_CHANGED = "resident.queue.changed"


RUNTIME_SETTINGS_CHANGED = "resident.runtime_settings.changed"


# Activity is observation, not a record: conversation_states keeps the current
# list and each change is pushed live on this channel without entering
# conversation_events or moving the cursor.
LIVE_EVENT_TYPES = frozenset({ACTIVITY_CHANGED})


LIVE_CHANNEL = compatibility_value("conversation_live_channel", "tfi_conversation_live")


INPUT_STATES = frozenset({"queued", "running", "completed", "failed", "cancelled"})


TERMINAL_INPUT_STATES = frozenset({"completed", "failed", "cancelled"})


WORK_STATES = frozenset({"idle", "queued", "running", "stopping"})


WORK_PHASES = frozenset({"starting_session", "waiting_capacity", "waiting_dispatch", "waiting_quota"})


INTAKE_STATES = frozenset({"open", "closed"})


ACTIVITY_STATES = frozenset({"running", "completed", "failed"})


MAX_INLINE_NOTIFICATION_BYTES = 16 * 1024


# A live event cannot be re-read from the event log, so it carries more inline.
MAX_INLINE_LIVE_NOTIFICATION_BYTES = 64 * 1024


DEFAULT_HISTORY_LIMIT = 200


MAX_HISTORY_LIMIT = 1000


def compact_json(value: object) -> str:
    return json_text(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def conversation_event_notification(
    event: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the bounded realtime envelope for one durable event."""

    schema = str(event.get("schema") or "")
    if not schema_matches(schema, "tfi.resident-messages-state.v1"):
        raise ValueError("unsupported Resident Conversation event schema")
    live = event.get("live") is True
    limit = MAX_INLINE_LIVE_NOTIFICATION_BYTES if live else MAX_INLINE_NOTIFICATION_BYTES
    if (
        event.get("event_type") != STATE_CHANGED
        and len(compact_json(event).encode("utf-8")) <= limit
    ):
        return copy.deepcopy(dict(event))
    omitted = {
        "schema": schema,
        "event_type": str(event.get("event_type") or ""),
        "resident_id": str(event.get("resident_id") or ""),
        "cursor": int(event.get("cursor") or 0),
        "occurred_at": str(event.get("occurred_at") or ""),
        "payload_omitted": True,
    }
    if live:
        omitted["live"] = True
    return omitted


def live_activity_event(
    resident_id: str,
    cursor: int,
    activities: object,
    *,
    occurred_at: str = "",
) -> dict[str, Any]:
    """The live event for a Resident's current activity list."""

    return {
        "schema": RESIDENT_CONVERSATION_SCHEMA,
        "event_type": ACTIVITY_CHANGED,
        "resident_id": resident_id,
        "cursor": int(cursor or 0),
        "occurred_at": occurred_at or utc_now(),
        "payload": {"activities": protocol_activities(resident_id, activities)},
        "live": True,
    }


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _content_parts(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    supplied = message.get("content_parts")
    if not isinstance(supplied, list):
        raise ValueError("Conversation message requires content_parts")
    return [copy.deepcopy(item) for item in supplied if isinstance(item, dict)]


def protocol_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy one public Resident Message."""

    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        raise ValueError("Conversation message requires message_id")
    role = str(message.get("role") or "").strip().casefold()
    if role not in {"user", "agent", "system"}:
        raise ValueError("Conversation message role must be user, agent, or system")
    content_parts = _content_parts(message)
    if role == "system" and message.get("harness_input"):
        from management_portal.execution.message_projection import unwrap_message
        content_parts = [
            {**part, "text": unwrap_message(part["text"])}
            if part.get("type") == "text" and isinstance(part.get("text"), str) else part
            for part in content_parts
        ]
    result: dict[str, Any] = {
        "message_id": message_id,
        "role": role,
        "content_parts": content_parts,
        "created_at": str(message.get("created_at") or ""),
    }
    activated_at = str(message.get("activated_at") or "").strip()
    if activated_at:
        result["activated_at"] = activated_at
    result.update(message_attribution(message))
    from management_common.principal_presentation import principal_label
    sender = result.get('origin', {}).get('sender')
    if not sender and result.get('author_id'):
        from management_common.principal import actor_principal
        try:
            sender = actor_principal(result['author_id']).to_dict()
        except ValueError:
            # Legacy display-only authors are not converted into Principals.
            sender = None
    sender_label = principal_label(sender) if sender else ''
    if sender_label:
        result['sender_label'] = sender_label
    reply_to = str(message.get("reply_to_message_id") or "").strip()
    if reply_to and role != "user":
        result["reply_to_message_id"] = reply_to
    recipients = message_recipients(message)
    if recipients:
        result["recipients"] = recipients
        from management_common.principal_presentation import recipient_labels
        labels = recipient_labels(recipients)
        if labels:
            result["recipient_labels"] = labels
    quote = message_quote(message) if role == "user" else {}
    if quote:
        result["quote"] = quote
    usage = message.get("usage")
    if isinstance(usage, Mapping) and usage:
        result["usage"] = copy.deepcopy(dict(usage))
    return result


def protocol_activity(activity: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy one transient Resident Activity."""

    activity_id = str(activity.get("activity_id") or "").strip()
    resident_id = str(activity.get("resident_id") or "").strip()
    kind = str(activity.get("kind") or "").strip().casefold()
    status = str(activity.get("status") or "").strip().casefold()
    if not activity_id or not resident_id:
        raise ValueError("Resident Activity requires activity_id and resident_id")
    if not kind:
        raise ValueError("Resident Activity requires kind")
    if status not in ACTIVITY_STATES:
        raise ValueError(f"Unsupported Resident Activity status: {status}")
    result = {
        "activity_id": activity_id,
        "resident_id": resident_id,
        "message_id": str(activity.get("message_id") or "").strip(),
        "kind": kind,
        "status": status,
        "title": str(activity.get("title") or ""),
        "content": str(activity.get("content") or ""),
        "started_at": str(activity.get("started_at") or ""),
        "updated_at": str(activity.get("updated_at") or ""),
    }
    phase = str(activity.get("phase") or "").strip().casefold()
    if phase:
        result["phase"] = phase
    native_plan = activity.get("native_plan")
    if kind == "plan" and status == "running" and isinstance(native_plan, Mapping):
        execution_id = str(activity.get("execution_id") or "").strip()
        if (
            native_plan.get("type") != "native_turn"
            or not execution_id
            or native_plan.get("execution_id") != execution_id
            or any(not str(native_plan.get(key) or "").strip() for key in ("thread_id", "turn_id", "message_id"))
            or native_plan.get("diff")
        ):
            raise ValueError("Live Plan requires its exact Execution/Thread/Turn identity and no final Diff")
        result["execution_id"] = execution_id
        result["native_plan"] = copy.deepcopy(dict(native_plan))
    subagent = activity.get("subagent")
    if kind == "subagent" and isinstance(subagent, Mapping):
        # The card of a subagent run the Resident's model waits on, and the
        # Execution it runs in (CancelSubagent names it).
        result["subagent"] = copy.deepcopy(dict(subagent))
        if str(activity.get("execution_id") or "").strip():
            result["execution_id"] = str(activity["execution_id"]).strip()
    return result


def protocol_activities(resident_id: str, activities: object) -> list[dict[str, Any]]:
    """Validate the complete transient Activity projection for one Resident."""

    value = [
        protocol_activity(item)
        for item in _list(activities)
        if isinstance(item, Mapping)
    ]
    if any(item["resident_id"] != resident_id for item in value):
        raise ValueError("Resident Activity belongs to another Resident")
    return value


def protocol_queue(resident_id: str, queue: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy one ordered Resident Queue."""

    queue_resident_id = str(queue.get("resident_id") or "").strip()
    if queue_resident_id != resident_id:
        raise ValueError("Resident Queue belongs to another Resident")
    raw_items = queue.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Resident Queue items must be a list")
    items: list[dict[str, Any]] = []
    seen_message_ids: set[str] = set()
    for item in raw_items:
        if not isinstance(item, Mapping):
            raise ValueError("Resident Queue items must be objects")
        message_id = str(item.get("message_id") or "").strip()
        if not message_id:
            raise ValueError("Resident Queue item requires message_id")
        if message_id in seen_message_ids:
            raise ValueError("Resident Queue contains a duplicate message_id")
        seen_message_ids.add(message_id)
        queue_item = {
            "message_id": message_id,
            "author_id": str(item.get("author_id") or "").strip(),
            "content_preview": str(item.get("content_preview") or ""),
            "enqueued_at": str(item.get("enqueued_at") or ""),
        }
        content_parts = [
            copy.deepcopy(dict(part))
            for part in item.get("content_parts") or []
            if isinstance(part, Mapping)
        ]
        if content_parts:
            queue_item["content_parts"] = content_parts
        items.append(queue_item)
    return {"resident_id": resident_id, "items": items}


def message_from_storage(message: Mapping[str, Any]) -> dict[str, Any]:
    """Decode the durable AgentData representation into the public protocol."""

    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        raise ValueError("Stored conversation message has no identity")
    content_parts = message.get("content_parts")
    if not isinstance(content_parts, list):
        raise ValueError("Stored conversation message has no content_parts")
    if message.get("role") == "agent":
        from management_common.async_questions import decode_question_content
        content_parts = decode_question_content(content_parts, str(message.get("author_id") or ""), message_id)
    provider_response = message.get("provider_response")
    provider_artifact_id = f"{message_id}:provider_response"
    if isinstance(provider_response, Mapping) and provider_response:
        content_parts = copy.deepcopy(content_parts)
        if not any(
            isinstance(part, Mapping)
            and part.get("type") == "artifact_reference"
            and part.get("artifact_id") == provider_artifact_id
            for part in content_parts
        ):
            content_parts.append({
                "type": "artifact_reference",
                "artifact_id": provider_artifact_id,
                "name": "provider_response.json",
                "media_type": "application/json",
            })
    return protocol_message({
        "message_id": message_id,
        "role": str(message.get("role") or ""),
        "author_id": str(message.get("author_id") or ""),
        **({"origin": message["origin"]} if "origin" in message else {}),
        "reply_to_message_id": str(message.get("reply_to_message_id") or ""),
        "recipients": message.get("recipients") or [],
        **({"quote": message["quote"]} if message.get("quote") else {}),
        "content_parts": content_parts,
        "created_at": str(message.get("created_at") or ""),
        "activated_at": str(message.get("activated_at") or ""),
        "usage": message.get("usage") or {},
    }) | {
        # How the Conversation shows it: an info inside the request it came
        # with, a Mailbox group member inside its group, an input the Harness
        # gave its Provider on its own.
        key: copy.deepcopy(message[key])
        for key in DISPLAY_FIELDS
        if message.get(key) not in (None, "", {})
    }


# Private fields kept with a stored message, never part of its public identity.
PRIVATE_FIELDS = (
    "provider_response",
    "execution_id",
    "provider_turn_id",
    "business_ref",
    "released_with",
    "grouped_into_message_id",
    "harness_input",
)


DISPLAY_FIELDS = ("released_with", "grouped_into_message_id", "harness_input")


def message_for_storage(
    message: Mapping[str, Any],
    *,
    private_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Encode one public Message for AgentData's segment-backed storage."""

    public = protocol_message(message)
    payload: dict[str, Any] = copy.deepcopy(public)
    for key in PRIVATE_FIELDS:
        value = (private_fields or {}).get(key)
        if value not in (None, "", {}):
            payload[key] = copy.deepcopy(value)
    return payload


def immutable_message_fields(message: Mapping[str, Any]) -> dict[str, Any]:
    """Return the client-visible fields that define one Message identity."""

    immutable = protocol_message(message)
    immutable.pop("activated_at", None)
    return immutable


def message_header(message: Mapping[str, Any]) -> dict[str, Any]:
    """What a message event keeps of its message: who it is and what it answers.

    The body is the trace's (decision_71aa039e3211): readers of the event log
    get the message as the trace holds it now.
    """

    header = {"message_id": str(message["message_id"]), "role": str(message.get("role") or "")}
    if message.get("reply_to_message_id"):
        header["reply_to_message_id"] = str(message["reply_to_message_id"])
    return header


def final_reply(state: object, replies: tuple[str, str] | None) -> list[str]:
    """A finished input's replies: its final reply, the last message answering it.

    A completed input's is the Agent's last answer when the Agent answered;
    a failed or stopped one ends with Portal's notice.
    """

    if state not in TERMINAL_INPUT_STATES or not replies:
        return []
    last_agent, last = replies
    return [last_agent or last] if state == "completed" else [last]


def input_state_value(row: Mapping[str, Any]) -> dict[str, Any]:
    """An input's stored state; its replies are the trace's (see ``_with_replies``)."""

    value = {
        "message_id": str(row["message_id"]),
        "state": str(row["state"]),
        "updated_at": iso_time(row["updated_at"]),
    }
    error = json_value(row["error_json"])
    if error:
        value["error"] = error
    return value


def _message_text(message: Mapping[str, Any]) -> str:
    return "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, Mapping) and part.get("type") == "text"
    )


def business_ref_excluded(message: Mapping[str, Any], pattern: str) -> bool:
    return bool(pattern) and bool(re.search(pattern, str(message.get("business_ref") or "")))


def instant_or_none(value: object) -> datetime | None:
    try:
        return instant(value)
    except (TypeError, ValueError):
        return None


def instants(times: Mapping[str, object]) -> dict[str, datetime]:
    """Activity times as instants: their texts differ in precision and cannot be compared."""
    return {key: at for key, value in times.items() if (at := instant_or_none(value))}


def with_outcomes(activity: Mapping[str, datetime], outcomes: Mapping[str, datetime]) -> dict[str, datetime]:
    """A conversation's time for the actor: their messages there, messages to them and
    the outcomes of their requests. An outcome alone does not make it theirs."""
    return {resident_id: max(at, outcomes.get(resident_id, at)) for resident_id, at in activity.items()}


def newest_first(activity: Mapping[str, datetime]) -> list[tuple[str, datetime]]:
    """Newest first, equal times by Resident id: the order activity pages keep."""
    ordered = sorted(activity.items(), key=lambda item: item[0])
    ordered.sort(key=lambda item: item[1], reverse=True)
    return ordered


def required_resident_id(value: object) -> str:
    resident_id = str(value or "").strip()
    if not resident_id.startswith("resident_"):
        raise ValueError("Resident protocol requires a Resident ID")
    return resident_id


def required_message_id(message: Mapping[str, Any]) -> str:
    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        raise ValueError("Conversation message requires message_id")
    return message_id


def default_state() -> dict[str, Any]:
    return {
        "work_state": "idle",
        "active_message_id": "",
        "queued_count": 0,
        "intake_state": "open",
    }


def normalized_state(
    value: Mapping[str, Any],
    *,
    strict: bool,
) -> dict[str, Any]:
    work_state = str(value.get("work_state") or "idle").strip().casefold()
    intake_state = str(value.get("intake_state") or "open").strip().casefold()
    work_phase = str(value.get("work_phase") or "").strip().casefold()
    active_message_id = str(value.get("active_message_id") or "").strip()
    try:
        queued_count = max(0, int(value.get("queued_count") or 0))
    except (TypeError, ValueError):
        if strict:
            raise ValueError("Resident State queued_count must be an integer")
        queued_count = 0
    if work_state not in WORK_STATES:
        if strict:
            raise ValueError(f"Unsupported Resident work_state: {work_state}")
        work_state = "idle"
    if intake_state not in INTAKE_STATES:
        if strict:
            raise ValueError(f"Unsupported Resident intake_state: {intake_state}")
        intake_state = "open"
    if work_phase and work_phase not in WORK_PHASES:
        if strict:
            raise ValueError(f"Unsupported Resident work_phase: {work_phase}")
        work_phase = ""
    if work_phase in {"waiting_capacity", "waiting_dispatch", "waiting_quota"} and work_state != "queued":
        work_phase = ""
    if work_phase == "starting_session" and work_state not in {"queued", "running"}:
        work_phase = ""
    if work_state == "idle":
        active_message_id = ""
    state = {
        "work_state": work_state,
        "active_message_id": active_message_id,
        "queued_count": queued_count,
        "intake_state": intake_state,
    }
    if work_phase:
        state["work_phase"] = work_phase
    return state
