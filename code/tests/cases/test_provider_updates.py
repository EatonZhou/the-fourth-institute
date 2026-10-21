"""Resident-invoked runtime checks must not replace an active backend.

:module: spec.design-governance
:importance: P2
"""

import json
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

from management_portal.execution import provider_updates as updates


INSTALLED = {
    updates.CodexRuntime: ("0.159.2",), updates.ClaudeRuntime: ("0.2.162",),
    updates.KimiRuntime: ("2.1.1",), updates.DshRuntime: ("0.2.0-rc.2", "0.2.0-rc.2"),
}


def _isolated(monkeypatch, tmp_path):
    # The pass runs over these runtimes, whatever other Providers the catalog declares.
    monkeypatch.setattr(updates, "runtimes", lambda: {
        "codex": updates.CodexRuntime(), "claude": updates.ClaudeRuntime(),
        "kimi": updates.KimiRuntime(), "dsh": updates.DshRuntime(),
    })
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(updates, "RUNTIMES", tmp_path / "runtimes")
    monkeypatch.setattr(updates, "npm_version", lambda package, tag: {
        "@openai/codex": "0.159.2",
        "@moonshot-ai/kimi-code": "2.2.0",
        "@deepseek-ai/dsh": "0.2.0-rc.2",
        "@deepseek-ai/dsh-llm": "0.2.0-rc.2",
    }[package])
    monkeypatch.setattr(updates, "pypi_version", lambda package: "0.2.162")
    for runtime, versions in INSTALLED.items():
        monkeypatch.setattr(runtime, "installed", lambda self, versions=versions: versions)


def _stage_into(monkeypatch, staged, root):
    """Each runtime's ``stage`` records (provider, versions) and returns ``root(provider, versions)``."""
    for name, runtime in updates.runtimes().items():
        monkeypatch.setattr(type(runtime), "stage", lambda self, versions, name=name: (
            staged.append((name, versions)) or root(name, versions)
        ))


def test_every_provider_portal_installs_declares_its_runtime():
    """每个由 Portal 安装运行时的 Provider 声明自己的运行时类

    更新检查遍历 AgentProvider.runtime_updater 声明的运行时，不按 Provider 名称分支。
    """
    # A Provider's runtime is checked and switched by its own declaration, not by its name.
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS

    runtimes = updates.runtimes()
    assert set(runtimes) == {name for name, definition in AGENT_PROVIDERS.items() if definition.runtime_updater}
    assert all(isinstance(runtime, updates.ProviderRuntime) for runtime in runtimes.values())
    assert {name: type(runtimes[name]) for name in ("codex", "claude", "kimi", "dsh")} == {
        "codex": updates.CodexRuntime, "claude": updates.ClaudeRuntime,
        "kimi": updates.KimiRuntime, "dsh": updates.DshRuntime,
    }


def test_unit_installation_does_not_read_running_codex_accounts(monkeypatch):
    """首次部署无登记数据库时仍能安装静态服务，Codex 模板由部署器单独渲染。"""
    from management_portal.execution import account_registrations
    from scripts import portal_service_units
    def unavailable(*args, **kwargs):
        raise OSError("registration database unavailable during installation")
    monkeypatch.setattr(account_registrations, "registrations", unavailable)
    assert set(portal_service_units.provider_units()) == {
        "portal-claude-code.service", "portal-kimi-code.service"}


def test_codex_runtime_services_follow_registered_running_instances(monkeypatch):
    """更新运行中的登记实例，包括已停用但仍存活的进程，不启动空闲停服账号。"""
    from management_portal.execution import account_registrations, account_hosting
    rows = [{"account_id": "codex_custom", "enabled": True},
            {"account_id": "codex_disabled_live", "enabled": False},
            {"account_id": "codex_stopped", "enabled": False}]
    monkeypatch.setattr(account_registrations, "registrations", lambda provider: rows if provider == "codex" else [])
    monkeypatch.setattr(account_hosting, "codex_instance_state", lambda aid: {
        "unit": f"portal-codex-app-server@{aid}.service",
        "active": "inactive" if aid == "codex_stopped" else "active"})
    runtime = updates.CodexRuntime()
    assert runtime.services == ("portal-codex-app-server@codex_custom.service",
                                "portal-codex-app-server@codex_disabled_live.service")
    rows.clear()
    assert runtime.services == ()


