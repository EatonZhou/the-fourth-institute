"""OpenCode's routes and models, from the shared runtime Portal installed.

OpenCode reaches models through its own service, OpenCode Zen, and through
other model services.  Portal offers:

- ``zen_free``: OpenCode Zen's free models; they need no account or key.
- one route per service in ``SERVICES`` (today OpenRouter), named after the
  service, with an explicitly bound Model API registration providing the
  key and billing account.
- one route per other ``api_profile`` account: its Model API, which speaks
  Chat Completions, becomes an OpenAI-compatible provider of OpenCode's own,
  named after the route, with the models the registration declares.

The runtime records the models when it is staged (``models.json``,
``OpenCodeRuntime``): Zen's free models under ``models`` and each service's
models that can call tools under the service's OpenCode name.  A host
without the runtime offers no route.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import NamedTuple
from management_common.portal_paths import path as portal_path, resolve_directory
from management_common.site_config import read_site_config

from management_portal.ai_provider_registry import AIProviderRegistry, shared_ai_provider_registry

RUNTIME = Path(os.environ.get(
    "PORTAL_OPENCODE_RUNTIME_ROOT",
    str(resolve_directory(read_site_config().get("deployment", {}).get("providers", {}).get(
        "runtime_root", str(portal_path("staging_root") / "provider-runtimes"))) / "opencode-current"),
))
ROUTE = "zen_free"
# OpenCode Zen, the service of the free route.
ZEN = "opencode"


class Service(NamedTuple):
    """A model service OpenCode reaches with a key from the LLM API Registry."""

    name: str  # OpenCode's name for the service
    label: str
    # Where OpenCode looks for the key; only listing the models at staging uses it.
    key_variable: str


# By the registry entry's provider, which also names the service's route.
SERVICES = {"openrouter": Service("openrouter", "OpenRouter", "OPENROUTER_API_KEY")}
# OpenCode's name for a custom route's provider is this prefix and the route.
CUSTOM_PREFIX = "portal-"
# The output budget of a declared model that names only its context window.
CUSTOM_OUTPUT_TOKENS = 32768


def binary() -> Path:
    return RUNTIME / ("opencode.cmd" if sys.platform == "win32" else "bin/opencode")


def config_home() -> Path:
    """OpenCode's configuration directory in Portal's sessions (``XDG_CONFIG_HOME``).

    It belongs to the runtime, so the host user's own OpenCode rules, agents
    and plugins stay out of Portal's sessions.
    """

    return RUNTIME / "config"


def _staged() -> dict:
    try:
        return json.loads((RUNTIME / "models.json").read_text())
    except (OSError, ValueError):
        return {}


def version() -> str:
    return str(_staged().get("version") or "")


def _registry_entry(route: str) -> dict:
    """The LLM API Registry entry whose key a service route uses."""

    if route not in SERVICES:
        raise ValueError(f"Unsupported OpenCode route: {route}")
    from management_portal import model_api_store
    api_id = model_api_store.route_api_id("opencode", route)
    if api_id:
        row = shared_ai_provider_registry.get(api_id)
        if row.get("provider") == route and row.get("kind") == "openai_compatible_chat":
            return row
    raise ValueError(f"OpenCode route {route} has no enabled, explicitly bound API")


def _custom_entry(route: str) -> dict | None:
    """The Model API registration of a custom route; ``None`` for the free and service routes."""

    if route in {"", ROUTE} or route in SERVICES:
        return None
    from management_portal.execution.account_registrations import registrations
    row = next((row for row in registrations("opencode")
                if row["kind"] == "api_profile" and row["route_id"] == route), None)
    if row is None:
        raise ValueError(f"Unsupported OpenCode route: {route}")
    entry = shared_ai_provider_registry.get(row["config"]["api_id"])
    if "openai_chat" not in AIProviderRegistry.harness_protocols(entry):
        raise ValueError(f"OpenCode route {route} needs a Chat Completions Model API")
    return entry


def service(route: str) -> str:
    """OpenCode's name for the service the route's requests go to."""

    if route in {"", ROUTE}:
        return ZEN
    if _custom_entry(route) is not None:
        return CUSTOM_PREFIX + route
    _registry_entry(route)
    return SERVICES[route].name


def api_key(route: str) -> str:
    """The route's key, from the registry's secret file; the free route has none."""

    if route in {"", ROUTE}:
        return ""
    row = _custom_entry(route) or _registry_entry(route)
    return shared_ai_provider_registry.read_secret(shared_ai_provider_registry.secret_path(row))


def _declared_models(entry: dict) -> dict[str, int]:
    """A custom route's models and their context windows (0 when not declared)."""

    models = AIProviderRegistry.harness_models(entry)
    # The default model comes first; it also answers OpenCode's small requests (titles).
    models.sort(key=lambda model: model != entry.get("default_model"))
    return {model: int(AIProviderRegistry.model_entry(entry, model).get("context_window") or 0) for model in models}


