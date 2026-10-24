from unittest import mock

import pytest

from management_portal.claude_account_usage import ClaudeAccountUsage


@pytest.mark.parametrize('platform,filename', [('linux', 'claude'), ('darwin', 'claude'), ('win32', 'claude.exe')])
def test_cli_resolution_uses_active_sdk_without_path_fallback(tmp_path, monkeypatch, platform, filename):
    """Claude 额度查询只用当前 SDK 自带的 CLI

    额度与认证查询使用当前激活的 Claude Agent SDK 自带的 CLI，不回落到 PATH 中的其他 claude 可执行文件。

    :module: execution.providers
    :design: decision_3948ce9b9443
    """
    import sys
    from types import SimpleNamespace
    from management_portal import claude_runtime
    from management_portal.claude_runtime import native_cli_path
    sdk = tmp_path / 'claude_agent_sdk'
    bundled = sdk / '_bundled' / filename
    bundled.parent.mkdir(parents=True)
    bundled.write_text('#!/bin/sh\n')
    bundled.chmod(0o700)
    monkeypatch.setitem(sys.modules, 'claude_agent_sdk', SimpleNamespace(__file__=str(sdk / '__init__.py')))
    monkeypatch.setattr(claude_runtime, 'sys', SimpleNamespace(platform=platform), raising=False)
    monkeypatch.setenv('PATH', '')
    assert native_cli_path() == str(bundled)
    bundled.unlink()
    with pytest.raises(ValueError, match='运行时安装'):
        native_cli_path()


def test_native_query_uses_bundled_cli_and_keeps_zero_cost_validation(monkeypatch):
    """Claude 原生额度查询使用内置 CLI 且保持零成本校验

    原生额度查询通过 SDK 内置的 CLI 发出，仍只做不消耗模型额度的校验请求。

    :module: execution.providers
    :design: decision_3948ce9b9443
    """
    import asyncio
    import json
    from management_portal import claude_account_usage as module
    monkeypatch.setattr(module, 'native_cli_path', lambda: '/sdk/_bundled/claude')
    monkeypatch.setattr(module, 'login_environment', lambda _: {'CLAUDE_CONFIG_DIR': '/account/one'})
    process = mock.Mock(returncode=0)
    response = {'result': 'Current session: 12% used\nCurrent week (all models): 24% used', 'total_cost_usd': 0}
    process.communicate = mock.AsyncMock(side_effect=lambda: (json.dumps(response).encode(), b''))
    spawn = mock.AsyncMock(return_value=process)
    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', spawn)
    profile = {'config_dir': '/account/one'}
    result = asyncio.run(module.read_native_usage(profile))
    assert result['five_hour']['utilization'] == .12
    assert spawn.call_args.args[:3] == ('/sdk/_bundled/claude', '--print', '/usage')
    assert spawn.call_args.kwargs['env']['CLAUDE_CONFIG_DIR'] == '/account/one'
    response['total_cost_usd'] = .01
    with pytest.raises(ValueError, match='本地命令结果'):
        asyncio.run(module.read_native_usage(profile))


def test_claude_windows_keep_account_identity_and_percentages_until_a_read_fails():
    """Claude 额度窗口按账号隔离，读数在 /usage 查询失败前一直有效

    Claude 额度窗口保留各自账号的百分比；读数不因时间过期，只有 /usage 查询失败时才标为过期，fresh_until 只供调度器的额度保护使用。

    :design: decision_4dea0db1f45a
    """
    cache = ClaudeAccountUsage()
    cache.observe("one", "/account/one", {"unifiedWindows": {
        "five_hour": {"utilization": 0, "resetsAt": 1200},
        "seven_day": {"utilization": 1, "resetsAt": 9000},
    }}, now=1000)
    snapshot = cache.snapshot("one", "/account/one")
    assert [w["remaining_percent"] for w in snapshot["rate_limits"]] == [100, 0]
    assert snapshot["quota_status"] == "available"
    assert cache.snapshot("two", "/account/one")["rate_limits"] == []
    assert cache.snapshot("one", "/account/replaced")["rate_limits"] == []
    # Age never expires a reading; the scheduler alone bounds its trust by fresh_until.
    assert not any(w["stale"] for w in snapshot["rate_limits"])
    assert snapshot["rate_limits"][0]["fresh_until"] == 1200
    cache._errors[("one", "/account/one")] = "Claude 用量查询暂不可用"
    assert all(w["stale"] for w in cache.snapshot("one", "/account/one")["rate_limits"])
    assert cache.snapshot("one", "/account/one")["quota_status"] == "stale"
    assert ClaudeAccountUsage().snapshot("one", "/account/one")["rate_limits"] == []


