"""Account and model last selected when a user successfully created a Resident."""
from __future__ import annotations

from management_common.principal import actor_principal
from management_portal import presentation_store


def remembered(actor):
    principal = actor_principal(actor)
    if principal.kind == "user":
        return presentation_store.preferences(principal.id).get("last_resident_creation") or {}
    return {}


def remember(actor, resident, settings):
    principal = actor_principal(actor)
    if principal.kind != "user" or not resident.get("backend") or "backend_route_id" not in resident:
        return
    choice = {key: resident[key] for key in ("backend", "backend_route_id")}
    choice["model"] = str(settings.get("model") or "")
    presentation_store.update_preferences(principal.id, lambda current: {**current, "last_resident_creation": choice})


def available(choice, catalog, load_models=None):
    """Drop unavailable routes or models. Some providers list models on demand."""
    if not isinstance(choice, dict) or not choice.get("backend") or "backend_route_id" not in choice:
        return {}
    for backend in catalog.get("backends", ()):
        if backend["backend"] != choice["backend"]:
            continue
        for route in backend["routes"]:
            if route["backend_route_id"] != choice["backend_route_id"]:
                continue
            model = choice.get("model") or ""
            if load_models and model and "models" not in route:
                route = load_models(route["account_id"], backend=choice["backend"])
            if "models" in route and model not in route["models"]:
                model = route.get("default_model") or ""
            return {**choice, "model": model}
    return {}


def for_creation(actor, provider, account, settings):
    if actor_principal(actor).kind != "user" or (provider and account and "model" in settings):
        return {}
    choice = remembered(actor)
    if not isinstance(choice, dict) or not choice.get("backend") or "backend_route_id" not in choice:
        return {}
    if (provider and provider != choice["backend"]) or (account and account != choice["backend_route_id"]):
        return {}
    from management_portal.execution.accounts import default_execution_accounts
    accounts = default_execution_accounts(tolerate_unavailable=True)
    catalog = accounts.runtime_catalog(include_backends=True)
    return available(choice, catalog, None if "model" in settings else accounts.runtime_catalog)


def form_default(actor, catalog):
    return available(remembered(actor), catalog)
