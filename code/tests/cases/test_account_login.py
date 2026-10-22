""":module: execution.providers"""
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import json
import os
import sys
import time
import tempfile
from unittest.mock import Mock

import pytest

from management_common import instance_paths
from management_portal.execution import account_login as login
from management_portal.execution import account_hosting


@pytest.fixture
def isolated_login(tmp_path, tmp_path_factory, monkeypatch):
    monkeypatch.setattr(instance_paths.portal_paths, "CONFIG_PATH", tmp_path / "no-site-config")
    # Real Unix sockets must fit sun_path even with the runner's UUID basetemp.
    # Keep account data under the case path; only the IPC root needs a short name.
    monkeypatch.setenv("PORTAL_INSTANCES_ROOT", str(tmp_path_factory.mktemp("login")))
    monkeypatch.setenv("PORTAL_ACCOUNTS_ROOT", str(tmp_path / "accounts"))
    monkeypatch.setattr(account_hosting, "accounts_root", lambda: tmp_path / "accounts")
    monkeypatch.delenv("PORTAL_PROVIDER_EGRESS_ENV_FILE", raising=False)
    store = login.LoginSessionStore()
    monkeypatch.setattr(login, "login_store", lambda: store)
    return store


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError("Login worker did not reach the expected state")


def test_login_output_only_keeps_authorization_prompt():
    """登录解析支持分块和 ANSI 输出，只保留授权链接及设备码

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    parser = login.LoginOutputParser("codex")
    parser.feed(b"Device login\nhttps://auth.openai.com/device")
    assert parser.url == ""
    assert parser.feed(b"auth\n\x1b[32mABCD-EFGH\x1b[0m\n") == ("ABCD-EFGH", "https://auth.openai.com/deviceauth")
    parser.feed("access_token=ZZZZ-YYYY\nhttps://attacker.invalid/deviceauth\n"
                "https://auth.openai.com/oauth/callback?access_token=synthetic\n")
    assert (parser.code, parser.url) == ("ABCD-EFGH", "https://auth.openai.com/deviceauth")
    claude = login.LoginOutputParser("claude")
    assert claude.feed("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=example\n")[1].startswith("https://claude.com/")


def test_login_environment_excludes_other_credentials_and_requires_configured_proxy(isolated_login, tmp_path, monkeypatch):
    """登录环境隔离其他账号和 API 凭据，配置的出口丢失时停止登录

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-other-account")
    monkeypatch.setenv("CODEX_HOME", "/other/account")
    proxy = tmp_path / "egress.env"
    proxy.write_text('HTTPS_PROXY="http://127.0.0.1:3128"\nANTHROPIC_API_KEY=synthetic-ignored\n')
    monkeypatch.setenv("PORTAL_PROVIDER_EGRESS_ENV_FILE", str(proxy))
    env = login.login_environment("claude", "login_claude")
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "accounts/claude/login_claude")
    assert "ANTHROPIC_API_KEY" not in env and "CODEX_HOME" not in env
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:3128"
    proxy.unlink()
    with pytest.raises(FileNotFoundError):
        login.login_environment("codex", "login_codex")


