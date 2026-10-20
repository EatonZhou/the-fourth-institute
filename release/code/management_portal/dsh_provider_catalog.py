"""DeepSeek Harness model/route semantics, based on the pinned native adapter.

DSH offers one route, ``deepseek``, named after the service.  It uses the key,
address and models of the explicitly bound DeepSeek entry in the LLM API
Registry, and that entry's balance pays for it.
"""
from management_portal.ai_provider_registry import AIProviderRegistry
from management_portal.ai_provider_registry import shared_ai_provider_registry
from management_portal import model_api_store

ROUTE = "deepseek"


def supports_entry(row):
    return AIProviderRegistry.is_enabled(row) and row.get("kind") == "openai_compatible_chat" and row.get("provider") == "deepseek"


def profile(route):
    """The LLM API Registry entry whose key, address and models the route uses."""

    if route != ROUTE:
        raise ValueError(f"Unsupported DeepSeek Harness route: {route}")
    api_id = model_api_store.route_api_id("dsh", route)
    if api_id:
        row = shared_ai_provider_registry.get(api_id)
        if supports_entry(row):
            return row
    raise ValueError("DeepSeek Harness route has no enabled, explicitly bound DeepSeek API")


def catalog(route):
    row = profile(route)
    models = [m for m in row.get("models", []) if isinstance(m, dict) and m.get("id") in AIProviderRegistry.models(row)]
    efforts = {m["id"]: [e for e in m.get("support_efforts", []) if e in {"low", "high", "max"}] for m in models}
    if row.get("default_model") not in efforts:
        raise ValueError("DeepSeek Harness route needs an enabled default model")
    return {"schema": "tfi.agent-runtime-catalog.v1", "backend": "dsh", "provider": "dsh",
            "label": "DeepSeek Harness", "backend_route_id": route, "provider_profile": row["id"],
            "models": list(efforts), "default_model": row["default_model"], "efforts_by_model": efforts,
            "runtime_settings": ["model", "reasoning_effort"], "settings_apply": "next_turn",
            "service_tiers_by_model": {m: [] for m in efforts},
            "permission_policy": {"permission_preset_editable": False},
            "capabilities": {"turn": True, "release": True, "fork": False, "compact": True,
                             "active_turn_input": True, "media": False, "interactive_approvals": False}}


def routes():
    try:
        result = catalog(ROUTE)
    except ValueError:
        return []
    return [result]
