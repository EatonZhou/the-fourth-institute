"""Chat reads: the messages of an Agent's trace, from its message projection."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from management_common.json_columns import json_value

from .agent_data_messages import chat_role
from .agent_data_segments import existing_segment_ids, read_segments
from .agent_data_store import canonical_json, in_batches
from .agent_data_traces import PROVIDER_TURN_SQL, trace_id
from .conversation_projection import CHAT_PREDICATE
from .message_projection import project_message
from . import chat_position_index


# An Agent's Chat is not stored apart from its trace: it is the trace's
# message Segments (``CHAT_SEGMENT``) and notices after its last context
# reset, in trace order, as the trace's message projection places them
# (chat_position_index).  A message's position (``ordinal``) is its trace position.
# A message that waits in a Resident's Mailbox is not here yet: it enters
# the trace when it is delivered.
# A Chat message is the trace Segment that carries it: its metadata holds the
# message (``{"message": {...}}``).  The queries that find messages in
# Segments use ``CHAT_PREDICATE``, so that the partial indexes of
# agent_data_tables apply; its statistics objects size their lookups.


# An input a User typed, by the metadata of its Segment: a Resident's delivery,
# a Mailbox group or a service notice is an input too but names another sender.
# Messages from before sender records name none.
TYPED_BY_USER = "COALESCE(({metadata})::jsonb #>> '{{message,origin,sender,kind}}', 'user') = 'user'"


# A projected message Chat shows: every one but a final answer Segment
# repeating a message that an earlier Segment of its trace carries.
SHOWN_POSITION = (
    "NOT (position.source_kind='segment' AND position.message_kind='final_answer' AND EXISTS ("
    "SELECT 1 FROM chat_message_positions earlier WHERE earlier.sequence_id=position.sequence_id "
    "AND earlier.source_kind='segment' AND earlier.message_id=position.message_id "
    "AND earlier.ordinal<position.ordinal))"
)
# A projected message's metadata, read from its Segment or its notice.
POSITION_METADATA = (
    "CASE WHEN position.source_kind='segment' "
    "THEN (SELECT metadata_json::jsonb FROM segments WHERE segment_id=position.source_id) "
    "ELSE (SELECT data_json::jsonb->'metadata' FROM agent_operations WHERE operation_id=position.source_id) END"
)
# The row Chat reads build a message from (``ChatReads.messages_from_rows``).
_POSITION_ROW = (
    "position.ordinal, position.source_id AS segment_id, position.segment_type, position.created_at, "
    f"position.actor_ref AS created_by_actor_ref, {POSITION_METADATA} AS metadata_json"
)


CHAT_COUNT_SQL = (
    "SELECT COUNT(*) AS count FROM chat_message_positions position "
    "WHERE position.sequence_id=%s AND position.source_kind<>'sync' AND position.ordinal<=%s "
    f"AND {SHOWN_POSITION}"
)


def chat_payload(segment_id: object, metadata_json: object, content: Any = "") -> dict[str, Any]:
    """The message a Chat Segment carries, with that Segment's identity."""

    metadata = json_value(metadata_json or "{}")
    return project_message(segment_id, metadata.get("message") or {}, content)


def chat_floor(connection: Any, sequence_id: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(position.ordinal),0) AS ordinal FROM operation_positions position "
        "JOIN agent_operations operation USING(operation_id) WHERE position.sequence_id=%s AND operation.kind='context_reset'",
        (sequence_id,),
    ).fetchone()
    return int(row["ordinal"])


def chat_floors(connection: Any, sequence_ids: Iterable[str]) -> dict[str, int]:
    rows = connection.execute(
        "SELECT position.sequence_id, MAX(position.ordinal) AS ordinal FROM operation_positions position "
        "JOIN agent_operations operation USING(operation_id) WHERE position.sequence_id=ANY(%s) "
        "AND operation.kind='context_reset' GROUP BY position.sequence_id", (list(sequence_ids),),
    ).fetchall()
    return {str(row["sequence_id"]): int(row["ordinal"]) for row in rows}


