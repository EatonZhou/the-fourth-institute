"""Codex accounts that authenticate with a Model API key instead of a ChatGPT login.

The key lives in the account home the way a ChatGPT login keeps its tokens:
Codex's own ``auth.json``, written by ``codex login --with-api-key``, private to
the account. Portal owns a few ``config.toml`` keys that point the whole
app-server process at the Model API's Responses endpoint and switch off what
would reach other services (web search, connectors, plugins, telemetry), so
the key is only sent to that endpoint. The service definition and its
``auth.json`` start condition stay the same on every platform.
"""
from __future__ import annotations

import datetime as _datetime
import json
import re
import tomllib
from typing import Any

from management_common.file_permissions import atomic_write_private_bytes
from management_common.process_runtime import run_process
from management_portal.ai_provider_registry import AIProviderRegistry, shared_ai_provider_registry
from management_portal.hatg.contracts import InstructionError

PROTOCOL = "openai_responses"
PROVIDER_ID = "portal_api"
# Reasoning efforts the app-server sends as given; it replaces any other value
# (e.g. "ultra") with its default effort, so those are not offered.
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
# Model API fields that change what the app-server process is configured with.
CONFIGURED_FIELDS = frozenset({"base_url", "protocols", "default_headers", "max_retries", "timeout_s", "label",
                               "default_model", "api_key_file", "enabled"})


def api_accounts(api_id: str | None = None) -> list[dict[str, Any]]:
    """Codex api_profile registrations, optionally only those using ``api_id``."""
    from .account_registrations import registrations
    return [row for row in registrations("codex") if row["kind"] == "api_profile"
            and (api_id is None or row["config"].get("api_id") == api_id)]


def catalog(entry: dict[str, Any], account_id: str) -> dict[str, Any]:
    """The Codex runtime catalog of an API route, from its Model API registration."""
    models = AIProviderRegistry.harness_models(entry)
    efforts = {model: [value for value in AIProviderRegistry.model_entry(entry, model).get("reasoning_efforts") or []
                       if value in EFFORTS] for model in models}
    return {"source": "model_api", "account_id": account_id, "models": models,
            "default_model": str(entry.get("default_model") or "") or models[0], "default_reasoning_effort": "",
            "efforts_by_model": efforts, "default_efforts_by_model": {model: "" for model in models},
            "service_tiers_by_model": {model: [] for model in models},
            "input_modalities_by_model": {model: ["text"] for model in models}}


def managed_config(entry: dict[str, Any]) -> dict[str, Any]:
    base_url, _auth = AIProviderRegistry.harness_endpoint(entry, PROTOCOL)
    provider: dict[str, Any] = {"name": str(entry.get("label") or entry["id"]), "base_url": base_url,
                                "wire_api": "responses", "requires_openai_auth": True}
    headers = {str(name): str(value).strip() for name, value in (entry.get("default_headers") or {}).items()
               if str(value or "").strip()}
    if headers:
        provider["http_headers"] = headers
    if entry.get("max_retries") is not None:
        provider["request_max_retries"] = int(entry["max_retries"])
    if entry.get("timeout_s"):
        provider["stream_idle_timeout_ms"] = int(float(entry["timeout_s"]) * 1000)
    return {
        "model_provider": PROVIDER_ID, "model": str(entry.get("default_model") or ""),
        "forced_login_method": "api", "cli_auth_credentials_store": "file", "web_search": "disabled",
        "model_providers": {PROVIDER_ID: provider},
        "features": {"apps": False, "plugins": False, "memories": False},
        "analytics": {"enabled": False},
        "otel": {"exporter": "none", "metrics_exporter": "none", "trace_exporter": "none"},
    }


def merged_config(existing: dict[str, Any], managed: dict[str, Any]) -> dict[str, Any]:
    # Codex keeps its own settings (trusted projects…) in the same file.
    result = dict(existing)
    for key, value in managed.items():
        current = existing.get(key)
        result[key] = {**current, **value} if isinstance(value, dict) and isinstance(current, dict) else value
    return result