@pytest.mark.parametrize("utilization", [None, True, -1, 2, float("nan"), float("inf"), "0.3"])
def test_claude_unknown_percentage_never_becomes_full_quota(utilization):
    """Claude 用量百分比缺失或无效时显示为不可用，不当作满额度"""
    cache = ClaudeAccountUsage()
    cache.observe("one", "/one", {"status": "allowed", "rateLimitType": "five_hour",
                                   "resetsAt": 2000, "utilization": utilization}, now=1000)
    snapshot = cache.snapshot("one", "/one")
    assert snapshot["quota_status"] == "unavailable"
    assert snapshot["rate_limits"][0]["used_percent"] is None
    assert snapshot["rate_limits"][0]["remaining_percent"] is None
    assert snapshot["rate_limits"][0]["resets_at"] == 2000


def test_claude_partial_updates_do_not_refresh_other_windows_or_keep_old_percent():
    """Claude 单窗口状态事件不沿用该窗口旧的 SDK 百分比，也不刷新其他窗口"""
    cache = ClaudeAccountUsage()
    cache.observe("one", "/one", {"unifiedWindows": {
        "five_hour": {"utilization": .2}, "seven_day": {"utilization": .4},
    }}, now=1000)
    cache.observe("one", "/one", {"rateLimitType": "five_hour", "status": "allowed"}, now=1250)
    windows = cache.snapshot("one", "/one")["rate_limits"]
    assert windows[0]["used_percent"] is None and not windows[0]["stale"]
    assert windows[1]["used_percent"] == 40 and windows[1]["observed_at_unix"] == 1000
    cache.observe("one", "/one", {"rateLimitType": "five_hour", "status": {},
                                   "unifiedWindows": {"five_hour": "malformed"}}, now=1400)
    assert cache.snapshot("one", "/one")["rate_limits"][0]["status"] == "unknown"


def test_native_usage_parser_reads_subscription_rows_and_utc_year_rollover():
    """解析 CLI /usage 输出的订阅窗口行，UTC 重置时间可跨年"""
    from datetime import datetime, timezone
    from management_portal.claude_account_usage import parse_native_usage
    now = datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc).timestamp()
    data = parse_native_usage("""Current session: 0% used · resets Jan 1, 3am (UTC)
Current week (all models): 25.5% used · resets Jan 4, 12:59am (UTC)
Current week (Sonnet only): 100% used · resets Jan 4, 12:59am (UTC)
Last 24h · 150 requests · 42 sessions
  Top MCP servers: portal 90%""", now=now)
    assert data["five_hour"]["utilization"] == 0
    assert data["seven_day"]["utilization"] == .255
    assert data["seven_day_sonnet"]["status"] == "rejected"
    assert data["five_hour"]["resetsAt"] == datetime(2027, 1, 1, 3, tzinfo=timezone.utc).timestamp()
    with pytest.raises(ValueError, match="完整"):
        parse_native_usage("Last 24h · 10 requests · 0% used", now=now)
    data = parse_native_usage("Current session: 0% used · resets Jan 1, 3am (Asia/Hong_Kong)\nCurrent week (all models): 0% used", now=now)
    assert data["five_hour"]["resetsAt"] is None