def test_login_store_expiration_and_late_observations(isolated_login, monkeypatch):
    """登录会话十五分钟到期后不接受迟到成功，旧观察不能覆盖重新登录

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    store = isolated_login
    stamp = login.now()
    monkeypatch.setattr(login, "now", lambda: stamp)
    session = store.begin("login_expiry")
    store.transition(session, "awaiting_user", code="TEST-CODE", url="https://auth.openai.com/deviceauth")
    store.observe("login_expiry", "logged_in", observed_at=stamp)
    store.observe("login_expiry", "login_expired", observed_at=stamp - timedelta(seconds=1))
    assert store.login_state("login_expiry") == "logged_in"
    stamp += timedelta(seconds=901)
    expired = store.get("login_expiry")
    assert expired["state"] == "expired" and not expired["verification_code"] and not expired["verification_url"]
    assert not store.transition(session, "succeeded")
    replaced = store.begin("login_expiry")
    assert not store.transition(session, "failed")
    assert store.get("login_expiry")["started_at"] == replaced["started_at"]


@pytest.mark.parametrize("provider,authenticated", [("codex", True), ("codex", False), ("claude", True), ("claude", False)])
def test_first_login_state_reads_native_status_through_migration_link(isolated_login, monkeypatch, tmp_path, provider, authenticated):
    """迁移账号首次读取原生认证状态并缓存，临时目录软链不妨碍只读查询

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    registration = {"account_id": "login_migrated", "provider": provider, "kind": "subscription_login"}
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    home = login.account_home(provider, registration["account_id"])
    home.parent.mkdir(parents=True)
    home.symlink_to(legacy, target_is_directory=True)
    monkeypatch.setattr(login, "login_command", lambda name, *, status: [name, "status"])

    def native_status(command, *, env, **kwargs):
        assert command == [provider, "status"]
        directory = env["CODEX_HOME" if provider == "codex" else "CLAUDE_CONFIG_DIR"]
        assert directory == str(home) and home.resolve() == legacy
        return SimpleNamespace(returncode=0 if authenticated or provider == "claude" else 1,
                               stdout=json.dumps({"loggedIn": authenticated}).encode())

    probe = Mock(side_effect=native_status)
    monkeypatch.setattr(login, "run_process", probe)
    expected = "logged_in" if authenticated else "logged_out"
    assert login.get_login_state(registration) == expected
    assert login.get_login_state(registration) == expected
    assert isolated_login.login_state(registration["account_id"]) == expected
    assert probe.call_count == 1
    assert login.get_login_state({**registration, "kind": "api_profile"}) == "not_applicable"
    assert probe.call_count == 1


@pytest.mark.parametrize("probe_fails", [False, True])
def test_initial_login_probe_preserves_newer_authentication_observation(isolated_login, monkeypatch, probe_fails):
    """首次查询期间出现更新的认证观察时，迟到的结果或查询失败均不覆盖它

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    registration = {"account_id": "login_probe_race", "provider": "codex", "kind": "subscription_login"}
    stamp = login.now()
    monkeypatch.setattr(login, "now", lambda: stamp)

    def probe(*_):
        isolated_login.observe(registration["account_id"], "logged_in", observed_at=stamp + timedelta(seconds=1))
        if probe_fails:
            raise OSError("synthetic private diagnostic")
        return False

    monkeypatch.setattr(login, "provider_logged_in", probe)
    assert login.get_login_state(registration) == "logged_in"
    assert isolated_login.login_state(registration["account_id"]) == "logged_in"


def test_initial_login_probe_failure_is_safe_and_does_not_cache_logged_out(isolated_login, monkeypatch):
    """无法执行原生状态查询时只返回固定错误，不把未知状态缓存为退出登录

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    registration = {"account_id": "login_probe_failure", "provider": "claude", "kind": "subscription_login"}
    monkeypatch.setattr(login, "provider_logged_in", Mock(side_effect=OSError("synthetic private diagnostic")))
    with pytest.raises(login.InstructionError) as exc:
        login.get_login_state(registration)
    assert (exc.value.code, exc.value.details["reason"], exc.value.retryable) == (
        "instruction.owner_unavailable", "account.login_unavailable", True)
    assert "synthetic private diagnostic" not in str(exc.value)
    assert isolated_login.login_state(registration["account_id"], default=None) is None
    monkeypatch.setattr(login, "provider_logged_in", lambda *_: True)
    assert login.get_login_state(registration) == "logged_in"


