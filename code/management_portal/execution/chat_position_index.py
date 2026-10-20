"""The message projection of Agent traces, which every Chat read uses.

Each row of ``chat_message_positions`` places one communication message or
operational notice of a trace: its position, Segment type, message identity
and kind, and the facts reads need without parsing Segment JSON: when and by
whom it was written, the business reference that marks a relayed copy, the
users it names as recipients, the input it answers and whether it can answer
one at all.

``chat_actor_activity`` keeps, for each trace and person, when they last
wrote there or were written to since the trace's last context reset: the
Mailbox orders conversations by it without grouping all of a person's
messages on every read.

Every write that changes a trace keeps both in its own transaction and marks
the trace with the version (MVCC xmin) of its Sequence row. A trace whose
mark is behind (a writer that did not keep the projection, or a rewrite of
the Sequence rows) is rebuilt from the locked trace before it is read
(``lagging`` and ``bring_current``) and when it is written to next.
"""
from __future__ import annotations

from typing import Any, Sequence

# A message a Family facade relays to a member keeps its sender, but the
# sender's conversation is with the facade: the copy is not their activity.
RELAYED_COPY_PATTERN = r"^(resident_[0-9a-f]{12})/message/(msg_[0-9a-f]{16,64})$"
POSITION_COLUMNS = (
    "sequence_id", "source_kind", "source_id", "ordinal", "segment_type", "message_id",
    "message_kind", "created_at", "actor_ref", "business_ref", "recipient_users",
    "reply_to", "reply_candidate",
)
ACTIVITY_COLUMNS = ("sequence_id", "actor", "latest_at")
TABLE_SQL = """
CREATE TABLE chat_message_positions (
    sequence_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('segment', 'notice', 'sync')),
    source_id TEXT NOT NULL,
    ordinal BIGINT NOT NULL,
    segment_type TEXT NOT NULL,
    message_id TEXT,
    message_kind TEXT NOT NULL,
    created_at TIMESTAMPTZ,
    actor_ref TEXT,
    business_ref TEXT,
    recipient_users TEXT[],
    reply_to TEXT,
    reply_candidate BOOLEAN,
    PRIMARY KEY (sequence_id, source_kind, source_id)
);
CREATE TABLE chat_actor_activity (
    sequence_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    latest_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (sequence_id, actor)
);
"""
INDEX_SQL = {
    "chat_message_positions_page_idx": "ON chat_message_positions(sequence_id, ordinal DESC)",
    "chat_message_positions_message_idx": (
        "ON chat_message_positions(sequence_id, message_id, ordinal) "
        "WHERE source_kind='segment' AND message_id IS NOT NULL"
    ),
    # A message, Segment or notice, by its identity.  Only the identity is a
    # key: a lookup names many messages and traces, and PostgreSQL 16 would
    # descend once for every pair of them.
    "chat_message_positions_identity_idx": (
        "ON chat_message_positions(message_id) WHERE message_id IS NOT NULL AND source_kind<>'sync'"
    ),
    "chat_message_positions_input_idx": (
        "ON chat_message_positions(sequence_id, ordinal DESC) WHERE segment_type='input'"
    ),
    # The messages naming an actor among given traces.
    "chat_message_positions_recipient_idx": (
        "ON chat_message_positions USING gin(recipient_users) "
        "WHERE segment_type='model' AND source_kind<>'sync'"
    ),
    # The messages answering given inputs within a trace.
    "chat_message_positions_reply_idx": (
        "ON chat_message_positions(sequence_id, reply_to) WHERE reply_to IS NOT NULL AND source_kind<>'sync'"
    ),
    # Every trace's version mark in one small scan, for the listing check.
    "chat_message_positions_mark_idx": (
        "ON chat_message_positions(sequence_id, source_id, message_id) WHERE source_kind='sync'"
    ),
    "chat_actor_activity_actor_idx": "ON chat_actor_activity(actor, latest_at DESC, sequence_id)",
}
MARK = "__sync__"
BEHIND = "mark.message_id IS DISTINCT FROM sequence.xmin::text"


def _mark_of(trace: str) -> str:
    return (
        "(SELECT mark.message_id FROM chat_message_positions mark "
        f"WHERE mark.sequence_id={trace} AND mark.source_kind='sync' AND mark.source_id='{MARK}')"
    )


def behind(trace: str) -> str:
    """Whether the mark of the trace ``trace`` (an SQL expression) is behind its Sequence row.

    Scalar lookups rather than a join: joined with the Agent and Sequence
    rows, one trace's check took 0.2 ms to plan and 0.02 ms to run.
    """
    return (
        f"{_mark_of(trace)} IS DISTINCT FROM "
        f"(SELECT sequence.xmin::text FROM sequences sequence WHERE sequence.sequence_id={trace})"
    )
