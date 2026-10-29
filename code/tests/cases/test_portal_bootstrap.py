"""The bootstrap CLI must be safe to inspect before installing dependencies."""

import os
import runpy
import builtins
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "scripts" / "bootstrap_portal"


def test_dry_run_lists_profiles_and_does_not_create_environment(tmp_path):
    """安装 dry-run 列出组件准备步骤，不创建虚拟环境"""
    environment = tmp_path / "portal-venv"
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "install", "test", "provider", "embeddings",
         "--venv", str(environment), "--dry-run"],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    assert not environment.exists()
    assert "requirements.txt" in result.stdout
    assert "requirements-test.txt" in result.stdout
    assert "npm run setup:js" in result.stdout
    assert "install_portal_embedding_model.py" in result.stdout
    assert "prepare_agent_runtimes.py" in result.stdout
    assert "requirements-society-service.txt" in result.stdout


def test_windows_and_macos_provider_dry_run_uses_runtime_preparation(tmp_path, monkeypatch, capsys):
    script = runpy.run_path(str(BOOTSTRAP))
    for system in ("Windows", "Darwin"):
        monkeypatch.setattr(script["platform"], "system", lambda system=system: system)
        environment = tmp_path / f"{system} portal venv"
        assert script["main"](["install", "provider", "--venv", str(environment), "--dry-run"]) == 0
        output = capsys.readouterr().out
        assert "prepare_agent_runtimes.py" in output
        assert "requires Linux" not in output
        assert not environment.exists()


def test_provider_doctor_checks_actual_link_on_macos(tmp_path, monkeypatch, capsys):
    from management_portal.execution.provider_updates import DshRuntime

    script = runpy.run_path(str(BOOTSTRAP))
    package = tmp_path / "dsh runtime"
    for name in ("dsh", "cordis", "dsh-llm"):
        directory = package / "node_modules/@deepseek-ai" / name
        directory.mkdir(parents=True)
        (directory / "package.json").write_text("{}")
    link = tmp_path / "dsh-current"
    link.symlink_to(package, target_is_directory=True)
    monkeypatch.setitem(script["doctor"].__globals__, "DSH_PACKAGE", package)
    monkeypatch.setattr(DshRuntime, "link", lambda self: link)
    monkeypatch.setattr(script["platform"], "system", lambda: "Darwin")
    script["doctor"](SimpleNamespace(profiles=["provider"], venv=tmp_path / "missing", json=False))
    assert any(line.startswith("OK") and "provider runtime integration" in line
               for line in capsys.readouterr().out.splitlines())


def test_provider_doctor_reports_missing_runtime_dependencies(tmp_path, monkeypatch, capsys):
    script = runpy.run_path(str(BOOTSTRAP))
    original_import = builtins.__import__

    def missing_provider_runtime(name, *args, **kwargs):
        if name == "management_portal.execution.provider_updates":
            raise ImportError("runtime dependencies missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_provider_runtime)
    result = script["doctor"](SimpleNamespace(profiles=["provider"], venv=tmp_path / "missing", json=False))
    assert result == 1
    assert "MISSING provider runtime integration: ImportError: runtime dependencies missing" in capsys.readouterr().out


def test_tau_profile_is_optional_and_uses_its_own_packages(tmp_path):
    """TAU's browser can be installed without restoring the removed UI suite."""
    environment = tmp_path / "portal-venv"
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "install", "tau",
         "--venv", str(environment), "--dry-run"],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    assert "tau_runtime" in result.stdout
    assert "npm install --package-lock=false --no-audit --no-fund" in result.stdout
    assert "cli.js install chromium" in result.stdout
    assert "setup:js" not in result.stdout
    assert not environment.exists()


def test_tau_runtime_reports_missing_optional_browser(tmp_path, monkeypatch):
    from management_portal.test_framework import tau

    storage = tmp_path / "state.json"
    storage.write_text("{}", encoding="utf-8")
    storage.chmod(0o600)
    monkeypatch.setattr(tau, "TAU_PLAYWRIGHT", tmp_path / "missing-playwright")
    assert "bootstrap_portal install tau" in tau.TauConfig(storage).unavailable_reason()


