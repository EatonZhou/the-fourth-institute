"""Native data is normalized by adapters; collection and freshness are shared."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.execution.account_adapters import CodexAccounts
from management_portal.execution.accounts import ExecutionAccounts
from management_portal.execution.account_monitor import AccountMonitor
from management_portal.codex_app_server_client import CodexTurnAuthenticationError


def monitor(monkeypatch):
    route = SimpleNamespace(account_id="zero", max_concurrency=10, socket_path="native-test.sock")
    adapter = CodexAccounts(SimpleNamespace(routes=lambda: (route,), get=lambda _: route))
    accounts = ExecutionAccounts((adapter,))
    scheduler = Mock(quota_reserve_enabled=True)
    scheduler.health.return_value = {"capacity_partitions": {}}
    client = Mock()
    monkeypatch.setattr("management_portal.codex_app_server_client.client_for_socket", lambda path: client)
    clock = [1000.0]
    monkeypatch.setattr("management_portal.execution.account_monitor.time.time", lambda: clock[0])
    return AccountMonitor(accounts, scheduler), accounts.get("codex_zero"), client, clock


def test_native_usage_failure_cannot_hide_fresh_quota(monkeypatch):
    """codex 用量读取失败不影响先读到的最新额度"""
    state, account, client, clock = monitor(monkeypatch)
    def request(method, params, **kwargs):
        if method == "account/usage/read":
            raise RuntimeError("unavailable")
        return {"rateLimits": {"primary": {"usedPercent": 20, "windowDurationMins": 300}}}
    client.request.side_effect = request
    state.refresh_account(account)
    assert state.snapshot()["accounts"][0]["rate_limits"][0]["remaining_percent"] == 80
    assert state.usage_snapshot()["accounts"][0]["usage"]["status"] == "unavailable"
    assert client.request.call_args_list[0].args[0] == "account/rateLimits/read"


def test_quota_windows_stay_current_until_a_read_fails(monkeypatch):
    """Codex 额度窗口在读取失败前一直有效

    Codex 额度读数过了一天仍是当前读数，窗口不带有效期字段；额度读取失败后保留百分比并标为过期。

    :module: execution.service
    """
    state, account, client, clock = monitor(monkeypatch)
    client.request.return_value = {"rateLimits": {"primary": {"usedPercent": 20, "windowDurationMins": 10080}}}
    state.refresh_account(account)
    clock[0] += 86400
    window = state.snapshot()["accounts"][0]["rate_limits"][0]
    assert window["remaining_percent"] == 80 and window["stale"] is False and "fresh_until" not in window
    client.request.side_effect = RuntimeError("unavailable")
    state.refresh_account(account)
    window = state.snapshot()["accounts"][0]["rate_limits"][0]
    assert window["remaining_percent"] == 80 and window["stale"] is True


def test_credits_keep_original_sample_time_on_failure_and_refresh_on_recovery(monkeypatch):
    """Credits 的采样时间属于余额读数，读取失败不能换成最近尝试时间。"""
    state, account, client, clock = monitor(monkeypatch)
    client.request.return_value = {"rateLimits": {
        "primary": {"usedPercent": 20, "windowDurationMins": 10080},
        "credits": {"balance": "61988.95", "hasCredits": True},
    }}
    state.refresh_account(account)
    sample = state.snapshot()["accounts"][0]
    assert sample["credits"]["observed_at"] == sample["rate_limits"][0]["observed_at"] == 1000
    assert sample["credits"]["stale"] is False
    clock[0] = 2000
    client.request.side_effect = TimeoutError("unavailable")
    state.refresh_account(account)
    retained = state.snapshot()["accounts"][0]["credits"]
    assert retained["observed_at"] == 1000
    assert retained["balance"] == 61988.95 and retained["stale"] is True
    client.request.side_effect = None
    clock[0] = 3000
    state.refresh_account(account)
    current = state.snapshot()["accounts"][0]["credits"]
    assert current["observed_at"] == 3000 and current["stale"] is False


@pytest.mark.parametrize("with_previous_sample", [True, False])
@pytest.mark.parametrize("failure, code, message", [
    (CodexTurnAuthenticationError("", "", "private provider detail"), "provider_authentication_failed", "登录凭据失效，请重新登录"),
    (TimeoutError("private provider detail"), "account_read_timeout", "额度读取超时，请稍后重试"),
    (RuntimeError("401 private provider detail"), "account_read_unavailable", "账号用量暂不可读取"),
])
def test_quota_read_errors_are_safe_specific_and_clear_after_recovery(monkeypatch, with_previous_sample, failure, code, message):
    """codex 额度读取失败按原因给出不含内部细节的中文提示，恢复后清除

    :module: execution.service"""
    state, account, client, clock = monitor(monkeypatch)
    client.request.return_value = {"rateLimits": {"primary": {"usedPercent": 20, "windowDurationMins": 300}}}
    if with_previous_sample:
        state.refresh_account(account)
    client.request.side_effect = failure
    state.refresh_account(account)
    failed = state.snapshot()["accounts"][0]
    assert failed["status"] == "unavailable"
    assert failed["error_code"] == code
    assert failed["error"] == message
    assert "private provider detail" not in str(failed)
    if with_previous_sample:
        assert failed["rate_limits"][0]["remaining_percent"] == 80
        assert failed["rate_limits"][0]["stale"] is True
    client.request.side_effect = None
    state.refresh_account(account)
    recovered = state.snapshot()["accounts"][0]
    assert recovered["status"] == "ok"
    assert "error_code" not in recovered
    assert "error" not in recovered
    assert recovered["rate_limits"][0]["stale"] is False


def test_usage_cache_has_independent_retry_and_does_not_relabel_old_data(monkeypatch):
    """codex 用量缓存有独立的刷新间隔和失败重试，旧数据保持过期标记"""
    state, account, client, clock = monitor(monkeypatch)
    client.request.return_value = {"dailyUsageBuckets": [{"startDate": "2026-09-21", "tokens": 50}]}
    state.refresh_usage(account)
    clock[0] += 30
    state.refresh_usage(account)
    assert client.request.call_count == 1
    clock[0] += 1800
    client.request.side_effect = RuntimeError("unavailable")
    state.refresh_usage(account)
    usage = state.usage_snapshot()["accounts"][0]["usage"]
    assert usage["status"] == "stale"
    assert usage["daily_buckets"] == [{"date": "2026-09-21", "tokens": 50}]
    clock[0] += 15
    state.refresh_usage(account)
    assert client.request.call_count == 2
    clock[0] += 60
    state.refresh_usage(account)
    assert client.request.call_count == 3


@pytest.mark.parametrize("payload", [
    {}, {"dailyUsageBuckets": [None]}, {"dailyUsageBuckets": [{}]},
    {"dailyUsageBuckets": [{"startDate": "2026-09-21"}]},
    {"dailyUsageBuckets": [{"startDate": "2026-09-21", "tokens": None}]},
])
def test_native_missing_usage_is_not_reported_as_zero(monkeypatch, payload):
    """codex 原生用量缺失或不完整时显示为不可用，不当作 0"""
    state, account, client, clock = monitor(monkeypatch)
    client.request.return_value = payload
    state.refresh_usage(account)
    assert state.usage_snapshot()["accounts"][0]["usage"]["status"] == "unavailable"


def test_restart_and_failed_refresh_restore_last_success_without_advancing_observed_time(monkeypatch):
    """重启或刷新失败后恢复上次成功的 codex 用量，观测时间不前移"""
    state, account, client, clock = monitor(monkeypatch)
    client.request.return_value = {"dailyUsageBuckets": [{"startDate": "2026-09-21", "tokens": 50}]}
    state.refresh_usage(account)
    restarted, account, client, clock = monitor(monkeypatch)
    saved = restarted.usage_snapshot()["accounts"][0]["usage"]
    assert saved["daily_buckets"] == [{"date": "2026-09-21", "tokens": 50}]
    assert saved["status"] == "stale"
    assert saved["observed_at"] == 1000
    clock[0] += 5000
    client.request.side_effect = RuntimeError("unavailable")
    restarted.refresh_usage(account)
    failed = restarted.usage_snapshot()["accounts"][0]["usage"]
    assert failed["daily_buckets"] == saved["daily_buckets"]
    assert failed["observed_at"] == 1000
    assert failed["checked_at"] == 6000
    assert failed["status"] == "stale"
    client.request.side_effect = None
    client.request.return_value = {"dailyUsageBuckets": [{"startDate": "2026-09-22", "tokens": 10}]}
    clock[0] += 61
    restarted.refresh_usage(account)
    recovered = restarted.usage_snapshot()["accounts"][0]["usage"]
    assert recovered["status"] == "ok"
    assert recovered["observed_at"] == 6061
    assert sum(row["tokens"] for row in recovered["daily_buckets"]) == 60


def test_failed_persistence_keeps_previous_usage_in_memory(monkeypatch):
    """codex 用量保存失败时内存中保留上次成功的读数并标为过期"""
    state, account, client, clock = monitor(monkeypatch)
    client.request.return_value = {"dailyUsageBuckets": [{"startDate": "2026-09-21", "tokens": 50}]}
    state.refresh_usage(account)
    clock[0] += 1801
    monkeypatch.setattr(state._usage_store, "save", Mock(side_effect=OSError("database unavailable")))
    monkeypatch.setattr(state._usage_store, "read", Mock(side_effect=OSError("database unavailable")))
    client.request.return_value = {"dailyUsageBuckets": [{"startDate": "2026-09-21", "tokens": 60}]}
    state.refresh_usage(account)
    usage = state.usage_snapshot()["accounts"][0]["usage"]
    assert usage["status"] == "stale"
    assert usage["daily_buckets"][0]["tokens"] == 50
    assert usage["observed_at"] == 1000


def test_a_failed_usage_read_keeps_its_cause_and_the_usage_warning_names_the_account(monkeypatch, capsys):
    """账号原生用量读取失败时保留错误类型，用量警告点名不完整的账号

    :module: execution.service
    """
    # A TAU run saw "Usage refresh failed" with a retained snapshot and could not
    # tell which account had failed or why: codex_zero's read had raised.
    from management_portal import agent_status_backend

    state, account, client, clock = monitor(monkeypatch)
    client.request.return_value = {"dailyUsageBuckets": [{"startDate": "2026-09-21", "tokens": 50}]}
    state.refresh_usage(account)
    clock[0] += 1800
    client.request.side_effect = TimeoutError("read timed out after 30s")
    state.refresh_usage(account)
    usage = state.usage_snapshot()["accounts"][0]["usage"]
    assert usage["status"] == "stale" and usage["error"] == {"code": "TimeoutError", "at": 2800.0}
    assert "execution account usage read failed" in capsys.readouterr().err

    accounts = [{"provider": "codex", "account_id": "codex_zero", "usage": usage},
                {"provider": "codex", "account_id": "codex_one", "usage": {"status": "ok"}}]
    incomplete = agent_status_backend._incomplete_usage_accounts(accounts)
    assert [(item["account_id"], item["status"], item["error_code"]) for item in incomplete] == [("codex_zero", "stale", "TimeoutError")]
    assert agent_status_backend._incomplete_usage_message(incomplete) == (
        "部分账号的原生用量未更新：codex_zero（stale，TimeoutError）；其余数据已刷新")
