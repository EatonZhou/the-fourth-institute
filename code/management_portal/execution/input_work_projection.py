"""Work belonging to an input, independently of its transport completion.

Reservations identify primary inputs. A materialized Provider append identifies
one particular execution and turn; it must never follow the primary's retries.
This reader uses only AgentData facts and does not infer work from Chat state.
"""
from __future__ import annotations

from collections.abc import Sequence
from collections import Counter
from typing import Any

from management_common.json_columns import json_value
from management_common.database_time import iso_time

from .agent_data_chat import SHOWN_POSITION
from .agent_data_store import in_batches, stable_segment_id


def _unknown() -> dict[str, str]:
    return {"state": "unknown", "kind": "unknown", "delivery": "unknown", "updated_at": ""}


def _state(value: str) -> str:
    if value in {"preparing", "queued"}:
        return "queued"
    return value if value in {"running", "completed", "failed", "cancelled"} else "unknown"


def _updated(row: Any, *, delivered_at: Any = None) -> str:
    state = _state(row["status"])
    if state == "queued":
        return iso_time(row["created_at"]) or ""
    if state == "running":
        facts = [value for value in (row["started_at"], delivered_at) if value is not None]
        return iso_time(max(facts)) if facts else ""
    if state in {"completed", "failed", "cancelled"}:
        return iso_time(row["finished_at"]) or ""
    return ""


def execution_input_work_states(store: Any, agent_id: str, execution_id: str) -> dict[str, dict[str, str]]:
    """Inputs of exactly this execution, for work-change event publication.

    Enumerate its indexed reservations and append Segments only. Passing the
    execution through resolution freezes append scope. Superseded primary
    reservations are omitted so an old callback cannot rewind a retried input.
    """
    with store.database.read() as connection:
        rows = connection.execute(
            "SELECT i.message_id FROM execution_inputs i JOIN executions e USING(execution_id) "
            "WHERE i.execution_id=%s AND e.agent_id=%s "
            "UNION SELECT s.metadata_json::jsonb#>>'{message,message_id}' AS message_id "
            "FROM segments s JOIN executions e USING(execution_id) "
            "WHERE s.execution_id=%s AND e.agent_id=%s AND s.created_by_agent_id=%s "
            "AND s.metadata_json::jsonb->>'kind'='appended_provider_input'",
            (execution_id, agent_id, execution_id, agent_id, agent_id),
        ).fetchall()
        return _read_work(store, agent_id, [row["message_id"] for row in rows], execution_id=execution_id)


def input_work_states(store: Any, agent_id: str, message_ids: Sequence[str]) -> dict[str, dict[str, str]]:
    """Return explicit unknowns as well as proven associations, in bounded batches.

    Primary/member reservations follow that input's latest execution, as explicit
    retries do. Info references alone prove no work. Appends require the durable
    confirmed-delivery derivation, not merely an input Segment marked completed.
    An append's delivery survives a turn change, but its work state becomes unknown.
    """
    return _read_work(store, agent_id, message_ids)