def test_activation_rollback_keeps_original_dynamic_service_snapshot(monkeypatch, tmp_path):
    """首次重启失败后单元不再active，回退仍重启同一组实例。"""
    old, new, link = tmp_path / "old", tmp_path / "new", tmp_path / "current"
    old.mkdir(); new.mkdir(); link.symlink_to(old)
    active, calls = [True], []
    class DynamicRuntime(updates.ProviderRuntime):
        @property
        def services(self):
            return ("registered.service",) if active[0] else ()
        def link(self):
            return link
    monkeypatch.setattr(updates, "_active_executions", lambda provider: 0)
    def command(*args, **kwargs):
        calls.append(args)
        if active[0]:
            active[0] = False
            raise RuntimeError("new runtime did not start")
        return ""
    monkeypatch.setattr(updates, "_service_control", lambda: SimpleNamespace(
        restart=lambda service: command("systemctl", "--user", "restart", service),
        status=lambda service: {"active": "active"}))
    with pytest.raises(RuntimeError, match="did not start"):
        updates._activate("codex", DynamicRuntime(), new)
    assert link.resolve() == old
    assert calls == [("systemctl", "--user", "restart", "registered.service")] * 2


def test_management_version_refresh_only_observes_and_preserves_pending(monkeypatch, tmp_path):
    """管理页只查版本并保存观察，保留待切换版本且不安装、不激活

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    from unittest.mock import Mock
    _isolated(monkeypatch, tmp_path)
    updates.STATE_ROOT.mkdir()
    path = updates.STATE_ROOT / "provider-updates.json"
    path.write_text(json.dumps({"pending": {"codex": "staged-runtime"}}))
    stage = Mock(side_effect=AssertionError("must not install"))
    activate = Mock(side_effect=AssertionError("must not activate"))
    for runtime in INSTALLED:
        monkeypatch.setattr(runtime, "stage", stage)
    monkeypatch.setattr(updates, "_activate", activate)
    checked = updates.provider_runtime_status(refresh=True)
    assert checked["kimi"]["available_version"] == "2.2.0"
    assert checked["codex"]["pending_switch"] is True
    assert checked["codex"]["checked_at"].endswith("+00:00")
    assert json.loads(path.read_text())["pending"] == {"codex": "staged-runtime"}
    monkeypatch.setattr(updates, "npm_version", Mock(side_effect=AssertionError("cached reads must not query upstream")))
    assert updates.provider_runtime_status()["kimi"]["available_version"] == "2.2.0"
    stage.assert_not_called()
    activate.assert_not_called()


def test_management_version_check_failure_is_isolated_and_safe(monkeypatch, tmp_path):
    """单个 Provider 版本查询失败不影响其他 Provider，错误不泄露上游细节

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(updates.CodexRuntime, "available", lambda _: (_ for _ in ()).throw(OSError("private upstream detail")))
    checked = updates.provider_runtime_status(refresh=True)
    assert checked["codex"]["check_error"] == "OSError"
    assert checked["codex"]["available_version"] == ""
    assert checked["kimi"]["available_version"] == "2.2.0"
    assert "private upstream detail" not in (updates.STATE_ROOT / "provider-updates.json").read_text()


def test_list_providers_forwards_refresh_and_publishes_runtime_observations(monkeypatch):
    """ListProviders 的 refresh 参数交给运行时 owner，返回实际版本观察

    :design: decision_7956d7cb4bde
    :importance: P1
    """
    from unittest.mock import Mock
    from management_portal.execution import isa, account_registrations
    from management_portal.hatg.catalog import declaration
    declaration("ListProviders").validate_arguments({"refresh": True})
    observation = {"installed_version": "1.0", "available_version": "2.0", "pending_switch": False, "checked_at": "2026-10-19T00:00:00Z"}
    read = Mock(return_value={"codex": observation})
    monkeypatch.setattr(updates, "provider_runtime_status", read)
    monkeypatch.setattr(account_registrations, "registrations", lambda: [])
    inv = SimpleNamespace(argument=lambda name: True if name == "refresh" else None)
    result = isa.list_providers(inv)
    read.assert_called_once_with(refresh=True)
    assert next(row for row in result["providers"] if row["provider"] == "codex")["runtime"] == observation


def test_explicit_check_stages_once_and_waits_for_idle(monkeypatch, tmp_path):
    """手动检查只准备一次新运行时，等 Provider 空闲后再激活"""
    _isolated(monkeypatch, tmp_path)
    staged = []
    attempts = []
    idle = False

    def activate(provider, runtime, root):
        attempts.append((provider, root))
        return idle

    _stage_into(monkeypatch, staged, lambda name, versions: tmp_path / f"{name}-{versions[0]}")
    monkeypatch.setattr(updates, "_activate", activate)
    first = updates.run_update_pass()
    state = json.loads((tmp_path / "state/provider-updates.json").read_text())
    assert staged == [("kimi", ("2.2.0",))]
    assert state["pending"]["kimi"].endswith("kimi-2.2.0")
    assert first["pending"] == ["kimi"]

    idle = True
    second = updates.activate_pending_once()
    state = json.loads((tmp_path / "state/provider-updates.json").read_text())
    assert staged == [("kimi", ("2.2.0",))]
    assert state["pending"] == {}
    assert second["activated"] == ["kimi"]
    assert len(attempts) == 2


