"""A Resident's runtime settings: model, reasoning effort, speed, permissions and collaboration mode.

The settings model every Provider shares: normalizing stored or requested
values, merging updates, the permission presets and who set them last.
The Agent Harness configuration it carries is normalized by
``harness_settings``.
"""
from __future__ import annotations

from typing import Any
from .harness_settings import boolean_setting, normalize_agent_harness_settings


EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


SANDBOXES = ("read-only", "workspace-write", "danger-full-access")


PERMISSION_PRESETS = (
    {
        "id": "default",
        "label": "Ask for permissions",
        "permission_profile": ":workspace",
        "sandbox": "workspace-write",
        "approval_policy": "on-request",
        "approvals_reviewer": "user",
        "description": "Codex can read and edit files in the current workspace, and run commands. Approval is required to access the internet or edit other files.",
    },
    {
        "id": "auto-review",
        "label": "Auto-review",
        "permission_profile": ":workspace",
        "sandbox": "workspace-write",
        "approval_policy": "on-request",
        "approvals_reviewer": "auto_review",
        "description": "Same workspace-write permissions as Ask for permissions, but eligible on-request approvals are routed through the auto-reviewer subagent.",
    },
    {
        "id": "full-access",
        "label": "Full Access",
        "permission_profile": ":danger-full-access",
        "sandbox": "danger-full-access",
        "approval_policy": "never",
        "approvals_reviewer": "user",
        "description": "Codex can edit files outside this workspace and access the internet without asking for approval. Exercise caution when using.",
    },
)


def now_settings(
    *,
    backend: str,
    model: str = "",
    reasoning_effort: str = "",
    sandbox: str = "",
    service_tier: str = "",
    permissions: str = "",
    approvals_reviewer: str = "",
    approval_policy: str = "",
    permission_preset: str = "",
) -> dict[str, Any]:
    return normalize_settings({
        "backend": backend,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "sandbox": sandbox,
        "service_tier": service_tier,
        "permissions": permissions,
        "approvals_reviewer": approvals_reviewer,
        "approval_policy": approval_policy,
        "permission_preset": permission_preset,
    })


def normalize_settings(raw: object) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    model = _clean_model(data.get("model") or "")
    effort = _clean_effort(data.get("reasoning_effort") or "")
    sandbox = _clean_sandbox(data.get("sandbox") or "")
    service_tier = _clean_service_tier(data.get("service_tier") or "")
    collaboration_mode = _clean_collaboration_mode(data.get("collaboration_mode") or "")
    permissions = _clean_permission_profile(data.get("permissions") or "")
    approvals_reviewer = _clean_approvals_reviewer(data.get("approvals_reviewer") or "")
    approval_policy = _clean_approval_policy(data.get("approval_policy") or "")
    explicit_preset = _permission_preset(data.get("permission_preset") or "")
    # The Resident's backend is its own column; settings name it only when given.
    backend = str(data.get("backend") or "").strip()
    out: dict[str, Any] = {
        **({"backend": backend} if backend else {}),
        "model": model,
        "reasoning_effort": effort,
        "service_tier": service_tier,
        "collaboration_mode": collaboration_mode,
        "permissions": permissions,
        "approvals_reviewer": approvals_reviewer,
        "approval_policy": approval_policy,
        "sandbox": sandbox,
        "sandbox_explicit": bool(data.get("sandbox_explicit")),
        "profile": str(data.get("profile") or "").strip(),
    }
    if explicit_preset:
        out = _apply_permission_preset(out, explicit_preset["id"])
    if out["sandbox"] == "workspace-write" and not out["sandbox_explicit"]:
        out["sandbox"] = ""
    out["permission_preset"] = _permission_preset_id(out)
    updated_at = str(data.get("updated_at") or "").strip()
    if updated_at:
        out["updated_at"] = updated_at
    updated_by = str(data.get("updated_by") or "").strip()
    if updated_by:
        out["updated_by"] = updated_by
    authority = _clean_runtime_authority(data.get("runtime_settings_authority") or "")
    if authority:
        out["runtime_settings_authority"] = authority
    authority_at = str(data.get("runtime_settings_authority_at") or "").strip()
    if authority_at:
        out["runtime_settings_authority_at"] = authority_at
    authority_by = str(data.get("runtime_settings_authority_by") or "").strip()
    if authority_by:
        out["runtime_settings_authority_by"] = authority_by
    if "allow_subagents" in data:
        out["allow_subagents"] = boolean_setting(data["allow_subagents"], default=False)
    if data.get("backend_role"):
        out["backend_role"] = str(data["backend_role"])
    if isinstance(data.get("backend_role_fallback"), dict):
        fallback = data["backend_role_fallback"]
        out["backend_role_fallback"] = {"requested": str(fallback.get("requested") or ""), "used": "default"}
    if data.get("prompt_injection") is not None:
        from management_portal import prompt_injection
        out["prompt_injection"] = prompt_injection.normalize(data["prompt_injection"])
    if "capability_config" in data:
        from .resident_capability_settings import normalize_capability_config
        out["capability_config"] = normalize_capability_config(data["capability_config"])
    from .resident_configuration import normalize_configuration_settings
    out.update(normalize_configuration_settings(data, backend))
    raw_harness = data.get("agent_harness")
    out["agent_harness"] = normalize_agent_harness_settings(raw_harness)
    return out


