"""Helpers shared by SocietyService instruction handlers."""
from __future__ import annotations

from typing import Any

from management_portal.hatg.contracts import invalid, not_found
from management_portal.hatg.kernel import Invocation


PLACEMENT_FIELDS = ("near_resident_id", "placement", "x", "y")


def domain():
    from management_portal.society_service import lineage_domain

    return lineage_domain


def protocol():
    from management_portal.society_service import resident_protocol

    return resident_protocol


def store():
    from management_portal.society_service.state_store import get_lineage_data_store

    return get_lineage_data_store()


def lineage_of_resident(resident_id: str) -> str:
    from management_portal.society_service import resident_lifecycle_domain

    clean = str(resident_id or "").strip().lower()
    if not clean.startswith("resident_"):
        raise invalid(f"不是 Resident ID：{resident_id}")
    try:
        return resident_lifecycle_domain.resident_aggregate_lineage_id(clean)
    except KeyError:
        raise not_found(f"Resident 不存在：{clean}") from None


def lineage_revision(lineage_id: str) -> int:
    """The Lineage's change counter; WatchLineage resumes from it."""

    try:
        return store().lineage_revision(lineage_id)
    except KeyError:
        raise not_found(f"Lineage 不存在：{lineage_id}") from None


def placement(inv: Invocation, lineage_id: str) -> dict[str, Any]:
    """Placement arguments; a Resident caller in the Lineage anchors new nodes near itself."""

    fields = {name: inv.argument(name) for name in PLACEMENT_FIELDS if inv.call.arguments.get(name) is not None}
    if "near_resident_id" not in fields and inv.context.is_resident and inv.entry != "lineage":
        try:
            members = domain().lineage_access_projection(lineage_id)["lineage_node_ids"]
        except Exception:  # noqa: BLE001 - placement is best effort
            members = []
        if inv.caller in members:
            fields["near_resident_id"] = inv.caller
    return fields


def caller_account(inv: Invocation, provider: str = "") -> str:
    """Inherit a Resident account only for the same execution provider."""

    if not inv.context.is_resident or inv.entry == "lineage":
        return ""
    try:
        record = protocol().get_resident(inv.caller, inv.caller)
    except Exception:  # noqa: BLE001
        return ""
    if provider and str(record.get("provider") or "codex") != provider:
        return ""
    return str(record.get("account") or "").strip()
