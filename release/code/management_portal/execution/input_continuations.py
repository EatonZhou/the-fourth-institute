"""Read the durable succession of an input withdrawn before its first claim.

Execution remains an immutable audit fact. Only its owner's handoff receipt
allows an input to await a successor instead of inheriting an internal cancel.
These reads borrow the caller's transaction and never submit or reserve work.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from management_common.json_columns import json_value
from management_common.portal_database import _database_schema


def _prefix() -> str:
    return '"' + _database_schema("agent") + '".'


def _execution(connection: Any, identity: str):
    return connection.execute(
        f"SELECT execution_id,agent_id,actor_ref,status,started_at,finished_at "
        f"FROM {_prefix()}executions WHERE execution_id=%s", (identity,),
    ).fetchone()


def _inputs(connection: Any, identity: str) -> set[str]:
    return {r["message_id"] for r in connection.execute(
        f"SELECT message_id FROM {_prefix()}execution_inputs "
        "WHERE execution_id=%s AND role IN ('primary','member')", (identity,),
    ).fetchall()}


def _receipt(connection: Any, source: Mapping[str, Any]) -> dict[str, str]:
    row = connection.execute(
        f"SELECT kind,agent_id,data_json FROM {_prefix()}agent_operations WHERE operation_id=%s",
        ("execution_handoff:" + source["execution_id"],),
    ).fetchone()
    if row is None:
        return {}
    receipt = json_value(row["data_json"]).get("receipt") or {}
    if (row["kind"] != "execution_handoff" or row["agent_id"] != source["agent_id"]
            or source["status"] != "cancelled" or source["started_at"] is not None
            or receipt.get("execution_id") != source["execution_id"]
            or receipt.get("binding_id") != "resident:" + source["agent_id"]
            or not receipt.get("operation_id") or not receipt.get("replacement_execution_id")
            or receipt["replacement_execution_id"] == source["execution_id"]):
        raise ValueError("Invalid durable unstarted input handoff")
    return receipt


def _validate_successor(connection: Any, source: Mapping[str, Any], successor: Mapping[str, Any]) -> None:
    original_inputs = _inputs(connection, source["execution_id"])
    if (source["agent_id"] != successor["agent_id"] or source["actor_ref"] != successor["actor_ref"]
            or not original_inputs
            or not original_inputs.issubset(_inputs(connection, successor["execution_id"]))):
        raise ValueError("Input continuation must retain its Resident, actor and admitted inputs")


def permits_link(connection: Any, *, predecessors: Iterable[str], execution_id: str,
                 resident_id: str, message_id: str) -> bool:
    """Validate a specific new link, not a permission to reopen terminal work."""
    successor = _execution(connection, execution_id)
    if (successor is None or successor["agent_id"] != resident_id
            or successor["started_at"] is not None or successor["status"] not in {"preparing", "queued"}
            or message_id not in _inputs(connection, execution_id)):
        return False
    for identity in predecessors:
        source = _execution(connection, identity)
        if source is None or source["agent_id"] != resident_id or message_id not in _inputs(connection, identity):
            continue
        receipt = _receipt(connection, source)
        if receipt.get("replacement_execution_id") == execution_id:
            _validate_successor(connection, source, successor)
            return True
    return False


def outcomes(connection: Any, executions: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Project input outcomes while leaving all physical Execution facts intact.

    A committed handoff with no reserved successor is pending, not cancelled.
    Chains follow only exact owner receipts; malformed or cyclic chains fail
    closed. A later ordinary requeue is never a continuation of these receipts.
    """
    executions = list(executions)
    known = {r["execution_id"]: r for r in executions}
    resolved: dict[str, dict[str, Any]] = {}
    def resolve(identity: str, visiting: set[str]) -> dict[str, Any]:
        if identity in visiting:
            raise ValueError("Cyclic input execution handoff")
        if identity in resolved:
            return resolved[identity]
        observed = known.get(identity)
        if observed is not None and "status" in observed and observed["status"] != "cancelled":
            result = {"status": observed["status"], "finished_at": observed["finished_at"], "execution_id": identity}
            resolved[identity] = result
            return result
        source = _execution(connection, identity)
        if source is None:
            return {"status": "queued", "finished_at": None, "execution_id": identity}
        receipt = _receipt(connection, source)
        result = {"status": source["status"], "finished_at": source["finished_at"], "execution_id": identity}
        if receipt:
            successor = _execution(connection, receipt["replacement_execution_id"])
            if successor is not None:
                _validate_successor(connection, source, successor)
            result = {**resolve(receipt["replacement_execution_id"], visiting | {identity}), "handoff": receipt}
        resolved[identity] = result
        return result
    return {row["execution_id"]: resolve(row["execution_id"], set()) for row in executions}
