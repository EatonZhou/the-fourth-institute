"""Canonical mutation scope: object consistency, independent of ISA authorization."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any

from management_portal.hatg.contracts import invalid

# These instructions formerly inferred their source solely from an object ID.
SOURCE_ARGUMENTS = {
    "ForkResident": ("source_resident_id",),
    "PromoteSeed": ("seed_id",),
    "UpdateResident": ("resident_id",),
    "DeleteResident": ("resident_id",),
    "MoveResident": ("resident_id",),
    "MoveLineageObjects": ("object_ids",),
    "CreateCandidateFork": ("seed_id", "source_resident_id"),
    "DeleteCandidateFork": ("seed_id",),
    "CreateWorkspaceHandoff": ("source_resident_id", "target_resident_id"),
    "UpdateWorkspaceHandoff": ("handoff_id",),
    "DeleteWorkspaceHandoff": ("handoff_id",),
    "PrepareWorkspaceHandoff": ("handoff_id", "source_resident_id", "target_resident_id"),
}


@dataclass
class _Scope:
    lineage_id: str
    object_ids: frozenset[str]
    transaction_id: str = ""


_SCOPE: ContextVar[_Scope | None] = ContextVar("lineage_definition_scope", default=None)


def _require_members(lineage: dict[str, Any], scope: _Scope) -> None:
    program = lineage.get("program") or {}
    present = {str(item.get("id") or "") for collection in (
        lineage.get("nodes"), lineage.get("edges"), program.get("components"),
        program.get("wires"), lineage.get("function_blocks"),
    ) for item in collection or []}
    missing = scope.object_ids - present
    if missing:
        raise invalid("对象不属于声明的 Lineage", lineage_id=scope.lineage_id, object_ids=sorted(missing))


def check_definition_scope(connection: Any, current: dict[str, Any] | None, lineage_id: str) -> None:
    """Recheck the source in the actual write transaction under its definition lock.

    A committed earlier step may intentionally remove its original objects.
    An aborted transaction must not consume this check.
    """
    scope = _SCOPE.get()
    if scope is None or scope.lineage_id != lineage_id or not scope.object_ids:
        return
    if scope.transaction_id:
        row = connection.execute(
            "SELECT pg_current_xact_id()::text AS current_id, pg_xact_status(%s::xid8) AS status",
            (scope.transaction_id,),
        ).fetchone()
        if row["status"] == "committed" or row["current_id"] == scope.transaction_id:
            return
    _require_members(current or {}, scope)
    scope.transaction_id = connection.execute("SELECT pg_current_xact_id()::text AS id").fetchone()["id"]


def guard_source_scope(name, handler):
    if name not in SOURCE_ARGUMENTS:
        return handler

    @wraps(handler)
    def guarded(inv):
        from .isa.common import domain

        lineage_id = str(inv.argument("lineage_id") or "")
        if not lineage_id:
            raise invalid("lineage_id 必填")
        ids = set()
        for key in SOURCE_ARGUMENTS[name]:
            value = inv.argument(key)
            if isinstance(value, list):
                ids.update(value)
            elif value:
                ids.add(value)
        scope = _Scope(lineage_id, frozenset(ids))
        # Avoid external work on an invalid source; the commit checks again.
        current = domain()._read_lineage_state(lineage_id)
        if name == "DeleteResident":
            # A retry may finish cleanup of an already deleted Resident. Its
            # immutable tombstone still carries the original Lineage.
            from . import resident_catalog
            try:
                record = resident_catalog.get_catalog().get_historical(inv.argument("resident_id"))
            except KeyError:
                record = {}
            if record.get("deleted_at") and record.get("lineage_id") == lineage_id:
                scope.object_ids = frozenset()
        _require_members(current, scope)
        token = _SCOPE.set(scope)
        try:
            return handler(inv)
        finally:
            _SCOPE.reset(token)

    return guarded


def check_before_membership_write(store, lineage_id: str) -> None:
    """Membership changes share the definition transaction, before changing nodes."""
    scope = _SCOPE.get()
    if scope is None or scope.lineage_id != lineage_id:
        return
    current = store.lock_lineage_definition(lineage_id)
    store.database.write(lambda connection: check_definition_scope(connection, current, lineage_id))
