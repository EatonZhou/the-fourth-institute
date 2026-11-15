from __future__ import annotations

import math
from typing import Any

from .account_quota import quota_state
from .quota_reset_time import reset_epoch_seconds


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


def rate_limit_windows(payload, *, observed_at):
    """Preserve every returned pool for display; account admission uses codex only."""
    pools = payload.get('rateLimitsByLimitId')
    pools = dict(pools) if isinstance(pools, dict) else {}
    legacy = payload.get('rateLimits')
    if isinstance(legacy, dict):
        group = legacy.get('limitId') or 'codex'
        pools.setdefault(group, legacy)
    result = []
    for group, limit in pools.items():
        if not isinstance(group, str) or not isinstance(limit, dict):
            continue
        title = limit.get('limitName')
        title = title if isinstance(title, str) and title.strip() else group
        for name in ('primary', 'secondary'):
            window = limit.get(name)
            if not isinstance(window, dict):
                continue  # Not returned is distinct from exhausted or unknown.
            used = window.get('usedPercent')
            valid = type(used) in (int, float) and math.isfinite(used) and used >= 0
            minutes = window.get('windowDurationMins')
            label = '每周' if minutes == 10080 else '5 小时' if minutes == 300 else name
            result.append({'id': name if group == 'codex' else f'{group}/{name}',
                'quota_group': group, 'label': label if group == 'codex' else f'{title} · {label}',
                'remaining_percent': max(0.0, 100.0-used) if valid else None,
                'window_duration_mins': minutes, 'resets_at': reset_epoch_seconds(window.get('resetsAt')),
                'observed_at': observed_at, 'stale': False})
    return result


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
                "resets_at": reset_epoch_seconds(window.get("resetsAt")),
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


