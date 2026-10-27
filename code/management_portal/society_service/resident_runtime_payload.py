"""Normalize Resident runtime-setting input for creation and updates."""

from __future__ import annotations

from typing import Any


def boolean_flag_from_payload(value: object, *, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def runtime_settings_updates_from_payload(
    value: object, *, include_blank: bool = False,
) -> dict[str, Any]:
    if value is not None and not isinstance(value, dict):
        raise ValueError("runtime_settings must be an object")
    data = value or {}
    allowed = {
        "model", "reasoning_effort", "service_tier",
        "permission_preset", "collaboration_mode", "agent_harness",
        "prompt_injection", "capability_config", "allow_subagents",
        "prompt_context", "backend_options",
        "backend_role", "backend_role_fallback",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ValueError("Unsupported runtime settings: " + ", ".join(sorted(unknown)))
    updates: dict[str, Any] = {}
    for key in ("prompt_context", "backend_options", "backend_role_fallback"):
        if key in data:
            if not isinstance(data[key], dict):
                raise ValueError(f"{key} must be an object")
            updates[key] = dict(data[key])
    if "collaboration_mode" in data:
        mode = data["collaboration_mode"]
        if mode not in ("default", "plan"):
            raise ValueError("collaboration_mode 只支持 default 或 plan")
        updates["collaboration_mode"] = mode

    for source_key, target_key in (
        ("backend_role", "backend_role"),
        ("model", "model"),
        ("reasoning_effort", "reasoning_effort"),
        ("service_tier", "service_tier"),
        ("permission_preset", "permission_preset"),
    ):
        if source_key in data:
            text = str(data.get(source_key) or "").strip()
            if text or include_blank:
                updates[target_key] = text
    if (
        include_blank
        and "permission_preset" in data
        and not updates.get("permission_preset")
    ):
        updates.update({
            "permissions": "",
            "approvals_reviewer": "",
            "approval_policy": "",
            "sandbox": "",
            "profile": "",
        })

    if "allow_subagents" in data:
        updates["allow_subagents"] = boolean_flag_from_payload(data["allow_subagents"], default=False)
    if "prompt_injection" in data:
        from management_portal import prompt_injection
        updates["prompt_injection"] = prompt_injection.normalize(data["prompt_injection"])
    if "capability_config" in data:
        from management_portal.execution.resident_capability_settings import normalize_capability_config
        updates["capability_config"] = normalize_capability_config(data["capability_config"])
    if "agent_harness" in data:
        raw_harness = data["agent_harness"]
        if not isinstance(raw_harness, dict):
            raise ValueError("agent_harness must be an object")
        updates["agent_harness"] = dict(raw_harness)
    return updates
