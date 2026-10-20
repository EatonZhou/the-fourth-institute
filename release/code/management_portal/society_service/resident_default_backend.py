"""Resolve only unconfigured Residents; bound sessions retain their account."""
from management_portal.execution.account_registrations import role_configuration
from management_portal.hatg.contracts import InstructionError


def configuration(record, binding):
    if record.get("backend") in {"portal", "kimi"} or record.get("backend_route_id"):
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


def bind_before_execution(record, actor):
    from . import resident_backend
    from .resident_runtime_domain import _read_resident_runtime_settings, _write_resident_runtime_settings
    from management_portal.execution.resident_runtime_settings import merge_settings
    from .resident_workspace_domain import _save_resident_record
    from .resident_lifecycle_domain import _now_iso
    configured = configuration(record, resident_backend.session_record(record))
    if not configured:
        return
    current = _read_resident_runtime_settings(record)
    record.update(backend=configured["backend"], backend_route_id=configured["backend_route_id"])
    # The role was configured separately. Resolve no remote model catalog while
    # the execution caller holds this Resident's state lock.
    settings = merge_settings({**current, "backend": configured["backend"]},
                              configured["runtime_settings"], actor=actor, updated_at=_now_iso())
    _write_resident_runtime_settings(record, settings)
    _save_resident_record(record, commit_configuration=True)