@pytest.mark.parametrize("transport", [None, "unix", "tcp"])
def test_claude_code_submission_is_ephemeral_and_single_session(isolated_login, monkeypatch, capsys, transport):
    """同账号复用活跃登录，另一服务实例提交授权码后由 Provider 确认成功且不回显

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    store = isolated_login
    # Explicit Unix coverage must use a genuinely short path, independent of
    # the harness's long TMPDIR. Default transport deliberately keeps that path
    # to exercise the automatic authenticated fallback during authorization.
    with tempfile.TemporaryDirectory(prefix="tfi-login-", dir="/tmp") as ipc_root:
        return _claude_code_submission(store, monkeypatch, capsys, transport, ipc_root)


def _claude_code_submission(store, monkeypatch, capsys, transport, ipc_root):
    if transport == "unix":
        monkeypatch.setenv("PORTAL_INSTANCES_ROOT", ipc_root)
    elif transport is None:
        monkeypatch.setenv("PORTAL_INSTANCES_ROOT", str(Path(ipc_root) / ("用户资料" * 12)))
    from management_common import local_ipc
    monkeypatch.setattr(login, "LocalListener", lambda path, **kwargs: local_ipc.LocalListener(path, transport=transport, **kwargs))
    monkeypatch.setattr(login, "connect_local", lambda path, **kwargs: local_ipc.connect_local(path, transport=transport, **kwargs))
    script = "import sys; print('https://claude.com/cai/oauth/authorize?state=example',flush=True); code=input(); sys.exit(0 if code=='synthetic-authorization' else 1)"
    monkeypatch.setattr(login, "login_command", lambda *args, **kwargs: [sys.executable, "-c", script])
    monkeypatch.setattr(login, "provider_logged_in", lambda *_: True)
    registration = {"account_id": "login_claude", "provider": "claude", "kind": "subscription_login", "enabled": True}
    from management_portal.execution import account_registrations
    monkeypatch.setattr(account_registrations, "registration", lambda _: registration)
    second = login.LoginManager(store)
    opened = login.HANDLERS["OpenProviderAccountLogin"](SimpleNamespace(argument={"account_id": "login_claude"}.get))
    started = opened["session"]
    assert started["requires_code_input"] is True
    assert second.start(registration)["started_at"] == started["started_at"]
    wait_for(lambda: store.get("login_claude")["state"] == "awaiting_user")
    submitted = login.HANDLERS["UpdateProviderAccountLogin"](SimpleNamespace(argument={
        "account_id": "login_claude", "authorization_code": "synthetic-authorization"}.get))
    assert submitted["session"]["started_at"] == started["started_at"]
    wait_for(lambda: store.get("login_claude")["state"] == "succeeded")
    assert store.login_state("login_claude") == "logged_in"
    assert "synthetic-authorization" not in json.dumps(store.get("login_claude"))
    login.delete_login_data("login_claude")
    assert store.get("login_claude") is None
    assert not capsys.readouterr().out


@pytest.mark.parametrize("action", ["cancel", "expire", "reject_status"])
def test_login_terminal_states_stop_process_and_do_not_claim_success(isolated_login, monkeypatch, action):
    """取消和过期结束登录子进程，Provider 状态未确认时登录失败

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    store = isolated_login
    account_id = "login_" + action
    script = "import time; print('https://auth.openai.com/deviceauth',flush=True); print('TEST-CODE',flush=True); time.sleep(60)"
    if action == "reject_status":
        script = "print('https://auth.openai.com/deviceauth',flush=True)"
    monkeypatch.setattr(login, "login_command", lambda *args, **kwargs: [sys.executable, "-c", script])
    monkeypatch.setattr(login, "provider_logged_in", lambda *_: False)
    registration = {"account_id": account_id, "provider": "codex", "kind": "subscription_login"}
    manager = login.LoginManager(store)
    manager.start(registration)
    if action != "reject_status":
        wait_for(lambda: store.get(account_id)["state"] == "awaiting_user")
        if action == "cancel":
            manager.cancel(account_id)
        else:
            stamp = login.now() + timedelta(seconds=901)
            monkeypatch.setattr(login, "now", lambda: stamp)
    expected = "expired" if action == "expire" else "failed"
    wait_for(lambda: store.get(account_id)["state"] == expected)
    if action == "reject_status":
        assert store.get(account_id)["error"] == "Provider 未确认登录成功，请重试"
    login.delete_login_data(account_id)
    assert store.get(account_id) is None


