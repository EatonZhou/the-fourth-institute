"""Provider updates stage, replace and roll back managed runtimes.

:module: spec.design-governance
:importance: P2
"""

import json
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
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
    # Programs and their current links belong to the selected code; redirect
    # that one owner, not only the updater's copy of its directory.
    from tests.cases.provider_service_test_support import provider_instance
    provider_instance(monkeypatch, tmp_path)
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(updates, "npm_version", lambda package, tag: {
        "@openai/codex": "0.159.2",
        "@moonshot-ai/kimi-code": "2.2.0",
        "@deepseek-ai/dsh": "0.2.0-rc.2",
        "@deepseek-ai/dsh-llm": "0.2.0-rc.2",
    }[package])
    monkeypatch.setattr(updates, "pypi_version", lambda package: "0.2.162")
    for runtime, versions in INSTALLED.items():
        monkeypatch.setattr(runtime, "installed", lambda self, versions=versions: versions)
    # These passes update a Codex Portal installed, whatever the host's PATH holds.
    monkeypatch.setattr(updates.CodexRuntime, "link", updates.CodexRuntime.activation_link)


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
    with pytest.raises(RuntimeError, match="服务启用失败"):
        updates._activate_verified("claude", DynamicRuntime(), new)
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


def test_activation_schedules_recovery_and_switches_while_execution_exists(monkeypatch, tmp_path):
    """在途执行先登记恢复，更新不等待长任务结束。"""
    monkeypatch.setattr(updates, "_active_executions", lambda provider: 1)
    calls = []
    monkeypatch.setattr(updates, "_prepare_execution_recovery", lambda provider: calls.append(provider))
    monkeypatch.setattr(updates, "_switch_runtime", lambda *a, **kw: True)
    assert updates._activate_verified("kimi", updates.KimiRuntime(), tmp_path / "staged")
    assert calls == ["kimi"]


def test_codex_runtime_selection_keeps_existing_processes(tmp_path):
    """Codex 仅更新下次启动的入口，不查询或重启当前账号进程。"""
    from types import SimpleNamespace
    from unittest.mock import Mock

    staged = tmp_path / "next"
    staged.mkdir()
    link = tmp_path / "current"
    controller = Mock()
    runtime = SimpleNamespace(
        service_control=lambda: controller,
        activation_link=lambda: link,
        link_target=lambda root: root,
        account_processes=Mock(side_effect=AssertionError("must not read active Codex accounts")),
        restart_account_process=Mock(),
        ensure_ready=Mock(),
    )
    assert updates._switch_runtime("codex", runtime, staged, prepare=True)
    assert link.resolve() == staged.resolve()
    assert controller.mock_calls == []
    runtime.account_processes.assert_not_called()
    runtime.restart_account_process.assert_not_called()
    runtime.ensure_ready.assert_not_called()


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


def test_update_pass_prepares_managed_codex_independently_of_host_path(monkeypatch, tmp_path):
    """主机 PATH 不改变所选代码拥有的 Provider 程序更新。"""
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv('PATH', str(tmp_path / 'system-bin'))
    original = updates.npm_version
    monkeypatch.setattr(updates, "npm_version", lambda package, tag: (
        "0.160.0" if package == "@openai/codex" else original(package, tag)
    ))
    staged, attempts = [], []
    _stage_into(monkeypatch, staged, lambda name, versions: tmp_path / f"{name}-{versions[0]}")
    monkeypatch.setattr(updates, "_activate", lambda provider, runtime, root: attempts.append(provider) or True)
    result = updates.run_update_pass()
    assert result["checked"]["codex"] == {"installed": "0.159.2", "available": "0.160.0"}
    assert "codex" in [name for name, _ in staged] and "codex" in attempts
    assert result["errors"] == {} and "codex" not in result["pending"]


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
    assert result["errors"]["codex"]["phase"] == "download"
    assert "registry unavailable" not in str(result["errors"])
    assert json.loads(state_path.read_text())["pending"]["codex"] == old