def chat_rows(
    connection: Any,
    sequence_id: str,
    *,
    after: int = 0,
    before: int | None = None,
    limit: int = 0,
    user_only: bool = False,
    sender: str = "",
) -> list[Any]:
    """Chat members between two trace positions, oldest first: the newest ``limit`` of them.

    ``sender`` keeps only the inputs that User typed.  The trace's
    projection must be current (``chat_traces``).
    """

    if not sequence_id:
        return []
    clauses = ["position.sequence_id=%s", "position.source_kind<>'sync'", "position.ordinal>%s"]
    parameters: list[Any] = [sequence_id, int(after)]
    if before is not None:
        clauses.append("position.ordinal<%s")
        parameters.append(int(before))
    if user_only or sender:
        clauses.append("position.segment_type='input'")
    if sender:
        clauses.append("lower(position.actor_ref)=lower(%s)")
        parameters.append(sender)
        clauses.append(TYPED_BY_USER.format(metadata=POSITION_METADATA))
    clauses.append(SHOWN_POSITION)
    limit_clause = "LIMIT %s" if limit else ""
    if limit:
        parameters.append(int(limit))
    return list(connection.execute(
        f"SELECT {_POSITION_ROW} FROM (SELECT * FROM chat_message_positions position "
        f"WHERE {' AND '.join(clauses)} ORDER BY position.ordinal DESC {limit_clause}) position "
        "ORDER BY position.ordinal",
        parameters,
    ).fetchall())


def chat_member(connection: Any, sequence_id: str, message_id: str) -> Any | None:
    """A message's newest shown position in a trace, wherever it stands there.

    The trace's projection must be current: readers bring it current
    first (``chat_traces``), writers under the trace lock (``current_member``).
    """

    if not sequence_id or not message_id:
        return None
    return connection.execute(
        f"SELECT {_POSITION_ROW} FROM chat_message_positions position "
        "WHERE position.message_id=%s AND position.sequence_id=%s AND position.source_kind<>'sync' "
        f"AND {SHOWN_POSITION} ORDER BY position.ordinal DESC LIMIT 1",
        (message_id, sequence_id),
    ).fetchone()


def current_member(connection: Any, sequence_id: str, message_id: str) -> Any | None:
    """``chat_member`` in a write, the trace locked and its projection brought current first."""

    if sequence_id:
        chat_position_index.bring_current(connection, sequence_id)
    return chat_member(connection, sequence_id, message_id)


def _shown_member(connection: Any, sequence_id: str, message_id: str) -> Any | None:
    """A message's position while it is in the trace's Chat (after the last reset)."""

    row = chat_member(connection, sequence_id, str(message_id or "").strip())
    if row is None or int(row["ordinal"]) <= chat_floor(connection, sequence_id):
        return None
    return row


def message_contents(connection: Any, identities: Sequence[str]) -> dict[str, Any]:
    real_ids = existing_segment_ids(connection, identities)
    contents = {item["segment_id"]: item["content"] for item in read_segments(connection, list(real_ids))}
    remaining = [item for item in identities if item not in real_ids]
    if remaining:
        for row in connection.execute("SELECT operation_id, data_json FROM agent_operations WHERE operation_id=ANY(%s)", (remaining,)).fetchall():
            contents[str(row["operation_id"])] = json_value(row["data_json"]).get("content", "")
    return contents


def group_source_members(connection: Any, sequence_id: str, message_ids: Sequence[str]) -> list[Any]:
    """Accepted sources of groups in this trace, oldest grouping operation first.

    A consumed source has no current ordinal. Its content stays immutable;
    the latest recorded grouping owns the current grouping. Start from the
    indexed message identities, never a scan of the rendered trace.
    """
    if not message_ids or not sequence_id:
        return []
    return list(connection.execute(
            "SELECT source.segment_id,source.metadata_json,member.ordinal AS group_ordinal, "
            "source.metadata_json::jsonb#>>'{message,message_id}' AS member_id, "
            "target.metadata_json::jsonb#>>'{message,message_id}' AS group_id "
            "FROM (SELECT segment_id,metadata_json FROM segments "
            f"WHERE {CHAT_PREDICATE} AND metadata_json::jsonb#>>'{{message,message_id}}'=ANY(%s) OFFSET 0) source "
            "JOIN LATERAL (SELECT target_segment_id,created_at FROM segment_relations relation "
            "WHERE relation.source_segment_id=source.segment_id AND relation.operation_type='message.group' OFFSET 0) relation ON TRUE "
            "JOIN LATERAL (SELECT metadata_json FROM segments WHERE segment_id=relation.target_segment_id OFFSET 0) target ON TRUE "
            "JOIN LATERAL (WITH RECURSIVE versions(segment_id) AS ("
            "SELECT relation.target_segment_id UNION SELECT revision.target_segment_id "
            "FROM versions JOIN segment_relations revision ON revision.source_segment_id=versions.segment_id "
            "WHERE revision.relation_type IN ('transformed_from','merged_from')) "
            "SELECT member.ordinal FROM versions JOIN sequence_segments member USING(segment_id) "
            "JOIN segments current USING(segment_id) WHERE member.sequence_id=%s "
            "AND current.metadata_json::jsonb#>>'{message,message_id}'=target.metadata_json::jsonb#>>'{message,message_id}') member ON TRUE "
            "ORDER BY relation.created_at,member.ordinal,source.segment_id",
            (list(message_ids), sequence_id),
        ).fetchall())


