"""Agents and their traces: the Sequence of Segment references each Agent owns."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from management_common.database_time import decode_times, iso_time

from .agent_data_segments import existing_segment_ids, read_segments
from .agent_data_store import in_batches, new_identifier, utc_now
from .agent_operations import next_position
from . import chat_position_index


# Every Agent has one trace, the one record of what happened.  What a
# Provider session holds (its transcript and current context) is read from
# that trace (``session_transcript_ids``, ``session_context_ids``).  A
# subagent the Agent's model starts has a window of its own: its steps are
# a trace of its own, linked by agents.parent_agent_id. Sequences are generic
# ordered Segment references, without Agent identity, purpose or ancestry.
SUBAGENT_TRACE_KIND = "subagent_trace"
AGENT_SEQUENCE_KINDS = frozenset({
    "agent_trace",
    SUBAGENT_TRACE_KIND,
})


PROVIDER_TURN_SQL = (
    "CASE WHEN segment.metadata_json::jsonb->>'kind'='appended_provider_input' "
    "THEN segment.metadata_json::jsonb->>'provider_turn_id' ELSE execution.provider_turn_id END"
)


def sequence_row(connection: Any, sequence_id: str, *, lock: bool = False) -> Any | None:
    return connection.execute(
        'SELECT * FROM sequences WHERE sequence_id = %s' + (' FOR UPDATE' if lock else ''),
        (sequence_id,),
    ).fetchone()


def _create_sequence(connection: Any, actor_ref: str) -> Any:
    sequence_id, at = new_identifier("sequence"), utc_now()
    connection.execute(
        'INSERT INTO sequences(sequence_id, created_at, updated_at, created_by_actor_ref, updated_by_actor_ref) '
        'VALUES (%s, %s, %s, %s, %s)',
        (sequence_id, at, at, actor_ref, actor_ref),
    )
    # An empty Sequence is fully projected; its appends keep it current.
    chat_position_index.mark(connection, sequence_id)
    return sequence_row(connection, sequence_id)


def trace_id(connection: Any, agent_id: object) -> str:
    row = connection.execute(
        "SELECT trace_sequence_id FROM agents WHERE agent_id = %s", (str(agent_id or ""),),
    ).fetchone()
    return str(row["trace_sequence_id"]) if row is not None else ""


def trace_ids(connection: Any, agent_ids: Sequence[str]) -> dict[str, str]:
    """Each Agent's trace; an Agent that never had one is left out."""

    traces: dict[str, str] = {}
    for chunk in in_batches(list(agent_ids)):
        for row in connection.execute(
            "SELECT agent_id, trace_sequence_id FROM agents WHERE agent_id = ANY(%s)", (list(chunk),),
        ).fetchall():
            traces[str(row["agent_id"])] = str(row["trace_sequence_id"])
    return traces


def agent_trace(connection: Any, agent_id: str) -> Any | None:
    return sequence_row(connection, trace_id(connection, agent_id))


def _validated_agent_id(agent_id: str, sequence_kind: str, parent_agent_id: str | None) -> str:
    agent_id = str(agent_id or "").strip()
    if not agent_id or sequence_kind not in AGENT_SEQUENCE_KINDS:
        raise ValueError("Agent requires an identity and a valid trace purpose")
    if parent_agent_id == agent_id or (parent_agent_id and sequence_kind != SUBAGENT_TRACE_KIND):
        raise ValueError("Invalid Agent parent")
    return agent_id


def _needs_agent_parent_update(row: Any, sequence_kind: str, parent_agent_id: str | None) -> bool:
    if row["sequence_kind"] != sequence_kind:
        raise ValueError("Agent identity refers to different trace purpose or parent")
    if row["parent_agent_id"] == parent_agent_id:
        return False
    # Child traces may lack evidence of their direct parent. Only fill an
    # unknown edge; never replace a known one.
    if row["parent_agent_id"] is not None or parent_agent_id is None:
        raise ValueError("Agent identity refers to different trace purpose or parent")
    return True