def test_activation_never_switches_while_execution_exists(monkeypatch, tmp_path):
    """Provider 还有执行在运行时，激活不切换运行时"""
    monkeypatch.setattr(updates, "_active_executions", lambda provider: 1)
    monkeypatch.setattr(updates.KimiRuntime, "link", lambda self: tmp_path / "current")
    assert updates._activate("kimi", updates.KimiRuntime(), tmp_path / "staged") is False
    assert not (tmp_path / "current").exists()


def test_check_refreshes_pending_version_before_activation(monkeypatch, tmp_path):
    """更新检查在激活前把待激活运行时刷新到新发布的版本"""
    _isolated(monkeypatch, tmp_path)
    updates.STATE_ROOT.mkdir()
    state_path = updates.STATE_ROOT / "provider-updates.json"
    state_path.write_text(json.dumps({"pending": {"codex": str(tmp_path / "codex-old")}}))
    original = updates.npm_version
    monkeypatch.setattr(updates, "npm_version", lambda package, tag: (
        "0.160.0" if package == "@openai/codex" else original(package, tag)
    ))
    _stage_into(monkeypatch, [], lambda name, versions: tmp_path / f"{name}-{versions[0]}")
    attempts = []
    monkeypatch.setattr(updates, "_activate", lambda provider, runtime, root: attempts.append((provider, root)) or False)
    result = updates.run_update_pass()
    assert result["checked"]["codex"] == {"installed": "0.159.2", "available": "0.160.0"}
    assert json.loads(state_path.read_text())["pending"]["codex"].endswith("codex-0.160.0")
    assert ("codex", tmp_path / "codex-old") not in attempts
    assert result["errors"] == {}


def test_failed_refresh_preserves_pending_runtime(monkeypatch, tmp_path):
    """查询新版本失败时保留原有的待激活运行时并记录错误"""
    _isolated(monkeypatch, tmp_path)
    updates.STATE_ROOT.mkdir()
    state_path = updates.STATE_ROOT / "provider-updates.json"
    old = str(tmp_path / "codex-old")
    state_path.write_text(json.dumps({"pending": {"codex": old}}))
    original = updates.npm_version

    def version(package, tag):
        if package == "@openai/codex":
            raise RuntimeError("registry unavailable")
        return original(package, tag)

    monkeypatch.setattr(updates, "npm_version", version)
    _stage_into(monkeypatch, [], lambda name, versions: tmp_path / "staged")
    monkeypatch.setattr(updates, "_activate", lambda *args: False)
    result = updates.run_update_pass()
    assert result["errors"]["codex"] == "registry unavailable"
    assert json.loads(state_path.read_text())["pending"]["codex"] == old


