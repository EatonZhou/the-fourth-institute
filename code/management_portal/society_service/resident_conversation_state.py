"""Each Resident's current work state, activities and queue, and the snapshots that show them with recent messages."""
from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from management_common.async_questions import visible_message_input_ids
from management_common.database_time import iso_time
from management_common.json_columns import json_value

from management_portal.execution.agent_data_store import AgentDataStore

from .resident_conversation_events import ConversationEvents
from .resident_conversation_inputs import ConversationInputs
from .resident_conversation_messages import ConversationMessages
from .resident_conversation_records import (
    ACTIVITY_CHANGED,
    DEFAULT_HISTORY_LIMIT,
    INPUT_STATES,
    LIVE_CHANNEL,
    LIVE_EVENT_TYPES,
    MAX_HISTORY_LIMIT,
    QUEUE_CHANGED,
    RESIDENT_CONVERSATION_SCHEMA,
    STATE_CHANGED,
    compact_json,
    default_state,
    live_activity_event,
    message_from_storage,
    normalized_state,
    protocol_activities,
    protocol_queue,
    required_resident_id,
)
from .state_store import LineageDataStore

if TYPE_CHECKING:
    from .resident_conversation_stream import ResidentConversationRepository


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
        stream_id = required_resident_id(resident_id)
        encoded = compact_json(value)
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
            value=normalized_state(state, strict=True),
            event_type=STATE_CHANGED,
            payload_key="state",
        )

    def set_activities(self, resident_id: object, activities: object) -> dict[str, Any] | None:
        stream_id = required_resident_id(resident_id)
        return self._set_projection(
            stream_id,
            column="activity_json",
            value=protocol_activities(stream_id, activities),
            event_type=ACTIVITY_CHANGED,
            payload_key="activities",
        )

    def queues(self, resident_ids: Iterable[object]) -> dict[str, dict[str, Any]]:
        """Each Resident's waiting queue, read from its Mailbox (decision_15f7df737ed5).

        Nothing keeps a copy of it: the queue is the Mailbox's queue rows, behind
        a Job head that is held.
        """

        from .resident_execution_work import public_queue

        selected = list(dict.fromkeys(required_resident_id(item) for item in resident_ids))
        jobs = self.state_store.resident_execution_records(selected)
        return {
            resident_id: protocol_queue(resident_id, public_queue(self.repository, resident_id, jobs.get(resident_id) or {}))
            for resident_id in selected
        }

    def publish_queue(self, resident_id: object) -> dict[str, Any] | None:
        """Record a queue event when the Mailbox's queue is not the one watchers were told."""

        stream_id = required_resident_id(resident_id)
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
                "state": default_state(),
                "activities": [],
                "queue": dict(queue),
                "runtime_settings": self.state_store.read_resident_execution_settings(resident_id) or {},
                "cursor": 0,
            }
        return {
            "resident_id": resident_id,
            "state": normalized_state(
                json_value(row["state_json"]) or default_state(),
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
        stream_id = required_resident_id(resident_id)
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

        stream_id = required_resident_id(resident_id)
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
            row = connection.execute('SELECT state_json FROM conversation_states WHERE resident_id=%s', (required_resident_id(resident_id),)).fetchone()
        return normalized_state(json_value(row['state_json']), strict=False) if row else default_state()

    def current_state(self, resident_id):
        stream_id = required_resident_id(resident_id)
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
            required_resident_id(resident_id)
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
        selected = list(dict.fromkeys(required_resident_id(item) for item in resident_ids))
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
            required_resident_id(resident_id)
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
                    "state": default_state(),
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
                "state": normalized_state(
                    json_value(row["state_json"]) or default_state(),
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
        stream_id = required_resident_id(resident_id)
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
                [message_from_storage(item) for item in selected],
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
