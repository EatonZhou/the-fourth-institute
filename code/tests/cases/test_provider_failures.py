"""Kimi、Anthropic、DeepSeek 的余额耗尽错误（含 Kimi 的 HTTP 429）归为额度不足而不是速率限制；普通 429 仍是速率限制。"""
from management_portal.execution.provider_failures import (
    failure_code,
    failure_message,
    is_usage_limit_failure,
)


def test_nested_provider_failure_preserves_code_message_and_usage_classification():
    """嵌套在 error 中的 Provider 失败保留错误码与消息，并识别为额度不足"""
    failure = {
        "status": "failed",
        "error": {
            "code": "insufficient_quota",
            "message": "You've hit your usage limit.",
        },
    }

    assert failure_code(failure) == "insufficient_quota"
    assert failure_message(failure) == "You've hit your usage limit."
    assert is_usage_limit_failure(failure) is True


def test_nested_provider_failure_does_not_reclassify_unrelated_errors():
    """嵌套在 error 中的普通连接失败不被归为额度不足"""
    failure = {
        "status": "failed",
        "error": {
            "code": "provider_unavailable",
            "message": "Provider connection failed.",
        },
    }

    assert failure_code(failure) == "provider_unavailable"
    assert failure_message(failure) == "Provider connection failed."
    assert is_usage_limit_failure(failure) is False


def test_a_prepaid_account_out_of_balance_is_a_usage_limit_not_a_rate_limit():
    """预付费账号余额耗尽按额度不足处理

    :module: resident.fork-handoff
    :importance: P1
    """
    kimi = {
        "code": "rate_limit",
        "message": "Kimi Code turn failed before returning a response: 429 Your credit balance is "
                   "running low for account <org-1>, please recharge immediately to avoid service disruption",
    }
    assert is_usage_limit_failure(kimi) is True
    assert is_usage_limit_failure("Your credit balance is too low to access the Anthropic API.") is True
    assert is_usage_limit_failure({"error": {"message": "Insufficient Balance"}}) is True
    assert is_usage_limit_failure({"code": "rate_limit", "message": "429 Too Many Requests"}) is False


def test_a_kimi_turn_that_ran_out_of_balance_tells_the_user_the_account_is_out_of_quota():
    """预付费账号余额耗尽按额度不足处理

    :module: resident.fork-handoff
    :importance: P1
    """
    from management_portal.society_service import resident_record_domain

    raw = ("Kimi Code turn failed before returning a response: 429 Your credit balance is running low "
           "for account <org-1>, please recharge immediately to avoid service disruption")
    runtime_error = resident_record_domain._runtime_error_payload(raw, code="rate_limit")
    assert runtime_error["code"] == "provider_usage_limit"
    assert resident_record_domain._runtime_failure_summary(raw, runtime_error) == "当前模型账号额度不足"
