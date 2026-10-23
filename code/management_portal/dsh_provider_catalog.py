"""DeepSeek Harness model/route semantics, based on the pinned native adapter.

Each API account uses the address, credentials and declared models of its
Messages API. The installed ``deepseek`` route keeps its existing identity.
"""
from management_portal.ai_provider_registry import AIProviderRegistry
from management_portal.ai_provider_registry import shared_ai_provider_registry
from management_portal import model_api_store

ROUTE = "deepseek"


def supports_entry(row):
    return AIProviderRegistry.is_enabled(row) and "anthropic_messages" in AIProviderRegistry.harness_protocols(row)


def profile(route):
    """The LLM API Registry entry whose key, address and models the route uses."""

    from management_portal.execution.account_registrations import registrations
    account = next((r for r in registrations("dsh") if r["kind"] == "api_profile" and r["route_id"] == route), None)
    api_id = account["config"]["api_id"] if account else model_api_store.route_api_id("dsh", route)
    if api_id:
        row = shared_ai_provider_registry.get(api_id)
        if supports_entry(row):
            return row
    raise ValueError("DeepSeek Harness route has no enabled, explicitly bound Messages API")


def catalog(route):
    row = profile(route)
    models = AIProviderRegistry.harness_models(row)
    efforts = {m: [e for e in AIProviderRegistry.model_entry(row, m).get("reasoning_efforts",
                 AIProviderRegistry.model_entry(row, m).get("support_efforts", [])) if e in {"low", "high", "max"}] for m in models}
    if row.get("default_model") not in efforts:
        raise ValueError("DeepSeek Harness route needs an enabled default model")
    return {"schema": "tfi.agent-runtime-catalog.v1", "backend": "dsh", "provider": "dsh",
            "label": "DeepSeek Harness", "backend_route_id": route, "provider_profile": row["id"],
            "models": list(efforts), "default_model": row["default_model"], "efforts_by_model": efforts,
            "upstream_provider": row.get("provider", ""),
            "runtime_settings": ["model", "reasoning_effort"], "settings_apply": "next_turn",
            "service_tiers_by_model": {m: [] for m in efforts},
            "permission_policy": {"permission_preset_editable": False},
            "capabilities": {"turn": True, "release": True, "fork": False, "compact": True,
                             "active_turn_input": True, "media": False, "interactive_approvals": False}}


def routes():
    from management_portal.execution.account_registrations import registrations
    result = []
    for row in registrations("dsh"):
        try:
            result.append(catalog(row["route_id"]))
        except ValueError:
            continue
    if not any(row["backend_route_id"] == ROUTE for row in result):
        try:
            result.append(catalog(ROUTE))
        except ValueError:
            pass
    return result
