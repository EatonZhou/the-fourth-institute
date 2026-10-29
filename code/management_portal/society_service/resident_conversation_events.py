"""The ordered event log of each Resident Conversation, its cursor and its readers."""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from management_common.database_time import iso_time
from management_common.json_columns import json_value

from management_portal.execution.agent_data_store import AgentDataStore, utc_now

from . import invariant_locks
from .resident_conversation_records import (
    CONTEXT_CLEARED,
    INPUT_STATE_CHANGED,
    MESSAGE_APPENDED,
    MESSAGE_UPDATED,
    QUEUE_CHANGED,
    RESIDENT_CONVERSATION_SCHEMA,
    RUNTIME_SETTINGS_CHANGED,
    TERMINAL_INPUT_STATES,
    compact_json,
    final_reply,
    message_from_storage,
    required_resident_id,
)
from .state_store import LineageDataStore

if TYPE_CHECKING:
    from .resident_conversation_stream import ResidentConversationRepository


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
            (resident_id, cursor, event_type, subject_id, occurred_at, compact_json(event["payload"])),
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
        stream_id = required_resident_id(resident_id)

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
        return self.agent_data.displayed_chat_message_ids(required_resident_id(resident_id))

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

        stream_id = required_resident_id(resident_id)
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
        stream_id = required_resident_id(resident_id)
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
            messages = {message_id: message_from_storage(message) for message_id, message in stored.items()}
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
                state["reply_message_ids"] = final_reply(state.get("state"), replies.get(str(state.get("message_id") or "")))
                event["payload"] = {**payload, "input_state": state}
        return events
