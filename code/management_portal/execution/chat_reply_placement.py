"""Chat reply placement, independent of the execution's original input.

Only a confirmed user-authored Provider append in the same durable trace can
change the display request. Resident collaboration does not replace that request.
Mailbox arrival, timestamps and current runtime state cannot.
Execution outcome/retry associations remain untouched.
"""
from __future__ import annotations

from typing import Any

from .conversation_projection import TYPED_BY_USER


def message_contexts(connection: Any, segment_ids: list[str], message_ids: list[str]) -> dict[str, dict]:
    """Durable trace position and the work containing a received side input.

    Context is an occurrence, not reply ownership. Confirmed collaboration and
    synchronous Portal exchanges retain their position without becoming requests.
    """
    rows = connection.execute(
        "SELECT position.source_id AS segment_id,trace.agent_id,position.ordinal,context.message_id AS context_id "
        "FROM chat_message_positions position LEFT JOIN segments source "
        "ON position.source_id=source.segment_id AND position.source_kind='segment' "
        "JOIN agents trace ON trace.trace_sequence_id=position.sequence_id "
        "AND (source.segment_id IS NULL OR trace.agent_id=source.created_by_agent_id) "
        "LEFT JOIN LATERAL (SELECT candidate.message_id FROM chat_message_positions candidate "
        "JOIN segments input ON input.segment_id=candidate.source_id "
        "LEFT JOIN LATERAL (SELECT primary_input.execution_id FROM execution_inputs primary_input "
        "JOIN executions execution USING(execution_id) WHERE primary_input.message_id=candidate.message_id "
        "AND primary_input.role='primary' AND execution.agent_id=trace.agent_id "
        "ORDER BY execution.created_at DESC LIMIT 1) reserved ON TRUE "
        "WHERE candidate.sequence_id=position.sequence_id AND candidate.source_kind='segment' "
        "AND candidate.segment_type='input' AND candidate.ordinal<position.ordinal "
        "AND ((input.metadata_json::jsonb->>'kind'='appended_provider_input' "
        f"AND {TYPED_BY_USER.format(metadata='input.metadata_json')}) OR reserved.execution_id IS NOT NULL) "
        "AND (NULLIF(source.execution_id,'') IS NULL "
        "OR source.execution_id=COALESCE(NULLIF(input.execution_id,''),reserved.execution_id)) "
        "ORDER BY candidate.ordinal DESC LIMIT 1) context ON ("
        "(source.metadata_json::jsonb->>'kind'='appended_provider_input' "
        f"AND NOT ({TYPED_BY_USER.format(metadata='source.metadata_json')})) "
        "OR (NULLIF(source.execution_id,'') IS NULL AND ("
        "(position.segment_type='input' AND EXISTS (SELECT 1 FROM chat_message_positions receipt "
        "WHERE receipt.sequence_id=position.sequence_id AND receipt.segment_type='harness' "
        "AND receipt.reply_to=position.message_id)) "
        "OR (position.segment_type='harness' AND position.reply_to IS NOT NULL)))) "
        "WHERE position.message_id=ANY(%s) AND position.source_id=ANY(%s) "
        "AND position.source_kind<>'sync'", (message_ids, segment_ids),
    ).fetchall()
    return {str(row['segment_id']): {
        'conversation_position': {'resident_id': str(row['agent_id']), 'ordinal': int(row['ordinal'])},
        **({'conversation_context': {'request_message_id': str(row['context_id'])}} if row['context_id'] else {}),
    } for row in rows}


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
        f"AND {TYPED_BY_USER.format(metadata='input.metadata_json')} "
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
        f"AND {TYPED_BY_USER.format(metadata='appended.metadata_json')} "
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
