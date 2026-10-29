"""Invocation-local preconditions, checked only by short definition writes.

``isa.common.guard_definition_handler`` sets them for one instruction.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from management_portal.hatg.contracts import InstructionError


@dataclass
class Preconditions:
    expected: dict[str, Any]
    checked_transactions: dict[str, str] = field(default_factory=dict)


PRECONDITIONS: ContextVar[Preconditions | None] = ContextVar("lineage_definition_preconditions", default=None)


def check_definition_version(connection: Any, lineage_id: str) -> None:
    """Check the first definition commit in this invocation, under its row lock.

    Remember the database transaction, not just a Python boolean: a failed
    transaction may be retried and must not consume the caller's precondition.
    """
    context = PRECONDITIONS.get()
    if context is None or lineage_id not in context.expected:
        return
    previous = context.checked_transactions.get(lineage_id)
    if previous:
        transaction = connection.execute(
            "SELECT pg_current_xact_id()::text AS current_id, pg_xact_status(%s::xid8) AS status",
            (previous,),
        ).fetchone()
        if transaction["status"] == "committed":
            context.expected.pop(lineage_id)
            return
        if transaction["current_id"] == previous:
            return
    current = connection.execute(
        "SELECT revision, pg_current_xact_id()::text AS transaction_id FROM lineages "
        "WHERE lineage_id=%s AND deleted_at IS NULL FOR NO KEY UPDATE", (lineage_id,),
    ).fetchone()
    revision = int(current["revision"]) if current else 0
    expected = context.expected[lineage_id]
    if current is None or str(expected) != str(revision):
        raise InstructionError("instruction.conflict", "Lineage 定义已变化，请重新读取 GetLineage", details={
            "object_id": lineage_id, "expected_version": expected, "current_version": revision,
        })
    context.checked_transactions[lineage_id] = str(current["transaction_id"])
