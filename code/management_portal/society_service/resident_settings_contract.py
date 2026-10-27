"""The two constrained parts of a Resident's current execution configuration.

Execution settings describe model and invocation policy. Harness configuration
describes the optional pipeline structure. API runtime_settings is their read
projection; execution requests may retain that projection as a dated snapshot.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


EXECUTION_SETTING_FIELDS = frozenset({
    "backend", "model", "reasoning_effort", "service_tier", "collaboration_mode",
    "permissions", "approvals_reviewer", "approval_policy", "sandbox",
    "sandbox_explicit", "profile", "permission_preset",
    "prompt_injection", "capability_config", "allow_subagents", "updated_at", "updated_by",
    "prompt_context", "backend_options",
    "backend_role", "backend_role_fallback",
    "runtime_settings_authority", "runtime_settings_authority_at",
    "runtime_settings_authority_by",
})
RUNTIME_SETTING_FIELDS = EXECUTION_SETTING_FIELDS | {"agent_harness"}


def split_runtime_settings(settings: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from management_portal.execution import resident_runtime_settings

    unknown = set(settings) - RUNTIME_SETTING_FIELDS
    if unknown:
        raise ValueError("Unsupported Resident execution settings: " + ", ".join(sorted(unknown)))
    normalized = resident_runtime_settings.normalize_settings(dict(settings))
    harness = normalized.pop("agent_harness")
    return normalized, harness


def runtime_settings_from_parts(
    execution_settings: Mapping[str, Any], harness_config: Mapping[str, Any],
) -> dict[str, Any]:
    from management_portal.execution import resident_runtime_settings

    unknown = set(execution_settings) - EXECUTION_SETTING_FIELDS
    if unknown:
        raise ValueError("Unsupported Resident execution settings: " + ", ".join(sorted(unknown)))
    return resident_runtime_settings.normalize_settings({
        **deepcopy(dict(execution_settings)),
        "agent_harness": deepcopy(dict(harness_config)),
    })


def stage_runtime_settings(record: dict[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    """Stage the canonical parts on a registration/update record, never a copy."""
    execution, harness = split_runtime_settings(settings)
    record["execution_settings"] = execution
    record["harness_config"] = harness
    record.pop("runtime_settings", None)
    # backend and record timestamps describe the Resident as well as its
    # execution configuration. The other values only belong to these parts.
    for field in RUNTIME_SETTING_FIELDS - {"backend", "updated_at", "updated_by"}:
        record.pop(field, None)
    return runtime_settings_from_parts(execution, harness)