def test_activation_watch_loads_database_environment_file(monkeypatch, tmp_path):
    """运行时激活监视单元加载 PostgreSQL 环境文件"""
    path = Path(__file__).resolve().parents[2] / "scripts/update_provider_runtimes.py"
    spec = importlib.util.spec_from_file_location("update_provider_runtimes", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    env_file = tmp_path / "portal-postgres.env"
    monkeypatch.setenv("PORTAL_POSTGRES_ENV_FILE", str(env_file))
    commands = []
    monkeypatch.setattr(script.subprocess, "run", lambda command, **kwargs: (
        commands.append(command) or SimpleNamespace(returncode=1 if command[0] == "systemctl" else 0)
    ))
    script._start_activation_watch()
    assert f"--property=EnvironmentFile={env_file}" in commands[1]
    assert commands[1][-1] == "--activate-pending"


def test_dsh_llm_prerelease_alone_triggers_upgrade(monkeypatch, tmp_path):
    """只有 dsh-llm 发布新预发布版时也触发 dsh 运行时升级"""
    _isolated(monkeypatch, tmp_path)
    original = updates.npm_version
    monkeypatch.setattr(updates, "npm_version", lambda package, tag: (
        "0.2.0-rc.3" if package == "@deepseek-ai/dsh-llm" else original(package, tag)
    ))
    staged = []
    _stage_into(monkeypatch, staged, lambda name, versions: tmp_path / "staged")
    monkeypatch.setattr(updates, "_activate", lambda provider, runtime, root: False)
    updates.run_update_pass()
    assert staged == [("kimi", ("2.2.0",)), ("dsh", ("0.2.0-rc.2", "0.2.0-rc.3"))]


def test_dsh_upgrade_uses_target_llm_cordis_requirement(monkeypatch, tmp_path):
    """DSH 升级遵循目标 LLM 的 Cordis 依赖，保留正在使用的仓库运行时。"""
    monkeypatch.setattr(updates, "RUNTIMES", tmp_path)
    source = updates.ROOT / "management_portal/dsh_runtime/package.json"
    original = source.read_bytes()
    installed = []

    def command(*args, **kwargs):
        if args[:2] == ("npm", "view"):
            assert args[2:] == (
                "@deepseek-ai/dsh-llm@0.2.1-rc.2",
                "peerDependencies.@deepseek-ai/cordis", "--json",
            )
            return json.dumps("~4.0.5")
        if args[:2] == ("npm", "install"):
            root = Path(args[args.index("--prefix") + 1])
            installed.append(json.loads((root / "package.json").read_text())["dependencies"])
        if args[:2] == ("node", "--input-type=module"):
            root = tmp_path / "dsh-runtime-0.2.1-rc.2-0.2.1-rc.2"
            assert (root / "request_timing.mjs").read_bytes() == source.with_name("request_timing.mjs").read_bytes()
        return ""

    monkeypatch.setattr(updates, "run_command", command)
    for _ in range(2):
        root = updates.DshRuntime().stage(("0.2.1-rc.2", "0.2.1-rc.2"))
    assert installed == [{
        "@deepseek-ai/cordis": "~4.0.5",
        "@deepseek-ai/dsh": "0.2.1-rc.2",
        "@deepseek-ai/dsh-llm": "0.2.1-rc.2",
    }]
    assert (root / ".portal-validated").is_file()
    assert source.read_bytes() == original


def test_failed_service_restart_restores_previous_runtime(monkeypatch, tmp_path):
    """新运行时的服务重启失败时切回原运行时"""
    old = tmp_path / "old"
    old.mkdir()
    new = tmp_path / "new"
    new.mkdir()
    link = tmp_path / "current"
    link.symlink_to(old)
    monkeypatch.setattr(updates, "_active_executions", lambda provider: 0)
    monkeypatch.setattr(updates.KimiRuntime, "link", lambda self: link)
    calls = []

    def restart(*command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise RuntimeError("new runtime failed to start")
        return ""

    monkeypatch.setattr(updates, "_service_control", lambda: SimpleNamespace(
        restart=lambda service: restart(service), status=lambda service: {"active": "active"}))
    with pytest.raises(RuntimeError, match="failed to start"):
        updates._activate("kimi", updates.KimiRuntime(), new)
    assert link.resolve() == old
    assert len(calls) == 2


def test_partial_kimi_install_is_repaired_before_activation(monkeypatch, tmp_path):
    """未完成的 kimi 安装在激活前重新安装并校验，校验后不再重复安装"""
    monkeypatch.setattr(updates, "RUNTIMES", tmp_path)
    root = tmp_path / "kimi-code-2.2.0"
    binary = root / "bin/kimi"
    binary.parent.mkdir(parents=True)
    binary.write_text("partial install")
    installs = []

    def command(*args, **kwargs):
        if args[0:2] == ("npm", "install"):
            installs.append(args)
            return ""
        return "2.2.0" if args[-1] == "--version" else "--no-open"

    monkeypatch.setattr(updates, "run_command", command)
    assert updates.KimiRuntime().stage(("2.2.0",)) == root
    assert (root / ".portal-validated").read_text().strip() == "2.2.0"
    updates.KimiRuntime().stage(("2.2.0",))
    assert len(installs) == 1


def test_claude_runtime_installs_a_requirement_the_daemon_gains(monkeypatch, tmp_path):
    """Claude 运行环境补装守护进程新增的依赖

    已校验过的 Claude SDK 运行环境若标记中缺少 requirements-claude-code.txt 的某项依赖（如 PostgreSQL 驱动），再次准备时按完整依赖安装一次并更新标记，之后不再重复安装。

    A runtime validated before the daemon needed PostgreSQL gets its driver on the next stage.

    :module: execution.providers
    """
    root = tmp_path / "claude-agent-sdk-0.2.162"
    (root / "bin").mkdir(parents=True)
    (root / "bin/python").write_text("")
    (root / ".portal-validated").write_text("0.2.162\n")
    installs = []

    def command(*args, **kwargs):
        if args[1:4] == ("-m", "pip", "install"):
            installs.append(args[4:])
        return "/bundled/claude"

    monkeypatch.setattr(updates, "run_command", command)
    requirements = [line.strip() for line in (updates.ROOT / "requirements-claude-code.txt").read_text().splitlines()
                    if line.strip() and not line.startswith("#")]
    assert "psycopg-pool" in requirements
    for _ in range(2):
        assert updates.ClaudeRuntime().stage(("0.2.162",), root=root) == root
    staged = ["claude-agent-sdk==0.2.162", *(line for line in requirements if line != "claude-agent-sdk")]
    assert installs == [tuple(staged)]
    assert (root / ".portal-validated").read_text().splitlines() == staged
