from __future__ import annotations

from typing import Any


def collect_resident_usage(*, actor: str) -> dict[str, Any]:
    from .resident_usage_projection import collect

    return collect(actor=str(actor or ""))


def collect_active_agents(*, actor: str = "") -> dict[str, Any]:
    from .active_agent_projection import collect

    return collect(actor=str(actor or ""))


def read_context(
    address: str,
    *,
    actor: str,
    relative_to_address: str = "",
    max_messages: int = 160,
    max_chars: int = 120_000,
) -> dict[str, Any]:
    from .conversation_projection import read_context as project_context

    return project_context(
        address,
        actor=actor,
        relative_to_address=relative_to_address,
        max_messages=max_messages,
        max_chars=max_chars,
    )
