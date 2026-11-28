"""Validation of stored session handoff records for the restart status view."""
from collections.abc import Mapping

from . import resident_backend


def context_operation(record: Mapping) -> Mapping:
    state = record.get("session_restart") or {}
    if state.get("operation") == "clear_context":
        return state.get("context_before_clear") or {}
    return state


def validate_handoff(record: Mapping) -> None:
    """Validate the stored handoff's identity and lifetime.

    The lifecycle owner replaces this record on each account selection. Delivery
    never clears it: a failed request must retain the same frozen material.
    """
    state = context_operation(record)
    pending = state.get("pending_handoff")
    if not isinstance(pending, Mapping):
        raise ValueError("Pending session handoff is malformed")
    operation = pending.get("operation_id")
    if not operation or operation != state.get("restart_id"):
        raise ValueError("Pending session handoff operation has changed")
    # Read-only compatibility for an already committed pre-v2 handoff.
    # New requests cannot select these strategies; never regenerate its material.
    legacy = (state.get("configuration_only") is True and state.get("status") == "completed"
              and pending.get("strategy") in {"compact", "visible"}
              and state.get("context_strategy") == pending.get("strategy"))
    if pending.get("strategy") not in {"native", "local"} and not legacy:
        raise ValueError("Pending session handoff strategy is unsupported")
    if "context_reset_at" not in pending:
        raise ValueError("Pending session handoff context was reset")
    if pending["context_reset_at"] != str(record.get("context_reset_at") or ""):
        # ClearContext retains the historical operation record. Its new
        # boundary discards this old material, without blocking a fresh input.
        return
    target = pending.get("target")
    if not isinstance(target, Mapping) or not target.get("provider") or not isinstance(target.get("route"), str):
        raise ValueError("Pending session handoff target is missing")
    expected = (target["provider"], target["route"])
    if expected != (resident_backend.execution_backend(record), record.get("backend_route_id")):
        raise ValueError("Pending session handoff target has changed")
    binding = resident_backend.session_record(record)
    if binding and expected != (binding.get("provider"), binding.get("backend_route_id")):
        raise ValueError("Pending session handoff binding has changed")
    material = pending.get("prompt")
    if not isinstance(material, str) or not material.strip():
        raise ValueError("Pending session handoff material is missing")