def test_partial_sdk_event_keeps_native_percentage_without_freshening_it():
    """SDK 单窗口事件保留 CLI /usage 读到的百分比，且不延长其有效期"""
    cache = ClaudeAccountUsage()
    cache.observe("one", "/one", {"unifiedWindows": {"five_hour": {"utilization": .1, "resetsAt": 2000}}}, now=1000, source="claude_cli_usage")
    cache.observe("one", "/one", {"rateLimitType": "five_hour", "status": "allowed", "resetsAt": 2060}, now=1100)
    window = cache.snapshot("one", "/one")["rate_limits"][0]
    assert window["remaining_percent"] == 90 and window["observed_at_unix"] == 1000
    assert window["fresh_until"] == 1300


def test_native_refresh_coalesces_and_retains_observation_on_failure(monkeypatch):
    """Claude 原生用量刷新合并并发请求，查询失败时保留上次读数并标为过期"""
    import asyncio
    from management_portal import claude_account_usage as module
    async def scenario():
        profile = {"id": "one", "config_dir": "/one"}
        cache = ClaudeAccountUsage()
        release = asyncio.Event()
        async def read(_profile):
            await release.wait()
            return {"five_hour": {"utilization": .25}, "seven_day": {"utilization": 0}}
        reader = mock.AsyncMock(side_effect=read)
        monkeypatch.setattr(module, "read_native_usage", reader)
        cache.request_refresh(profile)
        cache.request_refresh(profile, force=True)
        assert cache.snapshot("one", "/one")["quota_refreshing"]
        release.set()
        await cache._refreshes[("one", "/one")]
        assert reader.await_count == 1
        assert cache.snapshot("one", "/one")["quota_status"] == "available"
        cache.request_refresh(profile, force=True)
        assert reader.await_count == 1
        cache._attempts.clear()
        reader.side_effect = RuntimeError("private transport details")
        cache.request_refresh(profile)
        await cache._refreshes[("one", "/one")]
        result = cache.snapshot("one", "/one")
        assert result["rate_limits"][0]["remaining_percent"] == 75
        assert all(window["stale"] for window in result["rate_limits"])
        assert result["quota_error"] == "Claude 用量查询暂不可用"
        assert not result["quota_refreshing"]
    asyncio.run(scenario())


def test_native_usage_allows_a_slow_cli_and_names_a_timeout(monkeypatch):
    """Claude 原生用量查询允许 CLI 运行至少 60 秒，超时后结束进程并提示超时"""
    import asyncio
    from management_portal import claude_account_usage as module
    # The CLI's /usage measured 12-20 s on a loaded host; 20 s failed most refreshes.
    assert module.NATIVE_USAGE_TIMEOUT_SECONDS >= 60
    monkeypatch.setattr(module, "NATIVE_USAGE_TIMEOUT_SECONDS", .05)
    monkeypatch.setattr(module, "native_cli_path", lambda: "/sdk/_bundled/claude")
    monkeypatch.setattr(module, "login_environment", lambda _: {})
    process = mock.Mock(returncode=None)
    hung = asyncio.Event()
    async def communicate():
        if process.returncode is None:
            await hung.wait()
        return b"", b""
    process.communicate = mock.AsyncMock(side_effect=communicate)
    process.kill = mock.Mock(side_effect=lambda: (setattr(process, "returncode", -9), hung.set()))
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", mock.AsyncMock(return_value=process))
    async def scenario():
        cache = ClaudeAccountUsage()
        cache.request_refresh({"id": "one", "config_dir": "/one"}, force=True)
        await cache._refreshes[("one", "/one")]
        return cache.snapshot("one", "/one")
    result = asyncio.run(scenario())
    process.kill.assert_called_once()
    assert result["quota_error"] == "Claude 用量查询超时，请稍后刷新"
    assert not result["quota_refreshing"]