def merge_settings(base: object, updates: object, *, actor: str = "", updated_at: str = "") -> dict[str, Any]:
    merged = normalize_settings(base)
    raw_updates = updates if isinstance(updates, dict) else {}
    combined = {**merged, **raw_updates}
    raw_harness_key = "agent_harness" if "agent_harness" in raw_updates else ""
    if raw_harness_key:
        current_harness = normalize_agent_harness_settings(
            merged.get("agent_harness")
        )
        harness_updates = raw_updates.get(raw_harness_key)
        harness_updates = harness_updates if isinstance(harness_updates, dict) else {}
        current_features = current_harness.get("features")
        current_features = current_features if isinstance(current_features, dict) else {}
        update_features = harness_updates.get("features")
        update_features = update_features if isinstance(update_features, dict) else {}
        merged_features = dict(current_features)
        for feature_id, feature_updates in update_features.items():
            if not isinstance(feature_updates, dict):
                continue
            current_feature = merged_features.get(feature_id)
            current_feature = current_feature if isinstance(current_feature, dict) else {}
            merged_features[feature_id] = {
                **current_feature,
                **feature_updates,
            }
        combined["agent_harness"] = {
            **current_harness,
            **harness_updates,
            "features": merged_features,
        }
    clean_updates = normalize_settings(combined)
    for key in (
        "model",
        "reasoning_effort",
        "service_tier",
        "collaboration_mode",
        "permissions",
        "approvals_reviewer",
        "approval_policy",
        "sandbox",
        "sandbox_explicit",
        "profile",
        "permission_preset",
        "runtime_settings_authority",
        "runtime_settings_authority_at",
        "runtime_settings_authority_by",
        "prompt_injection",
        "capability_config",
        "allow_subagents",
        "agent_harness",
        "prompt_context",
        "backend_options",
        "backend_role",
        "backend_role_fallback",
    ):
        if key in clean_updates:
            merged[key] = clean_updates[key]
    if actor:
        merged["updated_by"] = str(actor)
    if updated_at:
        merged["updated_at"] = str(updated_at)
    return merged


def mark_portal_manual_authority(settings: object, *, actor: str = "", updated_at: str = "") -> dict[str, Any]:
    data = normalize_settings(settings)
    data["runtime_settings_authority"] = "portal_manual"
    if actor:
        data["runtime_settings_authority_by"] = str(actor)
    if updated_at:
        data["runtime_settings_authority_at"] = str(updated_at)
    return data


def effective_settings(settings: object) -> dict[str, Any]:
    """The settings with their permission preset spelled out; model and effort defaults stay the Provider's."""
    return with_permission_preset(normalize_settings(settings))


def with_permission_preset(data: dict[str, Any]) -> dict[str, Any]:
    preset_id = str(data.get("permission_preset") or _permission_preset_id(data) or "").strip()
    return _apply_permission_preset(data, preset_id) if preset_id else data