def _before_retraction(connection: Any, rows: Sequence[Any]) -> list[dict[str, Any]]:
    """Chat rows, a retracted message's row carrying the version it replaced."""

    rows = [dict(row) for row in rows]
    retracted = [
        str(row["segment_id"]) for row in rows
        if (json_value(row["metadata_json"] or "{}").get("message") or {}).get("retracted_at")
    ]
    if not retracted:
        return rows
    previous = {str(row["target_segment_id"]): row for row in connection.execute(
        "SELECT relation.target_segment_id, segment.segment_id, segment.metadata_json::jsonb AS metadata_json "
        "FROM segment_relations relation JOIN segments segment ON segment.segment_id=relation.source_segment_id "
        "WHERE relation.relation_type='transformed_from' AND relation.target_segment_id=ANY(%s)",
        (retracted,),
    ).fetchall()}
    for row in rows:
        source = previous.get(str(row["segment_id"]))
        if source is not None:
            row.update(segment_id=source["segment_id"], metadata_json=source["metadata_json"])
    return rows


class ChatReads:
    """Reads of Agent Chats, each over a current message projection."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def chat_traces(self, agent_ids: Sequence[str]) -> dict[str, str]:
        """Each Agent's trace, its message projection current; an Agent that never had one is left out.

        Chat reads only the projection (chat_position_index).  A trace whose
        mark is behind its Sequence row is rebuilt from the locked trace
        first.  Called in a read, it uses that read's connection, and a
        rebuild joins its transaction: the read's next statements see it.  A
        snapshot must open after it.
        """
        requested = list(dict.fromkeys(str(item) for item in agent_ids if str(item)))
        traces: dict[str, str] = {}
        behind: list[str] = []
        with self.database.read() as connection:
            for chunk in in_batches(requested):
                for row in connection.execute(
                    "SELECT agent.agent_id, agent.trace_sequence_id, "
                    f"{chat_position_index.behind('agent.trace_sequence_id')} AS behind "
                    "FROM agents agent WHERE agent.agent_id = ANY(%s)", (list(chunk),),
                ).fetchall():
                    traces[str(row["agent_id"])] = str(row["trace_sequence_id"])
                    if row["behind"]:
                        behind.append(str(row["trace_sequence_id"]))
        self._bring_current(behind)
        return traces

    def _chat_trace(self, agent_id: str) -> str:
        return self.chat_traces([agent_id]).get(str(agent_id), "")

    def _bring_current(self, sequence_ids: Iterable[str]) -> None:
        for sequence_id in sequence_ids:
            self.database.write(lambda connection, sequence_id=sequence_id: (
                chat_position_index.bring_current(connection, sequence_id)
            ))

    def bring_chat_current(self) -> None:
        """Bring every Agent trace's message projection current, before a listing reads them all."""
        with self.database.read() as connection:
            behind = chat_position_index.lagging(connection)
        self._bring_current(behind)

    def messages_from_rows(self, rows: Sequence[Any]) -> list[dict[str, Any]]:
        if not rows:
            return []
        from .scheduler_usage import normalize_token_usage
        metadata = {str(row["segment_id"]): json_value(row["metadata_json"]) for row in rows}
        with self.database.read() as connection:
            contents = message_contents(connection, [row["segment_id"] for row in rows])
            executions = list({item.get("message", {}).get("execution_id") for item in metadata.values()
                               if item.get("message", {}).get("execution_id")})
            endings = connection.execute(
                "SELECT execution_id,usage_json,final_message_id AS message_id "
                "FROM executions WHERE execution_id=ANY(%s) AND status IN ('completed','failed','cancelled')",
                (executions,),
            ).fetchall() if executions else []
            usage = {}
            for ending in endings:
                if not ending["message_id"]:
                    continue
                raw_usage = json_value(ending["usage_json"])
                usage[(str(ending["execution_id"]), str(ending["message_id"]))] = {
                    **normalize_token_usage(raw_usage.get("accounted") or raw_usage),
                    **{key: raw_usage[key] for key in ("api_id", "model")
                       if isinstance(raw_usage.get(key), str) and raw_usage[key]},
                }
            extras = {str(row["operation_id"]).removeprefix("message_artifact:"): json_value(row["data_json"]) for row in connection.execute(
                "SELECT operation_id,data_json FROM agent_operations WHERE operation_id=ANY(%s)",
                (["message_artifact:" + str(row["segment_id"]) for row in rows],),
            ).fetchall()}
        messages = [chat_payload(row["segment_id"], row["metadata_json"], contents[row["segment_id"]]) for row in rows]
        for message in messages:
            message.update(extras.get(message["segment_id"], {}))
            counted = usage.get((message.get("execution_id"), message.get("message_id")))
            if counted:
                message["usage"] = counted
        return messages

    def displayed_chat_message_ids(self, agent_id: str) -> list[str]:
        """The messages a clear now would end, oldest first, without their bodies."""

        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            if not sequence_id:
                return []
            floor = chat_floor(connection, sequence_id)
            return [
                str(row["message_id"])
                for row in connection.execute(
                    "SELECT position.message_id FROM chat_message_positions position "
                    "WHERE position.sequence_id = %s AND position.source_kind <> 'sync' AND position.ordinal > %s "
                    f"AND {SHOWN_POSITION} ORDER BY position.ordinal",
                    (sequence_id, floor),
                ).fetchall()
            ]

    def read_chat_messages(
        self,
        agent_id: str,
        *,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """The Agent's Chat, oldest first; the newest ``limit`` messages when given."""

        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            if not sequence_id:
                return []
            return self.messages_from_rows(chat_rows(
                connection, sequence_id, after=chat_floor(connection, sequence_id),
                limit=max(0, int(limit)),
            ))

    def read_chat_messages_many(self, agent_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        """Read current Chats together, retaining each trace's occurrence order.

        Search consumes bounded batches through this owner, using the same Chat
        policy and content projection as a single conversation. Database reads
        for traces, reset positions, content and usage are shared by the batch.
        """
        requested = list(dict.fromkeys(agent_ids))
        messages: dict[str, list[dict[str, Any]]] = {agent_id: [] for agent_id in requested}
        if not requested:
            return messages
        with self.database.read() as connection:
            traces = self.chat_traces(requested)
            if not traces:
                return messages
            floors = chat_floors(connection, traces.values())
            rows = connection.execute(
                "WITH requested AS (SELECT * FROM unnest(%s::text[], %s::bigint[]) "
                "AS wanted(sequence_id, floor)) "
                f"SELECT requested.sequence_id, {_POSITION_ROW} "
                "FROM requested CROSS JOIN LATERAL ("
                "SELECT * FROM chat_message_positions position "
                "WHERE position.sequence_id=requested.sequence_id AND position.source_kind<>'sync' "
                f"AND position.ordinal>requested.floor AND {SHOWN_POSITION} OFFSET 0"
                ") position ORDER BY requested.sequence_id, position.ordinal",
                (list(traces.values()), [floors.get(trace, 0) for trace in traces.values()]),
            ).fetchall()
            projected = self.messages_from_rows(rows)
        by_trace: dict[str, list[dict[str, Any]]] = {}
        for row, message in zip(rows, projected):
            by_trace.setdefault(str(row["sequence_id"]), []).append(message)
        messages.update({agent_id: by_trace.get(trace, []) for agent_id, trace in traces.items()})
        return messages

    def read_chat_messages_through_turn(
        self,
        agent_id: str,
        message_ids: Sequence[object],
        *,
        limit: int,
        include_turn: bool = True,
    ) -> list[dict[str, Any]]:
        """The last ``limit`` messages up to the turn of the newest of ``message_ids``.

        The turn ends where the next user message begins. With include_turn=False,
        stop strictly before the requested input (for stateless API history).
        """

        requested = list(dict.fromkeys(
            str(value or "").strip()
            for value in message_ids
            if str(value or "").strip()
        ))
        if not requested:
            return []
        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            if not sequence_id:
                return []
            floor = chat_floor(connection, sequence_id)
            target = connection.execute(
                "SELECT MAX(position.ordinal) AS ordinal FROM chat_message_positions position "
                "WHERE position.message_id = ANY(%s) AND position.sequence_id = %s "
                f"AND position.source_kind <> 'sync' AND {SHOWN_POSITION}",
                (requested, sequence_id),
            ).fetchone()
            target_ordinal = int(target["ordinal"] or 0) if target else 0
            if target_ordinal <= floor:
                return []
            if not include_turn:
                reset = connection.execute(
                    "SELECT COALESCE(MAX(p.ordinal),0) AS ordinal FROM operation_positions p "
                    "JOIN agent_operations o USING(operation_id) WHERE p.sequence_id=%s "
                    "AND o.kind='provider_context_reset' AND p.ordinal < %s",
                    (sequence_id, target_ordinal),
                ).fetchone()
                floor = max(floor, int(reset["ordinal"]))
                return self.messages_from_rows(chat_rows(
                    connection, sequence_id, after=floor, before=target_ordinal,
                    limit=max(1, int(limit)),
                ))
            following = connection.execute(
                "SELECT position.ordinal FROM chat_message_positions position "
                "WHERE position.sequence_id = %s AND position.segment_type = 'input' AND position.ordinal > %s "
                f"AND position.source_kind <> 'sync' AND {SHOWN_POSITION} ORDER BY position.ordinal LIMIT 1",
                (sequence_id, target_ordinal),
            ).fetchone()
            return self.messages_from_rows(chat_rows(
                connection, sequence_id, after=floor,
                before=int(following["ordinal"]) if following else None,
                limit=max(1, int(limit)),
            ))

    def read_chat_message_page(
        self,
        agent_id: str,
        *,
        before_ordinal: int | None = None,
        after_ordinal: int = 0,
        limit: int,
    ) -> dict[str, Any]:
        """The newest Chat messages between two trace positions.

        ``ordinals`` lists each returned message's trace position, in the
        same order, for streams that keep their own read or delivery positions.
        """
        safe_limit = max(1, int(limit))
        boundary = int(before_ordinal) if before_ordinal is not None else None
        if boundary is not None and boundary <= 0:
            raise ValueError("Chat message page boundary must be positive")
        lower = int(after_ordinal or 0)
        if lower < 0:
            raise ValueError("Chat message page lower boundary must not be negative")
        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            rows = chat_rows(
                connection, sequence_id, after=max(lower, chat_floor(connection, sequence_id)),
                before=boundary, limit=safe_limit + 1,
            ) if sequence_id else []
        has_more = len(rows) > safe_limit
        selected_rows = rows[1:] if has_more else rows
        return {
            "messages": self.messages_from_rows(selected_rows),
            "ordinals": [int(row["ordinal"]) for row in selected_rows],
            "next_before_ordinal": (
                int(selected_rows[0]["ordinal"])
                if has_more and selected_rows
                else None
            ),
        }

    def read_input_history(
        self,
        agent_id: str,
        sender: str,
        *,
        before_ordinal: int | None = None,
        limit: int,
    ) -> dict[str, Any]:
        """What ``sender`` typed in the Agent's trace, newest first: the composer's input history.

        The whole trace counts, before the last context reset too, and only that User's own inputs.  A
        retracted message gives its version before the retraction, so it can
        be sent again.  ``before_ordinal`` continues from
        an earlier page; ``ordinals`` lists each message's trace position.
        """
        safe_limit = max(1, int(limit))
        if before_ordinal is not None and int(before_ordinal) <= 0:
            raise ValueError("Input page boundary must be positive")
        clean_sender = str(sender or "").strip()
        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            rows = chat_rows(
                connection, sequence_id, before=before_ordinal, limit=safe_limit + 1, sender=clean_sender,
            ) if sequence_id and clean_sender else []
            has_more = len(rows) > safe_limit
            selected_rows = _before_retraction(connection, rows[1:] if has_more else rows)
        selected_rows.reverse()
        return {
            "messages": self.messages_from_rows(selected_rows),
            "ordinals": [int(row["ordinal"]) for row in selected_rows],
            "next_before_ordinal": (
                int(selected_rows[-1]["ordinal"]) if has_more and selected_rows else None
            ),
        }

    def chat_message_ordinal(self, agent_id: str, message_id: str) -> int | None:
        """Trace position of one message in the Agent's Chat; None when it is not there."""

        with self.database.read() as connection:
            row = _shown_member(connection, self._chat_trace(agent_id), message_id)
        return int(row["ordinal"]) if row is not None else None

    def chat_message(
        self,
        agent_id: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        """One message of the Agent's Chat; None when it is not there (or was cleared)."""

        with self.database.read() as connection:
            row = _shown_member(connection, self._chat_trace(agent_id), message_id)
            return self.messages_from_rows([row])[0] if row is not None else None

    def chat_message_ancestors(self, segment_id: str, message_id: str) -> list[dict[str, Any]]:
        """Immutable input versions used to validate a delivery replay.

        These are provenance records, never extra members of the current Chat.
        """
        with self.database.read() as connection:
            ids = [row["segment_id"] for row in connection.execute("""
                WITH RECURSIVE ancestors(segment_id) AS (
                    SELECT source_segment_id FROM segment_relations WHERE target_segment_id=%s
                    UNION
                    SELECT relation.source_segment_id FROM segment_relations relation
                    JOIN ancestors ON relation.target_segment_id=ancestors.segment_id
                )
                SELECT segment.segment_id FROM ancestors JOIN segments segment USING(segment_id)
                WHERE segment.metadata_json::jsonb#>>'{message,message_id}'=%s
            """, (segment_id, message_id)).fetchall()]
            return [chat_payload(row["segment_id"], canonical_json(row["metadata"]), row["content"])
                    for row in read_segments(connection, ids)]

    def chat_message_provenance(self, agent_id: str, message_id: str) -> dict[str, str]:
        """The current occurrence directly names the Execution that delivered it."""
        with self.database.read() as connection:
            message = _shown_member(connection, self._chat_trace(agent_id), message_id)
            if message is None:
                raise KeyError(message_id)
            segment_id = str(message["segment_id"])
            turn = connection.execute(
                f"SELECT execution.provider_session_ref, {PROVIDER_TURN_SQL} AS provider_turn_id "
                "FROM segments segment JOIN executions execution USING(execution_id) WHERE segment.segment_id=%s",
                (segment_id,),
            ).fetchone()
            return {"segment_id": segment_id,
                    "provider_session_ref": str(turn["provider_session_ref"] or "") if turn else "",
                    "provider_turn_id": str(turn["provider_turn_id"] or "") if turn else ""}

    def latest_chat_message(
        self,
        agent_id: str,
        *,
        role: str = "",
    ) -> dict[str, Any] | None:
        """The newest message of the Agent's Chat, of ``role`` when given."""

        wanted = str(role or "").strip().casefold()
        wanted = chat_role(wanted) if wanted else ""
        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            if not sequence_id:
                return None
            floor = chat_floor(connection, sequence_id)
            before = None
            while True:
                rows = chat_rows(
                    connection, sequence_id, after=floor, before=before, limit=50,
                    user_only=wanted == "user",
                )
                for message in reversed(self.messages_from_rows(rows)):
                    if not wanted or message["role"] == wanted:
                        return message
                if len(rows) < 50:
                    return None
                before = int(rows[0]["ordinal"])

    def trace_chat_messages(self, agent_id: str, message_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Messages of the Agent's trace by identity, wherever they stand in it (a clear does not hide them)."""

        requested = list(dict.fromkeys(str(item or "").strip() for item in message_ids if str(item or "").strip()))
        if not requested:
            return {}
        found: dict[str, dict[str, Any]] = {}
        with self.database.read() as connection:
            sequence_id = self._chat_trace(agent_id)
            if not sequence_id:
                return {}
            for chunk in in_batches(requested):
                # Each message's newest shown position.
                rows = connection.execute(
                    f"SELECT DISTINCT ON (position.message_id) {_POSITION_ROW} FROM chat_message_positions position "
                    "WHERE position.message_id = ANY(%s) AND position.sequence_id = %s "
                    f"AND position.source_kind <> 'sync' AND {SHOWN_POSITION} "
                    "ORDER BY position.message_id, position.ordinal DESC",
                    (list(chunk), sequence_id),
                ).fetchall()
                for message in self.messages_from_rows(rows):
                    found[str(message["message_id"])] = message
        return found

    def group_source_message(self, agent_id: str, message_id: str) -> dict[str, Any] | None:
        """Read a consumed member for audit/requeue, never as a current Chat entry."""
        with self.database.read() as connection:
            rows = group_source_members(connection, trace_id(connection, agent_id), [message_id])
            if rows:
                row = rows[-1]
                return chat_payload(row["segment_id"], row["metadata_json"], message_contents(connection, [row["segment_id"]])[row["segment_id"]])
        return None

    def message_groups(self, agent_id: str, message_ids: Sequence[str]) -> dict[str, str]:
        """Recover the latest grouping in this trace, including consumed members."""
        with self.database.read() as connection:
            rows = group_source_members(connection, trace_id(connection, agent_id), message_ids)
        return {str(row["member_id"]): str(row["group_id"]) for row in rows}
