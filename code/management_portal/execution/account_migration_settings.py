"""Model/effort compatibility for automatic account migration.

Account selection and migration intents consume the same catalog-only policy.
No account discovery, session changes or model calls happen here.
"""
from __future__ import annotations

import re
from typing import Any, Mapping


_EFFORT_RANK = {"none": 0, "minimal": 1, "low": 2, "medium": 3,
                "high": 4, "xhigh": 5, "max": 5, "ultra": 6}


def _light_model(provider: str, model: str) -> bool:
    # Only the explicitly supported correspondence is inferred. Unknown model
    # families use the destination default, never a guessed intelligence score.
    return bool((provider == "codex" and re.fullmatch(r"gpt-[0-9.]+-luna(?:-.*)?", model))
                or (provider == "claude" and re.fullmatch(r"(?:claude-)?haiku(?:-.*)?", model)))


def migration_settings(catalog: Mapping[str, Any], *, source_provider: str,
                       target_provider: str, model: str = "", effort: str = "") -> dict[str, str]:
    """Preserve compatible values; map known tiers, then target defaults.

    Empty effort deliberately delegates to the target adapter's normal default.
    Invalid catalogs are errors, not permission to reuse an unsupported model.
    """
    models = catalog.get("models") or []
    default = str(catalog.get("default_model") or "")
    selected = model if source_provider == target_provider and model in models else ""
    if not selected and _light_model(source_provider, model):
        peers = [m for m in models if _light_model(target_provider, m)]
        selected = default if default in peers else next(iter(peers), "")
    if not selected:
        if not default or default not in models:
            raise ValueError("Migration target catalog has no enabled default model")
        selected = default
    efforts = (catalog.get("efforts_by_model") or {}).get(selected) or []
    chosen = effort if effort in efforts else ""
    if not chosen and effort in _EFFORT_RANK:
        compatible = [e for e in efforts if e in _EFFORT_RANK
                      and _EFFORT_RANK[e] <= _EFFORT_RANK[effort]]
        chosen = max(compatible, key=_EFFORT_RANK.get, default="")
    if not chosen:
        target_default = (catalog.get("default_efforts_by_model") or {}).get(selected, "")
        if not target_default and selected == default:
            target_default = catalog.get("default_reasoning_effort", "")
        chosen = target_default if target_default in efforts else ""
    return {"model": selected, "reasoning_effort": chosen}
