"""Shared quota freshness and Close policy for all execution accounts."""
import math
import time
from typing import Any

from .contracts import BINDING_RE

RESERVE_PERCENT = 2.0
POLL_SECONDS = 15.0
MAX_SAMPLE_AGE_SECONDS = 60.0


def normalize_quota_override(policy: Any) -> dict[str, Any]:
    """The binding-scoped control state, without duplicated audit metadata."""
    if not isinstance(policy, dict) or set(policy) not in (
        {"binding_id", "revision", "enabled"},
        {"binding_id", "revision", "enabled", "credits_scope"},
    ):
        raise ValueError("quota policy requires only binding_id, revision and enabled")
    binding_id = policy["binding_id"]
    if not isinstance(binding_id, str) or not BINDING_RE.fullmatch(binding_id):
        raise ValueError("quota policy requires a valid binding_id")
    if type(policy["revision"]) is not int or policy["revision"] < 1 or type(policy["enabled"]) is not bool:
        raise ValueError("quota policy requires a positive revision and boolean enabled")
    if "credits_scope" in policy:
        return {**policy, "credits_scope": normalize_credits_scope(policy["credits_scope"])}
    return dict(policy)


def normalize_credits_scope(scope):
    if not isinstance(scope, dict):
        raise ValueError("credits_scope must be an account and operation scoped authorization")
    keys = set(scope)
    base = {"account_id", "operation_id"}
    if keys not in (base | {"execution_id"}, base | {"provider_session_ref"}):
        raise ValueError("credits_scope requires exactly one execution or Provider session")
    if any(not isinstance(value, str) or not value.strip() for value in scope.values()):
        raise ValueError("credits_scope identities must be nonempty strings")
    return dict(scope)


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def close_threshold(account, sample, setting=None):
    metric = "percent" if account.quota_policy == "reserve" else "balance"
    setting = setting or {}
    observed_unit = "%" if metric == "percent" else str(sample.get("currency") or "")
    # A reading without a currency keeps the configured threshold: an
    # unavailable balance is unknown, not a disabled (zero) threshold.
    unit = observed_unit or str(setting.get("unit") or "")
    default = RESERVE_PERCENT if metric == "percent" else 0.0
    value = setting.get("value", default) if setting.get("unit", unit) == unit else default
    return {"metric": metric, "unit": unit, "value": value, "supported": bool(observed_unit)}


def validate_threshold(value, expected):
    if not isinstance(value, dict) or not finite_number(value.get("value")):
        raise ValueError("Close threshold must be a finite number")
    if not expected["supported"] or value.get("unit") != expected["unit"]:
        raise ValueError("Close threshold unit is unavailable or does not match this account")
    amount = value["value"]
    if amount < 0 or (expected["metric"] == "percent" and amount > 100):
        raise ValueError("Close threshold must be nonnegative and percentages cannot exceed 100")
    return {**expected, "value": amount}


def supports_credits(account) -> bool:
    return bool(account and account.provider == "codex" and account.kind == "subscription_login")


def credits_available(quota, *, now=None):
    """One authenticated, fresh credit reading for account and Session consent."""
    reading = quota.get("credits") or {}
    observed = reading.get("observed_at")
    now = time.time() if now is None else now
    return bool(
        reading.get("authenticated") is True and reading.get("stale") is not True
        and finite_number(observed) and 0 <= now - observed <= 60
        and reading.get("has_credits") is True
        and (reading.get("unlimited") is True
             or (finite_number(reading.get("balance")) and reading["balance"] > 0))
    )


def quota_state(sample: dict[str, Any], *, now: float, threshold=None,
                allow_credits=False, credits_supported=False) -> dict[str, Any]:
    threshold = threshold or {"metric": "percent", "unit": "%", "value": RESERVE_PERCENT, "supported": True}
    metric = threshold["metric"]
    remaining = sample.get("remaining_percent" if metric == "percent" else "remaining_balance")
    fresh = (now - float(sample.get("observed_at") or 0) <= float(sample.get("max_age_seconds") or MAX_SAMPLE_AGE_SECONDS)
             and (sample.get("valid_until") is None or now < float(sample["valid_until"])))
    status = sample.get("status", "unknown")
    threshold_enabled = threshold["value"] != 0
    # Keep native exhaustion separate from the explicit account billing policy.
    used_up = metric == "percent" and (sample.get("exhausted") is True or (finite_number(remaining) and remaining <= 0))
    if used_up and (fresh or status == "reserved"):
        status = "reserved"
    elif not threshold_enabled:
        # Availability here is Portal admission policy, not a claim of a fresh
        # provider balance. Zero disables the threshold, also for holds it set earlier.
        status = "available"
    elif fresh and finite_number(remaining) and threshold["supported"]:
        status = "reserved" if remaining <= threshold["value"] else "available" if sample.get("complete", status != "unknown") else "unknown"
    elif not fresh and status != "reserved":
        status = "unknown"
    credits_active = bool(credits_supported and allow_credits and not threshold_enabled
                          and used_up and status == "reserved" and credits_available(sample, now=now))
    return {**sample, "status": status, "remaining": remaining, "fresh": fresh, "close_threshold": threshold,
            "allow_credits": bool(allow_credits), "credits_supported": bool(credits_supported),
            "credits_active": credits_active,
            "threshold_enabled": threshold_enabled, "used_up": used_up and status == "reserved",
            "reserve_percent": threshold["value"] if metric == "percent" else None,
            "automatic_paused": not credits_active and (status == "reserved" or (threshold_enabled and metric == "percent" and status != "available"))}
