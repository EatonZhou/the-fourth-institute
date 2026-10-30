"""Resolve only unconfigured Residents; bound sessions retain their account."""

from management_portal.execution.account_registrations import role_configuration
from management_portal.hatg.contracts import InstructionError


def configuration(record, binding):
    if record.get("backend") == "kimi" or record.get("backend_route_id"):
        return None
    if any(binding.get(key) for key in ("backend_route_id", "backend_session_id", "provider_session_ref")):
        return None
    requested = (record.get("execution_settings") or {}).get("backend_role") or "default"
    try:
        return role_configuration(requested)
    except InstructionError as error:
        if error.details.get("reason") == "backend_role.default_missing":
            return None
        raise
