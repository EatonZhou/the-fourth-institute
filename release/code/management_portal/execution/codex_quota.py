from __future__ import annotations

import math
from typing import Any

from .account_quota import quota_state


def _codex_limits(payload: dict[str, Any]) -> Any:
    buckets = payload.get("rateLimitsByLimitId") or {}
    limits = buckets.get("codex") if isinstance(buckets, dict) else None
    if not isinstance(limits, dict):
        limits = payload.get("rateLimits") or {}
    if isinstance(limits, dict) and limits.get("limitId") not in (None, "", "codex"):
        limits = {}
    return limits


def credits(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The account's Codex credits, which pay for work after the plan quota runs out."""
    limits = _codex_limits(payload)
    value = limits.get("credits") if isinstance(limits, dict) else None
    if not isinstance(value, dict):
        return None
    try:
        balance = float(value.get("balance"))
    except (TypeError, ValueError):
        balance = None
    return {"balance": balance if balance is not None and math.isfinite(balance) else None,
            "has_credits": value.get("hasCredits") is True, "unlimited": value.get("unlimited") is True}


def quota_sample(payload: dict[str, Any], *, observed_at: float) -> dict[str, Any]:
    limits = _codex_limits(payload)
    windows = []
    invalid_window = False
    if isinstance(limits, dict):
        for name in ("primary", "secondary"):
            window = limits.get(name)
            if window is None:
                continue
            if not isinstance(window, dict):
                invalid_window = True
                continue
            used = window.get("usedPercent")
            if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or used < 0:
                invalid_window = True
                continue
            windows.append({
                "window": name,
                "remaining_percent": max(0.0, 100.0 - used),
                "resets_at": window.get("resetsAt"),
                "window_duration_mins": window.get("windowDurationMins"),
            })
    remaining = min((window["remaining_percent"] for window in windows), default=None)
    return quota_state({
        "observed_at": observed_at,
        "status": "unknown" if remaining is None or invalid_window else "available",
        "complete": remaining is not None and not invalid_window,
        "remaining_percent": remaining,
        "windows": windows,
        # Codex keeps working on credits after the plan quota runs out; Portal
        # treats that state as used up so automatic work stops spending them.
        "exhausted": payload.get("ordinaryUsageAllowed") is False
        or bool(isinstance(limits, dict) and limits.get("rateLimitReachedType")),
    }, now=observed_at)