def test_activation_watch_uses_manifest_code_and_only_instance_root(monkeypatch, tmp_path):
    from scripts import update_provider_runtimes as script
    from tests.cases.instance_test_support import write_instance
    root = write_instance(tmp_path / 'instance')
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    monkeypatch.setenv('PORTAL_DATABASE_URL', 'foreign-endpoint-must-not-enter-unit')
    from scripts import tfi_shutdown
    commands, verified = [], []
    # The watcher is the instance's own job: an existing one must select this instance.
    monkeypatch.setattr(tfi_shutdown, 'verify_job', lambda unit, selected: verified.append((unit, selected)))
    monkeypatch.setattr(script.subprocess, 'run', lambda command, **kwargs: (
        commands.append(command) or SimpleNamespace(returncode=1 if command[0] == 'systemctl' else 0)))
    script._start_job("--activate-pending")
    assert verified == [('tfi-fixture-provider-activation.service', root)]
    assert commands[0][-1] == 'tfi-fixture-provider-activation.service'
    assert '--unit=tfi-fixture-provider-activation.service' in commands[1]
    assert [arg for arg in commands[1] if arg.startswith('--setenv=')] == ['--setenv=PORTAL_INSTANCE_ROOT=' + str(root)]
    assert not any('EnvironmentFile' in arg or 'foreign-endpoint' in arg for arg in commands[1])
    assert commands[1][-1] == '--activate-pending'


def test_activation_watch_requires_explicit_instance(monkeypatch):
    from scripts import update_provider_runtimes as script
    monkeypatch.delenv('PORTAL_INSTANCE_ROOT', raising=False)
    monkeypatch.setattr(script.subprocess, 'run', lambda *args, **kwargs: pytest.fail('No service manager without an instance'))
    with pytest.raises(ValueError, match='Select an instance'):
        script._start_job("--activate-pending")


def test_resident_requests_a_separate_instance_job_before_any_service_is_restarted(monkeypatch, capsys):
    from scripts import update_provider_runtimes as script
    modes = []
    monkeypatch.setattr(script, "_start_job", lambda mode: modes.append(mode) or "tfi-fixture-provider-activation.service")
    monkeypatch.setattr(script, "run_update_pass", lambda: pytest.fail("requesting Resident must not upgrade its own cgroup"))
    assert script.main([]) == 0
    assert modes == ["--run-update"]
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["accepted"] and receipt["unit"] == "tfi-fixture-provider-activation.service"