_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _toml_key(name: str) -> str:
    return name if _BARE_KEY.fullmatch(name) else _toml_string(name)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (_datetime.datetime, _datetime.date, _datetime.time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_toml_key(str(k))} = {_toml_value(v)}" for k, v in value.items()) + "}"
    raise TypeError(f"Unsupported TOML value: {type(value).__name__}")


def render_toml(document: dict[str, Any]) -> str:
    def table(values: dict[str, Any], path: tuple[str, ...]) -> list[str]:
        scalars = [(key, value) for key, value in values.items() if not isinstance(value, dict)]
        tables = [(key, value) for key, value in values.items() if isinstance(value, dict)]
        lines = ["", "[" + ".".join(map(_toml_key, path)) + "]"] if path and (scalars or not tables) else []
        lines += [f"{_toml_key(str(key))} = {_toml_value(value)}" for key, value in scalars]
        for key, value in tables:
            lines += table(value, (*path, str(key)))
        return lines

    return "\n".join(table(document, ())).strip() + "\n"


def _write_config(account_id: str, entry: dict[str, Any]) -> bool:
    from .account_hosting import account_home
    path = account_home("codex", account_id) / "config.toml"
    existing = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    desired = merged_config(existing, managed_config(entry))
    if desired == existing:
        return False
    atomic_write_private_bytes(path, render_toml(desired).encode("utf-8"))
    return True


def _write_credentials(account_id: str, entry: dict[str, Any]) -> bool:
    from .account_hosting import account_home
    from .account_login import local_login_environment, provider_binary
    home = account_home("codex", account_id)
    key = shared_ai_provider_registry.read_secret(shared_ai_provider_registry.secret_path(entry))
    try:
        stored = json.loads((home / "auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None
    if isinstance(stored, dict) and stored.get("auth_mode") == "apikey" and stored.get("OPENAI_API_KEY") == key:
        return False
    # Logging in with a key is local; the key goes on stdin and the output,
    # which may echo account details, is never kept.
    result = run_process([provider_binary("codex"), "login", "--with-api-key"], input=(key + "\n").encode("utf-8"),
                         env=local_login_environment("codex", account_id), capture_output=True, timeout=60)
    if result.returncode:
        raise InstructionError("instruction.failed", "Codex 未能保存模型 API 的 API Key",
                               details={"reason": "account.login_unavailable"})
    return True


def prepare(row: dict[str, Any]) -> bool:
    """Bring an API account's home in line with its Model API; whether anything changed."""
    from .account_hosting import ensure_account_home
    ensure_account_home("codex", row["account_id"])
    entry = shared_ai_provider_registry.get(row["config"]["api_id"])
    changed = _write_credentials(row["account_id"], entry)
    return _write_config(row["account_id"], entry) or changed


def busy_accounts(api_id: str) -> dict[str, dict[str, int]]:
    """Enabled API accounts of ``api_id`` whose running work a restart would cut off."""
    from .isa import account_busy_counts
    busy = {}
    for row in api_accounts(api_id):
        if row["enabled"]:
            counts = account_busy_counts(row)
            if counts["execution_count"]:
                busy[row["account_id"]] = counts
    return busy


def refresh(rows: list[dict[str, Any]]) -> list[str]:
    """Rewrite the homes of ``rows`` and restart the app-servers whose configuration changed."""
    from .account_hosting import start_codex_instance, stop_codex_instance
    from .provider_updates import runtime_available
    restarted = []
    if not rows or not runtime_available("codex"):
        return restarted
    for row in rows:
        if not row["enabled"]:
            continue
        try:
            shared_ai_provider_registry.get(row["config"]["api_id"])
        except ValueError:  # a disabled Model API admits no new work; keep the account as it is
            continue
        if prepare(row):
            stop_codex_instance(row["account_id"])
            start_codex_instance(row["account_id"])
            restarted.append(row["account_id"])
    return restarted
