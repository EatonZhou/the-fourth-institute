"""Reuse the installation user's runtime under a fresh Linux service environment.

:module: execution.providers
"""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

pwd = pytest.importorskip("pwd")
pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux service discovery")

from management_portal.execution import provider_updates as updates


@pytest.fixture
def fresh_service(monkeypatch, tmp_path):
    home = tmp_path / "installer"
    home.mkdir()
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(home)))
    monkeypatch.setenv("HOME", str(tmp_path / "new-instance/home"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("PORTAL_CLAUDE_RUNTIME_ROOT", raising=False)
    monkeypatch.setattr(updates, "RUNTIMES", tmp_path / "managed")
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(updates, "_PROVIDER_DEPLOYMENT", {})
    return home


def executable(path, output):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nprintf '%s\\n' '" + output + "'\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def sdk(tmp_path):
    # Real isolated interpreter/package import, no network or account access.
    root = tmp_path / "already-installed-sdk"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root)], check=True)
    site = next((root / "lib").glob("python*/site-packages"))
    package = site / "claude_agent_sdk"
    package.mkdir()
    (package / "__init__.py").write_text("__version__ = '0.2.174'\n")
    for name in ("psycopg", "psycopg_pool"):
        (site / (name + ".py")).write_text("")
    executable(package / "_bundled/claude", "2.1.288 (Claude Code)")
    return root


def service_runtime(monkeypatch, root):
    original = updates.run_command

    def command(*args, **kwargs):
        if args[0] == "systemctl":
            assert args == ("systemctl", "--user", "show", "portal-claude-code.service",
                            "--property=ExecStart", "--value")
            return ("{ path=" + str(root / "bin/python") + " ; argv[]="
                    + str(root / "bin/python")
                    + " -m management_portal.claude_code_daemon ; ignore_errors=no ; }")
        return original(*args, **kwargs)

    monkeypatch.setattr(updates, "run_command", command)


def test_fresh_service_finds_installing_users_codex_without_changing_environment(fresh_service):
    binary = executable(fresh_service / ".local/bin/codex", "codex-cli 0.163.0")
    before = dict(os.environ)
    assert updates.CodexRuntime().link() == binary
    assert updates.runtime_available("codex")
    assert dict(os.environ) == before
    assert updates.prepare_runtime("codex")["source"] == "existing"
    assert not updates.RUNTIMES.exists()


def test_existing_claude_service_sdk_is_available_without_installing(fresh_service, sdk, monkeypatch):
    service_runtime(monkeypatch, sdk)
    runtime = updates.ClaudeRuntime()
    assert runtime.link() == sdk
    assert runtime.installed() == ("0.2.174",)
    assert runtime.activation_link() == updates.RUNTIMES / "claude-agent-sdk-current"
    from management_portal.claude_code_client import ClaudeCodeClient, ClaudeCodeUnavailable

    def daemon_not_started(_client):
        raise ClaudeCodeUnavailable("daemon not started")

    # The reused SDK counts as installed, but the runtime is available only
    # once this instance's daemon answers.
    monkeypatch.setattr(ClaudeCodeClient, "health", daemon_not_started)
    assert updates._runtime_installed(runtime)
    assert not updates.runtime_available("claude")
    monkeypatch.setattr(ClaudeCodeClient, "health", lambda _client: {"backend": "claude", "protocol_version": 1})
    assert updates.runtime_available("claude")
    assert updates.prepare_runtime("claude")["source"] == "existing"
    assert not updates.RUNTIMES.exists()


def test_codex_explicit_and_activated_paths_remain_authoritative(fresh_service, monkeypatch):
    executable(fresh_service / ".local/bin/codex", "codex-cli 0.163.0")
    configured = fresh_service / "explicit-codex"
    monkeypatch.setattr(updates, "_PROVIDER_DEPLOYMENT", {"codex_binary": str(configured)})
    assert updates.CodexRuntime().link() == configured
    monkeypatch.setattr(updates, "_PROVIDER_DEPLOYMENT", {})
    managed = updates.CodexRuntime().activation_link()
    managed.parent.mkdir()
    managed.symlink_to(executable(fresh_service / "activated-codex", "codex-cli 0.164.0"))
    assert updates.CodexRuntime().link() == managed


def test_reused_claude_is_not_staged_or_activated_by_update_pass(fresh_service, sdk, monkeypatch):
    service_runtime(monkeypatch, sdk)
    runtime = updates.ClaudeRuntime()
    monkeypatch.setattr(updates, "runtimes", lambda: {"claude": runtime})
    monkeypatch.setattr(runtime, "available", lambda: ("0.2.175",))
    monkeypatch.setattr(runtime, "stage", lambda *args: pytest.fail("external SDK must not be replaced"))
    result = updates.run_update_pass()
    assert result["checked"]["claude"] == {"installed": "0.2.174", "available": "0.2.175"}
    assert result["errors"] == {} and result["activated"] == [] and result["pending"] == []
    assert not updates.RUNTIMES.exists()