def test_monitor_authentication_failure_and_recovery(isolated_login):
    """AccountMonitor 识别 Codex 认证错误和 Claude 401，重新登录或有效读取后恢复

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    from management_portal.execution.account_monitor import AccountMonitor
    from management_portal.codex_app_server_client import CodexTurnAuthenticationError
    for provider, failure in (("codex", CodexTurnAuthenticationError("", "", "private detail")),
                              ("claude", RuntimeError("HTTP 401 unauthorized private detail"))):
        account = SimpleNamespace(account_id="monitor_" + provider, provider=provider, quota_policy="reserve",
                                  billing_account_id="", quota_enabled=False)
        accounts = Mock(adapters={provider: object()})
        accounts.accounts.return_value = (account,)
        accounts.observe.side_effect = failure
        monitor = AccountMonitor(accounts, Mock(), usage_store=Mock(), login_state_store=isolated_login)
        monitor.refresh_account(account)
        assert isolated_login.login_state(account.account_id) == "login_expired"
        accounts.observe.side_effect = None
        accounts.observe.return_value = {"status": "ok"}
        monitor.refresh_account(account)
        assert isolated_login.login_state(account.account_id) == "logged_in"


@pytest.mark.parametrize("provider,state", [("claude", None), ("claude", "pending"), ("claude", "succeeded"), ("codex", "awaiting_user")])
def test_update_login_requires_existing_awaiting_claude_session(isolated_login, monkeypatch, provider, state):
    """提交返回码只更新等待中的 Claude 会话，不隐式打开登录

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    from management_portal.execution import account_registrations
    registration = {"account_id": "login_update", "provider": provider, "kind": "subscription_login"}
    monkeypatch.setattr(account_registrations, "registration", lambda _: registration)
    monkeypatch.setattr(login.LoginManager, "start", Mock(side_effect=AssertionError("Update must not open login")))
    if state:
        session = isolated_login.begin("login_update")
        isolated_login.transition(session, state)
    before = isolated_login.get("login_update")
    with pytest.raises(login.InstructionError) as exc:
        login.HANDLERS["UpdateProviderAccountLogin"](SimpleNamespace(argument={
            "account_id": "login_update", "authorization_code": "synthetic-secret"}.get))
    assert (exc.value.code, exc.value.details["reason"]) == ("instruction.conflict", "account.login_not_awaiting")
    assert isolated_login.get("login_update") == before


def test_login_receipt_and_permissions_do_not_contain_authorization_code():
    """登录授权码不进入 HATG 回执，四条指令区分打开和更新并使用正确权限

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    from pathlib import Path
    from management_portal.hatg.store import references
    from management_portal.hatg.catalog import declaration
    payload = {"account_id": "login_claude", "authorization_code": "synthetic-secret"}
    assert references(payload) == {"account_id": "login_claude"}
    declaration("UpdateProviderAccountLogin").validate_arguments(payload)
    declaration("OpenProviderAccountLogin").validate_arguments({"account_id": "login_claude"})
    with pytest.raises(login.InstructionError):
        declaration("OpenProviderAccountLogin").validate_arguments(payload)
    with pytest.raises(login.InstructionError):
        declaration("UpdateProviderAccountLogin").validate_arguments({"account_id": "login_claude"})
    with pytest.raises(login.InstructionError) as exc:
        declaration("StartProviderAccountLogin")
    assert exc.value.code == "instruction.unknown"
    assert "StartProviderAccountLogin" not in login.HANDLERS
    root = Path(__file__).resolve().parents[2]
    mappings = json.loads((root / "management_portal/permission_system/scheme/builtin/default.json").read_text())["instructions"]
    assert "StartProviderAccountLogin" not in mappings
    assert mappings["OpenProviderAccountLogin"] == mappings["UpdateProviderAccount"]
    assert mappings["UpdateProviderAccountLogin"] == mappings["UpdateProviderAccount"]
    assert mappings["CancelProviderAccountLogin"] == mappings["UpdateProviderAccount"]
    assert mappings["GetProviderAccountLogin"] == mappings["ListProviderAccounts"]
