"""Chat reply placement, independent of the execution's original input.

Only a confirmed Provider append in the same durable trace can change the
display request. Mailbox arrival, timestamps and current runtime state cannot.
Execution outcome/retry associations remain untouched.
"""
from __future__ import annotations

from typing import Any


def reply_placements(connection: Any, segment_ids: list[str]) -> dict[str, str]:
    rows = connection.execute(
        "SELECT output.segment_id, appended.message_id FROM segments output "
        "JOIN sequence_segments occurrence ON occurrence.segment_id=output.segment_id "
        "JOIN agents trace ON trace.trace_sequence_id=occurrence.sequence_id AND trace.agent_id=output.created_by_agent_id "
        "CROSS JOIN LATERAL (SELECT position.message_id FROM segments input "
        "JOIN chat_message_positions position ON position.source_id=input.segment_id "
        "AND position.sequence_id=occurrence.sequence_id AND position.source_kind='segment' "
        "WHERE input.execution_id=output.execution_id AND input.created_by_agent_id=output.created_by_agent_id "
        "AND input.metadata_json::jsonb->>'kind'='appended_provider_input' "
        "AND position.ordinal<occurrence.ordinal "
        "ORDER BY position.ordinal DESC LIMIT 1) appended "
        "WHERE output.segment_id=ANY(%s) AND output.segment_type='model'",
        (segment_ids,),
    ).fetchall()
    return {str(row['segment_id']): str(row['message_id']) for row in rows if row['message_id']}


def reply_windows(connection: Any, windows: list[tuple]) -> list[tuple]:
    """Resolve a requested display input to its execution reply window in batch."""
    if not windows:
        return []
    rows = connection.execute(
        "SELECT requested.sequence_id,requested.message_id, source.execution_id, "
        "COALESCE(primary_input.message_id,requested.message_id) AS reply_to, "
        "(SELECT MIN(position.ordinal) FROM segments appended "
        "JOIN chat_message_positions position ON position.source_id=appended.segment_id "
        "AND position.sequence_id=requested.sequence_id AND position.source_kind='segment' "
        "WHERE appended.execution_id=COALESCE(NULLIF(source.execution_id,''), reservation.execution_id) "
        "AND appended.metadata_json::jsonb->>'kind'='appended_provider_input' "
        "AND position.ordinal>requested.after_ordinal) AS end_ordinal "
        "FROM unnest(%s::text[],%s::text[],%s::bigint[]) requested(sequence_id,message_id,after_ordinal) "
        "JOIN chat_message_positions current ON current.sequence_id=requested.sequence_id "
        "AND current.message_id=requested.message_id AND current.source_kind='segment' "
        "JOIN segments source ON source.segment_id=current.source_id "
        "LEFT JOIN LATERAL (SELECT input.execution_id FROM execution_inputs input "
        "JOIN executions execution USING(execution_id) WHERE input.message_id=requested.message_id "
        "AND input.role='primary' AND execution.agent_id=source.created_by_agent_id "
        "ORDER BY execution.created_at DESC LIMIT 1) reservation ON TRUE "
        "LEFT JOIN execution_inputs primary_input ON primary_input.execution_id=COALESCE(NULLIF(source.execution_id,''), reservation.execution_id) "
        "AND primary_input.role='primary'",
        ([w[2] for w in windows], [w[1] for w in windows], [w[3] for w in windows]),
    ).fetchall()
    resolved = {(str(row['sequence_id']), str(row['message_id'])): row for row in rows}
    result = []
    for agent, message, sequence, start, end in windows:
        row = resolved.get((sequence, message))
        reply_to = str(row['reply_to']) if row else message
        if row and row['end_ordinal'] is not None:
            end = min(end, int(row['end_ordinal']) - 1)
        result.append((agent, message, sequence, start, end, reply_to))
    return result
