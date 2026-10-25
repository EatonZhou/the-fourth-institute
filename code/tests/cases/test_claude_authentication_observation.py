"""Authentication observations cross the Claude CLI/cache/adapter boundary.

:module: execution.providers
:design: decision_7956d7cb4bde
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from management_portal import claude_account_usage as usage
from management_portal.execution.account_adapters import ClaudeAccounts
from management_portal.execution.account_monitor import AccountMonitor


@pytest.mark.parametrize("returncode,is_error,detail", [
    (0, False, "Not logged in · Please run /login"),
    (1, True, "Failed to authenticate: OAuth session expired and could not be refreshed"),
])
def test_native_auth_failure_reaches_login_observer_and_recovery(monkeypatch, returncode, is_error, detail):
    """Claude 认证失败跨原生查询与账号观察传播，成功查询恢复登录状态。"""
    profile = {"id": "synthetic", "config_dir": "/synthetic-account"}
    response = {"result": detail + " private-provider-body", "is_error": is_error, "total_cost_usd": 0}
    process = Mock(returncode=returncode)
    process.communicate = AsyncMock(side_effect=lambda: (json.dumps(response).encode(), b""))
    monkeypatch.setattr(usage, "native_cli_path", lambda: "/synthetic-cli")
    monkeypatch.setattr(usage, "login_environment", lambda _: {})
    monkeypatch.setattr(usage.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    cache = usage.ClaudeAccountUsage()
    key = (profile["id"], profile["config_dir"])
    cache.observe(*key, {"unifiedWindows": {"five_hour": {"utilization": .1}, "seven_day": {"utilization": .2}}})
    asyncio.run(cache._refresh(profile, key))
    snapshot = cache.snapshot(*key)
    monkeypatch.setattr("management_portal.claude_code_client.shared_client", lambda: SimpleNamespace(
        account_usage=lambda *args, **kwargs: cache.snapshot(*key)))
    adapter = ClaudeAccounts.__new__(ClaudeAccounts)
    account = SimpleNamespace(account_id="synthetic", route_id="synthetic", provider="claude",
        kind="subscription_login", quota_policy="reserve", billing_account_id="", quota_enabled=False)
    login_store = Mock()
    accounts = SimpleNamespace(accounts=lambda: (account,), adapters={"claude": adapter}, observe=adapter.observe)
    monitor = AccountMonitor(accounts, Mock(), usage_store=Mock(), login_state_store=login_store)
    monitor.refresh_account(account)
    assert login_store.observe.call_args is not None
    assert login_store.observe.call_args.args == ("synthetic", "login_expired")
    assert snapshot["quota_error_code"] == "provider_authentication_failed"
    assert "private-provider-body" not in snapshot["quota_error"]
    assert all(window["stale"] for window in snapshot["rate_limits"])
    assert snapshot["rate_limits"][0]["used_percent"] == 10
    process.returncode = 0
    response.update(is_error=False, result="Current session: 10% used\nCurrent week (all models): 20% used")
    asyncio.run(cache._refresh(profile, key))
    monitor.refresh_account(account)
    assert login_store.observe.call_args.args == ("synthetic", "logged_in")
    assert not cache.snapshot(*key)["quota_error_code"]


@pytest.mark.parametrize("detail,cost", [
    ("Failed to authenticate. API Error: 403 Request not allowed", 0),
    ("Unable to connect to API: connection refused", 0),
    ("Unexpected CLI response", 0),
    ("Please run /login", .1),
])
def test_usage_failure_without_auth_evidence_does_not_expire_login(monkeypatch, detail, cost):
    """网络、访问限制、未知结果和计费模型正文不成为订阅认证观察。"""
    process = Mock(returncode=1)
    process.communicate = AsyncMock(return_value=(json.dumps({"result": detail, "is_error": True,
        "total_cost_usd": cost}).encode(), b""))
    monkeypatch.setattr(usage, "native_cli_path", lambda: "/synthetic-cli")
    monkeypatch.setattr(usage, "login_environment", lambda _: {})
    monkeypatch.setattr(usage.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    cache = usage.ClaudeAccountUsage()
    profile = {"id": "synthetic", "config_dir": "/synthetic-account"}
    asyncio.run(cache._refresh(profile, (profile["id"], profile["config_dir"])))
    snapshot = cache.snapshot(profile["id"], profile["config_dir"])
    assert snapshot["quota_error"]
    assert not snapshot.get("quota_error_code")


@pytest.mark.parametrize("logged_in", [False, True, OSError("status unavailable"),
                                      subprocess.TimeoutExpired("synthetic-cli", 60)])
def test_unclassified_usage_failure_checks_native_login_without_guessing(monkeypatch, logged_in):
    """缺用量原文不证明认证失效；只有原生登录检查明确失败才到期。"""
    snapshot = {"quota_status": "stale", "quota_error": "Claude 未返回完整的订阅用量，请稍后刷新"}
    monkeypatch.setattr("management_portal.claude_code_client.shared_client", lambda: SimpleNamespace(
        account_usage=lambda *args, **kwargs: snapshot))
    from management_portal.execution import account_login
    probe = Mock(side_effect=logged_in) if isinstance(logged_in, Exception) else Mock(return_value=logged_in)
    monkeypatch.setattr(account_login, "provider_logged_in", probe)
    account = SimpleNamespace(account_id="synthetic", route_id="synthetic", quota_policy="reserve")
    sample = ClaudeAccounts.__new__(ClaudeAccounts).observe(account)
    probe.assert_called_once_with("claude", "synthetic")
    if logged_in is False:
        assert sample["error_code"] == "provider_authentication_failed"
        assert "重新登录" in sample["error"]
    else:
        assert not sample["error_code"]
        assert sample["error"] == snapshot["quota_error"]


@pytest.mark.parametrize("returncode,stdout,expected", [
    (0, '{"loggedIn":true}', True),
    (1, '{"loggedIn":false}', False),
    (0, 'invalid-json', None),
    (1, '', None),
    (0, '{}', None),
    (1, '{"loggedIn":true}', None),
])
def test_native_status_failure_is_not_a_logged_out_observation(monkeypatch, returncode, stdout, expected):
    """Claude 原生命令须返回明确的 loggedIn 布尔值；查询错误不伪造成登出。"""
    from management_portal.execution import account_login
    monkeypatch.setattr(account_login, "login_command", lambda *args, **kwargs: ["synthetic-cli"])
    monkeypatch.setattr(account_login, "login_environment", lambda *args: {})
    monkeypatch.setattr(account_login, "run_process", Mock(return_value=SimpleNamespace(returncode=returncode, stdout=stdout)))
    if expected is None:
        with pytest.raises(ValueError):
            account_login.provider_logged_in("claude", "synthetic")
    else:
        assert account_login.provider_logged_in("claude", "synthetic") is expected


def test_authentication_observation_imports_from_standalone_package(tmp_path):
    """独立 Claude 包完整包含认证观察依赖，不借用源码树。"""
    from scripts.claude_app_server_package import build_package
    source = Path(__file__).resolve().parents[2]
    package = tmp_path / "claude-runtime"
    build_package(source, package)
    result = subprocess.run([sys.executable, "-I", "-B", "-c",
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "from management_portal.claude_account_usage import ClaudeAccountUsage;"
        "assert ClaudeAccountUsage().snapshot('synthetic','/synthetic')['quota_error_code']==''",
        str(package)], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_cached_auth_failure_cannot_undo_login_and_native_success_recovers_without_quota(monkeypatch):
    """旧认证失败保原观察时间；重新登录和原生成功不依赖完整额度数据。"""
    from management_portal.execution import account_login
    profile = {"id": "synthetic", "config_dir": "/synthetic-account"}
    key = (profile["id"], profile["config_dir"])
    cache = usage.ClaudeAccountUsage()
    monkeypatch.setattr(usage, "read_native_usage", AsyncMock(side_effect=usage.ClaudeUsageAuthenticationError()))
    asyncio.run(cache._refresh(profile, key))

    class LoginStore:
        state = "logged_in"
        updated = datetime.now(timezone.utc)

        def observe(self, account_id, state, *, observed_at):
            if observed_at >= self.updated:
                self.state, self.updated = state, observed_at

    store = LoginStore()
    successful_login_at = store.updated
    monkeypatch.setattr("management_portal.claude_code_client.shared_client", lambda: SimpleNamespace(
        account_usage=lambda *args, **kwargs: cache.snapshot(*key)))
    monkeypatch.setattr(account_login, "provider_logged_in", Mock(return_value=True))
    adapter = ClaudeAccounts.__new__(ClaudeAccounts)
    account = SimpleNamespace(account_id="synthetic", route_id="synthetic", provider="claude",
        kind="subscription_login", quota_policy="reserve", billing_account_id="", quota_enabled=False)
    accounts = SimpleNamespace(accounts=lambda: (account,), adapters={"claude": adapter}, observe=adapter.observe)
    monitor = AccountMonitor(accounts, Mock(), usage_store=Mock(), login_state_store=store)
    monitor.refresh_account(account)
    assert store.state == "logged_in"
    assert store.updated == successful_login_at

    # A user can also arrive with an expired observation from an earlier failure.
    # A new native True restores auth even if quota still cannot be read.
    store.observe("synthetic", "login_expired", observed_at=datetime.now(timezone.utc))
    monkeypatch.setattr(usage, "read_native_usage", AsyncMock(side_effect=ValueError("Claude 未返回完整的订阅用量，请稍后刷新")))
    asyncio.run(cache._refresh(profile, key))
    monitor.refresh_account(account)
    assert store.state == "logged_in"