# Each message's contribution: its sender for an input that is not a relayed
# copy, each user it names for a model message; after the last context reset.
# The reset floor names the trace as a parameter: tied to the outer row, PG
# ran it once per message (3.2 s for an 11,515-row trace instead of 8 ms).
_ACTIVITY_ROWS = (
    "SELECT position.sequence_id, contribution.actor, MAX(position.created_at) AS latest_at "
    "FROM chat_message_positions position CROSS JOIN LATERAL ("
    " SELECT lower(position.actor_ref) AS actor WHERE position.segment_type='input'"
    "  AND COALESCE(position.actor_ref,'')<>'' AND NOT COALESCE(position.business_ref ~ %s, FALSE)"
    " UNION ALL SELECT recipient FROM unnest(CASE WHEN position.segment_type='model'"
    "  THEN position.recipient_users ELSE '{}'::text[] END) recipient"
    ") contribution "
    "WHERE position.sequence_id=%s AND position.source_kind<>'sync' AND position.created_at IS NOT NULL "
    "AND position.ordinal > COALESCE((SELECT MAX(reset.ordinal) FROM operation_positions reset "
    "JOIN agent_operations operation USING(operation_id) "
    "WHERE reset.sequence_id=%s AND operation.kind='context_reset'), 0)"
)
_ACTIVITY_GROUP = " GROUP BY position.sequence_id, contribution.actor"

COLUMNS = "(" + ",".join(POSITION_COLUMNS) + ")"
UPDATE_COLUMNS = ",".join(f"{column}=EXCLUDED.{column}" for column in POSITION_COLUMNS[3:])


def create(connection: Any) -> None:
    """Create the projection in a new store."""
    connection.executescript(TABLE_SQL)
    for name, definition in INDEX_SQL.items():
        connection.execute(f"CREATE INDEX {name} {definition}")


def _recipient_users(recipients: str) -> str:
    return (
        "ARRAY(SELECT lower(recipient->>'id') FROM jsonb_array_elements("
        f"CASE WHEN jsonb_typeof({recipients})='array' THEN {recipients} ELSE '[]'::jsonb END"
        ") recipient WHERE recipient->>'kind'='user')"
    )


def _reply_facts(metadata: str) -> str:
    """The input the message answers, and conversation_projection.REPLY_SEGMENT for it."""
    parts = f"{metadata} #> '{{message,parts}}'"
    return (
        f"{metadata} #>> '{{message,reply_to_message_id}}',"
        f"NOT ({metadata} -> 'message' ? 'harness_input') AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements("
        f"CASE WHEN jsonb_typeof({parts})='array' THEN {parts} ELSE '[]'::jsonb END) part "
        "WHERE part->>'type' IN ('native_turn','resource_changes','subagent_run','tool_call','tool_result'))"
    )


SEGMENT_ROWS = (
    "SELECT member.sequence_id,'segment',segment.segment_id,member.ordinal,segment.segment_type,"
    "parsed.metadata #>> '{message,message_id}',COALESCE(parsed.metadata ->> 'kind',''),"
    "COALESCE(NULLIF(parsed.metadata#>>'{message,created_at}','')::timestamptz,segment.finished_at,segment.started_at),segment.created_by_actor_ref,parsed.metadata #>> '{message,business_ref}',"
    + _recipient_users("parsed.metadata #> '{message,recipients}'") + ","
    + _reply_facts("parsed.metadata")
    + " FROM sequence_segments member JOIN segments segment USING(segment_id) "
    "CROSS JOIN LATERAL (SELECT segment.metadata_json::jsonb AS metadata) parsed "
    "WHERE member.sequence_id=%s AND jsonb_typeof(segment.metadata_json::jsonb -> 'message')='object'"
)
NOTICE_ROWS = (
    "SELECT position.sequence_id,'notice',operation.operation_id,position.ordinal,"
    "COALESCE(parsed.data ->> 'segment_type',''),parsed.data #>> '{metadata,message,message_id}',"
    "COALESCE(parsed.data #>> '{metadata,kind}',''),operation.created_at,"
    "parsed.data ->> 'created_by_actor_ref',parsed.data #>> '{metadata,message,business_ref}',"
    + _recipient_users("parsed.data #> '{metadata,message,recipients}'") + ","
    + _reply_facts("(parsed.data -> 'metadata')")
    + " FROM operation_positions position JOIN agent_operations operation USING(operation_id) "
    "CROSS JOIN LATERAL (SELECT operation.data_json::jsonb AS data) parsed "
    "WHERE position.sequence_id=%s AND operation.kind='notice' "
    "AND jsonb_typeof(operation.data_json::jsonb #> '{metadata,message}')='object'"
)


def mark(connection: Any, sequence_id: str) -> None:
    """The trace's rows and kept activity are current at its version."""
    connection.execute(
        "INSERT INTO chat_message_positions "
        "(sequence_id,source_kind,source_id,ordinal,segment_type,message_id,message_kind) "
        "SELECT sequence_id,'sync',%s,0,'',xmin::text,'' FROM sequences WHERE sequence_id=%s "
        "ON CONFLICT (sequence_id,source_kind,source_id) DO UPDATE SET message_id=EXCLUDED.message_id",
        (MARK, sequence_id),
    )


