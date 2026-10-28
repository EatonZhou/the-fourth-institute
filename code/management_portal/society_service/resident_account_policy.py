"""Account facts and eligibility for Resident migrations.

Weekly headroom is distinct from the scheduler's minimum across all windows.
Both failure recovery and Family supervision use the authenticated account list.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from management_portal.execution.provider_catalog import AGENT_PROVIDERS

MIGRATION_RESERVE_PERCENT = 10.0


def _percent(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


def weekly_remaining(account: Mapping[str, Any]) -> float | None:
    quota = account.get("quota_reserve") or {}
    if quota.get("fresh") is not True:
        return None
    weekly = [w for w in quota.get("windows") or []
              if w.get("window") in {"seven_day", "weekly", "week"}
              or w.get("window_duration_mins") == 10080]
    if not weekly or any(not _percent(w.get("remaining_percent")) for w in weekly):
        return None
    return min(float(w["remaining_percent"]) for w in weekly)


def reserve_remaining(account: Mapping[str, Any]) -> float | None:
    """Fresh limiting-window percentage; unknown observations remain unknown."""
    quota = account.get("quota_reserve") or {}
    if quota.get("fresh") is not True:
        return None
    remaining = quota.get("remaining")
    if type(remaining) in (int, float) and math.isfinite(remaining) and remaining <= 0:
        return 0.0
    windows = quota.get("windows") or []
    if not windows or any(not _percent(w.get("remaining_percent")) for w in windows):
        return None
    return min(float(w["remaining_percent"]) for w in windows)


def account_available(account: Mapping[str, Any]) -> bool:
    quota = account.get("quota_reserve") or {}
    remaining = quota.get("remaining")
    return bool(
        account.get("provider") in AGENT_PROVIDERS
        and quota.get("status") == "available" and quota.get("fresh") is True
        and type(remaining) in (int, float) and math.isfinite(remaining) and remaining > 0
    )


def sufficient_reserve(account: Mapping[str, Any]) -> bool:
    if not account_available(account):
        return False
    remaining = reserve_remaining(account)
    if remaining is not None:
        windows = (account.get("quota_reserve") or {}).get("windows") or []
        return remaining > MIGRATION_RESERVE_PERCENT and all(
            _percent(w.get("remaining_percent")) and w["remaining_percent"] > MIGRATION_RESERVE_PERCENT for w in windows)
    # Metered Agent accounts have no weekly quota. An unknown subscription
    # window must not masquerade as an unlimited metered account.
    quota = account.get("quota_reserve") or {}
    return bool(quota.get("currency") and type(quota.get("remaining_balance")) in (int, float)
                and quota["remaining_balance"] > 0 and not quota.get("windows"))


def fallback_account(accounts: list[dict[str, Any]], provider: str, route: str,
                     tried: list[str], *, reserve: bool = False) -> dict[str, Any]:
    eligible = sufficient_reserve if reserve else account_available
    candidates = [a for a in accounts if eligible(a)
                  and (a.get("provider"), str(a.get("backend_route_id") or "")) != (provider, route)
                  and str(a.get("account_id") or "") not in tried]
    candidates.sort(key=lambda a: (a["provider"] != provider, a["provider"] != "codex",
                                   -(reserve_remaining(a) or 0), str(a.get("account_id") or "")))
    return candidates[0] if candidates else {}


def resident_account(record: Mapping[str, Any], accounts: list[dict[str, Any]]) -> dict[str, Any]:
    from . import resident_backend

    provider = resident_backend.execution_provider(record)
    binding = resident_backend.session_record(record)
    # For provider_api the Resident route identifies the API provider, while
    # the Execution account has an empty native route. Keep session pinning
    # and this provider/account distinction in the shared backend owner.
    route = resident_backend.execution_account_route(record, binding)
    if not route:
        try:
            configured = resident_backend.execution_account(record, binding)
        except (ValueError, KeyError):
            # Fresh installations may accept work before a default AI exists.
            # This is an absent account observation, not a recovery failure.
            return {}
        provider = str(configured.get("provider") or provider)
        route = str(configured.get("backend_route_id") or "")
    return next((a for a in accounts if a.get("provider") == provider
                 and str(a.get("backend_route_id") or "") == route), {})


def migration_target_options(account: Mapping[str, Any], source_provider: str) -> dict[str, Any]:
    """Use the target's configured model when crossing Providers, never the old model."""
    provider = str(account["provider"])
    route = str(account.get("backend_route_id") or "")
    options = {"target_provider": provider, "target_account": route, "context_strategy": "compact"}
    if provider != source_provider:
        model, _ = AGENT_PROVIDERS[provider].validate_settings(route, "")
        options.update(model=model, reasoning_effort="")
    return options