def ensure_agent_row(connection: Any, agent_id: str, *, actor_ref: str,
                     sequence_kind: str = "agent_trace", parent_agent_id: str | None = None) -> Any:
    agent_id = _validated_agent_id(agent_id, sequence_kind, parent_agent_id)
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('agent:' || %s, 0))", (agent_id,))
    row = connection.execute('SELECT * FROM agents WHERE agent_id = %s', (agent_id,)).fetchone()
    if row is not None:
        if _needs_agent_parent_update(row, sequence_kind, parent_agent_id):
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('agent-parent-edges', 0))")
            cycle = connection.execute(
                'WITH RECURSIVE ancestors AS ('
                'SELECT agent_id, parent_agent_id FROM agents WHERE agent_id=%s UNION '
                'SELECT a.agent_id, a.parent_agent_id FROM agents a JOIN ancestors p ON a.agent_id=p.parent_agent_id) '
                'SELECT 1 FROM ancestors WHERE agent_id=%s', (parent_agent_id, agent_id),
            ).fetchone()
            if cycle:
                raise ValueError("Agent parent would form a cycle")
            connection.execute('UPDATE agents SET parent_agent_id=%s, updated_at=%s WHERE agent_id=%s',
                               (parent_agent_id, utc_now(), agent_id))
            row = connection.execute('SELECT * FROM agents WHERE agent_id=%s', (agent_id,)).fetchone()
        return row
    trace = _create_sequence(connection, actor_ref)
    connection.execute(
        'INSERT INTO agents(agent_id, trace_sequence_id, updated_at, sequence_kind, parent_agent_id) '
        'VALUES (%s, %s, %s, %s, %s)',
        (agent_id, trace["sequence_id"], trace["updated_at"], sequence_kind, parent_agent_id),
    )
    return connection.execute('SELECT * FROM agents WHERE agent_id = %s', (agent_id,)).fetchone()


def ensure_agent_trace(connection: Any, agent_id: str, *, actor_ref: str) -> Any:
    row = ensure_agent_row(connection, agent_id, actor_ref=actor_ref)
    return sequence_row(connection, row["trace_sequence_id"])


def locked_trace(connection: Any, agent_id: str, actor_ref: str) -> str:
    trace = ensure_agent_trace(connection, agent_id, actor_ref=actor_ref)
    sequence_id = str(trace["sequence_id"])
    connection.execute("SELECT 1 FROM sequences WHERE sequence_id = %s FOR UPDATE", (sequence_id,))
    return sequence_id


def sequence_ids(
    connection: Any,
    sequence_id: str,
) -> list[str]:
    return [
        str(row["segment_id"])
        for row in connection.execute(
            'SELECT segment_id FROM sequence_segments WHERE sequence_id = %s ORDER BY ordinal',
            (sequence_id,),
        ).fetchall()
    ]


def sequence_count(
    connection: Any,
    sequence_id: str,
) -> int:
    row = connection.execute(
        'SELECT COUNT(*) AS count FROM sequence_segments WHERE sequence_id = %s',
        (sequence_id,),
    ).fetchone()
    return int(row["count"] if row is not None else 0)


def touch_sequence(
    connection: Any,
    sequence_id: str,
    actor_ref: str,
) -> str:
    # A writer that did not keep the projection leaves its mark behind. A
    # later append must not make that projection look current merely by
    # advancing the mark: rebuild it from the locked trace instead.
    was_current = chat_position_index.current(connection, sequence_id)
    at = utc_now()
    connection.execute(
        'UPDATE sequences SET updated_at = %s, updated_by_actor_ref = %s WHERE sequence_id = %s',
        (at, str(actor_ref or ""), sequence_id),
    )
    if was_current:
        chat_position_index.mark(connection, sequence_id)
    else:
        chat_position_index.refresh_sequence(connection, sequence_id)
    return at