def read_only_runtime_settings(settings: object = None) -> dict[str, Any]:
    """The settings of a session that may only read: what an actor without workspace authority gets."""
    data = normalize_settings(settings or {})
    data.update({
        "permissions": ":read-only",
        "approvals_reviewer": "",
        "approval_policy": "on-request",
        "sandbox": "read-only",
        "sandbox_explicit": True,
        "profile": "",
        "permission_preset": "",
    })
    return data


def _clean_model(value: object) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 120:
        return ""
    return text if all(ch.isalnum() or ch in "._:/@+-" for ch in text) else ""


def _clean_effort(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in EFFORTS else ""


def _clean_sandbox(value: object) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "readonly": "read-only",
        "read_only": "read-only",
        "workspace": "workspace-write",
        "workspace_write": "workspace-write",
        "danger": "danger-full-access",
        "full": "danger-full-access",
        "full-access": "danger-full-access",
    }
    text = aliases.get(text, text)
    return text if text in SANDBOXES else ""


def _clean_service_tier(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"", "none", "null"}:
        return ""
    if len(text) > 80:
        return ""
    return text if all(ch.isalnum() or ch in "._:/@+-" for ch in text) else ""


def _clean_collaboration_mode(value: object) -> str:
    text = str(value or "").strip().casefold().replace("_", "-")
    aliases = {
        "": "default",
        "off": "default",
        "normal": "default",
        "on": "plan",
    }
    text = aliases.get(text, text)
    return text if text in {"default", "plan"} else "default"


def _clean_permission_profile(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("id") or ""
    text = str(value or "").strip()
    if not text or len(text) > 120:
        return ""
    return text if all(ch.isalnum() or ch in "._:/@+-" for ch in text) else ""


def _clean_approvals_reviewer(value: object) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    return text if text in {"user", "auto_review", "guardian_subagent"} else ""


def _clean_approval_policy(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    return text if text in {"untrusted", "on-request", "never"} else ""


def _clean_runtime_authority(value: object) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    return text if text in {"portal_manual"} else ""


def _permission_preset(value: object) -> dict[str, str] | None:
    text = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "workspace": "default",
        ":workspace": "default",
        "default": "default",
        "ask": "default",
        "ask-permissions": "default",
        "ask-for-permissions": "default",
        "askforpermissions": "default",
        "auto": "auto-review",
        "auto-review": "auto-review",
        "auto-reviewer": "auto-review",
        "autoreview": "auto-review",
        "full": "full-access",
        "full-access": "full-access",
        "danger": "full-access",
        "danger-full-access": "full-access",
        ":danger-full-access": "full-access",
        ":danger-no-sandbox": "full-access",
    }
    target = aliases.get(text, text)
    for preset in PERMISSION_PRESETS:
        if preset["id"] == target:
            return preset
    return None


def _apply_permission_preset(settings: dict[str, Any], preset_id: object) -> dict[str, Any]:
    preset = _permission_preset(preset_id)
    out = dict(settings)
    if not preset:
        return out
    out.update({
        "permissions": preset["permission_profile"],
        "approvals_reviewer": preset["approvals_reviewer"],
        "approval_policy": preset["approval_policy"],
        "sandbox": preset["sandbox"],
        "sandbox_explicit": True,
        "profile": f"permissions:{preset['id']}",
        "permission_preset": preset["id"],
    })
    return out


def _permission_preset_id(settings: dict[str, Any]) -> str:
    explicit_preset = _permission_preset(settings.get("permission_preset") or "")
    if explicit_preset:
        return explicit_preset["id"]
    profile = str(settings.get("permissions") or "").strip()
    reviewer = str(settings.get("approvals_reviewer") or "").strip().lower()
    approval_policy = str(settings.get("approval_policy") or "").strip().lower()
    sandbox = str(settings.get("sandbox") or "").strip()
    explicit = str(settings.get("profile") or "")
    if explicit.startswith("permissions:"):
        preset = _permission_preset(explicit.split(":", 1)[1])
        if preset:
            return preset["id"]
    if profile in {":danger-full-access", ":danger-no-sandbox"}:
        return "full-access"
    if sandbox == "danger-full-access":
        return "full-access"
    if profile == ":workspace" and reviewer == "auto_review" and approval_policy in {"", "on-request"}:
        return "auto-review"
    if profile == ":workspace" and reviewer in {"", "user"} and approval_policy in {"", "on-request"}:
        return "default"
    return ""
