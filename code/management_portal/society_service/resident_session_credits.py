"""Human consent for one explicit target Session; never a Resident preference."""
from __future__ import annotations

from management_portal.portal_access_control import get_portal_access_store


def human_actor(actor):
    return bool(actor and not actor.startswith("resident_")
                and not get_portal_access_store().is_service_actor(actor))


def validate_consent(body, actor, target):
    consent = body.get("credits_consent")
    if consent is None:
        return False
    return validate_target_consent(consent, actor, target.account, target.provider,
                                   body.get("idempotency_key") if body.get("target_account") else "")


def validate_target_consent(consent, actor, account, provider, request_id):
    if not human_actor(actor):
        raise PermissionError("只有已登录的人类可以确认本次 Session 使用 credits")
    if (not isinstance(consent, dict) or set(consent) != {"account_id", "confirmed"}
            or consent.get("confirmed") is not True):
        raise ValueError("credits_consent 需要明确的 account_id 和 confirmed=true")
    if not request_id or consent["account_id"] != account.get("account_id"):
        raise ValueError("credits 确认必须对应本次操作和明确的目标账号")
    if provider != "codex" or not account.get("quota_protection_enabled"):
        raise ValueError("目标账号不支持本次 credits 授权")
    return True


def account_open_quota(account, *, use_credits=False):
    from .domain_ports import execution_port
    from management_portal.execution.account_session_credits import admit_session
    try:
        health = execution_port.health()
    except Exception as error:
        raise ValueError("无法读取目标 Backend 账号额度，请刷新后重试") from error
    quota = ((health.get("scheduler") or {}).get("capacity_partitions") or {}).get(
        str(account["capacity_key"]), {}).get("quota_reserve") or {}
    return admit_session(account, quota, use_credits=use_credits)


def authorization(restart):
    if not restart.body.get("credits_consent"):
        return {}
    from .resident_domain_common import now_iso
    return {"account_id": restart.target.account["account_id"], "operation_id": restart.restart_id,
            "confirmed_by": restart.actor_id, "confirmed_at": now_iso()}


def prepared_policy(policy, authorization, execution_id):
    return {**policy, "credits_scope": {
        "account_id": authorization["account_id"], "operation_id": authorization["operation_id"],
        "execution_id": execution_id,
    }}


def promote(record, authorization, provider_session_ref):
    from . import resident_backend, resident_record_domain
    if not provider_session_ref:
        raise ValueError("credits 授权缺少已建立的目标 Session")
    previous = resident_record_domain.resident_quota_override(record)
    record["quota_reserve_override"] = {
        "binding_id": resident_backend.execution_binding_id(record), "enabled": True,
        "revision": int(previous.get("revision") or 0) + 1,
        "credits_scope": {"account_id": authorization["account_id"],
                          "operation_id": authorization["operation_id"],
                          "provider_session_ref": provider_session_ref},
    }


def override_enabled(record):
    """Business supervision must not mistake an expired Session consent for OpenIntake."""
    from . import resident_record_domain
    from .domain_ports import execution_port
    policy = resident_record_domain.resident_quota_override(record)
    if not policy.get("enabled"):
        return False
    scope = policy.get("credits_scope")
    if not scope:
        return True
    try:
        state = execution_port.service().binding_quota_states({policy["binding_id"]: scope["account_id"]})
    except (OSError, RuntimeError, ValueError):
        return False
    return state.get(policy["binding_id"], {}).get("override_enabled") is True
