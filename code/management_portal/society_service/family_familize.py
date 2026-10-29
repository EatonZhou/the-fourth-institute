"""Family roles and membership queries over Function Block elements."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from management_common.actor_identity import canonical_actor_id

from . import family_facade


def _find(resident_id: str) -> dict[str, Any]:
    from . import resident_record_domain

    return resident_record_domain.find_resident(resident_id) or {}


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
        excluded.update(_role(block, role) for role in ("steward", "router"))
    return [node["id"] for node in lineage.get("nodes") or [] if node["id"] not in excluded]


def _role(block: Mapping[str, Any], role: str) -> str:
    return str((block.get("family") or {}).get(role) or "")


def facade_speaking_to(lineage: Mapping[str, Any], resident_id: str) -> str:
    """The facade a Family's internal notices to ``resident_id`` come from, if it is a steward.

    A Family speaks with one identity (decision_44cf587c1721): the Events and
    structural signals that wake its steward come from its facade, never from
    whoever happened to message a member or change the Family.
    """

    for block in family_blocks(lineage):
        if resident_id and _role(block, "steward") == resident_id:
            return _role(block, "facade")
    return ""


def facade_for_arrival_call(lineage, component_id, instruction, arguments, event, caller, *, lineage_id):
    """Only the generated Router may route its verified arrival as its facade.

    This is a single unchanged-delivery capability, not the external sender's
    Project permission. The relay owner still checks cancellation, membership,
    intake, quota and the original author's attachment grants.
    """
    from management_common.actor_identity import same_actor
    from . import family_facade, resident_conversation_stream
    from . import resident_record_domain

    if event is None or event.type != "resident.message_received":
        return ""
    relaying = instruction == "RelayFamilyMessage"
    if relaying:
        if set(arguments) != {"resident_id", "message_id", "member_id"}:
            return ""  # No reassign, review overrides or arbitrary delivery fields.
        message_id = arguments["message_id"]
    else:
        if (instruction != "DispatchFamilyTask" or arguments.get("trigger") != "route"
                or set(arguments) != {"resident_id", "source_message_id", "trigger"}):
            return ""
        message_id = arguments["source_message_id"]
    block = _component_family(lineage, component_id)
    if not block or not any(e["id"] == component_id and e.get("key") == "router"
                            for e in block.get("elements") or []):
        return ""
    facade_id = _role(block, "facade")
    payload = event.payload
    if (event.subject_resident_id != facade_id or payload.get("resident_id") != facade_id
            or arguments.get("resident_id") != facade_id
            or message_id != payload.get("message_id")):
        return ""
    facade = resident_record_domain.find_resident(facade_id)
    if (not family_facade.is_facade(facade) or facade.get("deleted_at")
            or facade.get("lineage_id") != lineage_id
            or facade["family"]["family_id"] != block["id"]):
        return ""
    repository = resident_conversation_stream.get_repository()
    message = repository.message(facade_id, message_id)
    if (not message or message.get("role") != "user"
            or not same_actor(message.get("author_id"), caller)):
        return ""
    from .family_chat_preferences import direct_member
    direct = direct_member(facade, message)
    if relaying and payload.get("direct_member_id"):
        return facade_id if direct == arguments["member_id"] == payload["direct_member_id"] else ""
    quoted = family_facade.quoted_member(facade, message)
    target = quoted if quoted == direct else ""
    if relaying:
        if target:
            return facade_id if target == arguments["member_id"] == payload.get("quoted_member_id") else ""
        # Recomputed from stored relays, never taken from the payload alone.
        continued = family_facade.continued_member(facade, message)
        return facade_id if continued and continued == arguments["member_id"] == payload.get("continued_member_id") else ""
    # Asking this Family's Router to choose is internal maintenance of an
    # accepted request; it gives the sender no right to dispatch other tasks.
    # A continuing request may still be asked about: it only takes longer.
    return facade_id if (not target and not payload.get("quoted_member_id")
                         and repository.input_state(facade_id, message_id).get("state") == "queued") else ""


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

    block = _component_family(lineage, component_id)
    if block and resident_id and resident_id in {_role(block, role) for role in roles}:
        return _role(block, "facade")
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

    block = _component_family(lineage, component_id)
    facade = _role(block, "facade") if block else ""
    return facade if facade and key in {prefix + facade for prefix in FAMILY_STATE_PREFIXES} else ""


def _component_family(lineage: Mapping[str, Any], component_id: str) -> dict[str, Any] | None:
    from .family_workflows import is_generated
    return next((block for block in family_blocks(lineage)
                 if any(item["id"] == component_id and item["kind"] == "component"
                        and is_generated(item)
                        for item in block.get("elements") or [])), None)


def family_of_router(lineage: Mapping[str, Any], resident_id: str) -> dict[str, Any] | None:
    """The Family whose Router Resident this is."""

    for block in family_blocks(lineage):
        if resident_id and _role(block, "router") == resident_id:
            return block
    return None


def router_of_facade(lineage: Mapping[str, Any], facade_id: str) -> str:
    """The Router Resident of the Family this facade speaks for; "" before one is made."""

    for block in family_blocks(lineage):
        if facade_id and _role(block, "facade") == facade_id:
            return _role(block, "router")
    return ""


def validate_members(lineage: Mapping[str, Any], resident_ids: Iterable[str], *, family_id: str = "") -> list[str]:
    """Members belong to this Lineage, to at most one Family, and are not stewards or Routers.

    A facade may be a member, which nests one Family inside another; a
    Family cannot contain its own facade.
    """

    ids = list(dict.fromkeys(str(item) for item in resident_ids if item))
    present = {str(node.get("id") or "") for node in lineage.get("nodes") or [] if isinstance(node, Mapping)}
    missing = [item for item in ids if item not in present]
    if missing:
        raise ValueError("Family 成员必须属于同一 Lineage：" + "、".join(missing))
    taken: dict[str, str] = {}
    own_facade = ""
    for block in family_blocks(lineage):
        if block.get("id") == family_id:
            own_facade = _role(block, "facade")
            continue
        if block["family"].get("lineage_scope") and _role(block, "facade") in ids:
            raise ValueError("Lineage 范围的 Family 不能加入另一个 Family")
        for member in member_ids(block):
            taken[member] = str(block.get("title") or block.get("id") or "")
    for resident_id in ids:
        if resident_id in taken:
            raise ValueError(f"{resident_id} 已是 Family「{taken[resident_id]}」的成员，一个 Resident 只能直接属于一个 Family")
        if resident_id == own_facade:
            raise ValueError("Family 的门面不能成为自己的成员")
        record = _find(resident_id)
        if family_facade.is_steward(record):
            raise ValueError(f"{resident_id} 是 steward，不能成为 Family 成员")
        if family_of_router(lineage, resident_id) is not None:
            raise ValueError(f"{resident_id} 是 Family Router，不能成为 Family 成员")
        if not record or record.get("deleted_at"):
            raise ValueError(f"{resident_id} 已删除或不存在，不能成为 Family 成员")
    return ids


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
        if creator_id in member_ids(block) or _role(block, "steward") == creator_id:
            return block
    return lineage_family(lineage)


def announce_member_retitle(resident_id: str, previous: str, title: str, actor: str) -> bool:
    """A member's new title names the work it gets from now on; its steward writes that down.

    The Router ranks a member's title above what its summary says it has
    done, so renaming a member is how an owner assigns it a duty.  The
    steward hears the rename from the facade and brings the routing guide
    and the memory roster in line; its own renames need no note to itself.
    Returns whether the steward was told.
    """

    from . import resident_lineage
    from .state_store import get_lineage_data_store

    record = _find(resident_id)
    lineage_id = resident_lineage.lineage_id(record) if record else ""
    if not lineage_id or not title or title == previous:
        return False
    try:
        definition = get_lineage_data_store().lineage_definition(lineage_id)
    except KeyError:
        return False
    renamer_id = canonical_actor_id(actor)
    for block in family_blocks(definition):
        if resident_id not in member_ids(block):
            continue
        steward_id = _role(block, "steward")
        if not steward_id or steward_id == renamer_id:
            return False
        renamer = str(_find(renamer_id).get("title") or "") if renamer_id.startswith("resident_") else ""
        family_facade.tell_steward(_role(block, "facade"), steward_id, actor, (
            f"结构信号：{f'{renamer}（{renamer_id}）' if renamer else renamer_id} 把成员「{previous or resident_id}」"
            f"（{resident_id}）的标题改为「{title}」。成员标题代表明确分工，Router 下次读取当前标题；"
            "请核对受影响成员的职责和协作关系，必要时挂一条下一轮背景更新，不撰写重复的路由指引。"
            + ("owner 改过的标题以 owner 为准，不要改回。" if not renamer_id.startswith("resident_") else "")
        ))
        return True
    return False


def join_new_resident(lineage_id: str, resident_id: str, creator: str, family_id: str) -> bool:
    from .templates.familize import update_family, read_family
    _lineage, block = read_family(lineage_id, family_id, creator)
    if resident_id in member_ids(block):
        return False
    update_family(lineage_id, family_id, creator, add_member_ids=[resident_id])
    return True
