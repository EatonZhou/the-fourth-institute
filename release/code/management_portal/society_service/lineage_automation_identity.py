"""Optional User authority for unattended Lineage work.

The definition stores an identity, never a credential snapshot. Configuration
uses the editor's current authority; execution resolves a configured User anew.
Without one, Portal observes the graph and each Resident uses its own authority.
"""
from __future__ import annotations

from collections.abc import Mapping

from management_common.principal import Principal, actor_principal
from management_portal import permission_system
from management_portal.portal_access_control import get_portal_access_store


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


def normalize(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("run_as_principal must be a User principal object or null")
    principal = Principal.from_dict(value)
    if principal.kind != "user":
        raise ValueError("run_as_principal requires a User")
    return principal.to_dict()


def _require_active(principal: Mapping[str, str]) -> str:
    identifier = principal["id"]
    access = get_portal_access_store()
    if not access.is_configured(identifier) or not access.is_active(identifier):
        raise PermissionError(f"run_as_principal User is unavailable or inactive: {identifier}")
    return identifier


def requested(payload: Mapping, previous: Mapping, actor: str) -> dict[str, str] | None:
    """Validate a definition edit; ordinary edits preserve a disabled identity.

    Setting a different User requires that UpdateUser would pass on that
    User, judged on the editor's own permissions. Owner/creator metadata and operation
    attribution cannot authorize impersonation.
    """
    before = normalize(previous.get("run_as_principal"))
    if "run_as_principal" not in payload:
        return before
    after = normalize(payload["run_as_principal"])
    if after == before or after is None:
        return after
    editor = actor_principal(actor)
    target = Principal.from_dict(after)
    if editor != target and not permission_system.permitted_ids(editor.id, "UpdateUser", "user_id", [target.id]):
        raise PermissionError("Setting another User as run_as_principal requires permission for UpdateUser on that User")
    _require_active(after)
    return after


class AutomationConfigurationError(PermissionError):
    """Configuration must change before unattended activation can succeed."""


def runtime_actor(lineage: Mapping) -> str:
    """Resolve explicit live authority, independently of ambient turn identity."""
    try:
        principal = normalize(lineage.get("run_as_principal"))
        if principal is None:
            return "portal"
        return _require_active(principal)
    except (PermissionError, ValueError) as exc:
        raise AutomationConfigurationError(str(exc)) from exc


def readiness_error(lineage: Mapping) -> str:
    """Explain the same live authority check used by polling, without mutation."""
    try:
        runtime_actor(lineage)
    except (PermissionError, ValueError) as exc:
        return str(exc)
    return ""
