"""Agent-owned provenance for authorized retries of the same admitted input.

A retry is independent work. It does not continue or change the Trigger Path
that delivered an earlier Execution, unlike an unstarted internal handoff.
"""
from __future__ import annotations

from typing import Any, Iterable

from management_common.json_columns import json_value
from management_common.portal_database import _database_schema


def _prefix() -> str:
    return '"' + _database_schema("agent") + '".'


def _execution(connection: Any, identity: str):
    return connection.execute(
        f"SELECT execution_id,agent_id,actor_ref,status,started_at FROM {_prefix()}executions "
        "WHERE execution_id=%s", (identity,),
    ).fetchone()


def _inputs(connection: Any, identity: str) -> dict[str, str]:
    return {row["message_id"]: row["role"] for row in connection.execute(
        f"SELECT message_id,role FROM {_prefix()}execution_inputs "
        "WHERE execution_id=%s AND role IN ('primary','member')", (identity,),
    ).fetchall()}


def is_whole_retry(connection: Any, predecessor: str, message_id: str) -> bool:
    return _inputs(connection, predecessor).get(message_id) == "primary"


def _validate(connection: Any, predecessor: str, execution_id: str, resident_id: str, message_id: str):
    source = _execution(connection, predecessor)
    successor = _execution(connection, execution_id)
    inputs = _inputs(connection, predecessor)
    if (not source or not successor or predecessor == execution_id
            or source["status"] not in {"failed", "cancelled"}
            or source["agent_id"] != resident_id or successor["agent_id"] != resident_id
            or source["actor_ref"] != successor["actor_ref"]
            or inputs.get(message_id) != "primary"
            or inputs != _inputs(connection, execution_id)):
        raise ValueError("Input retry must retain its terminal predecessor, Resident, actor and original inputs")
    return successor


def record(connection: Any, *, predecessor: str, execution_id: str,
           resident_id: str, message_id: str, at: str) -> None:
    """Commit the recovery owner's provenance with reservation and input links."""
    from .agent_operations import put_operation
    from .execution_lifecycle import _agent_transaction

    successor = _validate(connection, predecessor, execution_id, resident_id, message_id)
    identity = "input_retry:" + execution_id
    existing = connection.execute(
        f"SELECT operation_id FROM {_prefix()}agent_operations WHERE operation_id=%s", (identity,),
    ).fetchone()
    if not existing and (successor["status"] != "preparing" or successor["started_at"] is not None):
        raise ValueError("Input retry provenance must be recorded during reservation")
    with _agent_transaction(connection) as agent_connection:
        put_operation(
            agent_connection, operation_id=identity, kind="input_retry", agent_id=resident_id, created_at=at,
            data={"predecessor_execution_id": predecessor, "execution_id": execution_id,
                  "input_message_id": message_id},
        )


def _validate_member(connection: Any, predecessor: str, execution_id: str,
                     resident_id: str, source_message_id: str, group_message_id: str, source_actor: str):
    source = _execution(connection, predecessor)
    successor = _execution(connection, execution_id)
    original = _inputs(connection, predecessor)
    grouped = _inputs(connection, execution_id)
    if (not source or not successor or predecessor == execution_id
            or source["status"] not in {"failed", "cancelled"}
            or source["agent_id"] != resident_id or successor["agent_id"] != resident_id
            or source["actor_ref"] != source_actor
            or original.get(source_message_id) != "primary"
            or grouped.get(group_message_id) != "primary"
            or source_message_id == group_message_id
            or any(grouped.get(message) != "member" for message in original)):
        raise ValueError("Grouped retry must retain its terminal source and all original inputs as members")
    return successor, original


def record_member(connection: Any, *, predecessor: str, execution_id: str,
                  resident_id: str, source_message_id: str, group_message_id: str, source_actor: str, at: str) -> None:
    """Freeze an owner-authorized requeued input's provenance in a new group.

    The Mailbox organizer can differ from the original sender. This explicit
    relation does not relax the actor/input equality contract of a whole retry.
    """
    from .agent_operations import put_operation
    from .execution_lifecycle import _agent_transaction

    successor, original = _validate_member(connection, predecessor, execution_id,
                                           resident_id, source_message_id, group_message_id, source_actor)
    for message in original:
        identity = "input_retry_member:" + execution_id + ":" + message
        existing = connection.execute(
            f"SELECT operation_id FROM {_prefix()}agent_operations WHERE operation_id=%s", (identity,),
        ).fetchone()
        if not existing and (successor["status"] != "preparing" or successor["started_at"] is not None):
            raise ValueError("Grouped retry provenance must be recorded during reservation")
        with _agent_transaction(connection) as agent_connection:
            put_operation(agent_connection, operation_id=identity, kind="input_retry_member",
                          agent_id=resident_id, created_at=at,
                          data={"predecessor_execution_id":predecessor, "execution_id":execution_id,
                                "source_message_id":source_message_id, "group_message_id":group_message_id,
                                "source_actor":source_actor, "input_message_id":message})


def independent_of(connection: Any, *, predecessors: Iterable[str], execution_id: str,
                   resident_id: str, message_id: str) -> bool:
    """Recognize exact retry ancestry without authorizing a new graph link."""
    ancestors = set(predecessors)
    if execution_id in ancestors:
        return False  # This Execution is already part of this path.
    return any(predecessor in ancestors for predecessor in _ancestors(
        connection, execution_id=execution_id, resident_id=resident_id, message_id=message_id))


def original_execution(connection: Any, *, execution_id: str, resident_id: str, message_id: str) -> str:
    """Resolve a late source step to its original delivery, never a new retry."""
    original = execution_id
    for predecessor in _ancestors(connection, execution_id=execution_id,
                                  resident_id=resident_id, message_id=message_id):
        original = predecessor
    return original


def _ancestors(connection: Any, *, execution_id: str, resident_id: str, message_id: str):
    seen: set[str] = set()
    current = execution_id
    while current:
        if current in seen:
            raise ValueError("Cyclic input retry provenance")
        seen.add(current)
        member = connection.execute(
            f"SELECT kind,agent_id,data_json FROM {_prefix()}agent_operations WHERE operation_id=%s",
            ("input_retry_member:" + current + ":" + message_id,),
        ).fetchone()
        if member is not None:
            data = json_value(member["data_json"])
            predecessor = str(data.get("predecessor_execution_id") or "")
            if (member["kind"] != "input_retry_member" or member["agent_id"] != resident_id
                    or data.get("execution_id") != current or data.get("input_message_id") != message_id):
                raise ValueError("Invalid durable grouped retry provenance")
            _, original = _validate_member(connection, predecessor, current, resident_id,
                                           str(data.get("source_message_id") or ""),
                                           str(data.get("group_message_id") or ""),
                                           str(data.get("source_actor") or ""))
            if message_id not in original:
                raise ValueError("Grouped retry does not contain the graph input")
            yield predecessor
            current = predecessor
            continue
        row = connection.execute(
            f"SELECT kind,agent_id,data_json FROM {_prefix()}agent_operations WHERE operation_id=%s",
            ("input_retry:" + current,),
        ).fetchone()
        if row is None:
            return
        data = json_value(row["data_json"])
        predecessor = str(data.get("predecessor_execution_id") or "")
        if (row["kind"] != "input_retry" or row["agent_id"] != resident_id
                or data.get("execution_id") != current):
            raise ValueError("Invalid durable input retry provenance")
        _validate(connection, predecessor, current, resident_id, str(data.get("input_message_id") or ""))
        if message_id not in _inputs(connection, current):
            raise ValueError("Input retry does not contain the graph input")
        yield predecessor
        current = predecessor