def current(connection: Any, sequence_id: str) -> bool:
    row = connection.execute(
        f"SELECT {_mark_of('trace.sequence_id')} IS DISTINCT FROM trace.xmin::text AS behind "
        "FROM sequences trace WHERE trace.sequence_id=%s",
        (sequence_id,),
    ).fetchone()
    return bool(row) and not row["behind"]


def lagging(connection: Any, sequence_ids: Sequence[str] | None = None) -> list[str]:
    """The traces among ``sequence_ids`` (every Agent's trace for None) whose mark is behind."""
    if sequence_ids is None:
        # Every mark in one scan of its partial index (chat_message_positions_mark_idx)
        # rather than one primary-key descent per trace: 50 ms for 7,018 traces before, 10 ms after.
        rows = connection.execute(
            "WITH mark AS (SELECT sequence_id, message_id FROM chat_message_positions "
            f"WHERE source_kind='sync' AND source_id='{MARK}') "
            "SELECT sequence.sequence_id FROM agents agent "
            "JOIN sequences sequence ON sequence.sequence_id=agent.trace_sequence_id "
            f"LEFT JOIN mark ON mark.sequence_id=sequence.sequence_id WHERE {BEHIND}"
        ).fetchall()
    else:
        requested = list(dict.fromkeys(str(item) for item in sequence_ids if str(item)))
        if not requested:
            return []
        rows = connection.execute(
            "SELECT trace.sequence_id FROM sequences trace WHERE trace.sequence_id=ANY(%s) "
            f"AND {_mark_of('trace.sequence_id')} IS DISTINCT FROM trace.xmin::text",
            (requested,),
        ).fetchall()
    return [str(row["sequence_id"]) for row in rows]


def bring_current(connection: Any, sequence_id: str) -> None:
    """Rebuild a trace whose mark is behind, under its row lock; a current trace is left alone."""
    locked = connection.execute(
        "SELECT 1 FROM sequences WHERE sequence_id=%s FOR UPDATE", (sequence_id,),
    ).fetchone()
    if locked is not None and not current(connection, sequence_id):
        refresh_sequence(connection, sequence_id)


def _add_activity(connection: Any, sequence_id: str, source_kind: str, source_ids: Sequence[str]) -> None:
    """Raise the activity of the people these new or changed messages concern."""
    connection.execute(
        "INSERT INTO chat_actor_activity(sequence_id, actor, latest_at) " + _ACTIVITY_ROWS
        + " AND position.source_kind=%s AND position.source_id=ANY(%s)" + _ACTIVITY_GROUP
        + " ON CONFLICT (sequence_id, actor) DO UPDATE SET"
        " latest_at=GREATEST(chat_actor_activity.latest_at, EXCLUDED.latest_at)",
        (RELAYED_COPY_PATTERN, sequence_id, sequence_id, source_kind, list(source_ids)),
    )


def refresh_activity(connection: Any, sequence_id: str) -> None:
    """Compute a locked trace's activity again from its rows, as after a context reset."""
    connection.execute("DELETE FROM chat_actor_activity WHERE sequence_id=%s", (sequence_id,))
    connection.execute(
        "INSERT INTO chat_actor_activity(sequence_id, actor, latest_at) " + _ACTIVITY_ROWS + _ACTIVITY_GROUP,
        (RELAYED_COPY_PATTERN, sequence_id, sequence_id),
    )


def upsert_segments(connection: Any, sequence_id: str, segment_ids: Sequence[str]) -> None:
    if not segment_ids:
        return
    connection.execute(
        f"INSERT INTO chat_message_positions {COLUMNS} {SEGMENT_ROWS} AND member.segment_id=ANY(%s) "
        f"ON CONFLICT (sequence_id,source_kind,source_id) DO UPDATE SET {UPDATE_COLUMNS}",
        (sequence_id, list(segment_ids)),
    )
    _add_activity(connection, sequence_id, "segment", segment_ids)


def upsert_notices(connection: Any, sequence_id: str, operation_ids: Sequence[str]) -> None:
    if not operation_ids:
        return
    connection.execute(
        f"INSERT INTO chat_message_positions {COLUMNS} {NOTICE_ROWS} AND position.operation_id=ANY(%s) "
        f"ON CONFLICT (sequence_id,source_kind,source_id) DO UPDATE SET {UPDATE_COLUMNS}",
        (sequence_id, list(operation_ids)),
    )
    _add_activity(connection, sequence_id, "notice", operation_ids)


def refresh_sequence(connection: Any, sequence_id: str) -> None:
    """Rebuild a locked trace: after reposition, replacement, deletion, or a mark left behind."""
    connection.execute(
        "DELETE FROM chat_message_positions WHERE sequence_id=%s AND source_kind<>'sync'",
        (sequence_id,),
    )
    connection.execute(f"INSERT INTO chat_message_positions {COLUMNS} {SEGMENT_ROWS}", (sequence_id,))
    connection.execute(f"INSERT INTO chat_message_positions {COLUMNS} {NOTICE_ROWS}", (sequence_id,))
    refresh_activity(connection, sequence_id)
    mark(connection, sequence_id)
