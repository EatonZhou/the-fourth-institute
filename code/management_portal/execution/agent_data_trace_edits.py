"""The edits that change which Segments an Agent trace holds and where."""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .agent_data_executions import refresh_final_reply
from .agent_data_segments import insert_segment, read_segment
from .agent_data_store import stable_segment_id, utc_now
from .agent_data_traces import (
    agent_trace,
    append_segments,
    ensure_agent_row,
    insert_trace_member,
    locked_trace,
    place_operation,
    remove_trace_member,
    touch_sequence,
)
from .agent_operations import next_position, put_operation
from . import chat_position_index


class TraceEdits:
    """Appends, transformations, moves and context resets of Agent traces."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def append_agent_segments(self, agent_id: str, segment_ids: Iterable[str], *, actor_ref: str) -> dict[str, Any]:
        requested = list(segment_ids)
        def write(c: Any) -> dict[str, Any]:
            agent = c.execute('SELECT * FROM agents WHERE agent_id = %s', (agent_id,)).fetchone()
            if agent is None:
                agent = ensure_agent_row(c, agent_id, actor_ref=actor_ref)
            sequence_id = agent["trace_sequence_id"]
            c.execute("SELECT 1 FROM sequences WHERE sequence_id=%s FOR UPDATE", (sequence_id,))
            # A UI snapshot can arrive before the completed provider event.
            # Replace that projection position, never append the text twice.
            notices = c.execute("""SELECT position.operation_id,position.ordinal,segment.segment_id
                FROM operation_positions position JOIN agent_operations operation USING(operation_id)
                JOIN segments segment ON segment.segment_id=ANY(%s)
                  AND segment.metadata_json::jsonb#>>'{message,message_id}'=operation.data_json::jsonb#>>'{metadata,message,message_id}'
                WHERE position.sequence_id=%s AND operation.kind='notice'""", (requested,sequence_id)).fetchall()
            result = append_segments(c, sequence_id, requested, actor_ref)
            for notice in notices:
                c.execute("""INSERT INTO agent_operations
                    SELECT %s,kind,agent_id,session_ref,created_at,data_json FROM agent_operations
                    WHERE operation_id=%s ON CONFLICT DO NOTHING""",
                    ("message_artifact:" + notice["segment_id"], "message_artifact:" + notice["operation_id"]))
                c.execute("DELETE FROM operation_positions WHERE sequence_id=%s AND operation_id=%s", (sequence_id,notice["operation_id"]))
                c.execute("UPDATE sequence_segments SET ordinal=%s WHERE sequence_id=%s AND segment_id=%s", (notice["ordinal"],sequence_id,notice["segment_id"]))
            if notices:
                chat_position_index.refresh_sequence(c, sequence_id)
            if requested:
                ended = c.execute(
                    "SELECT DISTINCT segment.execution_id FROM segments segment "
                    "JOIN executions execution USING(execution_id) "
                    "WHERE segment.segment_id=ANY(%s) AND execution.status IN ('completed','failed','cancelled')",
                    (requested,),
                ).fetchall()
                for row in ended:
                    refresh_final_reply(c, str(row["execution_id"]))
            return result
        return self.database.write(write)

    def transform_agent_segments(
        self, agent_id: str, source_ids: Sequence[str], *, segment_id: str,
        content: Any, actor_ref: str, operation_id: str, operation_type: str,
        segment_type: str = "input", content_type: str = "text/plain; charset=utf-8",
        metadata: Mapping[str, Any] | None = None, execution_id: str = "",
        created_at: str = "",
    ) -> str:
        """Atomically replace consumed occurrences, retaining immutable ancestry.

        Only this Agent's sequence changes. A Fork already holding a source keeps
        it. Identical text in another occurrence has no bearing on replacement.
        """
        sources = list(dict.fromkeys(source_ids))
        if not sources:
            raise ValueError("A transformation needs source Segment identities")

        def write(connection: Any) -> str:
            sequence_id = locked_trace(connection, agent_id, actor_ref)
            members = connection.execute(
                "SELECT member.ordinal, member.segment_id, segment.content_sha256, "
                "segment.metadata_json::jsonb#>>'{message,message_id}' AS message_id "
                "FROM sequence_segments member JOIN segments segment USING(segment_id) "
                "WHERE member.sequence_id=%s AND member.segment_id=ANY(%s) ORDER BY member.ordinal",
                (sequence_id, sources),
            ).fetchall()
            if not members:
                replay = connection.execute(
                    "SELECT 1 FROM sequence_segments WHERE sequence_id=%s AND segment_id=%s",
                    (sequence_id, segment_id),
                ).fetchone()
                if replay:
                    return segment_id
                raise ValueError("Transformation sources are outside the current trace")
            if len(members) != len(sources):
                raise ValueError("Transformation must consume all its current sources")
            first = read_segment(connection, members[0]["segment_id"])
            facts = {**first["metadata"], **dict(metadata or {})}
            # Message identity is attached to the occurrence, not its old text.
            at = str(created_at or utc_now())
            insert_segment(
                connection, segment_id=segment_id, segment_type=segment_type,
                content=content, content_type=content_type, metadata=facts,
                 started_at=at, finished_at=at,
                status="completed", created_by_actor_ref=first["created_by_actor_ref"],
                performed_by_actor_ref=actor_ref, created_by_agent_id=agent_id,
                execution_id=execution_id or first["execution_id"],
            )
            for source_id in sources:
                relation_id = stable_segment_id("transform", operation_id, source_id, segment_id).replace("segment_", "relation_", 1)
                connection.execute(
                    "INSERT INTO segment_relations VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (relation_id, "transformed_from", source_id, segment_id,
                     operation_id, operation_type, "1", at, actor_ref, "{}"),
                )
            # Preserve the first position; gaps are harmless and retain cursors.
            connection.execute(
                "DELETE FROM sequence_segments WHERE sequence_id=%s AND segment_id=ANY(%s)",
                (sequence_id, sources),
            )
            connection.execute(
                "INSERT INTO sequence_segments(sequence_id,ordinal,segment_id) VALUES (%s,%s,%s)",
                (sequence_id, int(members[0]["ordinal"]), segment_id),
            )
            # Capture consumed positions in this same locked transaction. A
            # pre-click snapshot can precede input admission; polling can miss
            # preparation entirely. Neither can reconstruct these facts later.
            put_operation(
                connection,
                operation_id=stable_segment_id("trace-transformation", sequence_id, operation_id, segment_id),
                kind="trace_transformation", agent_id=agent_id, created_at=at,
                data={"schema": "tfi.trace-transformation.v1", "trace_id": sequence_id,
                      "operation_id": operation_id, "operation_type": operation_type,
                      "recorded_at": at, "execution_id": execution_id or first["execution_id"],
                      "sources": [{"ordinal": int(member["ordinal"]), "segment_id": member["segment_id"],
                                   "message_id": member["message_id"] or "",
                                   "content_sha256": member["content_sha256"]} for member in members],
                      "target_segment_id": segment_id, "target_ordinal": int(members[0]["ordinal"])},
            )
            touch_sequence(connection, sequence_id, actor_ref)
            chat_position_index.refresh_sequence(connection, sequence_id)
            return segment_id

        return self.database.write(write)

    def place_in_trace_before(
        self, agent_id: str, segment_id: str, following: Iterable[str], *, actor_ref: str,
    ) -> bool:
        """Move a trace member before the first of ``following`` that precedes it.

        A compaction recorded after the fact goes before the first message it
        kept, where it happened.  Returns whether it moved.
        """

        later = [str(item) for item in following if str(item)]
        clean_agent = str(agent_id or "").strip()
        if not later or not clean_agent:
            return False

        def write(connection: Any) -> bool:
            trace = agent_trace(connection, clean_agent)
            if trace is None:
                return False
            sequence_id = locked_trace(connection, clean_agent, actor_ref)
            current = connection.execute(
                "SELECT ordinal FROM sequence_segments WHERE sequence_id = %s AND segment_id = %s",
                (sequence_id, segment_id),
            ).fetchone()
            first = connection.execute(
                "SELECT MIN(ordinal) AS ordinal FROM sequence_segments WHERE sequence_id = %s AND segment_id = ANY(%s)",
                (sequence_id, later),
            ).fetchone()
            if current is None or first is None or first["ordinal"] is None:
                return False
            if int(first["ordinal"]) > int(current["ordinal"]):
                return False
            remove_trace_member(connection, sequence_id, segment_id)
            insert_trace_member(connection, sequence_id, int(first["ordinal"]), segment_id)
            touch_sequence(connection, sequence_id, str(actor_ref or ""))
            return True

        return self.database.write(write)

    def reset_provider_context(self, agent_id: str, *, reset_at: str, actor_ref: str) -> str:
        """Discard model context at a fresh restart while retaining chat history."""
        operation_id = stable_segment_id("provider_context_reset", agent_id, reset_at)
        def write(connection: Any) -> str:
            sequence_id = locked_trace(connection, agent_id, actor_ref)
            if connection.execute("SELECT 1 FROM agent_operations WHERE operation_id=%s", (operation_id,)).fetchone():
                return operation_id
            put_operation(connection, operation_id=operation_id, kind="provider_context_reset", agent_id=agent_id,
                          created_at=reset_at, data={"actor_ref": actor_ref})
            place_operation(connection, sequence_id, operation_id, next_position(connection, sequence_id), actor_ref)
            return operation_id
        return self.database.write(write)

    def record_context_reset(
        self, agent_id: str, *, reset_at: str, actor_ref: str,
        trace_position: int,
    ) -> str:
        """Record a clear action without adding model content to the trace."""
        if not agent_id or not reset_at:
            raise ValueError("A context reset needs its Agent and time")
        operation_id = stable_segment_id("context_reset", agent_id, reset_at)
        def write(connection: Any) -> str:
            sequence_id = locked_trace(connection, agent_id, actor_ref)
            if connection.execute("SELECT 1 FROM agent_operations WHERE operation_id=%s", (operation_id,)).fetchone():
                return operation_id
            # Public cutoffs count content occurrences; operations and
            # many-to-one transformations can leave gaps in ordinals.
            following = connection.execute(
                "SELECT ordinal FROM sequence_segments WHERE sequence_id=%s ORDER BY ordinal OFFSET %s LIMIT 1",
                (sequence_id, max(int(trace_position), 0)),
            ).fetchone()
            position = int(following["ordinal"]) if following else next_position(connection, sequence_id)
            connection.execute("UPDATE sequence_segments SET ordinal=ordinal+1000000000 WHERE sequence_id=%s AND ordinal>=%s", (sequence_id, position))
            connection.execute("UPDATE sequence_segments SET ordinal=ordinal-999999999 WHERE sequence_id=%s AND ordinal>1000000000", (sequence_id,))
            connection.execute("UPDATE operation_positions SET ordinal=ordinal+1 WHERE sequence_id=%s AND ordinal>=%s", (sequence_id, position))
            put_operation(connection, operation_id=operation_id, kind="context_reset", agent_id=agent_id,
                created_at=reset_at, data={"actor_ref": actor_ref})
            place_operation(connection, sequence_id, operation_id, position, actor_ref)
            chat_position_index.refresh_sequence(connection, sequence_id)
            return operation_id
        return self.database.write(write)

    def clear_chat(self, agent_id: str, *, actor_ref: str = "") -> str:
        """End an Agent's Chat here (a deleted Chatroom): a context reset after all it holds."""

        return self.record_context_reset(
            agent_id, reset_at=utc_now(), actor_ref=actor_ref,
            trace_position=2**31 - 1,
        )
