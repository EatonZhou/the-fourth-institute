"""A Provider Portal does not use, declared the way a real one would be.

``test_provider_extension`` adds it to the Provider catalog before anything
else is imported, as a line in ``provider_catalog.AGENT_PROVIDERS`` would, and
checks that every generic part of Portal takes it from its declaration.  It
stands for an agent such as Qwen Code or a Groq-backed harness:
its own route catalog, an Adapter on the native agent socket and the
capabilities it honours.
"""
from __future__ import annotations

from typing import Any

PROVIDER = "qwencode"
ROUTE = "qwencode_main"
MODELS = ("qwen3-coder-plus", "kimi-k2-groq")


def routes() -> list[dict[str, Any]]:
    return [catalog(ROUTE)]


def catalog(route: str = "") -> dict[str, Any]:
    if route not in {"", ROUTE}:
        raise ValueError(f"unknown Qwen Code route: {route}")
    return {
        "backend_route_id": ROUTE, "label": "Qwen Code", "models": list(MODELS), "default_model": MODELS[0],
        "efforts_by_model": {MODELS[0]: ["low", "high"], MODELS[1]: []},
        "capabilities": {"media": False},
    }


def register() -> None:
    """Declare the Provider, as its line in the Provider catalog would."""

    from management_portal.execution import harness_capabilities as capabilities
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS, AgentProvider

    AGENT_PROVIDERS[PROVIDER] = AgentProvider(
        PROVIDER, "Qwen Code", frozenset({"turn", "compact", "release"}), ("model", "reasoning_effort"),
        catalog_module=__name__, default_route=ROUTE,
        adapter="tests.cases.hypothetical_provider_backend.QwenCodeBackend", resume_without_session=True,
        active_turn_input=True,
        harness=frozenset({capabilities.PORTAL_TOOLS, capabilities.NATIVE_TOOL_EVENTS, capabilities.ERROR_CONTINUATION,
                           capabilities.SUBAGENTS}),
        subagents_by_default=True, upstream="dashscope",
        system_instructions_place="Qwen Code 的 agent 指令",
        credential_globs=(".qwen*",),
    )
