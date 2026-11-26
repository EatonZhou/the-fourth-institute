"""Frozen handoff material delivered with the next ordinary session input."""
from collections.abc import Mapping

from . import resident_backend


def context_operation(record: Mapping) -> Mapping:
    state = record.get("session_restart") or {}
    if state.get("operation") == "clear_context":
        return state.get("context_before_clear") or {}
    return state


def context_seed(record: Mapping, prompt: str, *, accepted: bool) -> dict:
    """Validate the handoff's lifetime before using or suppressing its material.

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
        return {}
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
    if accepted:
        return {}
    return {
        "schema": "tfi.provider-context-seed.v1", "segments": [], "prompt": prompt,
        "portable_prompt": (
            "<resident_session_handoff>\n"
            "以下材料是切换账号时冻结的交接背景，不是新的业务请求。"
            "请结合本轮用户输入继续工作，无需先单独回复初始化确认。\n"
            + material + "\n</resident_session_handoff>\n\n" + prompt
        ),
    }