@pytest.mark.parametrize("errors,busy,expected", [({}, False, 0), ({"claude": {"phase": "download"}}, False, 1), ({}, True, 1)])
def test_lineage_update_finishes_check_while_activation_waits(monkeypatch, tmp_path, errors, busy, expected):
    from scripts import update_provider_runtimes as script
    result = {"checked": {}, "pending": ["codex"], "errors": errors, "busy": busy}
    monkeypatch.setattr(script, "run_update_pass", lambda: result)
    monkeypatch.setattr(script, "sync_model_catalogs", lambda: {"status": "ok"})
    monkeypatch.setattr(script, "instance_runtime_root", lambda: tmp_path)
    modes = []
    monkeypatch.setattr(script, "_start_job", lambda mode: modes.append(mode))
    monkeypatch.setattr(script, "_watch_pending", lambda: pytest.fail("daily check must not wait for idle"))
    assert script.main(["--lineage-update"]) == expected
    assert modes == ["--activate-pending"]
    assert json.loads((tmp_path / "provider-update-result.json").read_text())["errors"] == errors


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
    # The installation's own service and account processes are not the test's.
    monkeypatch.setattr(updates.KimiRuntime, "services", ("portal-kimi-code.service",))
    monkeypatch.setattr(updates.KimiRuntime, "account_processes", lambda self: ())
    calls = []

    def restart(*command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise RuntimeError("new runtime failed to start")
        return ""

    monkeypatch.setattr(updates, "_service_control", lambda: SimpleNamespace(
        restart=lambda service: restart(service), status=lambda service: {"active": "active"}))
    with pytest.raises(RuntimeError, match="服务启用失败"):
        updates._activate_verified("kimi", updates.KimiRuntime(), new)
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


@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("node"), reason="Linux Codex launcher needs node")
def test_a_staged_codex_runs_with_the_node_that_installed_it(monkeypatch, tmp_path):
    """Linux 上暂存的 Codex 用安装它的 node 运行，账号服务的 PATH 中没有 node 也能启动"""
    monkeypatch.setattr(updates, "RUNTIMES", tmp_path)
    root = tmp_path / "codex-cli-1.2.3"
    original = updates.run_command

    def command(*args, **kwargs):
        if args[0:2] == ("npm", "install"):
            entry = root / "lib/node_modules/@openai/codex/bin/codex.js"
            entry.parent.mkdir(parents=True)
            entry.write_text("#!/usr/bin/env node\nconsole.log(`codex-cli 1.2.3 ${process.argv.slice(2).join(' ')}`)\n")
            (root / "bin").mkdir()
            (root / "bin/codex").symlink_to(entry)
            return ""
        return original(*args, **kwargs)

    monkeypatch.setattr(updates, "run_command", command)
    runtime = updates.CodexRuntime()
    assert runtime.stage(("1.2.3",)) == root
    result = subprocess.run([str(runtime.link_target(root)), "app-server", "--help"], env={"PATH": "/nonexistent"},
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "codex-cli 1.2.3 app-server --help"


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


def test_npm_range_chooses_latest_stable_compatible_release(monkeypatch):
    calls = []
    def command(*args, **kwargs):
        calls.append(args)
        return '["2.3.3", "2.4.0", "2.4.1"]'
    monkeypatch.setattr(updates, 'run_command', command)
    assert updates.npm_version('@moonshot-ai/kimi-code', '~2.4.0') == '2.4.1'
    assert calls == [('npm', 'view', '@moonshot-ai/kimi-code@~2.4.0', 'version', '--json')]


def test_kimi_installed_version_comes_from_binary_in_custom_directory(monkeypatch, tmp_path):
    root = tmp_path / "kimi-code-2.4.1-portal-deny-all-v1"
    root.mkdir()
    link = tmp_path / "current"
    link.symlink_to(root, target_is_directory=True)
    runtime = updates.KimiRuntime()
    monkeypatch.setattr(runtime, "link", lambda: link)
    calls = []

    def command(*args, **kwargs):
        calls.append((args, kwargs))
        return "2.4.1"

    monkeypatch.setattr(updates, "run_command", command)
    installed = runtime.installed()
    assert installed == ("2.4.1",)
    assert runtime.supports_versions(installed)
    assert not updates._newer(("2.4.1",), installed)
    assert calls == [((str(updates.npm_binary(link, "kimi")), "--version"), {"timeout": 60})]


def test_kimi_missing_runtime_reports_zero(monkeypatch, tmp_path):
    runtime = updates.KimiRuntime()
    monkeypatch.setattr(runtime, "link", lambda: tmp_path / "missing")
    assert runtime.installed() == ("0",)


def test_kimi_native_revision_stages_separately_from_running_upstream(monkeypatch, tmp_path):
    from management_portal.execution import kimi_runtime_patch
    monkeypatch.setattr(updates, 'RUNTIMES', tmp_path)
    runtime = updates.KimiRuntime()
    monkeypatch.setattr(runtime, 'prepare_prerequisites', lambda: None)
    calls = []
    def command(*args, **kwargs):
        calls.append(args)
        if args[:2] == ('npm', 'install'):
            root = Path(args[args.index('--prefix') + 1])
            binary = updates.npm_binary(root, 'kimi')
            binary.parent.mkdir(parents=True)
            binary.write_text('fake executable')
            return ''
        return '2.4.1' if args[-1] == '--version' else '--no-open'
    patched = []
    monkeypatch.setattr(updates, 'run_command', command)
    monkeypatch.setattr(kimi_runtime_patch, 'apply', lambda root: patched.append(root))
    root = runtime.stage(('2.4.1',))
    assert root.name == 'kimi-code-2.4.1-portal-deny-all-v1'
    assert patched == [root]
    assert not (tmp_path / 'kimi-code-2.4.1').exists()


def test_kimi_equal_upstream_version_still_requires_native_revision(monkeypatch, tmp_path):
    from management_portal.execution import kimi_runtime_patch
    runtime = updates.KimiRuntime()
    monkeypatch.setattr(runtime, 'link', lambda: tmp_path)
    monkeypatch.setattr(runtime, 'installed', lambda: ('2.4.1',))
    monkeypatch.setattr(runtime, 'prerequisites_available', lambda: True)
    monkeypatch.setattr(kimi_runtime_patch, 'verified', lambda root: False)
    assert not updates._runtime_installed(runtime)
    monkeypatch.setattr(kimi_runtime_patch, 'verified', lambda root: True)
    assert updates._runtime_installed(runtime)


def test_restricted_runtime_revision_defers_while_native_turns_exist(monkeypatch, tmp_path):
    from management_portal import kimi_account_runtime
    from contextlib import nullcontext
    from unittest.mock import Mock
    runtime = updates.KimiRuntime()
    monkeypatch.setattr(updates, 'activation_window', lambda provider: nullcontext(True))
    recover = Mock(side_effect=AssertionError('must not recover or replay existing inputs'))
    monkeypatch.setattr(updates, '_prepare_execution_recovery', recover)
    switch = Mock(return_value=True)
    monkeypatch.setattr(updates, '_switch_runtime', switch)
    monkeypatch.setattr(updates, '_active_executions', lambda provider: 0)
    monkeypatch.setattr(kimi_account_runtime, 'runtime_busy', lambda: True)
    root = tmp_path / 'kimi-code-2.4.1-portal-deny-all-v1'
    assert not updates._activate_verified('kimi', runtime, root)
    switch.assert_not_called()
    monkeypatch.setattr(kimi_account_runtime, 'runtime_busy', lambda: False)
    monkeypatch.setattr(updates, '_active_executions', lambda provider: 1)
    assert not updates._activate_verified('kimi', runtime, root)
    switch.assert_not_called()
    monkeypatch.setattr(updates, '_active_executions', lambda provider: 0)
    assert updates._activate_verified('kimi', runtime, root)
    recover.assert_not_called()


def test_kimi_incompatible_protocol_is_not_ready_or_staged(monkeypatch, tmp_path):
    runtime = updates.KimiRuntime()
    monkeypatch.setattr(runtime, 'installed', lambda: ('2.5.0',))
    monkeypatch.setattr(runtime, 'link', lambda: tmp_path)
    monkeypatch.setattr(runtime, 'prerequisites_available', lambda: True)
    assert not updates._runtime_installed(runtime)
    assert runtime.supports_versions(('2.4.1',))
    with pytest.raises(updates.PreparationError) as raised:
        runtime.stage(('2.5.0',))
    assert isinstance(raised.value.__cause__, ValueError)
    assert 'unsupported native protocol' in str(raised.value.__cause__)


def test_failed_candidate_is_retested_before_pending_activation(monkeypatch, tmp_path):
    from management_portal.execution import provider_candidate
    from management_portal.execution.provider_preparation import PreparationError
    _isolated(monkeypatch, tmp_path)
    root = tmp_path / "candidate"
    _stage_into(monkeypatch, [], lambda name, versions: root)
    attempts, switches = [], []
    def validate(provider, candidate, active):
        attempts.append((provider, candidate))
        if len(attempts) < 3:
            raise PreparationError("interface_validation", ValueError("private detail"))
    monkeypatch.setattr(provider_candidate, "validate", validate)
    monkeypatch.setattr(updates, "_activate_verified", lambda provider, *a, **kw: switches.append(provider) or True)
    for result in (updates.run_update_pass(), updates.activate_pending_once()):
        assert result["pending"] == ["kimi"] and result["activated"] == []
        assert result["errors"]["kimi"]["phase"] == "interface_validation"
        assert "private detail" not in str(result)
    assert switches == []
    assert updates.activate_pending_once()["activated"] == ["kimi"]
    assert attempts == [("kimi", root)] * 3 and switches == ["kimi"]


def test_candidate_uses_private_home_and_exact_sdk_paths(monkeypatch, tmp_path):
    from management_portal.execution import provider_candidate

    active, staged = tmp_path / "active", tmp_path / "staged"
    active.mkdir()
    staged.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "live-secret")
    attempts = []

    def run(command, *, env, cwd, **options):
        attempts.append((command[-1], env["HOME"]))
        assert "ANTHROPIC_API_KEY" not in env
        assert cwd.parent == Path(env["HOME"])
        marker = cwd / "old-session"
        if len(attempts) == 1:
            marker.write_text("saved")
        else:
            assert marker.read_text() == "saved"

    monkeypatch.setattr(provider_candidate, "run_process", run)
    provider_candidate.validate("codex", staged, active)
    assert [path for path, _ in attempts] == [str(active), str(staged)]
    assert attempts[0][1] == attempts[1][1] and not Path(attempts[0][1]).exists()


def test_antigravity_upgrade_needs_designated_account(monkeypatch, tmp_path):
    from management_portal.execution import provider_candidate

    active, staged = tmp_path / "active", tmp_path / "staged"
    active.mkdir()
    staged.mkdir()
    monkeypatch.delenv("AGY_TEST_PROFILE", raising=False)
    with pytest.raises(provider_candidate.CandidateCheckError) as failure:
        provider_candidate.validate("antigravity", staged, active)
    assert failure.value.public()["diagnostic"]["check"] == "test_profile"


def test_watcher_finishes_on_interface_failure(monkeypatch):
    from scripts import update_provider_runtimes as script

    result = {"pending": ["kimi"], "errors": {"kimi": {"phase": "interface_validation"}}}
    monkeypatch.setattr(script, "activate_pending_once", lambda: result)
    monkeypatch.setattr(script.time, "sleep", lambda _: pytest.fail("retry on next update pass"))
    assert script._watch_pending() == 1
