"""Family lifecycle handlers delegate to the familize template owner."""
from management_portal.hatg.contracts import InstructionError, invalid
from .common import placement
from ..templates import create_family, familize as family


def familize(inv):
    lineage_id = inv.argument("lineage_id")
    try:
        return create_family(lineage_id, inv.argument("resident_ids") or [], inv.caller,
            scope=inv.argument("scope") or "selection",
            title=inv.argument("title") or "", goal=inv.argument("goal") or "",
            parameters=inv.argument("parameters"), resident_settings=inv.argument("resident_settings"),
            placement_request=placement(inv, lineage_id))
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def unfamilize(inv):
    try:
        return family.unfamilize(inv.argument("lineage_id"), inv.argument("family_id"), inv.caller,
            successor_id=inv.argument("successor_resident_id") or "", rewire_to=inv.argument("rewire_to_resident_id") or "")
    except family.FamilyBusy as exc:
        raise InstructionError("instruction.conflict", str(exc), retryable=True) from exc
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def delete_family(inv):
    try:
        return family.delete_family(inv.argument("lineage_id"), inv.argument("family_id"), inv.caller)
    except family.FamilyBusy as exc:
        raise InstructionError("instruction.conflict", str(exc), retryable=True) from exc
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def update_family(inv):
    try:
        return family.update_family(inv.argument("lineage_id"), inv.argument("family_id"), inv.caller,
            **{name: inv.argument(name) for name in ("title", "goal", "route_threshold", "review_interval_seconds")},
            add_member_ids=inv.argument("add_member_ids") or [], remove_member_ids=inv.argument("remove_member_ids") or [],
            add_element_ids=inv.argument("add_element_ids") or [], remove_element_ids=inv.argument("remove_element_ids") or [])
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def update_facade_members(inv):
    from ..relay_domain import update_facade_members as update
    return update(inv.argument("resident_id"), inv.argument("member_ids"), inv.caller)


def check_relay_target(inv):
    from ..relay_domain import check_target
    return check_target(inv.argument("resident_id"), inv.argument("member_id"), inv.caller)


def get_relay_context(inv):
    from ..family_routing_context import read
    return read(inv.argument("resident_id"), inv.argument("message_id"), inv.caller,
                section=inv.argument("section") or "members")


def resolve_relayed_request(inv):
    from ..relay_domain import resolve_request
    return resolve_request(inv.argument("resident_id"), inv.argument("message_id"),
                           inv.argument("review_message_id"), inv.caller, reason=inv.argument("reason"))


HANDLERS = {
    "Familize": familize,
    "Unfamilize": unfamilize,
    "DeleteFamily": delete_family,
    "UpdateFamily": update_family,
    "UpdateFacadeMembers": update_facade_members,
    "CheckRelayTarget": check_relay_target,
    "GetRelayContext": get_relay_context,
    "ResolveRelayedRequest": resolve_relayed_request,
}


def get_family_chat_preferences(inv):
    from ..family_chat_preferences import get
    return get(inv.argument('resident_id'), inv.caller)


def update_family_chat_preferences(inv):
    from ..family_chat_preferences import change
    try:
        return change(inv.argument('resident_id'), inv.caller, inv.argument('member_id'),
                      inv.argument('control'), inv.argument('enabled'))
    except ValueError as exc:
        raise invalid(str(exc)) from exc


HANDLERS.update(GetFamilyChatPreferences=get_family_chat_preferences,
                UpdateFamilyChatPreferences=update_family_chat_preferences)
