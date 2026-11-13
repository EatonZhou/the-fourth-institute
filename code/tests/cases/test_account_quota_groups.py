"""Provider-declared quota pools preserve native percentages and isolation.

:module: execution.providers
:importance: P1
"""
from types import SimpleNamespace
from unittest.mock import Mock

from management_portal.execution.account_adapters import ClaudeAccounts, CodexAccounts
from management_portal.antigravity_accounts import quota_windows


def test_codex_subscription_windows_share_pool_without_changing_readings(monkeypatch):
    from management_portal import codex_app_server_client
    client = Mock()
    client.request.return_value = {"rateLimits": {
        "primary": {"usedPercent": 0, "windowDurationMins": 300},
        "secondary": {"usedPercent": 100, "windowDurationMins": 10080}}}
    monkeypatch.setattr(codex_app_server_client, "client_for_socket", lambda _: client)
    adapter = CodexAccounts.__new__(CodexAccounts)
    adapter.registry = Mock()
    adapter.registry.get.return_value = SimpleNamespace(kind="subscription_login", socket_path="/unused")
    windows = adapter.observe(SimpleNamespace(route_id="test"))["rate_limits"]
    assert [w["remaining_percent"] for w in windows] == [100, 0]
    assert {w["quota_group"] for w in windows} == {"codex"}


def test_claude_model_specific_limits_do_not_block_the_common_pool(monkeypatch):
    from management_portal import claude_code_client
    windows = [{"id": "five_hour", "remaining_percent": 100},
               {"id": "seven_day", "remaining_percent": 0},
               {"id": "seven_day_sonnet", "remaining_percent": 0}]
    client = Mock()
    client.account_usage.return_value = {"quota_status": "available", "rate_limits": windows}
    monkeypatch.setattr(claude_code_client, "shared_client", lambda: client)
    adapter = ClaudeAccounts.__new__(ClaudeAccounts)
    result = adapter.observe(SimpleNamespace(route_id="test", quota_policy="reserve"))["rate_limits"]
    assert [w["quota_group"] for w in result] == ["claude", "claude", "seven_day_sonnet"]
    assert [w["remaining_percent"] for w in result] == [100, 0, 0]
    assert all("quota_group" not in w for w in windows)


def test_claude_fable_pool_neither_closes_the_account_nor_keeps_a_native_label(monkeypatch):
    """An exhausted or unreadable Fable weekly pool leaves the account schedulable; labels follow window ids."""
    from management_portal import claude_code_client
    client = Mock()
    client.account_usage.return_value = {"quota_status": "available", "rate_limits": [
        {"id": "five_hour", "label": "5 小时", "remaining_percent": 80, "observed_at_unix": 1, "fresh_until": 2},
        {"id": "seven_day", "label": "每周", "remaining_percent": 90, "observed_at_unix": 1, "fresh_until": 2},
        {"id": "seven_day_overage_included", "label": "额外每周额度", "remaining_percent": 0}]}
    monkeypatch.setattr(claude_code_client, "shared_client", lambda: client)
    adapter = ClaudeAccounts.__new__(ClaudeAccounts)
    result = adapter.observe(SimpleNamespace(route_id="test", quota_policy="reserve"))
    assert [w["label"] for w in result["rate_limits"]] == ["5 小时", "每周", "Fable 每周"]
    assert result["quota"]["remaining_percent"] == 80
    assert result["quota"]["complete"] is True
    assert [w["window"] for w in result["quota"]["windows"]] == ["five_hour", "seven_day"]
    client.account_usage.return_value["rate_limits"][2]["remaining_percent"] = None
    assert adapter.observe(SimpleNamespace(route_id="test", quota_policy="reserve"))["quota"]["status"] == "available"


def test_antigravity_uses_native_sections_to_keep_model_pools_independent():
    report = ("### Quota Limits\n- **Gemini Models**\n"
              "  - **Five Hour Limit Remaining:** 100% remaining\n"
              "  - **Weekly Limit Remaining:** 0% remaining\n"
              "- **Gemini 4 Argon**\n"
              "  - **Gemini 4 Argon Weekly Limit:** 50% remaining\n"
              "- **Claude and GPT models**\n"
              "  - **Weekly Limit Remaining:** 0% remaining\n")
    windows = quota_windows(report, 123)
    assert [w["quota_group"] for w in windows] == [
        "Gemini Models", "Gemini Models", "Gemini 4 Argon", "Claude and GPT models"]
    assert [w["remaining_percent"] for w in windows] == [100, 0, 50, 0]