def test_tau_asks_node_for_the_browser_once_per_playwright_install(tmp_path, monkeypatch):
    """TAU 的浏览器检查每次 Playwright 安装只向 node 询问一次 Chromium 路径，安装变化或询问失败后重新询问，浏览器文件每次都检查

    Test 页面每次读取总览都会检查 TAU 是否可用；向 node 询问路径要 0.2 秒左右，占了总览读取的大部分时间。
    """
    from management_portal.test_framework import tau

    playwright = tmp_path / "playwright"
    playwright.mkdir()
    browser = tmp_path / "chrome"
    browser.write_text("", encoding="utf-8")
    calls, failing = [], []

    def ask(*_args, **_kwargs):
        calls.append(1)
        if failing:
            raise subprocess.CalledProcessError(1, "node")
        return str(browser)

    def reinstall():
        stat = playwright.stat()
        os.utime(playwright, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    monkeypatch.setattr(tau, "TAU_PLAYWRIGHT", playwright)
    monkeypatch.setattr(tau, "_BROWSER_PATHS", {})
    monkeypatch.setattr(tau.shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setattr(tau.subprocess, "check_output", ask)
    assert tau.browser_runtime_issue() == "" and tau.browser_runtime_issue() == ""
    assert len(calls) == 1
    browser.unlink()
    assert "缺少 Chromium" in tau.browser_runtime_issue()
    assert len(calls) == 1
    browser.write_text("", encoding="utf-8")
    reinstall()
    assert tau.browser_runtime_issue() == ""
    assert len(calls) == 2
    failing.append(True)
    reinstall()
    assert "无法加载" in tau.browser_runtime_issue() and "无法加载" in tau.browser_runtime_issue()
    assert len(calls) == 4, "a failed lookup is asked again"


def test_tau_reports_missing_native_executor_on_macos(tmp_path, monkeypatch):
    from management_portal.test_framework import tau

    monkeypatch.setattr(tau.platform, "system", lambda: "Darwin")
    assert "systemd-run" in tau.TauConfig(tmp_path / "state.json").unavailable_reason()


def test_doctor_tau_reports_missing_portal_test_session(tmp_path, monkeypatch, capsys):
    from management_portal.test_framework import tau

    monkeypatch.setenv("PORTAL_TEST_STORAGE_STATE", str(tmp_path / "missing-state.json"))
    monkeypatch.setattr(tau, "browser_runtime_issue", lambda: "")
    script = runpy.run_path(str(BOOTSTRAP))
    result = script["doctor"](SimpleNamespace(profiles=["tau"], venv=tmp_path / "missing-venv", json=False))

    assert result == 1
    assert "MISSING TAU test-operator session and DEV target: 尚未创建 test-actor 浏览器存储状态" in capsys.readouterr().out


def test_doctor_reports_missing_environment_without_modifying_it(tmp_path):
    """doctor 报告虚拟环境缺失，不会创建该环境"""
    environment = tmp_path / "portal-venv"
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "doctor", "core", "--venv", str(environment)],
        cwd=ROOT, text=True, capture_output=True,
    )
    assert result.returncode == 1
    assert "MISSING Portal virtual environment" in result.stdout
    assert not environment.exists()


def test_article_install_explains_optional_user_managed_tex(tmp_path, monkeypatch, capsys):
    """缺少可选 TeX 时说明用户安装方式，不在 Portal 安装阶段下载。"""
    script = runpy.run_path(str(BOOTSTRAP))
    monkeypatch.setattr(script["platform"], "system", lambda: "Windows")
    monkeypatch.setattr(script["shutil"], "which", lambda _: None)
    commands = []
    monkeypatch.setitem(script["main"].__globals__, "run", lambda command, **kw: commands.append(command))
    environment = tmp_path / "portal-venv"
    assert script["main"](["install", "article", "--venv", str(environment)]) == 0
    assert not any('install_article_tooling.py' in str(part) for command in commands for part in command)
    assert '由用户自行安装' in capsys.readouterr().err
    assert commands[-1][0].startswith(str(environment))
    assert not environment.exists()


