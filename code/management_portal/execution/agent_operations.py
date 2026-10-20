"""Operational facts, independent of model content and its trace.

An operation is a receipt, context delivery, clear action, or UI notice/card.
Positions locate actions alongside a sequence without making them its members.
"""
from __future__ import annotations

from typing import Any

from management_common.json_columns import json_text, json_value


TABLE_SQL = """
CREATE TABLE agent_operations (
    operation_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    session_ref TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    data_json JSONB NOT NULL
);
CREATE INDEX agent_operations_session_idx ON agent_operations(session_ref, agent_id, kind);
-- A trace's last context reset (where its Chat starts) among its few resets,
-- not by reading every operation the trace positions.
CREATE INDEX operations_context_reset_idx ON agent_operations(kind, operation_id)
    WHERE kind IN ('context_reset', 'provider_context_reset');
CREATE TABLE operation_positions (
    sequence_id TEXT NOT NULL REFERENCES sequences(sequence_id),
    operation_id TEXT NOT NULL REFERENCES agent_operations(operation_id),
    ordinal BIGINT NOT NULL,
    PRIMARY KEY(sequence_id, operation_id)
);
CREATE INDEX operation_positions_order_idx ON operation_positions(sequence_id, ordinal);
-- An operation's positions by the operation, as sequence_segments has for
-- segments: the traces a notice or a context reset is in.
CREATE INDEX operation_positions_operation_idx ON operation_positions(operation_id, sequence_id);
"""


def put_operation(connection: Any, *, operation_id: str, kind: str, agent_id: str,
                  created_at: str, data: dict, session_ref: str = "") -> str:
    encoded = json_text(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    from .harness_retention import protect_artifact_references
    protect_artifact_references(connection, encoded.encode())
    existing = connection.execute("SELECT kind, agent_id, session_ref, data_json FROM agent_operations WHERE operation_id=%s", (operation_id,)).fetchone()
    if existing:
        if existing["kind"] != kind or existing["agent_id"] != agent_id or existing["session_ref"] != session_ref or json_value(existing["data_json"]) != json_value(encoded):
            raise ValueError("Operation identity refers to different facts")
        return operation_id
    connection.execute("INSERT INTO agent_operations VALUES (%s,%s,%s,%s,%s,%s)",
        (operation_id, kind, agent_id, session_ref, created_at, encoded))
    return operation_id


def next_position(connection: Any, sequence_id: str) -> int:
    row = connection.execute(
        "SELECT GREATEST(COALESCE((SELECT MAX(ordinal) FROM sequence_segments WHERE sequence_id=%s),0), "
        "COALESCE((SELECT MAX(ordinal) FROM operation_positions WHERE sequence_id=%s),0)) + 1 AS ordinal",
        (sequence_id, sequence_id),
    ).fetchone()
    return int(row["ordinal"])
