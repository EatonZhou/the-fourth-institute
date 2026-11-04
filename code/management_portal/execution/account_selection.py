"""One account-selection policy for new sessions and quota recovery.

Registration supplies identity; AccountMonitor supplies observations. Selection
never refreshes credentials, spends credits, or changes a session binding.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


def mode(value: object = "auto") -> str:
    if not isinstance(value, str) or value not in {"auto", "fixed"}:
        raise ValueError("account_selection must be auto or fixed")
    return str(value)


def finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def headroom(account: Mapping[str, Any]) -> float | None:
    quota = account.get("quota_reserve") or {}
    if quota.get("fresh") is not True or quota.get("status") != "available":
        return None
    remaining = quota.get("remaining")
    if not finite(remaining):
        return None
    windows = quota.get("windows") or []
    if any(not finite(w.get("remaining_percent")) or w.get("stale") for w in windows):
        return None
    return min([float(remaining), *(float(w["remaining_percent"]) for w in windows)])


def compatible(account: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    if account.get("provider") != source.get("provider") or account.get("kind") != source.get("kind"):
        return False
    # A paid API is an explicit billing boundary, even when two APIs expose the
    # same model. Wrappers of that API are also distinct backends.
    return account.get("kind") == "subscription_login" or (
        account.get("billing_account_id") or (account.get("config") or {}).get("api_id")) == (
        source.get("billing_account_id") or (source.get("config") or {}).get("api_id"))


def candidates(accounts: list[dict], source: Mapping[str, Any], *, model: str = "",
               effort: str = "", current: str = "", excluded=(), reserve: float = 0) -> list[dict]:
    eligible = []
    for account in accounts:
        if not compatible(account, source) or account.get("enabled") is False or account.get("account_id") in excluded:
            continue
        if account.get("kind") == "subscription_login" and account.get("login_state") != "logged_in":
            continue
        remaining = headroom(account)
        if remaining is None or remaining <= reserve:
            continue
        catalog = account.get("selection_catalog") or {}
        if model and (catalog.get("fresh") is not True or model not in catalog.get("models", [])):
            continue
        if effort and effort not in (catalog.get("efforts_by_model") or {}).get(model, []):
            continue
        eligible.append(account)
    def rank(account):
        limit = max(1, account.get("max_concurrency") or 1)
        load = (account.get("active_count", 0) + account.get("queued_count", 0)) / limit
        # Existing sessions remain sticky. New sessions prefer spare capacity,
        # then headroom, with a stable tie-break; the scheduler owns final admission.
        return (account.get("account_id") != current, load >= 1, -headroom(account), load, account["account_id"])
    return sorted(eligible, key=rank)


def observations() -> list[dict]:
    from .service import get_execution_service
    return list(get_execution_service().accounts().get("accounts") or [])


def select(provider: str, *, anchor: str = "", model: str = "", effort: str = "", accounts=None) -> dict:
    from .account_registrations import registrations
    from .accounts import DefaultAccountUnavailable
    rows = registrations(provider)
    source = next((row for row in rows if row["account_id"] == anchor or row["route_id"] == anchor), None) if anchor else None
    if anchor and source is None:
        raise DefaultAccountUnavailable("The configured account scope no longer exists; waiting for configuration")
    if source is None:
        # An unqualified request may use subscriptions; it never opts into API billing.
        source = {"provider": provider, "kind": "subscription_login"}
    registered = {row["account_id"]: row for row in rows if row.get("enabled") is not False}
    samples = [sample for sample in (observations() if accounts is None else accounts)
               if sample.get("account_id") in registered]
    selected = candidates(samples, source, model=model, effort=effort)
    if not selected:
        raise DefaultAccountUnavailable("No compatible account has verified available quota; waiting for account observations or quota reset")
    return next(row for row in rows if row["account_id"] == selected[0]["account_id"])


def priority_candidates(accounts: list[dict], priority: list[str], *, source=None, model="", effort="", excluded=(), reserve=0) -> list[dict]:
    """Order explicitly allowed kinds; each target must advertise a usable model."""
    from .account_migration_types import migration_type
    from .provider_catalog import AGENT_PROVIDERS
    ordered = []
    for kind in priority:
        group = []
        for account in accounts:
            if account.get("provider") not in AGENT_PROVIDERS or migration_type(account)["id"] != kind:
                continue
            same = source is not None and compatible(account, source)
            catalog = account.get("selection_catalog") or {}
            target_model = model if same and model else catalog.get("default_model", "")
            if not target_model or catalog.get("fresh") is not True:
                continue
            if candidates([account], account, model=target_model, effort=effort if same else "",
                          excluded=excluded, reserve=reserve):
                group.append(account)
        if group:
            ordered.extend(candidates(group, group[0], excluded=excluded, reserve=reserve))
    return ordered