def test_doctor_checks_target_interpreter_and_non_linux_provider(tmp_path, monkeypatch, capsys):
    """doctor 检查目标环境的 Python 版本，并报告非 Linux 上缺少 Provider 运行时"""
    script = runpy.run_path(str(BOOTSTRAP))
    environment = tmp_path / "portal-venv"
    python = environment / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setitem(script["main"].__globals__, "interpreter_version", lambda _: (3, 13, 0))
    monkeypatch.setitem(script["main"].__globals__, "installed_versions", lambda _: {})
    monkeypatch.setattr(script["platform"], "system", lambda: "Windows")
    from management_portal.execution.provider_updates import DshRuntime
    monkeypatch.setattr(DshRuntime, "link", lambda self: tmp_path / "missing-provider-link")
    assert script["main"](["doctor", "provider", "--venv", str(environment)]) == 1
    output = capsys.readouterr().out
    assert "MISSING Portal Python 3.12: 3.13.0" in output
    assert "MISSING provider runtime integration" in output


def test_install_rejects_unsupported_python_before_venv_creation(tmp_path, monkeypatch):
    """Python 版本不受支持时，安装在创建虚拟环境前即被拒绝"""
    script = runpy.run_path(str(BOOTSTRAP))
    monkeypatch.setitem(script["main"].__globals__, "sys",
                        SimpleNamespace(version_info=(3, 13, 0), stderr=sys.stderr))
    environment = tmp_path / "portal-venv"
    assert script["main"](["install", "core", "--venv", str(environment)]) == 2
    assert not environment.exists()


def test_windows_install_requires_wheels_unless_source_builds_requested(tmp_path, monkeypatch, capsys):
    """Windows 安装默认只用二进制 wheel，指定 --allow-source-builds 后允许源码构建"""
    script = runpy.run_path(str(BOOTSTRAP))
    monkeypatch.setattr(script["platform"], "system", lambda: "Windows")
    environment = tmp_path / "portal-venv"
    args = ["install", "core", "--venv", str(environment), "--dry-run"]
    assert script["main"](args) == 0
    assert "--only-binary=:all:" in capsys.readouterr().out
    assert script["main"]([*args, "--allow-source-builds"]) == 0
    assert "--only-binary=:all:" not in capsys.readouterr().out
    assert not environment.exists()


def test_macos_embeddings_attempts_current_wheels(tmp_path, monkeypatch, capsys):
    """不按历史 ONNX 版本预先拒绝 macOS；由 pip 判断可安装性。"""
    script = runpy.run_path(str(BOOTSTRAP))
    monkeypatch.setattr(script["platform"], "system", lambda: "Darwin")
    environment = tmp_path / "portal-venv"
    args = ["install", "embeddings", "--venv", str(environment), "--dry-run"]

    assert script["main"](args) == 0
    assert "requirements-society-service.txt" in capsys.readouterr().out
    assert not environment.exists()


def test_doctor_accepts_installed_versions_without_exact_pin(tmp_path, monkeypatch, capsys):
    script = runpy.run_path(str(BOOTSTRAP))
    python = tmp_path / "bin" / "python"
    python.parent.mkdir()
    python.touch()
    globals_ = script["doctor"].__globals__
    monkeypatch.setitem(globals_, "interpreter_version", lambda _: (3, 12, 9))
    names = script["declared_requirements"](ROOT / "requirements.txt")
    monkeypatch.setitem(globals_, "installed_versions", lambda _: {name: "999.0" for name in names})
    assert script["doctor"](SimpleNamespace(profiles=["core"], venv=tmp_path, json=False)) == 0
    assert "MISSING" not in capsys.readouterr().out
