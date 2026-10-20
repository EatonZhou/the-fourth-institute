"""Bounded configuration shared by Resident backends.

The backend identity and account route belong to the Resident. Model selection
and prompt context have one location in its runtime settings. Only transport
and protocol parameters belong to backend_options; they cannot override identity or input.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any, Mapping

from .model_api_protocols import PROTOCOLS

CONFIGURATION_SETTINGS = frozenset({"prompt_context", "backend_options"})
API_RUNTIME_SETTINGS = ("model", "prompt_context", "backend_options", "prompt_injection", "agent_harness")

API_PARAMETERS = frozenset().union(*(adapter.parameters for adapter in PROTOCOLS.values()))


def _object(value: object, fields: set[str] | frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = set(value) - fields
    if unknown:
        raise ValueError(f"Unsupported {label} fields: " + ", ".join(sorted(unknown)))
    return deepcopy(value)


def _number(value: object, low: float, high: float, label: str, *, integer: bool = False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (integer and not isinstance(value, int)):
        raise ValueError(f"{label} must be {'an integer' if integer else 'a number'}")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be between {low} and {high}")
    return value


def normalize_prompt_context(value: object) -> dict[str, Any]:
    context = _object(value, {"system_prompt", "messages", "history"}, "prompt_context")
    system = context.get("system_prompt", "")
    if not isinstance(system, str) or len(system) > 200000:
        raise ValueError("prompt_context.system_prompt must be text of at most 200000 characters")
    messages = context.get("messages", [])
    if not isinstance(messages, list) or len(messages) > 200:
        raise ValueError("prompt_context.messages must contain at most 200 messages")
    for item in messages:
        message = _object(item, {"role", "content"}, "prompt_context.messages item")
        if message.get("role") not in {"system", "user", "assistant"} or not isinstance(message.get("content"), str):
            raise ValueError("Preset messages require a system/user/assistant role and text content")
    if len(json.dumps(context, ensure_ascii=False).encode()) > 2 * 1024 * 1024:
        raise ValueError("prompt_context is too large")
    result = {"system_prompt": system, "messages": messages}
    if "history" in context:
        history = _object(context["history"], {"enabled", "max_tokens"}, "prompt_context.history")
        if not isinstance(history.get("enabled", False), bool):
            raise ValueError("prompt_context.history.enabled must be boolean")
        result["history"] = {"enabled": history.get("enabled", False), "max_tokens": _number(
            history.get("max_tokens", 8000), 256, 200000, "history.max_tokens", integer=True)}
    return result


def normalize_backend_options(value: object, backend: str) -> dict[str, Any]:
    options = _object(value, {"parameters", "timeout_seconds", "max_retries"} if backend == "provider_api" else set(), "backend_options")
    if backend != "provider_api":
        return options
    parameters = _object(options.get("parameters", {}), API_PARAMETERS, "backend_options.parameters")
    if "questions" in parameters:
        from management_portal.jev_provider import validate_questions
        parameters["questions"] = validate_questions(parameters["questions"])
        if set(parameters) != {"questions"}:
            raise ValueError("Jev questions cannot be combined with chat generation parameters")
    for key, low, high in (("temperature", 0, 2), ("top_p", 0, 1), ("frequency_penalty", -2, 2), ("presence_penalty", -2, 2)):
        if key in parameters:
            _number(parameters[key], low, high, key)
    for key in ("max_tokens", "max_completion_tokens"):
        if key in parameters:
            _number(parameters[key], 1, 2**31-1, key, integer=True)
    if "reasoning_effort" in parameters and (not isinstance(parameters["reasoning_effort"], str) or not parameters["reasoning_effort"]):
        raise ValueError("reasoning_effort must be non-empty text")
    if "seed" in parameters:
        _number(parameters["seed"], -(2**63), 2**63-1, "seed", integer=True)
    if "stop" in parameters:
        stop = parameters["stop"]
        if not (isinstance(stop, str) or (isinstance(stop, list) and len(stop) <= 4 and all(isinstance(s, str) for s in stop))):
            raise ValueError("stop must be text or at most four strings")
    if "thinking" in parameters:
        thinking = _object(parameters["thinking"], {"type"}, "thinking")
        if thinking.get("type") not in {"enabled", "disabled"}:
            raise ValueError("thinking.type must be enabled or disabled")
    if "response_format" in parameters:
        fmt = _object(parameters["response_format"], {"type", "json_schema"}, "response_format")
        if fmt.get("type") not in {"text", "json_object", "json_schema"}:
            raise ValueError("Unsupported response_format.type")
        if fmt["type"] == "json_schema":
            schema = _object(fmt.get("json_schema"), {"name", "description", "strict", "schema"}, "response_format.json_schema")
            if not isinstance(schema.get("name"), str) or not schema["name"] or not isinstance(schema.get("schema"), dict):
                raise ValueError("response_format.json_schema requires name and schema")
            if "strict" in schema and not isinstance(schema["strict"], bool):
                raise ValueError("response_format strict must be boolean")
        elif "json_schema" in fmt:
            raise ValueError("json_schema requires response_format.type=json_schema")
    if len(json.dumps(parameters, ensure_ascii=False, allow_nan=False).encode()) > 2 * 1024 * 1024:
        raise ValueError("backend_options.parameters is too large")
    return {"parameters": parameters,
            "timeout_seconds": _number(options.get("timeout_seconds", 120), 1, 86400, "timeout_seconds"),
            "max_retries": _number(options.get("max_retries", 0), 0, 10, "max_retries", integer=True)}


def normalize_configuration_settings(settings: Mapping[str, Any], backend: str) -> dict[str, Any]:
    result = {}
    if "prompt_context" in settings:
        result["prompt_context"] = normalize_prompt_context(settings["prompt_context"])
        if backend != "provider_api" and "history" in result["prompt_context"]:
            raise ValueError(f"{backend} does not support API conversation history settings")
        if backend != "provider_api" and result["prompt_context"]["messages"]:
            raise ValueError(f"{backend} does not support preset structured messages; use context initialization")
    if "backend_options" in settings:
        result["backend_options"] = normalize_backend_options(settings["backend_options"], backend)
    return result


def validate_api_parameters(parameters: Mapping[str, Any], protocol: str, *, require_questions: bool = False) -> None:
    from .model_api_protocols import protocol_adapter
    protocol_adapter(protocol).validate(parameters, require_questions=require_questions)


def validate_api_settings(route: str, current: Mapping[str, Any], updates: Mapping[str, Any]) -> dict[str, Any]:
    from management_portal.ai_provider_registry import AIProviderRegistry, shared_ai_provider_registry
    unknown = set(updates) - set(API_RUNTIME_SETTINGS) - {"allow_subagents", "backend_role", "backend_role_fallback"}
    if unknown or updates.get("allow_subagents") is True:
        raise ValueError("Model API does not support requested settings: " + ", ".join(sorted(unknown or {"allow_subagents"})))
    entry = shared_ai_provider_registry.get(route)
    if not entry or not AIProviderRegistry.supports_model_api(entry):
        raise ValueError("backend_route_id must reference an enabled Model API")
    if "model" in updates:
        model = str(updates["model"] or "")
        models = AIProviderRegistry.models(entry)
        if model and models and model not in models:
            raise ValueError(f"Model API does not offer model {model!r}")
    result = dict(updates)
    result.update(normalize_configuration_settings(updates, "provider_api"))
    options = result.get("backend_options", current.get("backend_options", {})) or {}
    validate_api_parameters(options.get("parameters", {}), entry["kind"])
    from .model_api_protocols import protocol_settings
    capabilities = protocol_settings(entry)
    parameters = options.get("parameters", {})
    model = result.get("model") or current.get("model") or entry.get("default_model")
    model_entry = next((item for item in entry.get("models", []) if isinstance(item, dict) and item.get("id") == model), {})
    if "supported_parameters" in model_entry and entry["kind"] == "openai_compatible_chat":
        unsupported = set(parameters) - set(capabilities["parameters_by_model"].get(model, []))
        if unsupported:
            raise ValueError("Model API model does not declare parameters: " + ", ".join(sorted(unsupported)))
    if "reasoning_effort" in parameters and parameters["reasoning_effort"] not in capabilities["reasoning_efforts_by_model"].get(model, []):
        raise ValueError("Model API model does not declare this reasoning_effort")
    if options.get("timeout_seconds", 120) > capabilities["timeout_max"]:
        raise ValueError(f"Protocol timeout_seconds must be at most {capabilities['timeout_max']}")
    return result


def api_capabilities() -> dict[str, Any]:
    from .harness_capabilities import available_feature_ids
    return {"schema": "tfi.runtime-capabilities.v1", "settings": list(API_RUNTIME_SETTINGS),
            "operations": [], "permission_presets": [], "permission_labels": {},
            "default_permission_label": "", "harness_capabilities": [],
            "harness_features": available_feature_ids(frozenset())}
