"""Administrator settings commands whose installed authority is a Lineage Event."""
from management_portal.execution.service_settings import RETENTION_DAYS, ExecutionSettings
from management_portal.hatg.contracts import InstructionError, invalid
from management_portal.hatg.kernel import Invocation

from .. import system_workflow_switches


def update_execution_settings(inv: Invocation) -> dict:
    from management_portal import permission_system

    # A Portal 管理员 turning only the permission switch passes whatever the scheme says (decision_b820faf42218 §3.1).
    if not permission_system.allows(inv.caller, "UpdateExecutionSettings", inv.arguments):
        raise InstructionError("instruction.forbidden", "无权修改执行设置")
    changes = {field: inv.argument(field) for _option, field in system_workflow_switches.WORKFLOW_FIELDS.values()
               if field in inv.arguments}
    retention = {name: inv.argument(name) for name in RETENTION_DAYS if name in inv.arguments}
    permissions = inv.argument("permission_system_enabled") if "permission_system_enabled" in inv.arguments else None
    if not changes and not retention and permissions is None:
        raise invalid("至少提供一项设置：model_request_timing_enabled、harness_recording_enabled、permission_system_enabled、"
                      + "、".join(RETENTION_DAYS))
    if any(type(value) is not bool for value in changes.values()):
        raise invalid("model_request_timing_enabled 和 harness_recording_enabled 必须是布尔值")
    if permissions is not None and type(permissions) is not bool:
        raise invalid("permission_system_enabled 必须是布尔值")
    for name, days in retention.items():
        minimum, maximum, _default = RETENTION_DAYS[name]
        if type(days) is not int or not minimum <= days <= maximum:
            raise invalid(f"{name} 必须是 {minimum}–{maximum} 之间的整数天数")
    # Switches may still fail on an invalid Event binding; write retention after them.
    settings = system_workflow_switches.update_settings(changes, inv.caller) if changes else None
    if retention:
        settings = ExecutionSettings().update_retention(retention)
    reconciled = None
    if permissions is not None:
        settings, reconciled = _switch_permission_system(permissions, inv.caller)
    return {"settings": settings} if reconciled is None else {"settings": settings, "reconciled": reconciled}


def _switch_permission_system(enabled: bool, actor: str) -> tuple[dict, dict | None]:
    """Turn the permission system on or off for the whole Portal.

    Turning it on first brings the active scheme's instance up to date with
    the current objects (补齐), so what was made meanwhile is placed and gets
    the rows its scheme's rules give it, and what was deleted leaves; then
    the switch flips, and every Portal service follows within seconds.
    Returns the settings and, when turned on, the report.
    """
    from management_portal import permission_system

    settings = ExecutionSettings()
    if enabled == settings.permission_system_enabled():
        return settings.get(), None
    report = permission_system.reconcile_before_enabling(actor) if enabled else None
    result = settings.update_permission_system(enabled)
    permission_system.forget_mode()
    return result, report


HANDLERS = {"UpdateExecutionSettings": update_execution_settings}
