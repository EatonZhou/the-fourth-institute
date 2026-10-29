"""Family roles and membership queries over Function Block elements."""
from __future__ import annotations

from typing import Any, Mapping

from management_common.actor_identity import canonical_actor_id

from . import lineage_aggregate


def find_record(resident_id: str) -> dict[str, Any]:
    from . import resident_record_domain

    return resident_record_domain.find_resident(resident_id) or {}


def is_workflow(element):
    return str(element.get("key") or "").startswith("workflow:")


def is_generated(element):
    return element["kind"] in {"component", "wire"} and not is_workflow(element)


def family_blocks(lineage: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [item for item in lineage.get("function_blocks") or [] if item.get("family")]


def member_ids(block: Mapping[str, Any]) -> list[str]:
    return list((block.get("family") or {}).get("members") or [])


def lineage_family(lineage: Mapping[str, Any]) -> dict[str, Any] | None:
    return next((block for block in family_blocks(lineage) if block["family"].get("lineage_scope")), None)


def lineage_members(lineage: Mapping[str, Any]) -> list[str]:
    """Unassigned business Residents and root facades, excluding nested members and internal roles."""
    excluded = set()
    for block in family_blocks(lineage):
        excluded.update(member_ids(block))
        excluded.update(role_of(block, role) for role in ("steward", "router"))
    return [node["id"] for node in lineage.get("nodes") or [] if node["id"] not in excluded]


def role_of(block: Mapping[str, Any], role: str) -> str:
    return str((block.get("family") or {}).get(role) or "")


def facade_speaking_to(lineage: Mapping[str, Any], resident_id: str) -> str:
    """The facade a Family's internal notices to ``resident_id`` come from, if it is a steward.

    A Family speaks with one identity (decision_44cf587c1721): the Events and
    structural signals that wake its steward come from its facade, never from
    whoever happened to message a member or change the Family.
    """

    for block in family_blocks(lineage):
        if resident_id and role_of(block, "steward") == resident_id:
            return role_of(block, "facade")
    return ""


def facade_for_internal_note(
    lineage: Mapping[str, Any], component_id: str, resident_id: str, *, roles: tuple[str, ...] = ("steward", "router"),
) -> str:
    """The facade a Family component's call to its own steward or Router Resident is made as.

    The Family Router runs with the requester's authority so a relay keeps its
    real sender; asking its Router Resident to choose, and its note asking the
    steward to review a route, are the Family's own internal traffic and come
    from the facade.  Only components of that Resident's own Family block
    qualify; ``roles`` narrows which of its Residents.
    """

    block = component_family(lineage, component_id)
    if block and resident_id and resident_id in {role_of(block, role) for role in roles}:
        return role_of(block, "facade")
    return ""


# The Family's own State Cells: the routing guide the steward writes, the
# reassignment corrections the platform appends, and the record of what the
# Router Resident has been told (decision_44cf587c1721).
FAMILY_STATE_PREFIXES = ("family_corrections_", "family_preferences_", "family_activity_", "family_review_")


def facade_for_family_state(lineage: Mapping[str, Any], component_id: str, key: str) -> str:
    """The facade a Family component's read or write of its own State Cells is made as.

    The Router Effect runs with the requester's authority, but what it keeps
    for the Router belongs to the Family, so its reads and writes there are the
    Family's own traffic whoever sent the message.  Only that Family's cells
    qualify.
    """

    block = component_family(lineage, component_id)
    facade = role_of(block, "facade") if block else ""
    return facade if facade and key in {prefix + facade for prefix in FAMILY_STATE_PREFIXES} else ""


def component_family(lineage: Mapping[str, Any], component_id: str) -> dict[str, Any] | None:
    return next((block for block in family_blocks(lineage)
                 if any(item["id"] == component_id and item["kind"] == "component"
                        and is_generated(item)
                        for item in block.get("elements") or [])), None)


def family_of_router(lineage: Mapping[str, Any], resident_id: str) -> dict[str, Any] | None:
    """The Family whose Router Resident this is."""

    for block in family_blocks(lineage):
        if resident_id and role_of(block, "router") == resident_id:
            return block
    return None


def router_of_facade(lineage: Mapping[str, Any], facade_id: str) -> str:
    """The Router Resident of the Family this facade speaks for; "" before one is made."""

    for block in family_blocks(lineage):
        if facade_id and role_of(block, "facade") == facade_id:
            return role_of(block, "router")
    return ""


def family_for_new_resident(lineage: Mapping[str, Any], creator: str, requested: str = "") -> dict[str, Any] | None:
    """The Family a Resident about to be created joins (decision_44cf587c1721).

    Whoever creates it inside an expanded Family on the canvas names that
    Family; a Resident made by a member or by the steward joins their Family.
    Otherwise a new Resident joins the Lineage-scoped Family, when present.
    Membership follows the declared scope, never a guess from graph changes.
    """

    families = family_blocks(lineage)
    if requested:
        for block in families:
            if requested in {str(block.get("id") or ""), block["family"].get("resource_id")}:
                return block
        raise ValueError(f"{requested} 不是这个 Lineage 中的 Family")
    creator_id = canonical_actor_id(creator)
    for block in families:
        if creator_id in member_ids(block) or role_of(block, "steward") == creator_id:
            return block
    return lineage_family(lineage)


def read_family(lineage_id: str, family_id: str, actor: str) -> tuple[dict, dict]:
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
    return lineage, family_block(lineage, family_id)


def family_block(body: dict, family_id: str) -> dict:
    for block in body.get("function_blocks", []):
        if block.get("family") and family_id in {block["id"], block["family"].get("resource_id")}:
            return block
    raise ValueError(f"Lineage 中没有这个 Family：{family_id}")