def test_home_builds_execution_accounts_inside_summary_refresh(tmp_path, monkeypatch):
    """首页状态摘要刷新时读取执行账户额度，摘要缓存不区分用户"""
    from management_portal import agent_status_backend as status
    monkeypatch.setattr(status, "STATUS_CACHE_PATH", tmp_path / "status_cache.json")
    monkeypatch.setattr(status, "_REFRESHING_KEYS", set())
    monkeypatch.setattr(status, "get_gpu_device_status", lambda **_kwargs: {"status": "ok"})
    monkeypatch.setattr(status, "get_disk_resource_status", lambda **_kwargs: {"status": "ok"})
    client = mock.Mock()
    monkeypatch.setattr(status, "ExecutionService", lambda: client)
    client.accounts.return_value = {"refreshing": False, "accounts": [{"account_id": "claude_one", "rate_limits": [{"remaining_percent": 100}]}]}
    result = status._build_agent_status_summary("resident")
    assert result["accounts"][0]["rate_limits"][0]["remaining_percent"] == 100
    client.accounts.assert_called_once_with(force_refresh=False)

    client.accounts.reset_mock()
    read = status.get_agent_status_summary("resident")
    assert read["accounts"] == result["accounts"]
    assert read["status"] == "ok"
    assert not read["refreshing"]
    client.accounts.assert_called_once_with(force_refresh=False)
    assert not ({"gpu", "disk", "scheduled_triggered", "cache_ttl_seconds"} & read.keys())
    assert status._agent_status_summary_cache_key("alice") == status._agent_status_summary_cache_key("bob")
    client.accounts.return_value["refreshing"] = True
    assert status.get_agent_status_summary("resident")["refreshing"]


def test_home_summary_reads_current_accounts_and_marks_them_stale_only_when_a_read_fails(tmp_path, monkeypatch):
    """Home 账号摘要每次读取当前读数，只在读取失败时标为过期

    每次读取摘要都取 ExecutionService 当前的账号结果，不设时限；读数放多久都不过期；读取失败时保留上次读数并把额度窗口和余额标为过期，下次读取成功后恢复。

    :importance: P1
    :design: decision_4dea0db1f45a
    """
    from management_portal import agent_status_backend as status
    monkeypatch.setattr(status, "STATUS_CACHE_PATH", tmp_path / "status_cache.json")
    monkeypatch.setattr(status, "_REFRESHING_KEYS", set())
    monkeypatch.setattr(status, "_REFRESH_DONE", {})
    client = mock.Mock()
    monkeypatch.setattr(status, "ExecutionService", lambda: client)

    def readings(percent):
        return {"refreshing": False, "accounts": [{"account_id": "codex_one", "rate_limits": [{"id": "primary", "remaining_percent": percent}],
                                                   "balance": {"id": "codex_one", "status": "ok", "display": "1 USD"}}]}

    client.accounts.return_value = readings(40)
    status.get_agent_status_summary("alice")
    # The next read, however soon, answers with what ExecutionService holds now.
    client.accounts.return_value = readings(70)
    summary = status.get_agent_status_summary("alice")
    assert summary["accounts"][0]["rate_limits"][0] == {"id": "primary", "remaining_percent": 70}
    # A day later the reading is still current: age alone never expires it.
    monkeypatch.setattr(status, "_now_ts", lambda: status.time.time() + 86400)
    assert status.get_agent_status_summary("alice")["accounts"][0]["rate_limits"][0] == {"id": "primary", "remaining_percent": 70}
    client.accounts.side_effect = OSError("ExecutionService restarting")
    failed = status.get_agent_status_summary("alice")
    assert failed["accounts_error"] == "账号状态暂不可读取"
    assert failed["accounts"][0]["rate_limits"][0] == {"id": "primary", "remaining_percent": 70, "stale": True}
    assert failed["accounts"][0]["balance"]["stale"] is True
    client.accounts.side_effect = None
    assert "stale" not in status.get_agent_status_summary("alice")["accounts"][0]["rate_limits"][0]
