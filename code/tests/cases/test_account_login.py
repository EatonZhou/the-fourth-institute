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

from management_portal.execution import account_login as login
from management_portal.execution import account_hosting
from tests.cases.instance_test_support import select_instance


def test_antigravity_wrapper_imports_in_filtered_login_environment(tmp_path, monkeypatch):
    import subprocess
    instance_root = tmp_path / 'instance'
    select_instance(monkeypatch, instance_root)
    home = tmp_path / 'account'
    home.mkdir()
    monkeypatch.setattr(login, 'account_home', lambda *args: home)
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'must-not-inherit')
    monkeypatch.setenv('GEMINI_API_KEY', 'must-not-inherit')
    env = login.local_login_environment('antigravity', 'antigravity_one')
    assert env['PORTAL_INSTANCE_ROOT'] == str(instance_root)
    assert 'PORTAL_DATABASE_URL' not in env and 'GEMINI_API_KEY' not in env
    wrapper = Path(login.__file__).resolve().parents[2] / 'scripts/antigravity_account.py'
    result = subprocess.run([sys.executable, '-c',
        "import runpy,sys;runpy.run_path(sys.argv[1],run_name='login_import_probe');print('READY')", str(wrapper)],
        cwd=home, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'READY'


@pytest.mark.parametrize("configured", [True, False])
def test_windows_codex_login_uses_npm_command_in_runtime_directory(tmp_path, monkeypatch, configured):
    from management_portal.execution import provider_updates as updates
    from management_common import site_config
    entry = tmp_path / "codex.cmd"
    entry.write_text("@echo off")
    entry.chmod(0o700)
    monkeypatch.setattr(login, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(updates, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(site_config, "read_site_config", lambda: {
        "deployment": {"providers": {"codex_binary": str(tmp_path)} if configured else {}}})
    monkeypatch.setattr(updates.CodexRuntime, "link", lambda self: tmp_path)
    assert login.login_command("codex") == [str(entry), "login", "--device-auth"]


@pytest.fixture
def isolated_login(tmp_path, tmp_path_factory, monkeypatch):
    # Real Unix sockets must fit sun_path even with the runner's UUID basetemp.
    # Keep account data under the case path; only the instance holding the
    # login sockets needs a short name.
    select_instance(monkeypatch, tmp_path_factory.mktemp("login") / "instance")
    monkeypatch.delenv("PORTAL_PROVIDER_EGRESS_ENV_FILE", raising=False)
    store = login.LoginSessionStore()
    monkeypatch.setattr(login, "login_store", lambda: store)
    from management_portal.execution.account_registrations import create_registration
    for account_id, provider in [("login_claude", "claude"), ("login_codex_reload", "codex"), ("login_codex", "codex"),
                                 *(('login_' + action, 'codex') for action in ('cancel', 'expire', 'reject_status'))]:
        create_registration({"config": {"account_dir": str(tmp_path / "accounts" / account_id)}, "account_id": account_id, "provider": provider, "kind": "subscription_login"}, "test")
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


def test_login_environment_excludes_other_credentials_and_uses_the_saved_proxy(isolated_login, tmp_path, monkeypatch):
    """登录环境隔离其他账号和 API 凭据，只采用实例 secrets 中保存的出口设置

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    from management_common.instance_manifest import load
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-other-account")
    monkeypatch.setenv("CODEX_HOME", "/other/account")
    for key in login.PROXY_KEYS:
        monkeypatch.delenv(key, raising=False)
    proxy = load().directory("secrets") / "network.env"
    proxy.parent.mkdir(parents=True, exist_ok=True)
    proxy.write_text('HTTPS_PROXY="http://127.0.0.1:3128"\nANTHROPIC_API_KEY=synthetic-ignored\n')
    env = login.login_environment("claude", "login_claude")
    assert env["CLAUDE_CONFIG_DIR"] == str(account_hosting.account_home("claude", "login_claude"))
    assert "ANTHROPIC_API_KEY" not in env and "CODEX_HOME" not in env
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:3128"
    proxy.unlink()
    assert "HTTPS_PROXY" not in login.login_environment("codex", "login_codex")


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


def test_login_success_and_observation_commit_together(isolated_login, monkeypatch):
    """成功会话与已登录观察一起可见，观察写入失败不留下虚假成功。"""
    store = isolated_login
    session = store.begin("login_atomic")
    store.observe("login_atomic", "logged_out")
    observe = store._observe

    def fail_observation(*args):
        raise RuntimeError("observation unavailable")

    monkeypatch.setattr(store, "_observe", fail_observation)
    with pytest.raises(RuntimeError, match="observation unavailable"):
        store.transition(session, "succeeded")
    assert store.get("login_atomic")["state"] == "pending"
    assert store.login_state("login_atomic") == "logged_out"

    monkeypatch.setattr(store, "_observe", observe)
    assert store.transition(session, "succeeded")
    assert store.get("login_atomic")["state"] == "succeeded"
    assert store.login_state("login_atomic") == "logged_in"
    store.observe("login_atomic", "login_expired")
    assert not store.transition(session, "succeeded")
    assert store.login_state("login_atomic") == "login_expired"


@pytest.mark.parametrize("provider,authenticated", [("codex", True), ("codex", False), ("claude", True), ("claude", False)])
def test_first_login_state_reads_native_status_from_registered_directory(isolated_login, monkeypatch, tmp_path, provider, authenticated):
    """已登记目录首次读取原生认证状态并缓存

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    registration = {"config": {"account_dir": str(tmp_path / "accounts" / "login_migrated")}, "account_id": "login_migrated", "provider": provider, "kind": "subscription_login"}
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    from management_portal.execution.account_registrations import create_registration
    registration = create_registration({**registration, "config": {"account_dir": str(legacy)}}, "test")
    home = legacy
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
def test_initial_login_probe_preserves_newer_authentication_observation(tmp_path, isolated_login, monkeypatch, probe_fails):
    """首次查询期间出现更新的认证观察时，迟到的结果或查询失败均不覆盖它

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    registration = {"config": {"account_dir": str(tmp_path / "accounts" / "login_probe_race")}, "account_id": "login_probe_race", "provider": "codex", "kind": "subscription_login"}
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


def test_initial_login_probe_failure_is_safe_and_does_not_cache_logged_out(tmp_path, isolated_login, monkeypatch):
    """无法执行原生状态查询时只返回固定错误，不把未知状态缓存为退出登录

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    registration = {"config": {"account_dir": str(tmp_path / "accounts" / "login_probe_failure")}, "account_id": "login_probe_failure", "provider": "claude", "kind": "subscription_login"}
    monkeypatch.setattr(login, "provider_logged_in", Mock(side_effect=OSError("synthetic private diagnostic")))
    with pytest.raises(login.InstructionError) as exc:
        login.get_login_state(registration)
    assert (exc.value.code, exc.value.details["reason"], exc.value.retryable) == (
        "instruction.owner_unavailable", "account.login_unavailable", True)
    assert "synthetic private diagnostic" not in str(exc.value)
    assert isolated_login.login_state(registration["account_id"], default=None) is None
    monkeypatch.setattr(login, "provider_logged_in", lambda *_: True)
    assert login.get_login_state(registration) == "logged_in"


def test_kimi_missing_runtime_keeps_registration_and_onboarding_readable(tmp_path, isolated_login, monkeypatch):
    """A fresh Kimi account must reach preparation before its server exists."""
    from management_portal import permission_system
    from management_portal.kimi_code_client import KimiCodeUnavailable
    from management_portal.execution import isa, account_setup

    registration = {"account_id": "fresh_kimi", "route_id": "fresh_kimi", "provider": "kimi",
                    "kind": "subscription_login", "enabled": True, "config": {"account_dir": str(tmp_path / "accounts" / "fresh_kimi")}}
    monkeypatch.setattr(login, "provider_logged_in", Mock(side_effect=KimiCodeUnavailable("runtime missing")))
    monkeypatch.setattr(permission_system, "allows", lambda *_: False)
    assert isa._registration_public(registration, "alice")["login_state"] == "unavailable"
    monkeypatch.setattr(account_setup, "account", lambda _: registration)
    monkeypatch.setattr(account_setup.provider_updates, "preparation_status", lambda _: {"status": "missing"})
    monkeypatch.setattr(account_setup, "catalog", Mock(side_effect=KimiCodeUnavailable("runtime missing")))
    observation = account_setup.get_setup(SimpleNamespace(argument=lambda _: "fresh_kimi"))["setup"]
    assert observation["runtime_ready"] is False
    assert observation["preparation"] == {"status": "missing"}
    assert observation["login_state"] == "unavailable"
    assert isolated_login.login_state("fresh_kimi", default=None) is None
    monkeypatch.setattr(login, "provider_logged_in", lambda *_: False)
    assert login.get_login_state(registration) == "logged_out"


def test_antigravity_without_runtime_registers_and_remains_manageable(tmp_path, isolated_login, monkeypatch):
    """A missing native program must not report a failed save after committing it."""
    from management_portal import antigravity_runtime, permission_system
    from management_portal.execution import account_registrations, isa, account_setup

    monkeypatch.setattr(antigravity_runtime, "platform_key", lambda: "linux-x86_64")
    monkeypatch.setattr(antigravity_runtime.AntigravityRuntime, "link", lambda _: tmp_path / "missing-runtime")
    monkeypatch.setattr(permission_system, "allows", lambda *_: True)
    arguments = {"account_id": "gemini", "provider": "antigravity", "kind": "subscription_login",
                 "config": {"account_dir": str(tmp_path / "selected-account")}}
    inv = SimpleNamespace(caller="alice", argument=arguments.get)
    row = isa.register_provider_account(inv)["registration"]
    assert row["account_id"] == "gemini" and row["login_state"] == "unavailable"
    assert account_registrations.registration("gemini") is not None
    monkeypatch.setattr(isa, "_accounts_snapshot", lambda _: {"accounts": []})
    assert isa.list_provider_accounts(inv)["accounts"][0]["registration"]["account_id"] == "gemini"
    setup = account_setup.get_setup(inv)["setup"]
    assert setup["login_state"] == "unavailable" and setup["runtime_ready"] is False
    assert isolated_login.login_state("gemini", default=None) is None
    # Deleting the incomplete registration releases its directory, not credentials.
    home = Path(row["home_path"])
    (home / "existing-login").write_text("synthetic-login")
    monkeypatch.setattr(isa, "account_busy_counts", lambda _: {"session_count": 0, "execution_count": 0})
    assert isa.delete_provider_account(inv) == {"account_id": "gemini"}
    assert account_registrations.registration("gemini") is None
    assert (home / "existing-login").read_text() == "synthetic-login"
    arguments["account_id"] = "gemini-zero"
    assert isa.register_provider_account(inv)["registration"]["account_id"] == "gemini-zero"


@pytest.mark.parametrize("transport", [None, "unix", "tcp"])
@pytest.mark.parametrize("provider", ["claude", "antigravity"])
def test_claude_code_submission_is_ephemeral_and_single_session(isolated_login, monkeypatch, capsys, transport, provider):
    """同账号复用活跃登录，另一服务实例提交授权码后由 Provider 确认成功且不回显

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    store = isolated_login
    # Explicit Unix coverage must use a genuinely short path, independent of
    # the harness's long TMPDIR. Default transport deliberately keeps that path
    # to exercise the automatic authenticated fallback during authorization.
    with tempfile.TemporaryDirectory(prefix="tfi-login-", dir="/tmp") as ipc_root:
        return _claude_code_submission(store, monkeypatch, capsys, transport, ipc_root, provider)


def _claude_code_submission(store, monkeypatch, capsys, transport, ipc_root, provider):
    if transport == "unix":
        select_instance(monkeypatch, Path(ipc_root) / "instance")
    elif transport is None:
        select_instance(monkeypatch, Path(ipc_root) / ("用户资料" * 12) / "instance")
    from management_common import local_ipc
    monkeypatch.setattr(login, "LocalListener", lambda path, **kwargs: local_ipc.LocalListener(path, transport=transport, **kwargs))
    monkeypatch.setattr(login, "connect_local", lambda path, **kwargs: local_ipc.connect_local(path, transport=transport, **kwargs))
    url = "https://accounts.google.com/o/oauth2/auth?state=example" if provider == "antigravity" else "https://claude.com/cai/oauth/authorize?state=example"
    script = f"import sys; print({url!r},flush=True); code=input(); sys.exit(0 if code=='synthetic-authorization' else 1)"
    monkeypatch.setattr(login, "login_command", lambda *args, **kwargs: [sys.executable, "-c", script])
    monkeypatch.setattr(login, "provider_logged_in", lambda *_: True)
    directory = Path(ipc_root) / "selected-account"
    directory.mkdir()
    registration = {"account_id": "login_claude", "provider": provider, "kind": "subscription_login", "enabled": True,
                    "config": {"account_dir": str(directory)}}
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
def test_login_terminal_states_stop_process_and_do_not_claim_success(tmp_path, isolated_login, monkeypatch, action):
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
    registration = {"config": {"account_dir": str(tmp_path / "accounts" / account_id)}, "account_id": account_id, "provider": "codex", "kind": "subscription_login"}
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
    from management_portal.codex_app_server_protocol import CodexTurnAuthenticationError
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


@pytest.mark.parametrize("reload_fails", [False, True])
def test_codex_device_login_reloads_running_server_without_restarting(tmp_path, isolated_login, monkeypatch, reload_fails):
    """CLI login refreshes native auth; a backend failure never erases the completed login."""
    account_id = "login_codex_reload"
    monkeypatch.setattr(login, "login_command", lambda *args, **kwargs: [sys.executable, "-c", "pass"])
    monkeypatch.setattr(login, "provider_logged_in", lambda *_: True)
    events = []
    monkeypatch.setattr(login, "start_codex_instance", lambda key: events.append(("start", key)))
    client = Mock()
    def request(method, params, **kwargs):
        events.append((method, params))
        if reload_fails:
            raise OSError("private backend detail")
        # A monitor request started during reload may still observe the old token.
        isolated_login.observe(account_id, "login_expired", observed_at=login.now() - timedelta(seconds=1))
        return {"account": {"type": "chatgpt"}}
    client.request.side_effect = request
    monkeypatch.setattr("management_portal.codex_app_server_client.client_for_socket", lambda _: client)
    manager = login.LoginManager(isolated_login)
    manager.start({"config": {"account_dir": str(tmp_path / "accounts" / account_id)}, "account_id": account_id, "provider": "codex", "kind": "subscription_login", "enabled": True})
    wait_for(lambda: client.request.called)
    wait_for(lambda: isolated_login.login_state(account_id) == "logged_in")
    assert events == [("start", account_id), ("account/read", {"refreshToken": True})]
    assert isolated_login.get(account_id)["state"] == "succeeded"
    assert not isolated_login.get(account_id)["error"]


@pytest.mark.parametrize("provider,state", [("claude", None), ("claude", "pending"), ("claude", "succeeded"), ("codex", "awaiting_user")])
def test_update_login_requires_existing_awaiting_claude_session(tmp_path, isolated_login, monkeypatch, provider, state):
    """提交返回码只更新等待中的 Claude 会话，不隐式打开登录

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    from management_portal.execution import account_registrations
    registration = {"config": {"account_dir": str(tmp_path / "accounts" / "login_update")}, "account_id": "login_update", "provider": provider, "kind": "subscription_login"}
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