def provider_config(route: str) -> dict:
    """OpenCode's definition of a custom route's provider, without its key; {} for other routes.

    The key reaches OpenCode through its credential store (``PUT /auth``), so
    neither this configuration nor the tools' environment holds it.
    """

    entry = _custom_entry(route)
    if entry is None:
        return {}
    base_url, _auth = AIProviderRegistry.harness_endpoint(entry, "openai_chat")
    options: dict = {"baseURL": base_url}
    headers = {str(name): str(value).strip() for name, value in (entry.get("default_headers") or {}).items()
               if str(value or "").strip()}
    if headers:
        options["headers"] = headers
    models = {model: {"name": model, **({"limit": {"context": window, "output": min(window, CUSTOM_OUTPUT_TOKENS)}}
                                         if window else {})}
              for model, window in _declared_models(entry).items()}
    return {service(route): {"npm": "@ai-sdk/openai-compatible", "name": str(entry.get("label") or entry["id"]),
                             "options": options, "models": models}}


def catalog(route: str = ROUTE) -> dict:
    entry = _custom_entry(route)
    if entry is not None:
        # A custom provider is not in the staged listing; its registration declares the models.
        windows = _declared_models(entry)
        if not windows:
            raise ValueError(f"OpenCode route {route} has no declared model")
        order = list(windows)
        default = str(entry.get("default_model") or "") if entry.get("default_model") in windows else order[0]
        return _catalog(route, str(entry.get("label") or entry["id"]), entry["id"], "model_api", order, default,
                        {model: [] for model in order}, windows)
    staged = _staged()
    if route in {"", ROUTE}:
        route, name, label, rows, preferred = ROUTE, ZEN, "OpenCode Zen (free)", staged.get("models") or [], []
        # No balance pays for the free models, so there is none to read.
        billing = ""
    else:
        row = _registry_entry(route)
        name, label, billing = SERVICES[route].name, SERVICES[route].label, row["id"]
        rows = staged.get(name) or []
        # The models the registry enables come first; its default stays the default.
        preferred = [row.get("default_model"), *AIProviderRegistry.models(row)]
    models = {item["id"]: item for item in rows if item.get("id")}
    if not models:
        raise ValueError(f"OpenCode runtime has no models for {route}; stage the shared OpenCode runtime")
    order = list(dict.fromkeys([model for model in preferred if model in models] + sorted(models)))
    default = "big-pickle" if name == ZEN and "big-pickle" in models else order[0]
    return _catalog(route, label, billing, name, order, default,
                    {model: list(models[model].get("variants") or []) for model in order},
                    {model: int(models[model].get("context") or 0) for model in order})


def _catalog(route: str, label: str, billing: str, upstream: str, order: list[str], default: str,
             efforts: dict[str, list[str]], windows: dict[str, int]) -> dict:
    return {"schema": "tfi.agent-runtime-catalog.v1", "backend": "opencode", "provider": "opencode",
            "label": label, "backend_route_id": route, "provider_profile": billing, "upstream_provider": upstream,
            "models": order, "default_model": default, "efforts_by_model": efforts, "context_windows": windows,
            "runtime_settings": ["model", "reasoning_effort"], "settings_apply": "next_turn",
            "service_tiers_by_model": {model: [] for model in order},
            "permission_policy": {"permission_preset_editable": False},
            "capabilities": {"turn": True, "release": True, "fork": False, "compact": True,
                             "active_turn_input": True, "media": False, "interactive_approvals": False}}


def routes() -> list[dict]:
    from management_portal.execution.account_registrations import registrations
    custom = [row["route_id"] for row in registrations("opencode")
              if row["kind"] == "api_profile" and row["enabled"] and row["route_id"] not in {ROUTE, *SERVICES}]
    offered = []
    for route in [ROUTE, *SERVICES, *custom]:
        try:
            offered.append(catalog(route))
        except ValueError:
            continue
    return offered
