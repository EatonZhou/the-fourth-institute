"""Provider programs are found only beside the selected code, never on the host.

:module: execution.providers
"""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX virtual environment layout")

from management_portal.execution import provider_updates as updates


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    from tests.cases.provider_service_test_support import provider_instance, real_provider_links
    links = real_provider_links()
    _, runtimes = provider_instance(monkeypatch, tmp_path)
    # Host programs on PATH must not stand in for the code-owned ones.
    host = tmp_path / "host-bin"
    for name in ("codex", "python3"):
        executable(host / name, "host program")
    monkeypatch.setenv("PATH", str(host) + os.pathsep + "/usr/bin:/bin")
    yield runtimes
    assert real_provider_links() == links


def executable(path, output):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nprintf '%s\\n' '" + output + "'\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def sdk(tmp_path):
    # Real isolated interpreter/package import, no network or account access.
    root = tmp_path / "staged-sdk"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root)], check=True)
    site = next((root / "lib").glob("python*/site-packages"))
    package = site / "claude_agent_sdk"
    package.mkdir()
    (package / "__init__.py").write_text("__version__ = '0.2.174'\n")
    for name in ("psycopg", "psycopg_pool"):
        (site / (name + ".py")).write_text("")
    executable(package / "_bundled/claude", "2.1.288 (Claude Code)")
    return root


def test_host_programs_are_not_provider_runtimes(isolated):
    assert updates.CodexRuntime().link() == isolated / "codex"
    assert updates.ClaudeRuntime().link() == isolated / "claude-agent-sdk-current"
    assert updates.ClaudeRuntime().installed() == ("0",)
    assert not updates._runtime_installed(updates.ClaudeRuntime())


def test_activated_claude_is_read_from_its_package(isolated, sdk):
    current = isolated / "claude-agent-sdk-current"
    current.symlink_to(sdk, target_is_directory=True)
    runtime = updates.ClaudeRuntime()
    assert runtime.link() == runtime.activation_link() == current
    # The installed version comes from the package, not a directory name.
    assert runtime.installed() == ("0.2.174",)


@pytest.mark.parametrize("defect", ["sdk", "daemon_dependency", "bundled_cli"])
def test_incomplete_sdk_is_not_reported_installed(isolated, sdk, defect):
    (isolated / "claude-agent-sdk-current").symlink_to(sdk, target_is_directory=True)
    site = next((sdk / "lib").glob("python*/site-packages"))
    target = {"sdk": site / "claude_agent_sdk/__init__.py",
              "daemon_dependency": site / "psycopg_pool.py",
              "bundled_cli": site / "claude_agent_sdk/_bundled/claude"}[defect]
    target.unlink()
    assert updates.ClaudeRuntime().installed() == ("0",)
    assert not updates.runtime_available("claude")


def test_claude_activation_failure_rolls_back_the_code_owned_link(isolated, monkeypatch):
    old = isolated / "previous"
    old.mkdir()
    staged = isolated / "staged"
    staged.mkdir()
    runtime = updates.ClaudeRuntime()
    link = runtime.activation_link()
    assert link.is_relative_to(isolated)
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