@pytest.mark.parametrize("defect", ["sdk", "daemon_dependency", "bundled_cli"])
def test_incomplete_sdk_is_not_reported_ready(fresh_service, sdk, monkeypatch, defect):
    service_runtime(monkeypatch, sdk)
    site = next((sdk / "lib").glob("python*/site-packages"))
    target = {"sdk": site / "claude_agent_sdk/__init__.py",
              "daemon_dependency": site / "psycopg_pool.py",
              "bundled_cli": site / "claude_agent_sdk/_bundled/claude"}[defect]
    target.unlink()
    assert not updates.runtime_available("claude")


def test_managed_claude_wins_over_existing_service(fresh_service, sdk, monkeypatch):
    service_runtime(monkeypatch, sdk)
    managed = updates.RUNTIMES / "claude-agent-sdk-current"
    managed.parent.mkdir()
    managed.symlink_to(sdk, target_is_directory=True)
    runtime = updates.ClaudeRuntime()
    assert runtime.link() == runtime.activation_link() == managed
    # The installed version comes from the package, not a directory name.
    assert runtime.installed() == ("0.2.174",)


def test_explicit_claude_runtime_precedes_automatic_discovery(fresh_service, sdk, monkeypatch):
    service_runtime(monkeypatch, sdk)
    missing = fresh_service / "explicit-missing-sdk"
    monkeypatch.setenv("PORTAL_CLAUDE_RUNTIME_ROOT", str(missing))
    assert updates.ClaudeRuntime().link() == missing
    assert not updates.runtime_available("claude")


def test_python_on_path_can_supply_sdk_without_existing_service(fresh_service, sdk, monkeypatch):
    monkeypatch.setenv("PATH", str(sdk / "bin") + os.pathsep + os.environ["PATH"])
    assert updates.ClaudeRuntime().link() == sdk
    assert updates.ClaudeRuntime().installed() == ("0.2.174",)


def test_installer_python_is_checked_even_when_system_python_is_on_service_path(fresh_service, sdk):
    # A script wrapper preserves the environment's interpreter identity.
    binary = fresh_service / ".local/bin/python3"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nexec "' + str(sdk / "bin/python") + '" "$@"\n')
    binary.chmod(0o755)
    assert updates.ClaudeRuntime().link() == sdk


def test_unrelated_service_interpreter_is_not_reused(fresh_service, sdk, monkeypatch):
    original = updates.run_command

    def command(*args, **kwargs):
        if args[0] == "systemctl":
            return ("{ path=" + str(sdk / "bin/python") + " ; argv[]="
                    + str(sdk / "bin/python") + " -m other_daemon ; ignore_errors=no ; }")
        return original(*args, **kwargs)

    monkeypatch.setattr(updates, "run_command", command)
    assert updates.ClaudeRuntime().link() == updates.RUNTIMES / "claude-agent-sdk-current"


def test_managed_claude_activation_failure_rolls_back_without_touching_external_sdk(
        fresh_service, sdk, monkeypatch):
    old = fresh_service / "previous"
    old.mkdir()
    staged = fresh_service / "staged"
    staged.mkdir()
    runtime = updates.ClaudeRuntime()
    link = runtime.activation_link()
    link.parent.mkdir()
    link.symlink_to(old, target_is_directory=True)
    monkeypatch.setattr(updates, "_active_executions", lambda provider: 0)
    monkeypatch.setattr(runtime, "busy", lambda: False)
    monkeypatch.setattr(updates.ClaudeRuntime, "services", property(lambda self: ("private-claude",)))
    calls = []

    def restart(service):
        calls.append(link.resolve())
        if len(calls) == 1:
            raise RuntimeError("failed to start staged runtime")

    monkeypatch.setattr(updates, "_service_control", lambda: SimpleNamespace(restart=restart))
    from management_portal.execution.provider_preparation import PreparationError
    with pytest.raises(PreparationError, match="服务启用失败") as failure:
        updates._activate("claude", runtime, staged)
    # The public error names only the phase; the service failure stays chained.
    assert failure.value.phase == "service_activation"
    assert "failed to start" in str(failure.value.__cause__)
    assert failure.value.rollback_failed is False
    assert link.resolve() == old
    assert calls == [staged, old]
    assert (sdk / "bin/python").is_file()
