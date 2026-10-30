"""Resolve unattended Lineage work from its current Leader binding.

This chooses the actor, not its permissions. Accepted Events retain their own
stored identity; this resolver is only for new unattended work.
"""
from __future__ import annotations

from collections.abc import Mapping

from management_portal import portal_access_control
from management_portal.permission_system import personal_ama

from .identity_contract import is_valid_id


# These observations create new activations; derived events retain their
# accepted initiator instead of acquiring unattended authority.
AUTOMATIC_EVENT_TYPES = {
    "time.reached", "file.changed", "portal.release_published", "resident.message_received",
    "lineage.changed", "state.changed", "state.condition_met",
    "resident.context_pressure_exceeded", "resident.queue_pressure_exceeded",
    "resident.state_changed", "resident.work_duration_exceeded", "resident.work_started",
    "resident.work_finished", "resident.work_failed", "resident.work_cancelled",
    "residents.all_finished", "residents.all_idle", "workspace_handoff.ready", "workspace_handoff.failed",
}



class AutomationConfigurationError(PermissionError):
    """The graph needs a valid Leader before new unattended work can start."""


def runtime_actor(lineage: Mapping) -> str:
    """Use the graph's Leader (the facade for a Lineage-wide Family)."""
    leader = str(lineage.get("leader_resident_id") or "").strip()
    if not leader:
        raise AutomationConfigurationError("Lineage 尚无 Leader；加入 Resident 后自动运行才能开始")
    members = {str(node.get("id") or "") for node in lineage.get("nodes") or []
               if isinstance(node, Mapping)}
    if not is_valid_id(leader, "resident") or leader not in members:
        raise AutomationConfigurationError("Lineage Leader 必须是当前图中的有效 Resident")
    # A personal AMA Resident acts as its owner, so it has no authority once
    # the owner's account is disabled or deleted.
    owner = personal_ama.owner_of(leader)
    if owner and not portal_access_control.get_portal_access_store().is_active(owner):
        raise AutomationConfigurationError("Lineage Leader 所属的用户账号已停用或删除，自动运行已停止")
    return leader


def readiness_error(lineage: Mapping) -> str:
    """Explain the same Leader check used by polling, without mutation."""
    try:
        runtime_actor(lineage)
    except AutomationConfigurationError as exc:
        return str(exc)
    return ""
