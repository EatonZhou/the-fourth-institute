"""Native subscription quota, isolation from API billing, and failed reads.

:module: execution.providers
:importance: P1
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.execution.kimi_account_usage import quota_sample, observe
from management_portal.execution.account_quota import quota_state


def payload(**windows):
    return {"kind": "ok", "quota": {"usages": windows, "extraUsage": None}}


def test_windows_convert_used_ratios_and_do_not_invent_missing_limits():
    sample = quota_sample(payload(limit5h={"usedRatio": .25, "resetAt": "2030-01-01T01:00:00+01:00"},
        limit7d={"usedRatio": .42}, monthTotal={"usedRatio": .3}, monthCode={"usedRatio": 0}), 1000)
    windows = sample["rate_limits"]
    assert [w["remaining_percent"] for w in windows] == [75, 58, 70, 100]
    assert windows[0]["resets_at"] == 1893456000
    assert windows[1]["resets_at"] is None
    assert sample["quota"]["remaining_percent"] == 58
    assert quota_state(sample["quota"], now=1001)["status"] == "available"
    assert quota_state(sample["quota"], now=1061)["status"] == "unknown"
    optional = quota_sample(payload(limit7d={"usedRatio": .1, "resetAt": "invalid"}), 1000)
    assert [w["id"] for w in optional["rate_limits"]] == ["limit7d"]
    assert optional["rate_limits"][0]["resets_at"] is None


@pytest.mark.parametrize("value", [None, True, "0", float("nan"), float("inf"), -.1])
def test_invalid_usage_is_never_reported_as_free_quota(value):
    with pytest.raises(ValueError):
        quota_sample(payload(limit5h={"usedRatio": value}), 1000)


@pytest.mark.parametrize("data", [None, {}, {"kind": "error", "message": "sensitive upstream response"}, payload()])
def test_empty_and_inband_errors_are_failures(data):
    with pytest.raises(ValueError) as exc:
        quota_sample(data, 1000)
    assert "sensitive" not in str(exc.value)


def test_exhaustion_and_reset_boundaries_use_shared_quota_policy():
    sample = quota_sample(payload(limit5h={"usedRatio": 1.05, "resetAt": "1970-01-01T00:16:50Z"}), 1000)
    assert sample["quota"]["valid_until"] == 1010
    state = quota_state(sample["quota"], now=1001,
        threshold={"metric": "percent", "unit": "%", "value": 0, "supported": True})
    assert state["used_up"] and state["automatic_paused"]


def test_monitor_keeps_last_windows_on_usage_failure_and_recovers(monkeypatch):
    from management_portal import kimi_account_runtime as runtime
    from management_portal.execution.account_adapters import KimiAccounts
    from management_portal.execution.account_monitor import AccountMonitor
    from management_portal.execution.accounts import ExecutionAccount
    account = ExecutionAccount("kimi_sub", "kimi", "sub", "account:kimi_sub",
        kind="subscription_login", quota_policy="reserve")
    data = payload(limit5h={"usedRatio": .25}, limit7d={"usedRatio": .42})
    calls = []
    def request(method, path):
        calls.append((method, path))
        return {"managed_provider": {"status": "authenticated"}} if path == "auth" else data
    client = SimpleNamespace(_request=request)
    def control(route, *, for_control):
        assert route == "sub" and for_control is True
        return client
    monkeypatch.setattr(runtime, "client_for_route", control)
    adapter = KimiAccounts.__new__(KimiAccounts)
    accounts = Mock(adapters={"kimi": adapter})
    accounts.accounts.return_value = (account,)
    accounts.observe.side_effect = adapter.observe
    store, scheduler = Mock(), Mock(quota_reserve_enabled=True)
    monitor = AccountMonitor(accounts, scheduler, usage_store=Mock(), login_state_store=store)
    monitor.billing = Mock()
    monitor.refresh_account(account)
    first = deepcopy(monitor._samples[account.account_id])
    data = {"kind": "error", "status": 503, "message": "secret"}
    monitor.refresh_account(account)
    failed = monitor._samples[account.account_id]
    assert failed["status"] == "unavailable" and "secret" not in repr(failed)
    assert [w["remaining_percent"] for w in failed["rate_limits"]] == [75, 58]
    assert all(w["stale"] for w in failed["rate_limits"])
    assert failed["rate_limits"][0]["observed_at"] == first["rate_limits"][0]["observed_at"]
    assert store.observe.call_args.args == ("kimi_sub", "logged_in")
    assert scheduler.update_account_quota.call_args.args[1] == {"status": "unknown"}
    data = payload(limit5h={"usedRatio": 0}, limit7d={"usedRatio": 0})
    monitor.refresh_account(account)
    recovered = monitor._samples[account.account_id]
    assert recovered["status"] == "ok" and "error" not in recovered
    assert all(w["remaining_percent"] == 100 and not w["stale"] for w in recovered["rate_limits"])
    monitor.billing.observe.assert_not_called()
    assert set(calls) == {("GET", "auth"), ("GET", "oauth/usage")}


def test_failed_quota_does_not_change_auth_and_non_subscriptions_use_api_balances(monkeypatch):
    from management_portal.execution.account_adapters import KimiAccounts
    from management_portal import kimi_account_runtime as runtime
    client = Mock()
    client._request.side_effect = [{"managed_provider": {"status": "authenticated"}}, TimeoutError()]
    assert observe(client)["login_observation"] == "logged_in"
    client._request.reset_mock(side_effect=True)
    client._request.return_value = {"managed_provider": {"status": "expired"}}
    assert observe(client)["login_observation"] == "login_expired"
    client._request.assert_called_once_with("GET", "auth")
    adapter = KimiAccounts.__new__(KimiAccounts)
    adapter.entries = [{"account_id": "api", "route_id": "api", "kind": "api_profile",
        "config": {"api_id": "moonshot_api"}, "max_concurrency": 1}]
    account = adapter.accounts()[0]
    assert account.billing_account_id == "moonshot_api" and account.quota_policy == "none"
    monkeypatch.setattr(runtime, "client_for_route", Mock(side_effect=AssertionError("No OAuth for API accounts")))
    assert adapter.observe(account) == {"status": "ok", "rate_limits": []}
