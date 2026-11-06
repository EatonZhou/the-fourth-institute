"""Kimi API account models come from their bound Model API, not the service template."""
from management_portal.ai_provider_registry import AIProviderRegistry, shared_ai_provider_registry
import json


def profile(account):
    entry = shared_ai_provider_registry.get(account["config"]["api_id"])
    if not AIProviderRegistry.is_enabled(entry) or "openai_chat" not in AIProviderRegistry.harness_protocols(entry):
        raise ValueError("Kimi Code needs an enabled Chat Completions API")
    return entry


def catalog(account):
    if account["kind"] == "subscription_login":
        from .kimi_account_runtime import client_for_route
        client = client_for_route(account["route_id"])
        auth = client._request("GET", "auth")
        if (auth.get("managed_provider") or {}).get("status") != "authenticated":
            raise ValueError("请先完成 Kimi Code 订阅登录")
        config = client._request("GET", "config")
        entries = {name: model for name, model in config.get("models", {}).items()
                   if model.get("provider") == "managed:kimi-code"}
        default = config.get("default_model", "")
        if not entries or default not in entries:
            raise ValueError("Kimi Code 订阅模型目录尚未就绪")
        return {"models": list(entries), "default_model": default,
                "efforts_by_model": {}, "service_tiers_by_model": {},
                "context_windows": {name: int(model.get("max_context_size") or 32768)
                                    for name, model in entries.items()}}
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


def runtime_config(account, key, *, base_url=None):
    """Use a declared native SDK when its continuation state is protocol-specific."""
    entry = profile(account)
    models = catalog(account)
    native_google = "google_genai" in AIProviderRegistry.harness_protocols(entry)
    if native_google:
        base_url, _ = AIProviderRegistry.harness_endpoint(entry, "google_genai")
    return {"default_model": models["default_model"],
            "providers": {"portal_api": {"type": "google-genai" if native_google else "openai", "base_url": base_url or AIProviderRegistry.base_url(entry),
                                         "api_key": key, "custom_headers": entry.get("default_headers") or {}}},
            "models": {m: {"provider": "portal_api", "model": m,
                            "max_context_size": models["context_windows"][m]} for m in models["models"]},
            "telemetry": {"enabled": False}, "subagent": {"timeout_ms": 2147000000}}


def write_config(account, target):
    """The native host owns its configuration and any transient wire adapter."""
    from management_common.file_permissions import atomic_write_private_bytes
    from . import kimi_portal_tools
    if account['kind'] == 'subscription_login':
        kimi_portal_tools.install(target)
        return
    from .model_api_bridge import connection
    entry = profile(account)
    if "google_genai" in AIProviderRegistry.harness_protocols(entry):
        base, _ = AIProviderRegistry.harness_endpoint(entry, "google_genai")
        key = shared_ai_provider_registry.read_secret(shared_ai_provider_registry.secret_path(entry))
    else:
        base, auth, key = connection(entry, 'openai_chat', account_id=account['account_id'])
        if auth != 'bearer':
            raise ValueError('Kimi Chat transport requires bearer authentication')
    config = runtime_config(account, key, base_url=base)
    lines = []
    def table(value, parents=()):
        if parents:
            lines.append('[' + '.'.join(json.dumps(p) for p in parents) + ']')
        for name, child in value.items():
            if not isinstance(child, dict):
                lines.append(json.dumps(name) + ' = ' + json.dumps(child, ensure_ascii=False))
        for name, child in value.items():
            if isinstance(child, dict):
                table(child, (*parents, name))
    table(config)
    content = '\n'.join(lines) + '\n' + kimi_portal_tools.hook_config()
    atomic_write_private_bytes(target / 'config.toml', content.encode())
    kimi_portal_tools.install(target)