def _read_work(store: Any, agent_id: str, message_ids: Sequence[str], *, execution_id: str = "") -> dict[str, dict[str, str]]:
    wanted = list(dict.fromkeys(str(value) for value in message_ids if value))
    result = {message_id: _unknown() for message_id in wanted}
    if not wanted:
        return result
    with store.database.read() as connection:
        trace = store.chat.chat_traces([agent_id]).get(agent_id, "") if not execution_id else ""
        for chunk in in_batches(wanted):
            # Keep a malformed append visible to the resolver: it must not fall
            # through to an unrelated reservation just because evidence is bad.
            if execution_id:
                segment_source = (
                    "JOIN segments segment ON segment.execution_id=%s "
                    "AND segment.metadata_json::jsonb#>>'{message,message_id}'=requested.message_id "
                    "AND segment.metadata_json::jsonb->>'kind'='appended_provider_input' "
                )
                source_parameters = (execution_id,)
            else:
                segment_source = (
                    "JOIN LATERAL (SELECT position.source_id FROM chat_message_positions position "
                    "WHERE position.sequence_id=%s AND position.message_id=requested.message_id "
                    "AND position.source_kind='segment' "
                    f"AND {SHOWN_POSITION} ORDER BY position.ordinal DESC LIMIT 1) position ON true "
                    "JOIN segments segment ON segment.segment_id=position.source_id "
                )
                source_parameters = (trace,)
            segments = connection.execute(
                "SELECT requested.message_id,segment.segment_id,segment.segment_type,"
                "segment.metadata_json,segment.execution_id,segment.created_by_agent_id,"
                "e.status,e.provider_turn_id,e.agent_id AS execution_agent_id,"
                "e.created_at,e.started_at,e.finished_at,segment.finished_at AS delivered_at,"
                "(SELECT COUNT(*) FROM segment_relations relation "
                "JOIN segments source ON source.segment_id=relation.source_segment_id "
                "WHERE relation.target_segment_id=segment.segment_id "
                "AND relation.relation_type='transformed_from' "
                "AND relation.operation_type='provider_input.append' "
                "AND relation.operation_id=segment.metadata_json::jsonb->>'control_id' "
                "AND source.created_by_agent_id=%s AND source.segment_type='input' "
                "AND source.metadata_json::jsonb#>>'{message,message_id}'=requested.message_id) AS sources "
                "FROM unnest(%s::text[]) requested(message_id) "
                + segment_source +
                "LEFT JOIN executions e ON e.execution_id=segment.execution_id",
                (agent_id, list(chunk), *source_parameters),
            ).fetchall()
            reservations = connection.execute(
                "SELECT DISTINCT ON (i.message_id) i.message_id,e.execution_id,e.status,e.provider_turn_id,"
                "e.created_at,e.started_at,e.finished_at "
                "FROM execution_inputs i JOIN executions e USING(execution_id) "
                "WHERE e.agent_id=%s AND i.message_id=ANY(%s) AND i.role IN ('primary','member') "
                "ORDER BY i.message_id,e.created_at DESC,e.execution_id DESC",
                (agent_id, list(chunk)),
            ).fetchall()
            executions = list(dict.fromkeys(
                str(row["execution_id"]) for row in [*segments, *reservations] if row["execution_id"]
            ))
            primaries: dict[str, list[str]] = {}
            for identities in in_batches(executions):
                rows = connection.execute(
                    "SELECT execution_id,message_id FROM execution_inputs "
                    "WHERE execution_id=ANY(%s) AND role='primary'", (list(identities),),
                ).fetchall()
                for row in rows:
                    primaries.setdefault(str(row["execution_id"]), []).append(str(row["message_id"]))

            for row in reservations:
                identity = str(row["execution_id"])
                if execution_id and identity != execution_id:
                    result.pop(str(row["message_id"]), None)
                    continue
                primary = primaries.get(identity, [])
                if len(primary) != 1:
                    continue
                turn = str(row["provider_turn_id"] or "")
                work = {"state": _state(row["status"]), "kind": "primary",
                        "execution_id": identity, "primary_message_id": primary[0], "updated_at": _updated(row),
                        "delivery": "delivered" if turn else "pending"}
                if turn:
                    work["provider_turn_id"] = turn
                result[str(row["message_id"])] = work

            append_counts = Counter(str(row["message_id"]) for row in segments
                                    if json_value(row["metadata_json"]).get("kind") == "appended_provider_input")
            for row in segments:
                metadata = json_value(row["metadata_json"])
                if metadata.get("kind") != "appended_provider_input":
                    continue
                message_id = str(row["message_id"])
                result[message_id] = _unknown()
                control = metadata.get("control_id")
                turn = metadata.get("provider_turn_id")
                if (not isinstance(control, str) or not control or not isinstance(turn, str) or not turn
                        or row["segment_type"] != "input" or row["created_by_agent_id"] != agent_id
                        or row["execution_agent_id"] != agent_id or row["sources"] != 1
                        or row["segment_id"] != stable_segment_id("execution-appended-input", control)):
                    continue
                identity = str(row["execution_id"])
                primary = primaries.get(identity, [])
                work = {"state": "unknown", "kind": "appended", "delivery": "delivered",
                        "execution_id": identity, "provider_turn_id": turn, "updated_at": ""}
                if len(primary) == 1:
                    work["primary_message_id"] = primary[0]
                    if row["provider_turn_id"] == turn and append_counts[message_id] == 1:
                        work["state"] = _state(row["status"])
                        work["updated_at"] = _updated(row, delivered_at=row["delivered_at"])
                if append_counts[message_id] > 1:
                    # Several confirmed deliveries of the same message in one
                    # execution do not identify one frozen turn for this API.
                    work.pop("provider_turn_id", None)
                result[message_id] = work
    return result
