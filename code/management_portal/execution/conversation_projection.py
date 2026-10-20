"""Which trace Segments are Chat messages, and which of them can answer an input.

Chat reads read the trace's message projection (chat_position_index), which
records these facts per message; the queries that find messages among the
Segments themselves use these predicates.
"""
from __future__ import annotations


# A Portal communication is identified by semantic Message facts (identity,
# authorship and delivery). This policy says nothing about other clients, which
# may display reasoning, tools or every Segment instead.
CHAT_PREDICATE = "jsonb_typeof(metadata_json::jsonb -> 'message') = 'object'"
CHAT_SEGMENT = "jsonb_typeof(segment.metadata_json::jsonb -> 'message') = 'object'"
# Turn/changes/subagent cards describe work in the UI; they cannot answer an
# input or supersede its textual/structured reply merely by arriving later.
REPLY_SEGMENT = """NOT (segment.metadata_json::jsonb->'message' ? 'harness_input') AND NOT EXISTS (
 SELECT 1 FROM jsonb_array_elements(COALESCE(segment.metadata_json::jsonb#>'{message,parts}', '[]'::jsonb)) part
 WHERE part->>'type' IN ('native_turn','resource_changes','subagent_run','tool_call','tool_result')
)"""
