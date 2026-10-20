"""Invocation-local preconditions, checked only by short definition writes."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable

from management_portal.hatg.contracts import InstructionError, invalid
from management_portal.hatg.kernel import Invocation


from management_portal.hatg.lineage_versions import DEFINITION_MUTATIONS


def _targets(inv: Invocation) -> set[str]:
    from .isa.common import lineage_of_resident, store

    arguments = inv.arguments
    targets = {str(arguments[name]) for name in ("lineage_id", "target_lineage_id") if arguments.get(name)}
    for name in ("resident_id", "source_resident_id", "target_resident_id"):
        if arguments.get(name):
            targets.add(lineage_of_resident(arguments[name]))
    for name in ("seed_id", "handoff_id", "instance_id"):
        if arguments.get(name):
            lineage_id = store().lineage_id_for_object(arguments[name])
            if lineage_id:
                targets.add(lineage_id)
    for object_id in arguments.get("object_ids") or []:
        lineage_id = lineage_of_resident(object_id) if object_id.startswith("resident_") else store().lineage_id_for_object(object_id)
        if lineage_id:
            targets.add(lineage_id)
    return targets


@dataclass
class _Preconditions:
    expected: dict[str, Any]
    checked_transactions: dict[str, str] = field(default_factory=dict)


_PRECONDITIONS: ContextVar[_Preconditions | None] = ContextVar("lineage_definition_preconditions", default=None)


def check_definition_version(connection: Any, lineage_id: str) -> None:
    """Check the first definition commit in this invocation, under its row lock.

    Remember the database transaction, not just a Python boolean: a failed
    transaction may be retried and must not consume the caller's precondition.
    """
    context = _PRECONDITIONS.get()
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


def guard_definition_handler(name: str, handler: Callable[[Invocation], Any]) -> Callable[[Invocation], Any]:
    if name not in DEFINITION_MUTATIONS:
        return handler

    @wraps(handler)
    def guarded(inv: Invocation) -> Any:
        context = None
        if inv.expected_version is not None:
            targets = _targets(inv)
            if not targets:
                raise invalid("expected_version 需要已有 Lineage；版本来自 GetLineage 的 lineage.revision")
            if isinstance(inv.expected_version, dict) and not set(inv.expected_version) <= targets:
                raise invalid("expected_version 映射的键必须是本次修改的 Lineage ID")
            context = _Preconditions({lineage_id: inv.expected_version_for(lineage_id)
                for lineage_id in targets if inv.expected_version_for(lineage_id) is not None})
        token = _PRECONDITIONS.set(context)
        try:
            return handler(inv)
        finally:
            _PRECONDITIONS.reset(token)

    return guarded
