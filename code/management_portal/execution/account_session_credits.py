"""Account admission and Session-scoped interpretation of paid-credit consent."""
from __future__ import annotations

from .account_quota import credits_available


def admit_session(account, quota):
    """Validate a requested paid-credit grant; ordinary work uses Scheduler admission."""
    if account.get("enabled") is False:
        raise ValueError("目标 Backend 账号已停用，不能创建新 Session")
    if not credits_available(quota):
        raise ValueError("目标账号没有新鲜、已认证的可用 credits；请检查登录和余额后重新确认")
    return dict(quota)


def override_allows(policies, binding, account_id, execution_id=""):
    """Scopes were checked against current observations and bindings when loaded."""
    policy = policies.get(binding) or {}
    if policy.get("enabled") is not True:
        return False
    scope = policy.get("credits_scope")
    return not scope or (scope["account_id"] == account_id
                         and ("execution_id" not in scope or scope["execution_id"] == execution_id))
