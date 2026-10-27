"""Declarative Resident capability selection; no discovery or execution I/O.

References resolve in the existing tool, resource and Skill owners at assembly
time. Empty selections preserve the platform defaults, rather than clearing them.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any
import unicodedata


CAPABILITY_SCHEMA = "tfi.resident-capabilities.v1"
CAPABILITY_GROUPS = ("tools", "mcp", "skills")


def _reference(raw: object, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 512:
        raise ValueError(f"{field} must be a nonempty reference of at most 512 characters")
    if any(unicodedata.category(char).startswith("C") for char in raw):
        raise ValueError(f"{field} must not contain control characters")
    return raw.strip()


def normalize_capability_config(raw: object) -> dict[str, Any]:
    """Validate a complete, versioned reference-only selection document."""
    if not isinstance(raw, dict) or raw.get("schema") != CAPABILITY_SCHEMA:
        raise ValueError(f"capability_config.schema must be {CAPABILITY_SCHEMA}")
    if set(raw) - {"schema", *CAPABILITY_GROUPS}:
        raise ValueError("Unsupported capability_config fields")
    result: dict[str, Any] = {"schema": CAPABILITY_SCHEMA}
    for group in CAPABILITY_GROUPS:
        entries = raw.get(group, [])
        if not isinstance(entries, list):
            raise ValueError(f"capability_config.{group} must be an array")
        selections, seen = [], set()
        allowed = {"ref", "enabled", "required"} | ({"tools"} if group == "mcp" else set())
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) - allowed:
                raise ValueError(f"Invalid capability_config.{group} selection")
            ref = _reference(entry.get("ref"), f"capability_config.{group}.ref")
            if ref in seen:
                raise ValueError(f"Duplicate capability_config.{group} reference: {ref}")
            seen.add(ref)
            enabled, required = entry.get("enabled", True), entry.get("required", False)
            if type(enabled) is not bool or type(required) is not bool:
                raise ValueError("Capability enabled and required must be booleans")
            if required and not enabled:
                raise ValueError("A disabled capability cannot be required")
            item = {"ref": ref, "enabled": enabled, "required": required}
            if group == "mcp":
                names = entry.get("tools", [])
                if not isinstance(names, list):
                    raise ValueError("capability_config.mcp.tools must be an array")
                names = [_reference(name, "capability_config.mcp.tools") for name in names]
                if len(set(names)) != len(names):
                    raise ValueError("Duplicate MCP tool selection")
                item["tools"] = sorted(names)
            selections.append(item)
        result[group] = sorted(selections, key=lambda item: item["ref"])
    return result


def desired_capability_config(settings: object) -> dict[str, Any]:
    """Legacy settings inherit defaults without creating a persisted mirror."""
    if isinstance(settings, Mapping) and "capability_config" in settings:
        return normalize_capability_config(settings["capability_config"])
    return normalize_capability_config({"schema": CAPABILITY_SCHEMA})


def settings_revision(settings: Mapping[str, Any], *, backend: str = "", backend_route_id: str = "") -> str:
    """Content revision of the owner settings and their account configuration."""
    snapshot = {"backend": backend, "backend_route_id": backend_route_id, "settings": dict(settings)}
    serialized = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def settings_application(settings: Mapping[str, Any], *, backend: str, revision: str) -> dict[str, Any]:
    """Project intended application timing without inventing Provider receipts.

    Actual sent/accepted snapshots belong to execution. Configuration alone is
    insufficient evidence that any running session has received these values.
    """
    from management_portal import prompt_injection
    from .harness_capabilities import PORTAL_TOOLS
    from .provider_catalog import AGENT_PROVIDERS

    items: list[dict[str, Any]] = []
    def add(path: str, status: str, reason: str, **details: Any) -> None:
        items.append({"path": path, "status": status, "delivery": "unknown", "reason": reason, **details})

    config = desired_capability_config(settings)
    if not any(config[group] for group in CAPABILITY_GROUPS):
        add("capability_config", "unknown", "inherit_platform_defaults_without_delivery_observation")
    policy = prompt_injection.resolve(settings.get("prompt_injection"))
    provider = AGENT_PROVIDERS.get(backend)
    if provider is None:
        tool_status = "unknown"
    elif PORTAL_TOOLS not in provider.harness:
        tool_status = "unsupported"
    else:
        tool_status = "pending_session" if provider.session_tools_fixed else "next_turn"
    places = prompt_injection.placements(backend)
    sources = {
        "core_rules": "Portal core Skills and generated role rules",
        "resident_md": "resident.md",
        "runtime_identity": "Portal runtime identity",
        "authorization_context": "Portal actor and authorization context",
        "message_envelope": "Portal input message envelope",
        "turn_skills": "Current input Skill references",
    }
    prompt_status: dict[str, str] = {}
    for part, timing in policy.items():
        status = {
            "off": "disabled", "every_turn": "next_turn", "list": "next_turn",
            "session_start": "pending_session",
            "session_start_and_after_compaction": "pending_compaction_or_session",
            "with_rules": prompt_status.get("core_rules", "unknown"),
        }[timing]
        system_part = part == "core_rules" or (
            part == "resident_md" and provider and provider.initial_instructions_transport == "system_instructions"
        )
        if timing != "off" and system_part and provider:
            if provider.system_context_every_turn:
                status = "next_turn"
            elif provider.system_context_survives_compaction and timing == "session_start_and_after_compaction":
                status = "pending_session"
        prompt_status[part] = status
        add(f"prompt_injection.{part}", status, "configured_injection_policy_without_delivery_observation",
            timing=timing, placement=places[part], source=sources[part])
    for group in CAPABILITY_GROUPS:
        for item in config[group]:
            status = "disabled" if not item["enabled"] else (
                prompt_status["turn_skills"] if group == "skills" else tool_status
            )
            add(f"capability_config.{group}", status,
                "configured_call_policy_deny" if not item["enabled"] else "assembly_required_without_delivery_observation",
                **item)
    for name in ("model", "reasoning_effort", "service_tier", "collaboration_mode", "permission_preset",
                 "allow_subagents", "agent_harness", "prompt_context", "backend_options", "backend_role"):
        if name in settings:
            add(name, "next_turn", "configured_for_future_input_without_delivery_observation")
    return {"schema": "tfi.resident-application.v1", "settings_revision": revision, "items": items}
