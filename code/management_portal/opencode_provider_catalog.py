"""OpenCode's routes and models, from the shared runtime Portal installed.

OpenCode reaches models through its own service, OpenCode Zen, and through
other model services.  Portal offers:

- ``zen_free``: OpenCode Zen's free models; they need no account or key.
- one route per service in ``SERVICES`` (today OpenRouter), named after the
  service, with an explicitly bound Model API registration providing the
  key and billing account.

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


def service(route: str) -> str:
    """OpenCode's name for the service the route's requests go to."""

    if route in {"", ROUTE}:
        return ZEN
    _registry_entry(route)
    return SERVICES[route].name


def api_key(route: str) -> str:
    """The route's key, from the registry's secret file; the free route has none."""

    if route in {"", ROUTE}:
        return ""
    row = _registry_entry(route)
    return shared_ai_provider_registry.read_secret(shared_ai_provider_registry.secret_path(row))


def catalog(route: str = ROUTE) -> dict:
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
    return {"schema": "tfi.agent-runtime-catalog.v1", "backend": "opencode", "provider": "opencode",
            "label": label, "backend_route_id": route, "provider_profile": billing, "upstream_provider": name,
            "models": order, "default_model": default,
            "efforts_by_model": {model: list(models[model].get("variants") or []) for model in order},
            "context_windows": {model: int(models[model].get("context") or 0) for model in order},
            "runtime_settings": ["model", "reasoning_effort"], "settings_apply": "next_turn",
            "service_tiers_by_model": {model: [] for model in order},
            "permission_policy": {"permission_preset_editable": False},
            "capabilities": {"turn": True, "release": True, "fork": False, "compact": True,
                             "active_turn_input": True, "media": False, "interactive_approvals": False}}


def routes() -> list[dict]:
    offered = []
    for route in [ROUTE, *SERVICES]:
        try:
            offered.append(catalog(route))
        except ValueError:
            continue
    return offered
