from __future__ import annotations

from management_common.format_tags import schema_matches
from management_common.site_compatibility import compatibility_value

from management_common.database_time import iso_time, instant
from management_common.json_columns import json_text, json_value

import copy
import hashlib
import re
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from management_common.principal import message_attribution, message_quote, message_recipients
from management_common.async_questions import QUESTION_SET_CLOSED, question_set, visible_message_input_ids

from management_portal.execution.agent_data_store import (
    AgentDataStore,
    canonical_json,
    chat_segment_id,
    finish_conversation_summary,
    get_agent_data_store,
    utc_now,
)
from management_portal.execution.chat_position_index import RELAYED_COPY_PATTERN

from . import invariant_locks, resident_message_contract
from .state_store import LineageDataStore, get_lineage_data_store


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

_REPOSITORIES: dict[str, "ResidentConversationRepository"] = {}
_REPOSITORIES_LOCK = threading.Lock()


def _json(value: object) -> str:
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
        and len(_json(event).encode("utf-8")) <= limit
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


def _message_from_storage(message: Mapping[str, Any]) -> dict[str, Any]:
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


def _message_for_storage(
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


def _immutable_message_fields(message: Mapping[str, Any]) -> dict[str, Any]:
    """Return the client-visible fields that define one Message identity."""

    immutable = protocol_message(message)
    immutable.pop("activated_at", None)
    return immutable


def _message_header(message: Mapping[str, Any]) -> dict[str, Any]:
    """What a message event keeps of its message: who it is and what it answers.

    The body is the trace's (decision_71aa039e3211): readers of the event log
    get the message as the trace holds it now.
    """

    header = {"message_id": str(message["message_id"]), "role": str(message.get("role") or "")}
    if message.get("reply_to_message_id"):
        header["reply_to_message_id"] = str(message["reply_to_message_id"])
    return header


def _final_reply(state: object, replies: tuple[str, str] | None) -> list[str]:
    """A finished input's replies: its final reply, the last message answering it.

    A completed input's is the Agent's last answer when the Agent answered;
    a failed or stopped one ends with Portal's notice.
    """

    if state not in TERMINAL_INPUT_STATES or not replies:
        return []
    last_agent, last = replies
    return [last_agent or last] if state == "completed" else [last]


def _input_state_value(row: Mapping[str, Any]) -> dict[str, Any]:
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


def _business_ref_excluded(message: Mapping[str, Any], pattern: str) -> bool:
    return bool(pattern) and bool(re.search(pattern, str(message.get("business_ref") or "")))


def _instant_or_none(value: object) -> datetime | None:
    try:
        return instant(value)
    except (TypeError, ValueError):
        return None


def _instants(times: Mapping[str, object]) -> dict[str, datetime]:
    """Activity times as instants: their texts differ in precision and cannot be compared."""
    return {key: at for key, value in times.items() if (at := _instant_or_none(value))}


def _with_outcomes(activity: Mapping[str, datetime], outcomes: Mapping[str, datetime]) -> dict[str, datetime]:
    """A conversation's time for the actor: their messages there, messages to them and
    the outcomes of their requests. An outcome alone does not make it theirs."""
    return {resident_id: max(at, outcomes.get(resident_id, at)) for resident_id, at in activity.items()}


def _newest_first(activity: Mapping[str, datetime]) -> list[tuple[str, datetime]]:
    """Newest first, equal times by Resident id: the order activity pages keep."""
    ordered = sorted(activity.items(), key=lambda item: item[0])
    ordered.sort(key=lambda item: item[1], reverse=True)
    return ordered


def _resident_id(value: object) -> str:
    resident_id = str(value or "").strip()
    if not resident_id.startswith("resident_"):
        raise ValueError("Resident protocol requires a Resident ID")
    return resident_id


def _message_id(message: Mapping[str, Any]) -> str:
    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        raise ValueError("Conversation message requires message_id")
    return message_id


def _default_state() -> dict[str, Any]:
    return {
        "work_state": "idle",
        "active_message_id": "",
        "queued_count": 0,
        "intake_state": "open",
    }


def _state(
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


class ConversationEvents:
    """The ordered event log of each Resident Conversation, its cursor and its readers."""

    def __init__(
        self,
        state_store: LineageDataStore,
        agent_data: AgentDataStore,
        repository: "ResidentConversationRepository",
    ) -> None:
        self.state_store = state_store
        self.agent_data = agent_data
        self.repository = repository

    @contextmanager
    def protocol_scope(self, resident_id: str):
        """Serialize short Conversation commits independently of Resident jobs."""
        with invariant_locks.resident_conversation_lock(resident_id):
            yield

    def ensure_conversation(self, connection: Any, resident_id: str) -> None:
        self.state_store._lock_active_resident(connection, resident_id)
        connection.execute(
            'INSERT INTO conversation_states(resident_id) SELECT %s WHERE EXISTS ('
            'SELECT 1 FROM residents WHERE resident_id = %s) ON CONFLICT DO NOTHING',
            (resident_id, resident_id),
        )
        row = connection.execute(
            'SELECT 1 FROM conversation_states WHERE resident_id = %s',
            (resident_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Resident not found: {resident_id}")

    @staticmethod
    def event_exists(
        connection: Any,
        resident_id: str,
        event_type: str,
        subject_id: str,
    ) -> bool:
        return connection.execute(
            'SELECT 1 FROM conversation_events WHERE resident_id = %s AND event_type = %s AND subject_id = %s LIMIT 1',
            (resident_id, event_type, subject_id),
        ).fetchone() is not None

    def record_event(
        self,
        connection: Any,
        resident_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        subject_id: str = "",
        once: bool = False,
    ) -> dict[str, Any] | None:
        self.ensure_conversation(connection, resident_id)
        if once and self.event_exists(connection, resident_id, event_type, subject_id):
            return None
        row = connection.execute(
            'SELECT cursor FROM conversation_states WHERE resident_id = %s',
            (resident_id,),
        ).fetchone()
        cursor = int(row["cursor"] or 0) + 1
        occurred_at = utc_now()
        event = {
            "schema": RESIDENT_CONVERSATION_SCHEMA,
            "event_type": event_type,
            "resident_id": resident_id,
            "cursor": cursor,
            "occurred_at": occurred_at,
            "payload": copy.deepcopy(dict(payload)),
        }
        connection.execute(
            'UPDATE conversation_states SET cursor = %s WHERE resident_id = %s',
            (cursor, resident_id),
        )
        connection.execute(
            'INSERT INTO conversation_events(resident_id, cursor, event_type, subject_id, occurred_at, payload_json) VALUES (%s, %s, %s, %s, %s, %s)',
            (resident_id, cursor, event_type, subject_id, occurred_at, _json(event["payload"])),
        )
        from . import resident_unread
        resident_unread.project_event(self.repository, connection, event)
        return event

    def execution_settings_changed(
        self,
        resident_id: object,
        settings: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Publish a change receipt; current settings remain owned by Residents."""
        stream_id = _resident_id(resident_id)

        def write(connection: Any) -> dict[str, Any]:
            self.ensure_conversation(connection, stream_id)
            return self.record_event(
                connection, stream_id, RUNTIME_SETTINGS_CHANGED,
                {"runtime_settings": dict(settings)},
            )

        with self.protocol_scope(stream_id):
            return self.state_store.database.write(write)

    @staticmethod
    def told_queue(connection: Any, resident_id: str) -> dict[str, Any]:
        """The queue the last queue event told watchers; empty before the first."""

        row = connection.execute(
            "SELECT payload_json FROM conversation_events WHERE resident_id = %s AND event_type = %s "
            "AND subject_id = '' ORDER BY cursor DESC LIMIT 1",
            (resident_id, QUEUE_CHANGED),
        ).fetchone()
        if row is None:
            return {"resident_id": resident_id, "items": []}
        return json_value(row["payload_json"]).get("queue") or {}

    def context_reset_boundary(self, resident_id: object) -> list[str]:
        return self.agent_data.displayed_chat_message_ids(_resident_id(resident_id))

    def archive_context(
        self, resident_id: object, *, archived_at: str = "",
        message_ids: Sequence[str] | None = None,
    ) -> list[str]:
        """Tell the Conversation that a clear ended these messages' place in the Chat.

        The clear itself is the context reset mark in the trace
        (``AgentDataStore.record_context_reset``): the Chat is what comes
        after the last one.  Announcing the same clear again sends the same
        events once more, which readers apply idempotently.
        """

        stream_id = _resident_id(resident_id)
        del archived_at
        selected_ids = (
            [str(value) for value in message_ids]
            if message_ids is not None
            else self.context_reset_boundary(stream_id)
        )
        def write(connection: Any) -> None:
            for message_id in selected_ids:
                self.record_event(
                    connection,
                    stream_id,
                    MESSAGE_UPDATED,
                    {"message_id": message_id, "removed": True},
                    subject_id=message_id,
                )
            # The lifecycle owner has recorded the reset boundary before this
            # projection. Announce it even when there are no displayed messages:
            # a Fork's inheritance card still needs the completed clear fact.
            self.record_event(
                connection, stream_id, CONTEXT_CLEARED, {"context_cleared": True},
            )
        with self.protocol_scope(stream_id):
            self.state_store.database.write(write)
        return selected_ids

    def prune_event_deliveries(self, *, older_than: str, limit: int = 5000) -> int:
        """Delete relay work items for events that occurred before ``older_than``.

        Each instance's relay starts at the tail and follows within seconds;
        the events themselves stay in conversation_events.
        """

        def delete(connection: Any) -> int:
            return int(connection.execute(
                'DELETE FROM conversation_event_delivery WHERE sequence IN ('
                'SELECT delivery.sequence FROM conversation_event_delivery AS delivery '
                'JOIN conversation_events AS event ON event.resident_id = delivery.resident_id '
                'AND event.cursor = delivery.cursor WHERE event.occurred_at < %s '
                'ORDER BY delivery.sequence LIMIT %s)',
                (str(older_than), max(1, int(limit))),
            ).rowcount or 0)

        return self.state_store.database.write(delete)

    def events_after(
        self,
        resident_id: object,
        after_cursor: object,
        *,
        limit: int = 500,
    ) -> dict[str, Any]:
        stream_id = _resident_id(resident_id)
        cursor = max(0, int(after_cursor or 0))
        safe_limit = max(1, min(int(limit or 500), 2000))
        with self.state_store.database.read() as connection:
            state = connection.execute(
                'SELECT cursor FROM conversation_states WHERE resident_id = %s',
                (stream_id,),
            ).fetchone()
            if state is None:
                resident = connection.execute(
                    'SELECT 1 FROM residents WHERE resident_id = %s',
                    (stream_id,),
                ).fetchone()
                if resident is None:
                    raise KeyError(stream_id)
                # A registered, unused Resident has an empty stream. Reads must
                # not create runtime state just to let its first watcher attach.
                return {
                    "resident_id": stream_id,
                    "after_cursor": cursor,
                    "cursor": 0,
                    "events": [],
                }
            rows = connection.execute(
                'SELECT * FROM conversation_events WHERE resident_id = %s AND cursor > %s ORDER BY cursor LIMIT %s',
                (stream_id, cursor, safe_limit),
            ).fetchall()
        events = self.hydrate_events([
            {
                "schema": RESIDENT_CONVERSATION_SCHEMA,
                "event_type": str(row["event_type"]),
                "resident_id": stream_id,
                "cursor": int(row["cursor"]),
                "occurred_at": iso_time(row["occurred_at"]),
                "payload": json_value(row["payload_json"]),
            }
            for row in rows
        ])
        return {
            "resident_id": stream_id,
            "after_cursor": cursor,
            "cursor": int(state["cursor"] or 0),
            "events": events,
        }

    def hydrate_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fill events read from the log with what the trace holds now.

        A message event carries the message (the log keeps only its header);
        a finished input's state carries its final reply.  A message the trace
        no longer has leaves its event without payload, for the reader to
        take a Snapshot instead.
        """

        from . import resident_mailbox

        wanted: dict[str, tuple[set[str], set[str]]] = {}
        for event in events:
            payload = event.get("payload") or {}
            messages, inputs = wanted.setdefault(str(event["resident_id"]), (set(), set()))
            if event["event_type"] in {MESSAGE_APPENDED, MESSAGE_UPDATED} and isinstance(payload.get("message"), Mapping):
                messages.add(str(payload["message"].get("message_id") or ""))
            elif event["event_type"] == INPUT_STATE_CHANGED and isinstance(payload.get("input_state"), Mapping):
                if payload["input_state"].get("state") in TERMINAL_INPUT_STATES:
                    inputs.add(str(payload["input_state"].get("message_id") or ""))
        finals = self.agent_data.chat_final_replies_many(
            {resident_id: sorted(inputs) for resident_id, (_messages, inputs) in wanted.items() if inputs},
        )
        found: dict[str, tuple[dict[str, dict[str, Any]], dict[str, tuple[str, str]]]] = {}
        for resident_id, (message_ids, _inputs) in wanted.items():
            stored = self.agent_data.trace_chat_messages(resident_id, sorted(message_ids)) if message_ids else {}
            messages = {message_id: _message_from_storage(message) for message_id, message in stored.items()}
            resident_mailbox.project_messages(resident_id, list(messages.values()), repository=self.repository)
            found[resident_id] = (messages, finals.get(resident_id, {}))
        for event in events:
            payload = event.get("payload") or {}
            messages, replies = found.get(str(event["resident_id"]), ({}, {}))
            if event["event_type"] in {MESSAGE_APPENDED, MESSAGE_UPDATED} and isinstance(payload.get("message"), Mapping):
                stored = messages.get(str(payload["message"].get("message_id") or ""))
                if stored is None:
                    event.pop("payload", None)
                    event["payload_omitted"] = True
                else:
                    event["payload"] = {**payload, "message": copy.deepcopy(stored)}
            elif event["event_type"] == INPUT_STATE_CHANGED and isinstance(payload.get("input_state"), Mapping):
                state = dict(payload["input_state"])
                state["reply_message_ids"] = _final_reply(state.get("state"), replies.get(str(state.get("message_id") or "")))
                event["payload"] = {**payload, "input_state": state}
        return events


class ConversationMessages:
    """The messages of each Resident Conversation: accepted into its Mailbox, delivered into its trace."""

    def __init__(
        self,
        state_store: LineageDataStore,
        agent_data: AgentDataStore,
        events: ConversationEvents,
    ) -> None:
        self.state_store = state_store
        self.agent_data = agent_data
        self.events = events

    def commit_user_message(
        self,
        resident_id: object,
        message: Mapping[str, Any],
        *,
        activated_at: str = "",
    ) -> dict[str, Any]:
        if protocol_message(message)["role"] != "user":
            raise ValueError("Only user messages use the intake commit protocol")
        return self.append_message(
            resident_id,
            message,
            activated_at=activated_at,
        )

    def accept_user_message(
        self,
        resident_id: object,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Keep a Message that waits in the Mailbox; it enters the trace when it is delivered."""

        stream_id = _resident_id(resident_id)
        public_input = protocol_message(message)
        if public_input["role"] != "user":
            raise ValueError("Only user messages use the intake acceptance protocol")
        public_input.pop("activated_at", None)
        payload = _message_for_storage(public_input, private_fields=message)
        shown = self.agent_data.chat_message(stream_id, str(public_input["message_id"]))
        if shown is not None:
            stored = shown
        else:
            with self.events.protocol_scope(stream_id):
                stored, _inserted = self.state_store.mailbox_accept(stream_id, payload)
        return self._validate_stored_message_identity(stored, public_input)

    def validate_message_identity(
        self,
        resident_id: object,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate an idempotent replay without mutating the stream or storage."""

        public_input = protocol_message(message)
        stored = self.stored_message(resident_id, public_input["message_id"])
        if stored is None:
            raise KeyError(str(public_input["message_id"]))
        return self._validate_stored_message_identity(stored, public_input)

    def _validate_stored_message_identity(
        self, stored: Mapping[str, Any], public_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Check the accepted input's immutable ancestry, returning its current view."""
        public = _message_from_storage(stored)
        expected = _immutable_message_fields(public_input)
        if _immutable_message_fields(public) == expected:
            return public
        # Harness preparation may replace the displayed input. Acceptance,
        # read-only validation and delivery must all compare the original
        # envelope against the same provenance chain, never only the wrapper.
        if stored.get("segment_id") and public_input["role"] == "user":
            for ancestor in self.agent_data.chat_message_ancestors(
                str(stored["segment_id"]), str(public_input["message_id"]),
            ):
                if _immutable_message_fields(_message_from_storage(ancestor)) == expected:
                    return public
        raise resident_message_contract.MessageIdentityConflict(
            f"Conversation message identity refers to different immutable content: {public_input['message_id']}"
        )

    def execution_content_parts(
        self,
        resident_id: object,
        execution_id: str,
        content_parts: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        stream_id = _resident_id(resident_id)
        parts = copy.deepcopy([dict(part) for part in content_parts])
        identity_fields = (
            "type", "tool_name", "segment_id", "result_segment_id",
            "execution_id", "request_id", "thread_id", "turn_id", "item_id",
            "is_blocking", "status",
        )
        for index, part in enumerate(parts):
            if part.get("type") in {"native_turn", "resource_changes", "subagent_run"}:
                source = (self.agent_data.operation_record if part.get("type") in {"native_turn", "resource_changes", "subagent_run"} else self.agent_data.segment)(str(part.get("segment_id") or ""))
                canonical = source.get("content") or {}
                segment_type = "model" if part["type"] == "native_turn" else "artifact"
                if (source.get("created_by_agent_id") != stream_id or source.get("execution_id") != execution_id
                        or source.get("segment_type") != segment_type or canonical.get("type") != part["type"]
                        or any(canonical.get(key) != part.get(key) for key in ("segment_id", "execution_id", "thread_id", "turn_id"))):
                    raise ValueError("turn artifact must reference its own canonical Segment")
                parts[index] = copy.deepcopy(canonical)
                continue
            if part.get("tool_name") not in {"request_user_input", "request_approval", "mcp_elicitation"}:
                continue
            source = (self.agent_data.operation_record if part.get("type") in {"native_turn", "resource_changes", "subagent_run"} else self.agent_data.segment)(str(part.get("segment_id") or ""))
            canonical = source.get("content")
            if (
                not execution_id
                or source.get("segment_type") != "tool"
                or source.get("created_by_agent_id") != stream_id
                or source.get("execution_id") != execution_id
                or part.get("execution_id") != execution_id
                or part.get("type") not in {"tool_call", "tool_result"}
                or not isinstance(canonical, Mapping)
                or not canonical.get("request_id")
                or any(canonical.get(key) != part.get(key) for key in identity_fields)
            ):
                raise ValueError("tool projection must reference its own canonical Execution Segment")
            parts[index] = copy.deepcopy(dict(canonical))
        return parts

    def append_message(
        self,
        resident_id: object,
        message: Mapping[str, Any],
        *,
        activated_at: str = "",
    ) -> dict[str, Any]:
        """Deliver a message: put it at the end of the Resident's trace, and so of its Chat.

        A message that waited in the Mailbox leaves it; one already delivered
        returns its current version. A Mailbox group consumes its member
        occurrences and keeps their ancestry outside the current trace.
        """

        stream_id = _resident_id(resident_id)
        public_input = protocol_message(message)
        public_input.pop("activated_at", None)
        message_id = str(public_input["message_id"])

        def check(stored: Mapping[str, Any] | None) -> None:
            if stored is not None:
                self._validate_stored_message_identity(stored, public_input)

        # Fully committed replays are reads, not another protocol transaction.
        # A message without its durable event still enters the single writer
        # below, so recovery of a partially committed append is unchanged.
        existing = self.agent_data.chat_message(stream_id, message_id)
        if (existing is not None
                and str(message.get("execution_id") or "")
                and existing.get("execution_id") == message.get("execution_id")
                and str(existing.get("segment_id") or "") != chat_segment_id(stream_id, message_id)
                and ((public_input["role"] == "agent" and existing.get("author_id") == stream_id)
                     or (public_input["role"] == "system" and existing.get("harness_input")
                         and existing.get("harness_input") == message.get("harness_input")))
                and self.agent_data.has_segment(str(existing["segment_id"]))):
            # Execution's display event announces already collected content.
            # Use that current version, including any Harness transformation.
            public_input = protocol_message(_message_from_storage(existing))
            public_input.pop("activated_at", None)
        check(existing)
        if existing is not None:
            with self.state_store.database.read() as connection:
                if self.events.event_exists(connection, stream_id, MESSAGE_APPENDED, message_id):
                    # A requeued message that ran again leaves the Mailbox now.
                    waiting = self.state_store.mailbox_entry(stream_id, message_id)
                    if waiting is not None and waiting["place"] == "pending":
                        self.state_store.mailbox_forget(stream_id, [message_id], places=("pending",))
                    return _message_from_storage(existing)

        with self.events.protocol_scope(stream_id):
            waiting = self.state_store.mailbox_entry(stream_id, message_id)
            waiting_message = (waiting or {}).get("message") or {}
            check(waiting_message if waiting_message.get("content_parts") is not None else None)
            # What the Mailbox kept (a business reference) stays with the message.
            payload = _message_for_storage(
                public_input, private_fields={**waiting_message, **dict(message)},
            )
            members = self._group_members(stream_id, payload)
            with self.agent_data.database.transaction():
                for member in members:
                    self.agent_data.project_chat_message(stream_id, member, activated_at=activated_at)
                persisted, _changed = self.agent_data.project_chat_message(
                    stream_id,
                    payload,
                    activated_at=activated_at,
                )
                if members:
                    self.agent_data.merge_message_group(
                        stream_id, [member["message_id"] for member in members], persisted["segment_id"],
                    )
            public = _message_from_storage(persisted)

            def write(connection: Any) -> None:
                self.events.record_event(
                    connection,
                    stream_id,
                    MESSAGE_APPENDED,
                    {"message": _message_header(public)},
                    subject_id=message_id,
                    once=True,
                )
                self.state_store.mailbox_forget(
                    stream_id, [message_id, *(str(item["message_id"]) for item in members)],
                    connection=connection,
                )

            self.state_store.database.write(write)
        return public

    def _group_members(self, resident_id: str, message: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The members a Mailbox group message delivers, each marked with its group."""

        group_id = str(message.get("message_id") or "")
        member_ids = [
            str(member.get("message_id") or "")
            for part in message.get("content_parts") or []
            if isinstance(part, Mapping) and part.get("type") == "mailbox"
            for member in part.get("members") or []
            if isinstance(member, Mapping) and member.get("message_id")
        ]
        if not member_ids or message.get("role") != "user":
            return []
        waiting = {
            entry["message_id"]: entry["message"]
            for entry in self.state_store.mailbox_entries([resident_id], message_ids=member_ids)
        }
        members: list[dict[str, Any]] = []
        for member_id in member_ids:
            stored = waiting.get(member_id)
            if not isinstance(stored, Mapping) or not isinstance(stored.get("content_parts"), list):
                stored = (self.agent_data.chat_message(resident_id, member_id)
                          or self.agent_data.group_source_message(resident_id, member_id))
                if stored is None:
                    raise ValueError(f"Mailbox group {group_id} lost its member {member_id}")
            members.append(_message_for_storage(
                {key: value for key, value in stored.items() if key != "activated_at"},
                private_fields={**stored, "grouped_into_message_id": group_id},
            ))
        return members

    def tool_status_messages(self, messages: list[dict[str, Any]], *, resident_id: str) -> list[dict[str, Any]]:
        for message in messages:
            if message.get("role") != "agent":
                continue
            for part in message.get("content_parts") or []:
                if part.get("type") != "tool_call" or part.get("tool_name") not in {"request_user_input", "request_approval", "mcp_elicitation"}:
                    continue
                source = (self.agent_data.operation_record if part.get("type") in {"native_turn", "resource_changes", "subagent_run"} else self.agent_data.segment)(str(part.get("segment_id") or ""))
                if source.get("created_by_agent_id") != resident_id:
                    part["status"] = "expired"
                    continue
                try:
                    result = self.agent_data.segment(str(part.get("result_segment_id") or ""))
                except KeyError:
                    result = {}
                content = result.get("content") or {}
                if content:
                    part["status"] = content.get("status", "expired")
                    part["answers"] = content.get("answers", {})
                else:
                    execution = self.agent_data.execution(str(part.get("execution_id") or "")) or {}
                    turn_id = str(execution.get("provider_turn_id") or "")
                    if execution.get("status") in {"completed", "failed", "cancelled"} or (turn_id and turn_id != part.get("turn_id")):
                        part["status"] = "expired"
        return messages

    def message(self, resident_id: object, message_id: object) -> dict[str, Any] | None:
        stored = self.stored_message(resident_id, message_id)
        return _message_from_storage(stored) if stored is not None else None

    def stored_message(self, resident_id: object, message_id: object) -> dict[str, Any] | None:
        """A message of the Resident's Chat, or one waiting in its Mailbox."""

        stream_id = _resident_id(resident_id)
        target = str(message_id or "").strip()
        if not target:
            return None
        shown = self.agent_data.chat_message(stream_id, target)
        if shown is not None:
            return shown
        waiting = self.state_store.mailbox_entry(stream_id, target)
        stored = (waiting or {}).get("message") or {}
        if isinstance(stored.get("content_parts"), list):
            return stored
        return self.agent_data.group_source_message(stream_id, target)

    def waiting(self, resident_id: object, message_id: object) -> bool:
        """Whether the message waits in the Resident's Mailbox (accepted, not delivered)."""

        return self.state_store.mailbox_entry(_resident_id(resident_id), str(message_id or "").strip()) is not None

    def message_business_ref(self, resident_id: object, message_id: object) -> str:
        """The business record a delivered Message belongs to; empty when none was given."""

        stored = self.stored_message(resident_id, message_id)
        return str((stored or {}).get("business_ref") or "").strip()

    def public_message(
        self,
        resident_id: object,
        message_id: object,
    ) -> dict[str, Any]:
        stored = self.message(resident_id, message_id)
        if stored is None:
            raise KeyError(str(message_id or ""))
        return stored

    def artifact(
        self,
        resident_id: object,
        artifact_id: object,
    ) -> dict[str, Any]:
        target = str(artifact_id or "").strip()
        suffix = ":provider_response"
        if not target.endswith(suffix):
            raise KeyError(target)
        message_id = target[:-len(suffix)]
        stored = self.stored_message(resident_id, message_id)
        response = stored.get("provider_response") if stored else None
        if not isinstance(response, Mapping):
            raise KeyError(target)
        return {
            "artifact_id": target,
            "media_type": "application/json",
            "value": copy.deepcopy(dict(response)),
        }

    def subagent_run(
        self,
        resident_id: object,
        trace_ref: object,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        """The steps of one subagent run of the Resident, from the run's own trace (its key ``trace_ref``)."""

        from management_common.provider_subagent_runs import trace_steps

        key = str(trace_ref or "").strip()
        if not key:
            raise KeyError(key)
        child_id = self.agent_data.subagent_id(_resident_id(resident_id), key)
        sequence_id = self.agent_data.trace_sequence_id(child_id) if self.agent_data.agent(child_id) else ""
        if not sequence_id:
            raise KeyError(key)
        page = self.agent_data.read_sequence(sequence_id, offset=offset, limit=limit)
        return {
            "trace_ref": key,
            "total": int(page.get("total") or 0),
            "offset": int(page.get("offset") or 0),
            "updated_at": str(page.get("updated_at") or ""),
            "steps": trace_steps(page.get("segments") or [], offset=int(page.get("offset") or 0)),
        }

    def update_message(
        self,
        resident_id: object,
        message_id: object,
        *,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Replace a delivered message where it stands in the trace by a new version of it."""

        stream_id = _resident_id(resident_id)
        target = str(message_id or "").strip()
        if not target:
            raise ValueError("Conversation message update requires message_id")
        with self.events.protocol_scope(stream_id):
            replacement = protocol_message(message or {})
            if _message_id(replacement) != target:
                raise ValueError("Conversation message update identity changed")
            stored = self.agent_data.chat_message(stream_id, target) or {}
            # Display fields come from the new version; other private fields stay.
            fields = {**stored, **{key: message[key] for key in DISPLAY_FIELDS if message.get(key)}}
            persisted = self.agent_data.update_chat_message(
                stream_id,
                target,
                _message_for_storage(replacement, private_fields=fields),
            )
            public = _message_from_storage(persisted)
            event = self.state_store.database.write(
                lambda connection: self.events.record_event(
                    connection,
                    stream_id,
                    MESSAGE_UPDATED,
                    {"message": _message_header(public)},
                    subject_id=target,
                )
            )
        return {**event, "payload": {"message": public}} if event else {}


class ConversationInputs:
    """Where each input stands, its final replies and the question sets that wait for an answer."""

    def __init__(
        self,
        state_store: LineageDataStore,
        agent_data: AgentDataStore,
        events: ConversationEvents,
        messages: ConversationMessages,
    ) -> None:
        self.state_store = state_store
        self.agent_data = agent_data
        self.events = events
        self.messages = messages

    def question_set_state(self, resident_id: object, message_id: object) -> str:
        """``answered``, ``closed`` or ``open``: the state of the question set an Agent message asks.

        A set is answered once its answer message exists and stays open until
        then: later messages, a person's included, and later question sets
        leave it answerable.  A person may close it unanswered instead; its
        answer slot then holds a cancelled input state.  The Chat derives the
        same state.
        """

        stream_id = _resident_id(resident_id)
        source_id = str(message_id or "").strip()
        questions = question_set(self.messages.message(stream_id, source_id))
        if questions is None or self.agent_data.chat_message_ordinal(stream_id, source_id) is None:
            raise ValueError("该 Resident 中的问题不存在或已不可用")
        answer_id = questions["answer_message_id"]
        if self.messages.stored_message(stream_id, answer_id) is not None:
            return "answered"
        try:
            slot = self.input_state(stream_id, answer_id)
        except KeyError:
            return "open"
        return "closed" if (slot.get("error") or {}).get("code") == QUESTION_SET_CLOSED else "open"

    def close_question_set(self, resident_id: object, message_id: object, *, actor: str) -> str:
        """Close an open question set unanswered; nothing is sent to the Resident."""

        state = self.question_set_state(resident_id, message_id)
        if state == "answered":
            raise ValueError("这组问题已回答")
        if state == "open":
            questions = question_set(self.messages.message(resident_id, message_id))
            self.set_input_state(resident_id, questions["answer_message_id"], "cancelled", error={
                "code": QUESTION_SET_CLOSED, "message": "这组问题已关闭，不再回答", "closed_by": str(actor or ""),
            })
        return "closed"

    def set_input_state(
        self,
        resident_id: object,
        message_id: object,
        state: str,
        *,
        error: Mapping[str, Any] | None = None,
        updated_at: str = "",
    ) -> dict[str, Any]:
        """Record where an input stands.  Its replies are not given: they are
        the trace's messages answering it (``reply_to_message_id``)."""

        stream_id = _resident_id(resident_id)
        target = str(message_id or "").strip()
        clean_state = str(state or "").strip().casefold()
        if not target:
            raise ValueError("Input State requires message_id")
        if clean_state not in INPUT_STATES:
            raise ValueError(f"Unsupported Input State: {clean_state}")

        def prepare_update(current: Any) -> tuple[dict[str, Any], bool]:
            previous = _input_state_value(current) if current is not None else None
            stored = copy.deepcopy(previous)
            if previous is not None:
                self.with_outcomes_many({stream_id: [previous]})
            existing_error = dict((previous or {}).get("error") or {})
            error_value = dict(error) if error is not None else existing_error
            value = {
                "message_id": target,
                "state": clean_state,
                "updated_at": iso_time(instant(updated_at or utc_now())),
            }
            if error_value:
                value["error"] = error_value
            if previous is not None:
                if (
                    previous["state"] == value["state"]
                    and existing_error == error_value
                    and not updated_at
                ):
                    return previous, previous != stored
                if previous == value:
                    return value, previous != stored
                if (
                    previous["state"] in TERMINAL_INPUT_STATES
                    and previous["state"] != clean_state
                ):
                    # Execution snapshots and cancellation acknowledgements may
                    # arrive out of order. The first terminal state is durable;
                    # a stale projection must not break later message/activity
                    # events or reopen the input.
                    return previous, False
            return value, True

        def write(connection: Any) -> dict[str, Any]:
            self.events.ensure_conversation(connection, stream_id)
            current = connection.execute(
                'SELECT * FROM conversation_input_states WHERE resident_id = %s AND message_id = %s',
                (stream_id, target),
            ).fetchone()
            value, changed = prepare_update(current)
            if not changed:
                return value
            connection.execute(
                'INSERT INTO conversation_input_states(resident_id, message_id, state, error_json, updated_at) '
                'VALUES (%s, %s, %s, %s, %s) ON CONFLICT(resident_id, message_id) DO UPDATE SET '
                'state=excluded.state, error_json=excluded.error_json, updated_at=excluded.updated_at',
                (stream_id, target, clean_state, _json(value.get("error", {})), value["updated_at"]),
            )
            if clean_state in TERMINAL_INPUT_STATES:
                # Usage is read from the now-settled Execution. The selected
                # reply may already have streamed before continuation ended;
                # publish its header again so live readers rehydrate the footer.
                replies = self.agent_data.chat_final_replies_many({stream_id: [target]})
                for reply_id in _final_reply(clean_state, replies.get(stream_id, {}).get(target)):
                    self.events.record_event(connection, stream_id, MESSAGE_UPDATED,
                                       {"message": {"message_id": reply_id}}, subject_id=reply_id)
            self.events.record_event(
                connection,
                stream_id,
                INPUT_STATE_CHANGED,
                {"input_state": value},
                subject_id=target,
            )
            if clean_state in TERMINAL_INPUT_STATES:
                # An input that ended undelivered (withdrawn, stopped) leaves the Mailbox.
                self.state_store.mailbox_forget(stream_id, [target], places=("pending",), connection=connection)
                # Temporary credentials lent with this message last as long as its input (decision_b820faf42218 §5.6).
                from management_portal import permission_system

                permission_system.end_task_credentials(connection, stream_id, target)
            return value

        with self.events.protocol_scope(stream_id):
            with self.state_store.database.read_current() as connection:
                current = connection.execute(
                    'SELECT inputs.* FROM conversation_input_states AS inputs JOIN conversation_states AS conversations USING (resident_id) WHERE inputs.resident_id = %s AND inputs.message_id = %s',
                    (stream_id, target),
                ).fetchone()
            value, changed = prepare_update(current) if current is not None else ({}, True)
            if changed:
                value = self.state_store.database.write(write)
        return self._with_replies(stream_id, [value])[0]

    def publish_archived_input_state(self, resident_id: str, message_id: str) -> None:
        """Finish the input projection after its terminal turn has been archived.

        The receipt lookup and commit share the Conversation scope with requeue;
        a reopened input or new active turn must retain its current state.
        """
        with self.events.protocol_scope(resident_id):
            try:
                value = self.input_state(resident_id, message_id)
            except KeyError:
                return
            if value["state"] in TERMINAL_INPUT_STATES:
                self.set_input_state(resident_id, message_id, value["state"],
                                     error=value.get("error", {}), updated_at=value["updated_at"])

    def _with_replies(self, resident_id: str, values: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Give each input state its replies: the final reply of a finished input, from the trace."""

        self._with_replies_many({resident_id: values})
        return list(values)

    def with_outcomes_many(self, values_by_resident: Mapping[str, Sequence[dict[str, Any]]]) -> None:
        terminal = self.state_store.terminal_input_receipts([
            (resident_id, str(value['message_id']))
            for resident_id, values in values_by_resident.items() for value in values
            if value.get('state') in {'queued', 'running'}
        ])
        for resident_id, values in values_by_resident.items():
            for value in values:
                receipt = terminal.get((resident_id, str(value['message_id'])), {})
                if receipt.get('outcome') in TERMINAL_INPUT_STATES:
                    value.update(state=receipt['outcome'], updated_at=receipt['finished_at'])
                    if receipt['outcome'] == 'failed':
                        value['error'] = dict(receipt.get('runtime_error') or value.get('error') or {
                            'code': 'execution_failed', 'message': receipt.get('error') or 'Resident execution failed',
                        })

    def _with_replies_many(self, values_by_resident: Mapping[str, Sequence[dict[str, Any]]]) -> None:
        self.with_outcomes_many(values_by_resident)
        self._fill_replies_many(values_by_resident)

    def _fill_replies_many(self, values_by_resident: Mapping[str, Sequence[dict[str, Any]]]) -> None:
        finished = {
            resident_id: [str(value["message_id"]) for value in values if value.get("state") in TERMINAL_INPUT_STATES]
            for resident_id, values in values_by_resident.items()
        }
        replies = self.agent_data.chat_final_replies_many(
            {resident_id: ids for resident_id, ids in finished.items() if ids},
        )
        for resident_id, values in values_by_resident.items():
            for value in values:
                value["reply_message_ids"] = _final_reply(
                    value.get("state"), replies.get(resident_id, {}).get(str(value["message_id"])),
                )

    def reopen_input(self, resident_id: object, message_id: object) -> dict[str, Any]:
        """Put a finished input back to ``queued`` for a Mailbox requeue (decision_b5d349190743).

        Every other writer keeps an input's first terminal state (see
        ``set_input_state``); only putting a stopped or failed round back
        reopens one.  The reopened input has no error, and the Conversation
        keeps the notices of the round that ended.
        """

        stream_id = _resident_id(resident_id)
        target = str(message_id or "").strip()
        if not target:
            raise ValueError("Input State requires message_id")

        def write(connection: Any) -> dict[str, Any]:
            self.events.ensure_conversation(connection, stream_id)
            current = connection.execute(
                'SELECT state, updated_at FROM conversation_input_states WHERE resident_id = %s AND message_id = %s',
                (stream_id, target),
            ).fetchone()
            if current is None:
                raise KeyError(target)
            observed = {"message_id": target, "state": str(current["state"]),
                        "updated_at": iso_time(current["updated_at"])}
            self.with_outcomes_many({stream_id: [observed]})
            if observed["state"] == "queued":
                return observed
            if observed["state"] not in TERMINAL_INPUT_STATES:
                raise ValueError(f"Input {target} is {current['state']}, not finished")
            # A requeue supersedes the receipt even within the same clock tick.
            reopened_at = max(datetime.fromisoformat(utc_now().replace("Z", "+00:00")),
                              datetime.fromisoformat(observed["updated_at"].replace("Z", "+00:00")) + timedelta(milliseconds=1))
            value = {"message_id": target, "state": "queued",
                     "updated_at": iso_time(reopened_at)}
            connection.execute(
                'UPDATE conversation_input_states SET state = %s, error_json = %s, '
                'updated_at = %s WHERE resident_id = %s AND message_id = %s',
                ("queued", _json({}), value["updated_at"], stream_id, target),
            )
            self.events.record_event(connection, stream_id, INPUT_STATE_CHANGED, {"input_state": value}, subject_id=target)
            return value

        with self.events.protocol_scope(stream_id):
            value = self.state_store.database.write(write)
        return self._with_replies(stream_id, [value])[0]

    def record_accepted_input(self, connection, resident_id: str, message_id: str) -> None:
        """Commit the initial receipt/event with admission, without reading projections."""
        connection.execute("INSERT INTO conversation_states(resident_id) VALUES (%s) ON CONFLICT DO NOTHING", (resident_id,))
        row = connection.execute(
            "INSERT INTO conversation_input_states(resident_id,message_id,state,updated_at) "
            "VALUES (%s,%s,'queued',%s) ON CONFLICT DO NOTHING RETURNING updated_at",
            (resident_id, message_id, utc_now()),
        ).fetchone()
        if row is not None:
            self.events.record_event(connection, resident_id, INPUT_STATE_CHANGED,
                               {"input_state": {"message_id": message_id, "state": "queued", "updated_at": iso_time(row["updated_at"])}},
                               subject_id=message_id)

    def input_state(self, resident_id: object, message_id: object) -> dict[str, Any]:
        key = (_resident_id(resident_id), str(message_id or "").strip())
        found = self.input_states_by_id([key])
        if key not in found:
            raise KeyError(key[1])
        return found[key]

    def input_states_by_id(
        self, requests: Sequence[tuple[str, str]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Resolve the requested input states and terminal receipts in batches."""
        requested = list(dict.fromkeys(requests))
        if not requested:
            return {}
        with self.state_store.database.read() as connection:
            rows = connection.execute(
                "SELECT state.* FROM conversation_input_states state "
                "JOIN unnest(%s::text[],%s::text[]) requested(resident_id,message_id) "
                "USING(resident_id,message_id)",
                ([resident for resident, _ in requested], [message for _, message in requested]),
            ).fetchall()
        values: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            values.setdefault(str(row["resident_id"]), []).append(_input_state_value(row))
        self._with_replies_many(values)
        return {(resident, value["message_id"]): value for resident, items in values.items() for value in items}

    def reply_rounds(
        self, requests: Sequence[tuple[str, str]], *, limit: int = 20,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Selected rounds, retained replies, and cancellation before delivery (withdrawal)."""
        requested = list(dict.fromkeys(requests))
        states = self.input_states_by_id(requested)
        windows: dict[str, dict[str, str]] = {}
        final_ids: dict[tuple[str, str], list[str]] = {}
        for resident, message in requested:
            state = states.get((resident, message), {})
            final = list(state.get("reply_message_ids") or []) if state.get("state") in TERMINAL_INPUT_STATES else []
            final_ids[(resident, message)] = final
            windows.setdefault(resident, {})[message] = final[-1] if final else ""
        replies = self.agent_data.chat_replies_many(windows, limit=limit)
        missing: dict[str, list[str]] = {}
        for key, final in final_ids.items():
            resident, message = key
            shown = {item["message_id"] for item in replies.get(resident, {}).get(message, [])}
            missing.setdefault(resident, []).extend(identity for identity in final[:limit] if identity not in shown)
            if states.get(key, {}).get("state") == "cancelled":
                # Pending inputs disappear when withdrawn. Check the durable
                # trace, not today's Chat: clearing Chat must not turn a
                # stopped execution into a withdrawal.
                missing[resident].append(message)
        # A context reset hides old replies from Chat, but a finished Family
        # round still names its retained ending in the member's trace.
        retained = {resident: self.agent_data.trace_chat_messages(resident, ids)
                    for resident, ids in missing.items() if ids}
        result = {}
        for key in requested:
            resident, message = key
            outputs = list(replies.get(resident, {}).get(message, []))
            shown = {item["message_id"] for item in outputs}
            outputs.extend(retained[resident][identity] for identity in final_ids[key][:limit]
                           if identity not in shown and identity in retained.get(resident, {}))
            result[key] = {"input_state": states.get(key, {}),
                           "replies": [_message_from_storage(item) for item in outputs]}
            if (states.get(key, {}).get("state") == "cancelled"
                    and message not in retained.get(resident, {}) and not outputs):
                result[key]["withdrawn"] = True
        return result

    def input_states(
        self,
        resident_id: object,
        *,
        states: Sequence[str] = (),
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        stream_id = _resident_id(resident_id)
        requested_states = tuple(dict.fromkeys(
            str(item or "").strip().lower()
            for item in states
            if str(item or "").strip()
        ))
        if any(item not in INPUT_STATES for item in requested_states):
            raise ValueError("Invalid Resident input state")
        safe_limit = max(0, int(limit or 0))
        # Pending observations can have an archived terminal round. Resolve all
        # pending candidates before filtering; bound the already-terminal branch
        # independently so a limit cannot hide a newer recovered outcome.
        terminal_states = sorted(set(requested_states or INPUT_STATES) & TERMINAL_INPUT_STATES)
        statement = "SELECT * FROM conversation_input_states WHERE resident_id = %s AND state IN ('queued','running')"
        parameters: list[object] = [stream_id]
        if terminal_states:
            terminal_query = (
                "SELECT * FROM conversation_input_states WHERE resident_id = %s "
                "AND state = ANY(%s) ORDER BY updated_at DESC, message_id DESC"
            )
            parameters.extend([stream_id, terminal_states])
            if safe_limit:
                terminal_query += " LIMIT %s"
                parameters.append(safe_limit)
            statement = "(" + statement + ") UNION ALL (" + terminal_query + ")"
        with self.state_store.database.read() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        values = [_input_state_value(row) for row in rows]
        self.with_outcomes_many({stream_id: values})
        values = [value for value in values if not requested_states or value["state"] in requested_states]
        values.sort(key=lambda value: (value["updated_at"], value["message_id"]), reverse=True)
        if safe_limit:
            values = values[:safe_limit]
        self._fill_replies_many({stream_id: values})
        return values

    def latest_notifiable_input_states_for_messages(
        self,
        message_ids_by_resident: Mapping[object, object],
    ) -> dict[str, dict[str, Any]]:
        """Read the latest reply or failure outcome, excluding control receipts."""

        requested = {
            _resident_id(resident_id): tuple(dict.fromkeys(
                str(message_id or "").strip()
                for message_id in (
                    message_ids
                    if isinstance(message_ids, (list, tuple, set, frozenset))
                    else [message_ids]
                )
                if str(message_id or "").strip()
            ))
            for resident_id, message_ids in message_ids_by_resident.items()
            if str(resident_id or "").strip()
        }
        requested = {key: value for key, value in requested.items() if value}
        if not requested:
            return {}
        pairs = [
            (resident_id, message_id)
            for resident_id, message_ids in requested.items()
            for message_id in message_ids
        ]
        # One join over the requested pairs: a user's Mailbox asks for tens of
        # thousands, and chunks of OR-ed pairs cost a planned statement each.
        with self.state_store.database.read() as connection:
            rows = connection.execute(
                "SELECT stored.* FROM unnest(%s::text[], %s::text[]) AS requested(resident_id, message_id) "
                "JOIN conversation_input_states stored USING (resident_id, message_id)",
                ([resident_id for resident_id, _ in pairs], [message_id for _, message_id in pairs]),
            ).fetchall()
        values_by_resident: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            values_by_resident.setdefault(str(row["resident_id"]), []).append(_input_state_value(row))
        self.with_outcomes_many(values_by_resident)
        values_by_resident = {
            resident_id: [value for value in values if value["state"] in TERMINAL_INPUT_STATES]
            for resident_id, values in values_by_resident.items()
        }
        values_by_resident = {resident_id: values for resident_id, values in values_by_resident.items() if values}
        # Start at the newest candidate. If it is only a control receipt, widen
        # the bounded window so long stretches of receipts do not cause one
        # database round trip per historical input. Stop each Resident at its
        # newest real outcome; ordinary conversations still resolve one input.
        # Each round costs a few fixed queries, so the window grows fast:
        # scheduled Residents and facades end many inputs with receipts only.
        for values in values_by_resident.values():
            values.sort(key=lambda value: (str(value["updated_at"]), str(value["message_id"])))
        result: dict[str, dict[str, Any]] = {}
        batch_size = 1
        while values_by_resident:
            batch = {resident_id: [values.pop() for _ in range(min(batch_size, len(values)))]
                     for resident_id, values in values_by_resident.items()}
            self._fill_replies_many(batch)
            reply_ids = list(dict.fromkeys(
                str(reply_id) for values in batch.values() for value in values
                if value["state"] == "completed" for reply_id in value["reply_message_ids"]
            ))
            agent_replies = {
                (header["agent_id"], header["message_id"]): header["reply_to_message_id"]
                for header in self.agent_data.chat_message_headers(list(batch), message_ids=reply_ids)
                if header["role"] == "agent" and header["has_content"]
            } if reply_ids else {}
            for resident_id, values in batch.items():
                for value in values:
                    if value["state"] == "completed":
                        value["reply_message_ids"] = [
                            reply_id for reply_id in value["reply_message_ids"]
                            if agent_replies.get((resident_id, reply_id)) == value["message_id"]
                        ]
                    if value["state"] != "completed" or value["reply_message_ids"]:
                        result[resident_id] = value
                        break
                if resident_id in result or not values_by_resident[resident_id]:
                    del values_by_resident[resident_id]
            batch_size = min(batch_size * 8, 64)
        return result

    def input_states_for_snapshot(
        self,
        connection: Any,
        resident_id: str,
        message_ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        requested = list(dict.fromkeys(message_ids))
        active_states = sorted(INPUT_STATES - TERMINAL_INPUT_STATES)
        terminal_states = sorted(TERMINAL_INPUT_STATES)
        # Active inputs are needed even when their messages are off-page.
        # Seek them by state; do not read every historical terminal payload.
        rows = connection.execute(
            "SELECT * FROM conversation_input_states WHERE resident_id = %s "
            f"AND state IN ({','.join('%s' for _ in active_states)})",
            (resident_id, *active_states),
        ).fetchall()
        for offset in range(0, len(requested), 500):
            batch = requested[offset:offset + 500]
            rows.extend(connection.execute(
                "SELECT * FROM conversation_input_states WHERE resident_id = %s "
                f"AND message_id IN ({','.join('%s' for _ in batch)}) "
                f"AND state IN ({','.join('%s' for _ in terminal_states)})",
                (resident_id, *batch, *terminal_states),
            ).fetchall())
        rows.sort(key=lambda row: (row["updated_at"], str(row["message_id"])))
        return self._with_replies(resident_id, [_input_state_value(row) for row in rows])


class ConversationProjection:
    """Each Resident's current work state, activities and queue, and the snapshots that show them with recent messages."""

    def __init__(
        self,
        state_store: LineageDataStore,
        agent_data: AgentDataStore,
        events: ConversationEvents,
        messages: ConversationMessages,
        inputs: ConversationInputs,
        repository: "ResidentConversationRepository",
    ) -> None:
        self.state_store = state_store
        self.agent_data = agent_data
        self.events = events
        self.messages = messages
        self.inputs = inputs
        self.repository = repository

    def _set_projection(
        self,
        resident_id: object,
        *,
        column: str,
        value: object,
        event_type: str,
        payload_key: str,
    ) -> dict[str, Any] | None:
        stream_id = _resident_id(resident_id)
        encoded = _json(value)
        stored = json_value(encoded)

        def write(connection: Any) -> dict[str, Any] | None:
            self.events.ensure_conversation(connection, stream_id)
            row = connection.execute(
                f"SELECT cursor, {column} FROM conversation_states WHERE resident_id = %s",
                (stream_id,),
            ).fetchone()
            if row is not None and json_value(row[column]) == stored:
                return None
            connection.execute(
                f"UPDATE conversation_states SET {column} = %s WHERE resident_id = %s",
                (encoded, stream_id),
            )
            if event_type in LIVE_EVENT_TYPES:
                # Delivered on commit to every instance's event relay.
                connection.execute(
                    "SELECT pg_notify(%s, current_schema() || ':' || %s)",
                    (LIVE_CHANNEL, stream_id),
                )
                return live_activity_event(stream_id, int(row["cursor"] or 0), value)
            return self.events.record_event(
                connection,
                stream_id,
                event_type,
                {payload_key: copy.deepcopy(value)},
            )

        with self.events.protocol_scope(stream_id):
            # Replayed runtime projections must not queue behind the shared
            # writer while holding this Resident's cancellation/state lock.
            # A changed/missing row still uses the authoritative write check.
            with self.state_store.database.read_current() as connection:
                row = connection.execute(
                    f"SELECT c.{column} FROM residents AS r "
                    "LEFT JOIN conversation_states AS c ON c.resident_id = r.resident_id "
                    "WHERE r.resident_id = %s AND r.deleted_at IS NULL",
                    (stream_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(stream_id)
                if row is not None and json_value(row[column]) == stored:
                    return None
            event = self.state_store.database.write(write)
        return event

    def set_state(self, resident_id: object, state: Mapping[str, Any]) -> dict[str, Any] | None:
        return self._set_projection(
            resident_id,
            column="state_json",
            value=_state(state, strict=True),
            event_type=STATE_CHANGED,
            payload_key="state",
        )

    def set_activities(self, resident_id: object, activities: object) -> dict[str, Any] | None:
        stream_id = _resident_id(resident_id)
        return self._set_projection(
            stream_id,
            column="activity_json",
            value=protocol_activities(stream_id, activities),
            event_type=ACTIVITY_CHANGED,
            payload_key="activities",
        )

    def queues(self, resident_ids: Iterable[object]) -> dict[str, dict[str, Any]]:
        """Each Resident's waiting queue, read from its Mailbox (decision_b5d349190743).

        Nothing keeps a copy of it: the queue is the Mailbox's queue rows, behind
        a Job head that is held.
        """

        from .resident_execution_work import public_queue

        selected = list(dict.fromkeys(_resident_id(item) for item in resident_ids))
        jobs = self.state_store.resident_execution_records(selected)
        return {
            resident_id: protocol_queue(resident_id, public_queue(self.repository, resident_id, jobs.get(resident_id) or {}))
            for resident_id in selected
        }

    def publish_queue(self, resident_id: object) -> dict[str, Any] | None:
        """Record a queue event when the Mailbox's queue is not the one watchers were told."""

        stream_id = _resident_id(resident_id)
        queue = self.queues([stream_id])[stream_id]

        def write(connection: Any) -> dict[str, Any] | None:
            self.events.ensure_conversation(connection, stream_id)
            if self.events.told_queue(connection, stream_id) == queue:
                return None
            return self.events.record_event(connection, stream_id, QUEUE_CHANGED, {"queue": queue})

        with self.events.protocol_scope(stream_id):
            with self.state_store.database.read_current() as connection:
                if self.events.told_queue(connection, stream_id) == queue:
                    return None
            return self.state_store.database.write(write)

    def _current_projection_from_row(
        self,
        resident_id: str,
        row: Any | None,
        queue: Mapping[str, Any],
    ) -> dict[str, Any]:
        if row is None:
            return {
                "resident_id": resident_id,
                "state": _default_state(),
                "activities": [],
                "queue": dict(queue),
                "runtime_settings": self.state_store.read_resident_execution_settings(resident_id) or {},
                "cursor": 0,
            }
        return {
            "resident_id": resident_id,
            "state": _state(
                json_value(row["state_json"]) or _default_state(),
                strict=False,
            ),
            "activities": protocol_activities(
                resident_id,
                json_value(row["activity_json"]),
            ),
            "queue": dict(queue),
            "runtime_settings": self.state_store.read_resident_execution_settings(resident_id) or {},
            "cursor": int(row["cursor"] or 0),
        }

    def current_projection(self, resident_id: object) -> dict[str, Any]:
        stream_id = _resident_id(resident_id)
        with self.state_store.database.read() as connection:
            row = connection.execute(
                'SELECT * FROM conversation_states WHERE resident_id = %s',
                (stream_id,),
            ).fetchone()
            if row is None:
                resident = connection.execute(
                    'SELECT 1 FROM residents WHERE resident_id = %s',
                    (stream_id,),
                ).fetchone()
                if resident is None:
                    raise KeyError(stream_id)
        return self._current_projection_from_row(stream_id, row, self.queues([stream_id])[stream_id])

    def activities(self, resident_id: object) -> list[dict[str, Any]]:
        """Read only the transient Activity projection, as the snapshot shows it."""

        stream_id = _resident_id(resident_id)
        with self.state_store.database.read() as connection:
            row = connection.execute(
                'SELECT activity_json FROM conversation_states WHERE resident_id = %s',
                (stream_id,),
            ).fetchone()
        if row is None:
            return []
        return protocol_activities(stream_id, json_value(row["activity_json"]))

    def published_state(self, resident_id):
        """Last UI notification, used only to avoid re-publishing its content."""
        with self.state_store.database.read() as connection:
            row = connection.execute('SELECT state_json FROM conversation_states WHERE resident_id=%s', (_resident_id(resident_id),)).fetchone()
        return _state(json_value(row['state_json']), strict=False) if row else _default_state()

    def current_state(self, resident_id):
        stream_id = _resident_id(resident_id)
        values = self.states([stream_id])
        if stream_id not in values:
            raise KeyError(stream_id)
        return values[stream_id]

    def current_projections(
        self,
        resident_ids: Iterable[object],
    ) -> dict[str, dict[str, Any]]:
        """Read complete current projections for multiple Residents atomically."""

        selected = list(dict.fromkeys(
            _resident_id(resident_id)
            for resident_id in resident_ids
        ))
        if not selected:
            return {}
        rows: dict[str, Any] = {}
        existing: set[str] = set()
        with self.state_store.database.read() as connection:
            for offset in range(0, len(selected), 500):
                batch = selected[offset:offset + 500]
                placeholders = ",".join("%s" for _ in batch)
                for row in connection.execute(
                    "SELECT * FROM conversation_states "
                    f"WHERE resident_id IN ({placeholders})",
                    batch,
                ).fetchall():
                    rows[str(row["resident_id"])] = row
                existing.update(
                    str(row["resident_id"])
                    for row in connection.execute(
                        "SELECT resident_id FROM residents "
                        f"WHERE resident_id IN ({placeholders}) AND deleted_at IS NULL",
                        batch,
                    ).fetchall()
                )
        queues = self.queues([resident_id for resident_id in selected if resident_id in existing])
        return {
            resident_id: self._current_projection_from_row(resident_id, rows.get(resident_id), queues[resident_id])
            for resident_id in queues
        }

    def states(self, resident_ids):
        """Derive work from the active turn/queue and intake from the Resident."""
        from . import resident_execution_work, resident_execution_storage
        from .domain_ports import execution_port
        selected = list(dict.fromkeys(_resident_id(item) for item in resident_ids))
        if not selected:
            return {}
        with self.state_store.database.read_transaction() as connection:
            rows = connection.execute('SELECT resident_id,intake_open FROM residents WHERE resident_id=ANY(%s) AND deleted_at IS NULL',(selected,)).fetchall()
            turns = resident_execution_storage.read(connection, selected, content=False)
            queued = {}
            for row in connection.execute(
                "SELECT resident_id,message_id FROM resident_mailbox WHERE resident_id=ANY(%s) AND place='queue' ORDER BY resident_id,position", (selected,),
            ).fetchall():
                queued.setdefault(row['resident_id'], []).append({'user_message_id': row['message_id']})
        requested = {job['execution_id']: job['actor'] for _revision,job in turns.values() if resident_execution_work.submitted_execution_id(job) and job.get('actor') and resident_execution_work.is_active(job) and not resident_execution_work.completion_applied(job) and not job.get('stop_requested_at')}
        snapshots = {}
        entries = list(requested.items())
        for offset in range(0,len(entries),500):
            snapshots.update(execution_port.service().states(dict(entries[offset:offset+500])))
        result = {}
        for row in rows:
            resident_id = row['resident_id']
            job = turns.get(resident_id,(0,{}))[1]
            job['queue'] = queued.get(resident_id,[])
            intake = 'closed' if row['intake_open'] is False else 'open'
            job['intake_state'] = intake
            result[resident_id] = resident_execution_work.conversation_state(job,intake_state=intake,snapshot=snapshots.get(job.get('execution_id'),{}))
        return result

    def state_snapshots(
        self,
        resident_ids: Iterable[object],
    ) -> dict[str, dict[str, Any]]:
        selected = list(dict.fromkeys(
            _resident_id(resident_id)
            for resident_id in resident_ids
        ))
        if not selected:
            return {}
        rows: dict[str, Any] = {}
        latest_input_states: dict[str, dict[str, Any]] = {}
        existing: set[str] = set()
        input_kinds = sorted(INPUT_STATES)
        with self.state_store.database.read() as connection:
            for offset in range(0, len(selected), 500):
                batch = selected[offset:offset + 500]
                placeholders = ",".join("%s" for _ in batch)
                for row in connection.execute(
                    "SELECT resident_id, cursor, state_json FROM conversation_states "
                    f"WHERE resident_id IN ({placeholders})",
                    batch,
                ).fetchall():
                    rows[str(row["resident_id"])] = row
                existing.update(
                    str(row["resident_id"])
                    for row in connection.execute(
                        "SELECT resident_id FROM residents "
                        f"WHERE resident_id IN ({placeholders}) AND deleted_at IS NULL",
                        batch,
                    ).fetchall()
                )
                for input_row in connection.execute(
                    # The (resident, state, updated_at) index yields at most
                    # one candidate per state. LATERAL runs that lookup once
                    # per (resident, state) pair; a correlated subquery joined
                    # back to the table can be re-evaluated for every history
                    # row when the planner has no statistics yet.
                    "WITH requested(resident_id) AS (VALUES "
                    + ",".join('(%s)' for _ in batch)
                    + "), kinds(state) AS (VALUES "
                    + ",".join('(%s)' for _ in input_kinds)
                    + ") SELECT latest.resident_id, latest.message_id, latest.state, latest.updated_at "
                    "FROM requested CROSS JOIN kinds CROSS JOIN LATERAL ("
                    "SELECT candidate.resident_id, candidate.message_id, candidate.state, candidate.updated_at "
                    "FROM conversation_input_states AS candidate "
                    "WHERE candidate.resident_id = requested.resident_id "
                    "AND candidate.state = kinds.state "
                    "ORDER BY candidate.updated_at DESC, candidate.message_id DESC LIMIT 1"
                    ") AS latest",
                    [*batch, *input_kinds],
                ).fetchall():
                    resident_id = str(input_row["resident_id"])
                    value = {
                        "message_id": str(input_row["message_id"]),
                        "state": str(input_row["state"]),
                        "updated_at": iso_time(input_row["updated_at"]),
                    }
                    current = latest_input_states.get(resident_id)
                    if current is None or (
                        value["updated_at"], value["message_id"]
                    ) > (current["updated_at"], current["message_id"]):
                        latest_input_states[resident_id] = value
        self.inputs.with_outcomes_many({resident_id: [value] for resident_id, value in latest_input_states.items()})
        live_states = self.states(selected)
        return {
            resident_id: {
                "resident_id": resident_id,
                "state": (
                    live_states[resident_id]
                ),
                "cursor": (
                    int(rows[resident_id]["cursor"] or 0)
                    if resident_id in rows
                    else 0
                ),
                "latest_input_state": dict(
                    latest_input_states.get(resident_id) or {}
                ),
            }
            for resident_id in selected
            if resident_id in existing
        }

    def _snapshot_cursor(self, resident_id: str) -> int:
        with self.state_store.database.read() as connection:
            row = connection.execute(
                'SELECT cursor FROM conversation_states WHERE resident_id = %s',
                (resident_id,),
            ).fetchone()
        return int(row["cursor"] or 0) if row is not None else 0

    def _snapshot_projection(
        self,
        resident_id: str,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        queue = self.queues([resident_id])[resident_id]
        with self.state_store.database.read() as connection:
            row = connection.execute(
                'SELECT * FROM conversation_states WHERE resident_id = %s',
                (resident_id,),
            ).fetchone()
            if row is None:
                resident = connection.execute(
                    'SELECT 1 FROM residents WHERE resident_id = %s',
                    (resident_id,),
                ).fetchone()
                if resident is None:
                    raise KeyError(resident_id)
                return {
                    "state": _default_state(),
                    "input_states": [],
                    "activities": [],
                    "queue": queue,
                    "runtime_settings": self.state_store.read_resident_execution_settings(resident_id) or {},
                    "cursor": 0,
                }
            return {
                "input_states": self.inputs.input_states_for_snapshot(
                    connection,
                    resident_id,
                    visible_message_input_ids(messages),
                ),
                "state": _state(
                    json_value(row["state_json"]) or _default_state(),
                    strict=False,
                ),
                "activities": protocol_activities(
                    resident_id,
                    json_value(row["activity_json"]),
                ),
                "queue": queue,
                "runtime_settings": self.state_store.read_resident_execution_settings(resident_id) or {},
                "cursor": int(row["cursor"] or 0),
            }

    def snapshot(
        self,
        resident_id: object,
        *,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> dict[str, Any]:
        stream_id = _resident_id(resident_id)
        limit = max(1, min(int(history_limit or DEFAULT_HISTORY_LIMIT), MAX_HISTORY_LIMIT))
        # A page snapshot is a read projection, not a writer command. Read the
        # two stores without the Conversation or Resident locks and retry once
        # when the durable event cursor changes while the page is assembled.
        # Keep the cursor captured BEFORE the message page. On a busy second
        # attempt, the projection may include commits absent from that page;
        # claiming its later cursor would make those messages unrecoverable.
        for attempt in range(2):
            before_cursor = self._snapshot_cursor(stream_id)
            page = self.agent_data.read_chat_message_page(
                stream_id,
                limit=limit,
            )
            selected = page.get("messages") or []
            messages = self.messages.tool_status_messages(
                [_message_from_storage(item) for item in selected],
                resident_id=stream_id,
            )
            projection = self._snapshot_projection(stream_id, messages)
            if projection["cursor"] == before_cursor or attempt:
                break
        return {
            "schema": RESIDENT_CONVERSATION_SCHEMA,
            "resident_id": stream_id,
            "messages": messages,
            "state": projection["state"],
            "input_states": projection["input_states"],
            "activities": projection["activities"],
            "queue": projection["queue"],
            "runtime_settings": projection["runtime_settings"],
            "cursor": before_cursor,
            "next_page_token": (
                str(page.get("next_before_ordinal"))
                if page.get("next_before_ordinal")
                else ""
            ),
        }


class ConversationHistory:
    """Read-only pages and searches of the messages of Resident Conversations."""

    def __init__(
        self,
        state_store: LineageDataStore,
        agent_data: AgentDataStore,
        messages: ConversationMessages,
        inputs: ConversationInputs,
    ) -> None:
        self.state_store = state_store
        self.agent_data = agent_data
        self.messages = messages
        self.inputs = inputs

    def read_messages(self, resident_id: object, *, limit: int = 0) -> list[dict[str, Any]]:
        return [
            _message_from_storage(message)
            for message in self.agent_data.read_chat_messages(
                _resident_id(resident_id),
                limit=limit,
            )
        ]

    def replies(
        self,
        resident_id: object,
        message_id: object,
        *,
        until_message_id: object = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """The Agent messages answering an input, intermediate outputs included, oldest first."""

        return [
            _message_from_storage(message)
            for message in self.agent_data.chat_replies(
                _resident_id(resident_id),
                str(message_id or "").strip(),
                until_message_id=str(until_message_id or "").strip(),
                limit=limit,
            )
        ]

    def latest_message(
        self,
        resident_id: object,
        *,
        role: str = "",
    ) -> dict[str, Any] | None:
        stream_id = _resident_id(resident_id)
        stored = self.agent_data.latest_chat_message(stream_id, role=role)
        if stored is not None and stored.get("grouped_into_message_id"):
            # A group's members come just before it: the group is the newest message.
            stored = self.agent_data.chat_message(stream_id, str(stored["grouped_into_message_id"])) or stored
        return _message_from_storage(stored) if stored is not None else None

    def public_messages(
        self,
        resident_id: object,
        *,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        return self.read_messages(resident_id, limit=limit)

    def messages_before_input(self, resident_id: str, message_id: str, *, limit: int) -> list[dict]:
        """Bound history to a durable input identity and the current context reset."""
        return [_message_from_storage(message) for message in self.agent_data.read_chat_messages_through_turn(
            _resident_id(resident_id), [message_id], limit=limit, include_turn=False,
        )]

    def messages_through_turn(
        self,
        resident_id: object,
        message_ids: Sequence[object],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        return [
            _message_from_storage(message)
            for message in self.agent_data.read_chat_messages_through_turn(
                _resident_id(resident_id),
                message_ids,
                limit=limit,
            )
        ]

    def messages_page(
        self,
        resident_id: object,
        page_token: object,
        *,
        limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> dict[str, Any]:
        stream_id = _resident_id(resident_id)
        safe_limit = max(1, min(int(limit or DEFAULT_HISTORY_LIMIT), MAX_HISTORY_LIMIT))
        try:
            before_ordinal = int(str(page_token or "").strip())
        except ValueError as exc:
            raise ValueError("Invalid Conversation page token") from exc
        if before_ordinal <= 0:
            raise ValueError("Invalid Conversation page token")
        page = self.agent_data.read_chat_message_page(
            stream_id,
            before_ordinal=before_ordinal,
            limit=safe_limit,
        )
        next_boundary = page.get("next_before_ordinal")
        messages = self.messages.tool_status_messages([_message_from_storage(item) for item in page.get("messages") or []], resident_id=stream_id)
        with self.state_store.database.read() as connection:
            input_states = self.inputs.input_states_for_snapshot(
                connection, stream_id, visible_message_input_ids(messages),
            )
        return {
            "resident_id": stream_id,
            "messages": messages,
            "input_states": input_states,
            "next_page_token": str(next_boundary) if next_boundary else "",
        }

    def input_history_page(
        self,
        resident_id: object,
        sender: object,
        page_token: object = "",
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        """What ``sender`` typed in the Resident's conversation, newest first, for the input history."""

        stream_id = _resident_id(resident_id)
        safe_limit = max(1, min(int(limit or 50), MAX_HISTORY_LIMIT))
        token = str(page_token or "").strip()
        before_ordinal = None
        if token:
            try:
                before_ordinal = int(token)
            except ValueError as exc:
                raise ValueError("Invalid input page token") from exc
            if before_ordinal <= 0:
                raise ValueError("Invalid input page token")
        page = self.agent_data.read_input_history(
            stream_id, str(sender or ""), before_ordinal=before_ordinal, limit=safe_limit,
        )
        next_boundary = page.get("next_before_ordinal")
        return {
            "resident_id": stream_id,
            "messages": [_message_from_storage(item) for item in page.get("messages") or []],
            "next_page_token": str(next_boundary) if next_boundary else "",
        }

    def search_messages(
        self,
        resident_ids: Sequence[object],
        query: object,
        *,
        page_token: object = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        requested = [
            _resident_id(item)
            for item in resident_ids
            if str(item or "").strip()
        ]
        needle = str(query or "").strip().casefold()
        if not needle:
            raise ValueError("Conversation search query required")
        safe_limit = max(1, min(int(limit or 50), 200))
        try:
            offset = int(str(page_token or "0").strip() or "0")
        except ValueError as exc:
            raise ValueError("Invalid Conversation search page token") from exc
        if offset < 0:
            raise ValueError("Invalid Conversation search page token")
        matches: list[dict[str, Any]] = []
        def conversations():
            for start in range(0, len(requested), 50):
                batch = requested[start:start + 50]
                histories = self.agent_data.read_chat_messages_many(batch)
                for stream_id in batch:
                    yield stream_id, histories[stream_id]

        for stream_id, messages in conversations():
            for index, stored in enumerate(messages):
                message = _message_from_storage(stored)
                searchable = "\n".join(
                    str(part.get("text") or "")
                    for part in message.get("content_parts") or []
                    if isinstance(part, Mapping) and str(part.get("type") or "") == "text"
                ).casefold()
                if needle not in searchable:
                    continue
                matches.append({
                    "resident_id": stream_id,
                    "message": message,
                    "previous_message_id": (
                        str(messages[index - 1].get("message_id") or "")
                        if index > 0
                        else ""
                    ),
                    "next_message_id": (
                        str(messages[index + 1].get("message_id") or "")
                        if index + 1 < len(messages)
                        else ""
                    ),
                })
        matches.sort(
            key=lambda item: (
                str((item.get("message") or {}).get("created_at") or ""),
                str((item.get("message") or {}).get("message_id") or ""),
            ),
            reverse=True,
        )
        selected = matches[offset:offset + safe_limit]
        next_offset = offset + len(selected)
        return {
            "query": str(query or "").strip(),
            "results": selected,
            "next_page_token": str(next_offset) if next_offset < len(matches) else "",
        }


class ConversationActivity:
    """What one actor did across Resident Conversations: activity times, summaries and change signatures."""

    def __init__(self, state_store: LineageDataStore, agent_data: AgentDataStore) -> None:
        self.state_store = state_store
        self.agent_data = agent_data

    # Aggregates count the Chat (the trace) and the Mailbox: a message that
    # waits there was sent to the Resident as much as one it read.

    def _waiting(
        self, resident_ids: Sequence[str], *, author: str = "", excluded_business_ref: str = "",
    ) -> list[dict[str, Any]]:
        return [
            entry for entry in self.state_store.mailbox_entries(resident_ids, author=author)
            if isinstance(entry["message"].get("content_parts"), list)
            and str(entry["message"].get("role") or "user") == "user"
            and not _business_ref_excluded(entry["message"], excluded_business_ref)
        ]

    def user_message_counts(self, resident_ids: Sequence[object]) -> dict[str, int]:
        requested = [_resident_id(item) for item in resident_ids if str(item or "").strip()]
        counts = self.agent_data.chat_user_counts(requested)
        for entry in self._waiting(requested):
            counts[entry["resident_id"]] = counts.get(entry["resident_id"], 0) + 1
        return counts

    def _outcome_activity(self, actor: str) -> dict[str, datetime]:
        """When the latest outcome of the actor's request happened, by Resident."""
        from management_portal import conversation_receipt_store

        with self.state_store.database.read() as connection:
            times = conversation_receipt_store.outcome_times(connection, actor, "resident:")
        return {key.removeprefix("resident:"): instant(at) for key, at in times.items()}

    def dismissed_activity(self, actor: str) -> dict[str, datetime]:
        """Through when the actor's "清除全部" hides each Resident conversation."""
        from management_portal import conversation_receipt_store

        with self.state_store.database.read() as connection:
            times = conversation_receipt_store.dismissed_times(connection, actor, "resident:")
        return {key.removeprefix("resident:"): instant(at) for key, at in times.items()}

    def _actor_activity(self, actor: str, resident_ids: Sequence[str] | None) -> list[tuple[str, str]]:
        """Each Resident's latest activity with the actor, newest first (ties by Resident id).

        Relayed copies are not their sender's activity (AgentDataStore.chat_actor_activity).
        """

        activity = _instants(self.agent_data.chat_actor_activity(actor, resident_ids))
        scope = None if resident_ids is None else set(resident_ids)
        for entry in self.state_store.mailbox_entries_by_author(actor):
            if scope is not None and entry["resident_id"] not in scope:
                continue
            if not isinstance(entry["message"].get("content_parts"), list) or _business_ref_excluded(
                entry["message"], RELAYED_COPY_PATTERN,
            ):
                continue
            at = _instant_or_none(entry["message"].get("created_at") or entry["accepted_at"])
            if at and (entry["resident_id"] not in activity or at > activity[entry["resident_id"]]):
                activity[entry["resident_id"]] = at
        return [(resident_id, iso_time(at)) for resident_id, at in _newest_first(
            _with_outcomes(activity, self._outcome_activity(actor)),
        )]

    def actor_activity(
        self,
        actor: object,
        resident_ids: Sequence[object] | None = None,
    ) -> dict[str, str]:
        requested = (
            None if resident_ids is None else [
                _resident_id(item) for item in resident_ids
                if str(item or "").strip()
            ]
        )
        actor_id = str(actor or "").strip()
        if not actor_id or requested == []:
            return {}
        return dict(self._actor_activity(actor_id, requested))

    def actor_activity_page(
        self, actor: object, *, limit: int = 50, page_token: str = "",
    ) -> dict[str, Any]:
        return next(self.iter_actor_activity_pages(actor, limit=limit, page_token=page_token))

    def iter_actor_activity_pages(
        self, actor: object, *, limit: int = 50, page_token: str = "",
    ) -> Iterator[dict[str, Any]]:
        """Read ordered candidate batches with a data-source keyset boundary."""
        size = int(limit)
        if not 1 <= size <= 100:
            raise ValueError("Conversation page size must be between 1 and 100")
        after = self.agent_data.chat_activity_page_boundary(page_token)
        actor_id = str(actor or "").strip()
        mailbox = _instants(self.state_store.mailbox_actor_activity(
            actor_id, excluded_business_ref=RELAYED_COPY_PATTERN,
        )) if actor_id else {}
        outcomes = self._outcome_activity(actor_id) if actor_id else {}
        # Residents in Mailbox are merged once using their true latest time.
        # Excluding them from SQL prevents an older trace entry reappearing on
        # the next page after their newer Mailbox entry has been consumed.
        # Outcomes raise the others' activity in SQL itself.
        engaged = dict(mailbox)
        if mailbox:
            for resident_id, at in _instants(self.agent_data.chat_actor_activity(
                actor_id, list(mailbox),
            )).items():
                engaged[resident_id] = max(engaged[resident_id], at)
        engaged = _with_outcomes(engaged, outcomes)
        known = {resident_id: at for resident_id, at in outcomes.items() if resident_id not in mailbox}
        if actor_id:
            # Once for all the pages of this listing.
            self.agent_data.bring_chat_current()
        boundary = None if after is None else (instant(after[0]), after[1])
        while True:
            rows = self.agent_data.chat_actor_activity_page(
                actor_id, after=after, limit=size + 1,
                exclude_agent_ids=tuple(mailbox), known_times=known,
            ) if actor_id else []
            merged = [*((resident_id, instant(at)) for resident_id, at in rows), *(
                (resident_id, at) for resident_id, at in engaged.items()
                if boundary is None or at < boundary[0] or (at == boundary[0] and resident_id > boundary[1])
            )]
            selected = _newest_first(dict(merged))[:size]
            next_token = (
                self.agent_data.chat_activity_page_token(selected[-1][0], iso_time(selected[-1][1]))
                if len(merged) > size else ""
            )
            yield {
                "activity": {resident_id: iso_time(at) for resident_id, at in selected},
                "next_page_token": next_token,
            }
            if not next_token:
                return
            after = (iso_time(selected[-1][1]), selected[-1][0])
            boundary = (selected[-1][1], selected[-1][0])

    def activity_page_token(self, resident_id: str, latest_at: str) -> str:
        return self.agent_data.chat_activity_page_token(resident_id, latest_at)

    def conversation_summaries(
        self,
        resident_ids: Sequence[object],
        actor: object,
        *,
        excluded_business_ref: str = "",
    ) -> dict[str, dict[str, Any]]:
        requested = [
            _resident_id(item)
            for item in resident_ids
            if str(item or "").strip()
        ]
        actor_id = str(actor or "").strip()
        summaries = self.agent_data.chat_conversation_summaries(
            requested,
            actor_id,
            excluded_business_ref=excluded_business_ref,
            finish=False,
        )
        clean_actor = actor_id.lstrip("@＠").strip().casefold()
        for entry in self._waiting(requested, excluded_business_ref=excluded_business_ref):
            summary = summaries.get(entry["resident_id"])
            if summary is None:
                continue
            at = str(entry["message"].get("created_at") or entry["accepted_at"])
            summary["user_message_count"] += 1
            if at > summary["last_user_message_at"]:
                summary["last_user_message_at"] = at
                summary["last_user_actor"] = entry["author_id"]
            if clean_actor and entry["author_id"].lstrip("@＠").strip().casefold() == clean_actor:
                summary["actor_requests"].append((at, entry["message_id"]))
        for summary in summaries.values():
            finish_conversation_summary(summary)
        return summaries

    def signature(self, resident_id: object) -> str:
        return self.signatures([resident_id]).get(_resident_id(resident_id), "")

    def signatures(self, resident_ids: Sequence[object]) -> dict[str, str]:
        """A value that changes whenever a Resident's Chat or Mailbox changes."""

        requested = [
            _resident_id(item)
            for item in resident_ids
            if str(item or "").strip()
        ]
        signatures = self.agent_data.chat_signatures(requested)
        waiting: dict[str, list[list[str]]] = {}
        for entry in self.state_store.mailbox_entries(requested):
            waiting.setdefault(entry["resident_id"], []).append(
                [entry["message_id"], entry["place"], entry["updated_at"]],
            )
        for resident_id, entries in waiting.items():
            signatures[resident_id] = hashlib.sha256(
                canonical_json([signatures.get(resident_id, ""), sorted(entries)]).encode("utf-8")
            ).hexdigest()
        return signatures


class ResidentConversationRepository:
    """Persistence authority for Resident messages, state and ordered events.

    Each responsibility is a component; the repository is their published
    interface and serves every method from the component that owns it.
    """

    def __init__(self, state_store: LineageDataStore, agent_data: AgentDataStore) -> None:
        self.state_store = state_store
        self.agent_data = agent_data
        self.events = ConversationEvents(state_store, agent_data, self)
        self.messages = ConversationMessages(state_store, agent_data, self.events)
        self.inputs = ConversationInputs(state_store, agent_data, self.events, self.messages)
        self.projection = ConversationProjection(state_store, agent_data, self.events, self.messages, self.inputs, self)
        self.history = ConversationHistory(state_store, agent_data, self.messages, self.inputs)
        self.activity = ConversationActivity(state_store, agent_data)

    # Event log

    def execution_settings_changed(
        self,
        resident_id: object,
        settings: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        return self.events.execution_settings_changed(resident_id, settings)

    def context_reset_boundary(self, resident_id: object) -> list[str]:
        return self.events.context_reset_boundary(resident_id)

    def archive_context(
        self, resident_id: object, *, archived_at: str = "",
        message_ids: Sequence[str] | None = None,
    ) -> list[str]:
        return self.events.archive_context(resident_id, archived_at=archived_at, message_ids=message_ids)

    def prune_event_deliveries(self, *, older_than: str, limit: int = 5000) -> int:
        return self.events.prune_event_deliveries(older_than=older_than, limit=limit)

    def events_after(
        self,
        resident_id: object,
        after_cursor: object,
        *,
        limit: int = 500,
    ) -> dict[str, Any]:
        return self.events.events_after(resident_id, after_cursor, limit=limit)

    def hydrate_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.events.hydrate_events(events)

    # Messages

    def commit_user_message(
        self,
        resident_id: object,
        message: Mapping[str, Any],
        *,
        activated_at: str = "",
    ) -> dict[str, Any]:
        return self.messages.commit_user_message(resident_id, message, activated_at=activated_at)

    def accept_user_message(
        self,
        resident_id: object,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.messages.accept_user_message(resident_id, message)

    def validate_message_identity(
        self,
        resident_id: object,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.messages.validate_message_identity(resident_id, message)

    def execution_content_parts(
        self,
        resident_id: object,
        execution_id: str,
        content_parts: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        return self.messages.execution_content_parts(resident_id, execution_id, content_parts)

    def append_message(
        self,
        resident_id: object,
        message: Mapping[str, Any],
        *,
        activated_at: str = "",
    ) -> dict[str, Any]:
        return self.messages.append_message(resident_id, message, activated_at=activated_at)

    def _tool_status_messages(self, messages: list[dict[str, Any]], *, resident_id: str) -> list[dict[str, Any]]:
        return self.messages.tool_status_messages(messages, resident_id=resident_id)

    def message(self, resident_id: object, message_id: object) -> dict[str, Any] | None:
        return self.messages.message(resident_id, message_id)

    def _stored_message(self, resident_id: object, message_id: object) -> dict[str, Any] | None:
        return self.messages.stored_message(resident_id, message_id)

    def waiting(self, resident_id: object, message_id: object) -> bool:
        return self.messages.waiting(resident_id, message_id)

    def message_business_ref(self, resident_id: object, message_id: object) -> str:
        return self.messages.message_business_ref(resident_id, message_id)

    def public_message(
        self,
        resident_id: object,
        message_id: object,
    ) -> dict[str, Any]:
        return self.messages.public_message(resident_id, message_id)

    def artifact(
        self,
        resident_id: object,
        artifact_id: object,
    ) -> dict[str, Any]:
        return self.messages.artifact(resident_id, artifact_id)

    def subagent_run(
        self,
        resident_id: object,
        trace_ref: object,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        return self.messages.subagent_run(resident_id, trace_ref, offset=offset, limit=limit)

    def update_message(
        self,
        resident_id: object,
        message_id: object,
        *,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.messages.update_message(resident_id, message_id, message=message)

    # Inputs and question sets

    def question_set_state(self, resident_id: object, message_id: object) -> str:
        return self.inputs.question_set_state(resident_id, message_id)

    def close_question_set(self, resident_id: object, message_id: object, *, actor: str) -> str:
        return self.inputs.close_question_set(resident_id, message_id, actor=actor)

    def set_input_state(
        self,
        resident_id: object,
        message_id: object,
        state: str,
        *,
        error: Mapping[str, Any] | None = None,
        updated_at: str = "",
    ) -> dict[str, Any]:
        return self.inputs.set_input_state(resident_id, message_id, state, error=error, updated_at=updated_at)

    def publish_archived_input_state(self, resident_id: str, message_id: str) -> None:
        return self.inputs.publish_archived_input_state(resident_id, message_id)

    def reopen_input(self, resident_id: object, message_id: object) -> dict[str, Any]:
        return self.inputs.reopen_input(resident_id, message_id)

    def record_accepted_input(self, connection, resident_id: str, message_id: str) -> None:
        return self.inputs.record_accepted_input(connection, resident_id, message_id)

    def input_state(self, resident_id: object, message_id: object) -> dict[str, Any]:
        return self.inputs.input_state(resident_id, message_id)

    def input_states_by_id(
        self, requests: Sequence[tuple[str, str]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        return self.inputs.input_states_by_id(requests)

    def reply_rounds(
        self, requests: Sequence[tuple[str, str]], *, limit: int = 20,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        return self.inputs.reply_rounds(requests, limit=limit)

    def input_states(
        self,
        resident_id: object,
        *,
        states: Sequence[str] = (),
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        return self.inputs.input_states(resident_id, states=states, limit=limit)

    def latest_notifiable_input_states_for_messages(
        self,
        message_ids_by_resident: Mapping[object, object],
    ) -> dict[str, dict[str, Any]]:
        return self.inputs.latest_notifiable_input_states_for_messages(message_ids_by_resident)

    # Current state and snapshots

    def set_state(self, resident_id: object, state: Mapping[str, Any]) -> dict[str, Any] | None:
        return self.projection.set_state(resident_id, state)

    def set_activities(self, resident_id: object, activities: object) -> dict[str, Any] | None:
        return self.projection.set_activities(resident_id, activities)

    def queues(self, resident_ids: Iterable[object]) -> dict[str, dict[str, Any]]:
        return self.projection.queues(resident_ids)

    def publish_queue(self, resident_id: object) -> dict[str, Any] | None:
        return self.projection.publish_queue(resident_id)

    def current_projection(self, resident_id: object) -> dict[str, Any]:
        return self.projection.current_projection(resident_id)

    def activities(self, resident_id: object) -> list[dict[str, Any]]:
        return self.projection.activities(resident_id)

    def published_state(self, resident_id):
        return self.projection.published_state(resident_id)

    def current_state(self, resident_id):
        return self.projection.current_state(resident_id)

    def current_projections(
        self,
        resident_ids: Iterable[object],
    ) -> dict[str, dict[str, Any]]:
        return self.projection.current_projections(resident_ids)

    def states(self, resident_ids):
        return self.projection.states(resident_ids)

    def state_snapshots(
        self,
        resident_ids: Iterable[object],
    ) -> dict[str, dict[str, Any]]:
        return self.projection.state_snapshots(resident_ids)

    def snapshot(
        self,
        resident_id: object,
        *,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> dict[str, Any]:
        return self.projection.snapshot(resident_id, history_limit=history_limit)

    # History

    def read_messages(self, resident_id: object, *, limit: int = 0) -> list[dict[str, Any]]:
        return self.history.read_messages(resident_id, limit=limit)

    def replies(
        self,
        resident_id: object,
        message_id: object,
        *,
        until_message_id: object = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self.history.replies(resident_id, message_id, until_message_id=until_message_id, limit=limit)

    def latest_message(
        self,
        resident_id: object,
        *,
        role: str = "",
    ) -> dict[str, Any] | None:
        return self.history.latest_message(resident_id, role=role)

    def public_messages(
        self,
        resident_id: object,
        *,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        return self.history.public_messages(resident_id, limit=limit)

    def messages_before_input(self, resident_id: str, message_id: str, *, limit: int) -> list[dict]:
        return self.history.messages_before_input(resident_id, message_id, limit=limit)

    def messages_through_turn(
        self,
        resident_id: object,
        message_ids: Sequence[object],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        return self.history.messages_through_turn(resident_id, message_ids, limit=limit)

    def messages_page(
        self,
        resident_id: object,
        page_token: object,
        *,
        limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> dict[str, Any]:
        return self.history.messages_page(resident_id, page_token, limit=limit)

    def input_history_page(
        self,
        resident_id: object,
        sender: object,
        page_token: object = "",
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        return self.history.input_history_page(resident_id, sender, page_token, limit=limit)

    def search_messages(
        self,
        resident_ids: Sequence[object],
        query: object,
        *,
        page_token: object = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return self.history.search_messages(resident_ids, query, page_token=page_token, limit=limit)

    # Activity across Conversations

    def user_message_counts(self, resident_ids: Sequence[object]) -> dict[str, int]:
        return self.activity.user_message_counts(resident_ids)

    def dismissed_activity(self, actor: str) -> dict[str, datetime]:
        return self.activity.dismissed_activity(actor)

    def actor_activity(
        self,
        actor: object,
        resident_ids: Sequence[object] | None = None,
    ) -> dict[str, str]:
        return self.activity.actor_activity(actor, resident_ids)

    def actor_activity_page(
        self, actor: object, *, limit: int = 50, page_token: str = "",
    ) -> dict[str, Any]:
        return self.activity.actor_activity_page(actor, limit=limit, page_token=page_token)

    def iter_actor_activity_pages(
        self, actor: object, *, limit: int = 50, page_token: str = "",
    ) -> Iterator[dict[str, Any]]:
        return self.activity.iter_actor_activity_pages(actor, limit=limit, page_token=page_token)

    def activity_page_token(self, resident_id: str, latest_at: str) -> str:
        return self.activity.activity_page_token(resident_id, latest_at)

    def conversation_summaries(
        self,
        resident_ids: Sequence[object],
        actor: object,
        *,
        excluded_business_ref: str = "",
    ) -> dict[str, dict[str, Any]]:
        return self.activity.conversation_summaries(resident_ids, actor, excluded_business_ref=excluded_business_ref)

    def signature(self, resident_id: object) -> str:
        return self.activity.signature(resident_id)

    def signatures(self, resident_ids: Sequence[object]) -> dict[str, str]:
        return self.activity.signatures(resident_ids)


def get_repository() -> ResidentConversationRepository:
    state_store = get_lineage_data_store()
    agent_data = get_agent_data_store()
    key = f"{state_store.database_identity}\0{agent_data.database_identity}"
    with _REPOSITORIES_LOCK:
        repository = _REPOSITORIES.get(key)
        if repository is None:
            repository = ResidentConversationRepository(state_store, agent_data)
            _REPOSITORIES[key] = repository
        return repository


def reset_repositories_for_tests() -> None:
    with _REPOSITORIES_LOCK:
        _REPOSITORIES.clear()
