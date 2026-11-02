"""Account admission and Session-scoped interpretation of paid-credit consent."""
from __future__ import annotations

import time
from .account_quota import finite_number


def admit_session(account, quota, *, use_credits=False):
    """The same account policy for explicit creation and migration preparation."""
    if account.get("enabled") is False:
        raise ValueError("目标 Backend 账号已停用，不能创建新 Session")
    if use_credits:
        if not credits_available(quota):
            raise ValueError("目标账号没有新鲜、已认证的可用 credits；请检查登录和余额后重新确认")
        return dict(quota)
    if quota.get("used_up") is True:
        raise ValueError("目标 Backend 账号额度已用完，不能创建新 Session；可由人类明确确认本次使用 credits")
    if quota.get("threshold_enabled") is False:
        return dict(quota)
    if (quota.get("status") not in {"available", "reserved"} or quota.get("fresh") is False
            or not finite_number(quota.get("remaining")) or quota["remaining"] <= 0):
        raise ValueError("目标 Backend 账号没有可用额度，不能创建新 Session")
    return dict(quota)


def credits_available(quota, *, now=None):
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


def normalize_scope(scope):
    if not isinstance(scope, dict):
        raise ValueError("credits_scope must be an account and operation scoped authorization")
    keys = set(scope)
    base = {"account_id", "operation_id"}
    if keys not in (base | {"execution_id"}, base | {"provider_session_ref"}):
        raise ValueError("credits_scope requires exactly one execution or Provider session")
    if any(not isinstance(value, str) or not value.strip() for value in scope.values()):
        raise ValueError("credits_scope identities must be nonempty strings")
    return dict(scope)


def override_allows(policies, binding, account_id, execution_id=""):
    """Scopes were checked against current observations and bindings when loaded."""
    policy = policies.get(binding) or {}
    if policy.get("enabled") is not True:
        return False
    scope = policy.get("credits_scope")
    return not scope or (scope["account_id"] == account_id
                         and ("execution_id" not in scope or scope["execution_id"] == execution_id))
