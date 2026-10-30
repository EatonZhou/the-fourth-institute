"""Collaboration access policy for an exact Resident identity."""

from __future__ import annotations

from typing import Any

from management_common.actor_identity import canonical_actor_id
from management_common.principal import actor_principal
from management_portal.permission_system import personal_ama
from management_portal.portal_access_control import get_portal_access_store

from . import resident_catalog, resident_lineage
from .domain_ports import resident_lineage_host_port


def can_manage_resident(record: dict[str, Any], actor: str, *, operation: str = "manage") -> bool:
    """A User, a Resident or the authenticated internal Portal service the personal AMA rule lets through.

    ``operation`` says which question the caller asks (``read`` or ``manage``).
    A personal AMA's Residents and branches are known only to their owner and
    the owner's AMA Residents (``personal_ama``, decision_bd5217d08c09), with
    the permission system on or off; reading also asks GetResident.
    """

    if not actor_can_manage_residents(actor):
        return False
    if not personal_ama.reaches(actor, record.get("personal_owner_id")):
        return False
    if operation == "read":
        from management_portal import permission_system

        return permission_system.allows(actor, "GetResident", {"resident_id": record.get("resident_id")})
    return True


def may_change_resident(record: dict[str, Any], actor: str) -> bool:
    """Whether pages should offer changes to the Resident: a valid identity UpdateResidentSettings would pass on it."""
    from .lineage_access_policy import changeable_ids

    resident_id = str(record.get("resident_id") or "").strip()
    return bool(resident_id) and can_manage_resident(record, actor) and resident_id in changeable_ids(
        actor, {resident_id: record.get("personal_owner_id") or ""}, "UpdateResidentSettings", "resident_id")


def resident_deliverable_to_actor(record: dict[str, Any], actor: str) -> bool:
    """Whether ``actor`` may deliver a message to the Resident: anyone may message a personal AMA, otherwise as reading.

    Delivery to someone else's AMA opens nothing else: its Conversation, state
    and queue stay unknown to the sender (``personal_ama``).
    """
    if str(record.get("personal_owner_id") or "").strip():
        return actor_can_manage_residents(actor)
    return resident_visible_to_actor(record, actor)


RESIDENT_ACTIONS = ("DeliverMessage", "UpdateResidentSettings", "OpenIntake", "RestartSession",
                    "CancelInput", "UpdateResident", "DeleteResident", "ForkResident")


def resident_allows(record: dict[str, Any], actor: str) -> dict[str, bool]:
    """``{instruction: bool}`` for the Resident's buttons, each asking only its own instruction (decision_b820faf42218 §3.1)."""
    from management_portal import permission_system

    resident_id = str(record.get("resident_id") or "").strip()
    if not resident_id or not can_manage_resident(record, actor):
        allowed = dict.fromkeys(RESIDENT_ACTIONS, False)
        if resident_id and resident_deliverable_to_actor(record, actor):
            allowed["DeliverMessage"] = permission_system.allows(actor, "DeliverMessage", {"resident_id": resident_id})
        return allowed
    # ForkResident is asked for a fork in this Resident's own Lineage.
    arguments = {"resident_id": resident_id, "lineage_id": str(record.get("lineage_id") or "").strip(),
                 "source_resident_id": resident_id}
    return permission_system.allows_each(actor, RESIDENT_ACTIONS, arguments)


def manage_refusal(record: dict[str, Any]) -> Exception:
    """What to raise when ``can_manage_resident`` refused: someone else's personal AMA is not found."""
    if str(record.get("personal_owner_id") or "").strip():
        return personal_ama.NotFound(record.get("resident_id"))
    return PermissionError("需要有效的 Portal 登录身份")


def actor_can_manage_residents(actor: str) -> bool:
    """Actor-wide permission; callers still enforce read access and lifecycle."""

    actor_id = canonical_actor_id(actor)
    if not actor_id:
        return False
    try:
        principal = actor_principal(actor_id)
    except ValueError:
        return False
    if principal.kind == "user":
        return get_portal_access_store().is_active(actor_id)
    return principal.kind == "resident" or (principal.kind == "service" and principal.id == "portal")


def resident_visible_to_actor(record: dict[str, Any], actor: str) -> bool:
    return can_manage_resident(record, actor, operation="read")


def ensure_resident_visible_to_actor(record: dict[str, Any], actor: str) -> None:
    if not resident_visible_to_actor(record, actor):
        raise KeyError("resident not found")


@resident_lineage.access_snapshot_scope()
def filter_visible_resident_ids(
    resident_ids: list[str], actor: str = "", service_actor: str = "",
) -> dict[str, Any]:
    """Project visible identities for a trusted internal directory consumer."""

    records = visible_resident_records(resident_ids, actor, service_actor)
    return {"schema": "tfi.resident-read-access-projection.v1",
            "resident_ids": [record["resident_id"] for record in records]}


@resident_lineage.access_snapshot_scope()
def visible_resident_records(
    resident_ids: list[str], actor: str = "", service_actor: str = "",
) -> list[dict[str, Any]]:
    """Return the checked canonical records for this read, in request order."""

    if not get_portal_access_store().is_service_actor(canonical_actor_id(service_actor)):
        raise PermissionError("Resident read-access projection requires a service actor")
    actor_id = canonical_actor_id(actor)
    requested = list(dict.fromkeys(
        str(resident_id or "").strip()
        for resident_id in resident_ids[:10000]
        if str(resident_id or "").strip()
    ))
    records_by_id = {
        str(record.get("resident_id") or "").strip(): record
        for record in resident_catalog.get_catalog().get_identity_records(requested)
    }
    lineage_ids = list(dict.fromkeys(
        str(record.get("lineage_id") or "").strip()
        for record in records_by_id.values()
        if str(record.get("lineage_id") or "").strip()
    ))
    visible_lineage_ids = {
        str(record.get("lineage_id") or "").strip()
        for record in resident_lineage_host_port.actor_lineage_access_records(actor_id, lineage_ids)
    }
    return [
        record for resident_id in requested
        if (
            (record := records_by_id.get(resident_id)) is not None
            and resident_catalog.registration_ready(record)
            and str(record.get("lineage_id") or "").strip() in visible_lineage_ids
        )
    ]


def can_manage(
    record: dict,
    actor: str,
) -> bool:
    """Whether this identity may change the Resident; see ``resident_access.can_manage_resident``."""
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        return False
    return can_manage_resident(record, actor_id)


def has_resident_workspace_authority(
    record: dict[str, Any],
    actor: str,
    resident: dict[str, Any] | None = None,
) -> bool:
    """Return authority for one exact Resident workspace."""

    return can_manage_resident(resident or record, actor)


def is_active_actor(actor: str) -> bool:
    """Any identity whose Portal account is not disabled: Users, Residents and Portal services."""

    return get_portal_access_store().is_active(canonical_actor_id(actor))
