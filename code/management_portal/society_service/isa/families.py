"""Family lifecycle handlers delegate to the familize template owner."""
from management_portal.hatg.contracts import InstructionError, invalid
from .common import placement
from ..templates import familize as family


def familize(inv):
    lineage_id = inv.argument("lineage_id")
    try:
        return family.familize(lineage_id, inv.argument("resident_ids") or [], inv.caller,
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
            **{name: inv.argument(name) for name in ("title", "goal", "route_threshold", "review_interval_seconds", "auto_migrate")},
            add_member_ids=inv.argument("add_member_ids") or [], remove_member_ids=inv.argument("remove_member_ids") or [],
            add_element_ids=inv.argument("add_element_ids") or [], remove_element_ids=inv.argument("remove_element_ids") or [])
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def dispatch_family_task(inv):
    from ..family_tasks import dispatch
    try:
        return dispatch(inv.argument("resident_id"), inv.argument("trigger"), inv.caller,
                        facts=inv.argument("facts"), message_id=inv.argument("message_id") or "",
                        source_message_id=inv.argument("source_message_id") or "",
                        event=inv.argument("event"))
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def archive_family_result(inv):
    from ..family_recovery import archive_result
    try:
        return archive_result(inv.argument("resident_id"), inv.argument("message_id"),
                              inv.argument("steward_message_id"), inv.caller, reason=inv.argument("reason"))
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def check_family_recovery(inv):
    from ..family_supervision import check
    try:
        return check(inv.argument("lineage_id"), inv.argument("family_id"), inv.caller,
                     event=inv.argument("event"), escalate_reason=inv.argument("escalate_reason") or "")
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def check_family_role_availability(inv):
    from ..family_quota import check_role_availability
    try:
        return check_role_availability(inv.argument("lineage_id"), inv.argument("family_id"), inv.caller)
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def check_family_progress(inv):
    from ..family_progress import check, DEFAULT_INTERVAL
    try:
        return check(inv.argument("lineage_id"), inv.argument("family_id"), inv.caller,
                     event=inv.argument("event"), every_turns=inv.argument("every_turns", DEFAULT_INTERVAL))
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def publish_family_progress(inv):
    from ..family_progress import publish
    try:
        return publish(inv.argument("lineage_id"), inv.argument("family_id"), inv.caller,
                       **{key: inv.argument(key) for key in ("batch_id", "report", "summary", "expected_revision", "source_versions")})
    except ValueError as exc:
        raise invalid(str(exc)) from exc


HANDLERS = {"Familize": familize, "Unfamilize": unfamilize, "DeleteFamily": delete_family, "UpdateFamily": update_family,
            "ResolveFamilyRequest": archive_family_result,
            "CheckFamilyRecovery": check_family_recovery, "CheckFamilyProgress": check_family_progress,
            "CheckFamilyRoleAvailability": check_family_role_availability,
            "PublishFamilyProgress": publish_family_progress, "DispatchFamilyTask": dispatch_family_task}
