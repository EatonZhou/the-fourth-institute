"""Kimi API account models come from their bound Model API, not the service template."""
from management_portal.ai_provider_registry import AIProviderRegistry, shared_ai_provider_registry


def profile(account):
    entry = shared_ai_provider_registry.get(account["config"]["api_id"])
    if not AIProviderRegistry.is_enabled(entry) or "openai_chat" not in AIProviderRegistry.harness_protocols(entry):
        raise ValueError("Kimi Code needs an enabled Chat Completions API")
    return entry


def catalog(account):
    if account["kind"] == "service":
        from .kimi_code_server import model_catalog
        return model_catalog()
    entry = profile(account)
    models = AIProviderRegistry.harness_models(entry)
    if not models or entry.get("default_model") not in models:
        raise ValueError("Kimi Code API needs an enabled default model")
    return {"models": models, "default_model": entry["default_model"],
            "efforts_by_model": {}, "service_tiers_by_model": {},
            "context_windows": {m: int(AIProviderRegistry.model_entry(entry, m).get("context_window") or 32768) for m in models}}


def runtime_config(account, key):
    """Explicit generic OpenAI transport, with no bundled Kimi model assumptions."""
    entry = profile(account)
    models = catalog(account)
    return {"default_model": models["default_model"],
            "providers": {"portal_api": {"type": "openai", "base_url": AIProviderRegistry.base_url(entry),
                                         "api_key": key, "custom_headers": entry.get("default_headers") or {}}},
            "models": {m: {"provider": "portal_api", "model": m,
                            "max_context_size": models["context_windows"][m]} for m in models["models"]},
            "telemetry": {"enabled": False}, "subagent": {"timeout_ms": 2147000000}}
