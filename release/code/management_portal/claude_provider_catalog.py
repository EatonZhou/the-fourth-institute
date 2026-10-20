"""Claude execution routes and their public capabilities.

The shared registry owns configuration parsing. This module owns Claude route
semantics; it never reads login credentials or writes execution/history state.
An existing DeepSeek route remains DeepSeek when a native route is registered.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from management_portal.ai_provider_registry import AIProviderRegistry

NATIVE_KIND = "subscription_login"
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def is_native(profile: dict[str, Any]) -> bool:
    return profile.get("kind") == NATIVE_KIND and profile.get("provider") == "claude"


def supports_entry(entry: dict[str, Any]) -> bool:
    return AIProviderRegistry.is_enabled(entry) and (is_native(entry) or (
        entry.get("kind") == "openai_compatible_chat" and entry.get("provider") == "deepseek"))


def profile(profile_id: str, *, allow_disabled: bool = False) -> dict[str, Any]:
    from management_portal.execution.account_registrations import registrations
    from management_portal.execution.account_hosting import account_home
    row = next((row for row in registrations("claude") if profile_id in (row["route_id"], row["account_id"])), None)
    if not row:
        raise ValueError(f"Claude route is unavailable: {profile_id}")
    if row["kind"] == "subscription_login":
        entry = {"id": row["route_id"], "provider": "claude", "kind": NATIVE_KIND,
                 "enabled": row["enabled"], "config_dir": str(account_home("claude", row["account_id"])),
                 **row["config"]}
    else:
        entry = {**AIProviderRegistry().get(row["config"]["api_id"]), "id": row["route_id"]}
        entry["enabled"] = row["enabled"] and AIProviderRegistry.is_enabled(entry)
    if not supports_entry({**entry, "enabled": True}):
        raise ValueError(f"Unsupported Claude route: {profile_id}")
    if not allow_disabled and not AIProviderRegistry.is_enabled(entry):
        raise ValueError(f"Claude route is unavailable: {profile_id}")
    if is_native(entry):
        config_directory(entry)
        if any(entry.get(key) for key in ("api_key_file", "api_key_env", "base_url")):
            raise ValueError("Claude subscription routes cannot specify API credentials or an endpoint")
        model_catalog(entry)
    return entry


def config_directory(entry: dict[str, Any]) -> Path:
    raw = str(entry.get("config_dir") or "").strip()
    path = Path(raw)
    if not raw or not path.is_absolute() or path == Path("/") or ".." in path.parts:
        raise ValueError("Claude subscription route requires an absolute, account-specific config_dir")
    return path


def login_environment(entry: dict[str, Any]) -> dict[str, str]:
    """Select local Claude login without borrowing API/cloud credentials.

SDK subprocesses inherit the parent environment; explicit empty values mask
conflicting credentials. Login/refresh remains owned by the native CLI.
    """
    if not is_native(entry):
        raise ValueError("Expected a Claude subscription route")
    return {
        "CLAUDE_CONFIG_DIR": str(config_directory(entry)),
        "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_AUTH_TOKEN": "",
        "ANTHROPIC_PROFILE": "",
        "CLAUDE_CODE_OAUTH_TOKEN": "",
        "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR": "",
        "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR": "",
        "CLAUDE_CODE_USE_BEDROCK": "",
        "CLAUDE_CODE_USE_VERTEX": "",
        "CLAUDE_CODE_USE_FOUNDRY": "",
        "ANTHROPIC_MODEL": "",
        "ANTHROPIC_SMALL_FAST_MODEL": "",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "",
    }


def _model_aliases(entry: dict[str, Any]) -> dict[str, str]:
    """Accept saved runtime aliases without offering ambiguous names in pickers."""
    rows = [row for row in entry.get("models") or []
            if isinstance(row, dict) and str(row.get("id") or "").strip() in AIProviderRegistry.models(entry)]
    ids = {str(row.get("id") or "").strip() for row in rows}
    aliases: dict[str, str] = {}
    for row in rows:
        values = row.get("aliases", [])
        if not isinstance(values, list):
            raise ValueError("Claude model aliases must be a list")
        for alias in values:
            if (not isinstance(alias, str) or not alias.strip() or alias != alias.strip()
                    or alias in ids or alias in aliases):
                raise ValueError("Claude model aliases must be unique and distinct from model IDs")
            aliases[alias] = str(row["id"]).strip()
    return aliases


def model_catalog(entry: dict[str, Any]) -> dict[str, Any]:
    enabled_models = set(AIProviderRegistry.models(entry))
    models: list[str] = []
    efforts: dict[str, list[str]] = {}
    for row in entry.get("models") or []:
        if not isinstance(row, dict) or not AIProviderRegistry.is_enabled(row):
            continue
        model = str(row.get("id") or "").strip()
        if model not in enabled_models or model in models:
            raise ValueError("Claude route requires unique, nonempty model IDs")
        supported = row.get("support_efforts", [] if is_native(entry) else list(EFFORTS))
        if not isinstance(supported, list) or any(value not in EFFORTS for value in supported):
            raise ValueError(f"Invalid Claude effort capabilities for {model}")
        models.append(model)
        efforts[model] = list(dict.fromkeys(supported))
    default = str(entry.get("default_model") or "").strip()
    if not models or default not in models:
        raise ValueError("Claude route default_model must be an enabled model")
    _model_aliases(entry)
    return {
        "schema": "tfi.claude-runtime-catalog.v1",
        "backend": "claude",
        "provider": "claude",
        "provider_profile": str(entry["id"]),
        "backend_route_id": str(entry["id"]),
        "label": str(entry.get("label") or entry["id"]),
        "upstream_provider": "anthropic" if is_native(entry) else "deepseek",
        "authentication": "claude_login" if is_native(entry) else "api_key",
        "source": "configured",
        "models": models,
        "default_model": default,
        "efforts_by_model": efforts,
        "service_tiers_by_model": {model: [] for model in models},
        "runtime_settings": ["model", "reasoning_effort", "permissions"],
        "settings_apply": "next_turn",
        # These are Portal adapter capabilities, not a claim about the CLI.
        "capabilities": {"turn": True, "fork": True, "release": True,
                         "active_turn_input": True, "compact": True,
                         "media": is_native(entry), "interactive_approvals": False},
    }


def catalog(profile_id: str) -> dict[str, Any]:
    # Existing sessions retain their capability display after registration is
    # disabled; execution still uses profile()'s enabled check.
    return model_catalog(profile(profile_id, allow_disabled=True))


def routes() -> list[dict[str, Any]]:
    from management_portal.execution.account_registrations import registrations
    return [catalog(str(row["id"]))
            for row in ({"id": value["route_id"]} for value in registrations("claude") if value["enabled"])]


def validate_settings(entry: dict[str, Any], model: str, effort: str = "") -> tuple[str, str]:
    data = model_catalog(entry)
    model = str(model or data["default_model"]).strip()
    model = _model_aliases(entry).get(model, model)
    effort = str(effort or "").strip()
    if model not in data["models"]:
        raise ValueError(f"Model {model!r} is not enabled for Claude route {entry['id']}")
    if effort and effort not in data["efforts_by_model"][model]:
        raise ValueError(f"Effort {effort!r} is not supported for Claude model {model!r}")
    return model, effort