def place_operation(connection: Any, sequence_id: str, operation_id: str,
                    position: int, actor_ref: str) -> None:
    connection.execute("INSERT INTO operation_positions VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                       (sequence_id, operation_id, position))
    chat_position_index.upsert_notices(connection, sequence_id, [operation_id])
    touch_sequence(connection, sequence_id, actor_ref)


def append_sequence_ids(
    connection: Any,
    *,
    sequence: Any,
    segment_ids: Iterable[str],
    actor_ref: str,
) -> list[str]:
    requested = list(dict.fromkeys(
        str(value or "").strip()
        for value in segment_ids
        if str(value or "").strip()
    ))
    if not requested:
        return []
    found = existing_segment_ids(connection, requested)
    missing = [item for item in requested if item not in found]
    if missing:
        raise KeyError(missing[0])
    sequence_id = str(sequence["sequence_id"])
    # MAX(ordinal) + 1 is safe only while this Sequence's parent row is
    # locked. Unrelated Sequences still append in parallel.
    locked = connection.execute(
        'SELECT sequence_id FROM sequences WHERE sequence_id = %s FOR UPDATE',
        (sequence_id,),
    ).fetchone()
    if locked is None:
        raise RuntimeError("Sequence disappeared during append")
    selected: set[str] = set()
    for chunk in in_batches(requested):
        placeholders = ",".join("%s" for _ in chunk)
        selected.update(
            str(row["segment_id"])
            for row in connection.execute(
                f"SELECT segment_id FROM sequence_segments "
                f"WHERE sequence_id = %s AND segment_id IN ({placeholders})",
                [sequence_id, *chunk],
            ).fetchall()
        )
    additions = [item for item in requested if item not in selected]
    if not additions:
        return []
    base = next_position(connection, sequence_id) - 1
    connection.executemany(
        'INSERT INTO sequence_segments(sequence_id, ordinal, segment_id) VALUES (%s, %s, %s)',
        [
            (sequence_id, base + index, segment_id)
            for index, segment_id in enumerate(additions, 1)
        ],
    )
    chat_position_index.upsert_segments(connection, sequence_id, additions)
    touch_sequence(connection, sequence_id, actor_ref)
    return additions


def append_segments(connection: Any, sequence_id: str, segment_ids: Iterable[str], actor_ref: str) -> dict[str, Any]:
    sequence = sequence_row(connection, sequence_id, lock=True)
    if sequence is None:
        raise KeyError(sequence_id)
    additions = append_sequence_ids(connection, sequence=sequence, segment_ids=segment_ids, actor_ref=actor_ref)
    current = sequence_row(connection, sequence_id)
    return {"sequence_id": sequence_id, "appended_segment_ids": additions,
            "segment_count": sequence_count(connection, sequence_id), "updated_at": (iso_time(current["updated_at"]) or "")}


def insert_trace_member(connection: Any, sequence_id: str, position: int, segment_id: str) -> None:
    # Two steps keep (sequence, ordinal) unique while later members move up by one.
    connection.execute(
        "UPDATE sequence_segments SET ordinal = ordinal + 1000000000 WHERE sequence_id = %s AND ordinal >= %s",
        (sequence_id, position),
    )
    connection.execute(
        "UPDATE sequence_segments SET ordinal = ordinal - 999999999 WHERE sequence_id = %s AND ordinal > 1000000000",
        (sequence_id,),
    )
    connection.execute(
        "INSERT INTO sequence_segments(sequence_id, ordinal, segment_id) VALUES (%s, %s, %s)",
        (sequence_id, position, segment_id),
    )
    connection.execute("UPDATE operation_positions SET ordinal=ordinal+1 WHERE sequence_id=%s AND ordinal>=%s", (sequence_id, position))
    touch_sequence(connection, sequence_id, "portal")
    chat_position_index.refresh_sequence(connection, sequence_id)


def remove_trace_member(connection: Any, sequence_id: str, segment_id: str) -> None:
    row = connection.execute(
        "DELETE FROM sequence_segments WHERE sequence_id = %s AND segment_id = %s RETURNING ordinal",
        (sequence_id, segment_id),
    ).fetchone()
    if row is None:
        return
    connection.execute(
        "UPDATE sequence_segments SET ordinal = ordinal + 1000000000 WHERE sequence_id = %s AND ordinal > %s",
        (sequence_id, int(row["ordinal"])),
    )
    connection.execute(
        "UPDATE sequence_segments SET ordinal = ordinal - 1000000001 WHERE sequence_id = %s AND ordinal > 1000000000",
        (sequence_id,),
    )
    connection.execute("UPDATE operation_positions SET ordinal=ordinal-1 WHERE sequence_id=%s AND ordinal>%s", (sequence_id, int(row["ordinal"])))
    touch_sequence(connection, sequence_id, "portal")
    chat_position_index.refresh_sequence(connection, sequence_id)


def trace_rows(
    connection: Any, sequence_id: str, *, first: int = 1, before: int | None = None,
    newest_first: bool = False, limit: int | None = None,
) -> list[dict[str, Any]]:
    """A trace's members in order, with their Execution's session and turn."""

    bounds = " AND member.ordinal >= %s"
    arguments: list[Any] = [sequence_id, first]
    if before is not None:
        bounds += " AND member.ordinal < %s"
        arguments.append(before)
    ordering = " DESC" if newest_first else " ASC"
    if limit is not None:
        ordering += " LIMIT %s"
        arguments.append(limit)
    return [decode_times(row, ("started_at", "finished_at")) for row in connection.execute(
        "SELECT member.ordinal, member.segment_id, segment.segment_type, segment.started_at, segment.finished_at, "
        "segment.metadata_json, segment.execution_id, segment.created_by_agent_id, segment.content_sha256, segment.content_type, "
        f"execution.provider_session_ref AS execution_session, {PROVIDER_TURN_SQL} AS provider_turn_id "
        "FROM sequence_segments member JOIN segments segment ON segment.segment_id = member.segment_id "
        "LEFT JOIN executions execution ON execution.execution_id = segment.execution_id "
        "WHERE member.sequence_id = %s" + bounds + " ORDER BY member.ordinal" + ordering,
        tuple(arguments),
    ).fetchall()]


class AgentTraces:
    """The Agent registry and reads of whole traces."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def ensure_agent(self, agent_id: str, *, actor_ref: str = "", sequence_kind: str = "agent_trace",
                     parent_agent_id: str | None = None) -> dict[str, Any]:
        agent_id = _validated_agent_id(agent_id, sequence_kind, parent_agent_id)
        with self.database.read() as connection:
            row = connection.execute('SELECT * FROM agents WHERE agent_id = %s', (agent_id,)).fetchone()
        if row is not None:
            if not _needs_agent_parent_update(row, sequence_kind, parent_agent_id):
                return decode_times(row, ("created_at", "updated_at"))
            # A newly learned parent edge must be checked and stored atomically.
        return decode_times(self.database.write(lambda c: ensure_agent_row(
            c, agent_id, actor_ref=actor_ref, sequence_kind=sequence_kind, parent_agent_id=parent_agent_id,
        )), ("updated_at",))

    def agent(self, agent_id: str) -> dict[str, Any]:
        with self.database.read() as c:
            row = c.execute('SELECT * FROM agents WHERE agent_id = %s', (agent_id,)).fetchone()
            return decode_times(row, ("created_at", "updated_at")) if row is not None else {}

    def trace_sequence_id(self, agent_id: str) -> str:
        with self.database.read() as c:
            return trace_id(c, agent_id)

    def trace_length(self, agent_id: str) -> int:
        """How many Segments the Agent's trace holds now."""

        with self.database.read() as connection:
            trace = agent_trace(connection, str(agent_id or ''))
            return sequence_count(connection, str(trace["sequence_id"])) if trace else 0

    def read_sequence(
        self,
        sequence_id: str,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        with self.database.read() as connection:
            sequence = sequence_row(connection, sequence_id)
            if sequence is None:
                return {
                    "sequence_id": "",
                    "total": 0,
                    "segments": [],
                }
            sequence_id = str(sequence["sequence_id"])
            total = sequence_count(connection, sequence_id)
            start = max(0, int(offset))
            bounded = max(1, min(2_000, int(limit)))
            rows = connection.execute(
                'SELECT segment_id FROM sequence_segments WHERE sequence_id = %s ORDER BY ordinal LIMIT %s OFFSET %s',
                (sequence_id, bounded, start),
            ).fetchall()
            return {
                "sequence_id": sequence_id,
                "updated_at": iso_time(sequence["updated_at"]),
                "offset": start,
                "total": total,
                "segments": read_segments(
                    connection,
                    [str(row["segment_id"]) for row in rows],
                ),
            }

    def sequence_segment_ids(self, sequence_id: str) -> list[str]:
        with self.database.read() as connection:
            return sequence_ids(connection, sequence_id)

    @staticmethod
    def subagent_id(root_agent_id: str, trace_key: str) -> str:
        """Backend-local child identity scoped to the Portal root Agent."""
        if not root_agent_id or not trace_key:
            raise ValueError("Subagent requires its root and backend trace identity")
        return f"{root_agent_id}/{trace_key}"
