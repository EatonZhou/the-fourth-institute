"""Kimi onboarding must prepare the shell before reporting a usable runtime.

:module: execution.providers
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import kimi_windows_runtime as shell
from management_portal.execution import provider_updates as updates


@pytest.fixture
def windows(monkeypatch, tmp_path):
    monkeypatch.setattr(shell, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(shell.sysconfig, "get_platform", lambda: "win-amd64")
    monkeypatch.setattr(shell, "_root", lambda: tmp_path)
    monkeypatch.setattr(shell.shutil, "which", lambda _: None)
    for key in ("KIMI_SHELL_PATH", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_missing_shell_prevents_ready_and_launch(windows):
    assert not shell.available()
    with pytest.raises(RuntimeError, match="Git Bash"):
        shell.environment()


def test_custom_shell_is_reused_without_download(windows, monkeypatch):
    bash = windows / "custom Git/bin/bash.exe"
    bash.parent.mkdir(parents=True)
    bash.touch()
    monkeypatch.setenv("KIMI_SHELL_PATH", str(bash))
    command = Mock(return_value=SimpleNamespace(stdout="portal-bash-ready"))
    monkeypatch.setattr(shell, "run_process", command)
    shell.prepare()
    assert shell.environment() == {"KIMI_SHELL_PATH": str(bash)}
    assert command.call_args.args[0][0] == str(bash)
    assert not (windows / "archives").exists()


def test_invalid_explicit_shell_is_not_silently_replaced(windows, monkeypatch):
    monkeypatch.setenv("KIMI_SHELL_PATH", str(windows / "missing.exe"))
    with pytest.raises(ValueError, match="KIMI_SHELL_PATH"):
        shell.prepare()


def test_pinned_shell_publishes_only_after_validation(windows, monkeypatch):
    from scripts import tfi_release_native_dependencies as native
    downloads, runs = [], []
    def download(entry, path):
        downloads.append(entry)
        path.parent.mkdir(parents=True)
        path.touch()
    def run(command, **kwargs):
        runs.append(command)
        if command[0].endswith(".7z.exe"):
            target = Path(command[2][2:]) / "bin/bash.exe"
            target.parent.mkdir(parents=True)
            target.touch()
        return SimpleNamespace(stdout="portal-bash-ready")
    monkeypatch.setattr(native, "download", download)
    monkeypatch.setattr(shell, "run_process", run)
    shell.prepare()
    assert shell.available()
    assert downloads[0]["sha256"] == shell.ARTIFACTS["amd64"][1]
    assert runs[1][0].endswith("bin/bash.exe")
    shell.prepare()
    assert len(downloads) == 1


def test_failed_download_never_executes_archive(windows, monkeypatch):
    from scripts import tfi_release_native_dependencies as native
    monkeypatch.setattr(native, "download", Mock(side_effect=ValueError("checksum mismatch")))
    command = Mock()
    monkeypatch.setattr(shell, "run_process", command)
    with pytest.raises(ValueError, match="checksum"):
        shell.prepare()
    command.assert_not_called()
    assert not shell.available()


def test_prepare_repairs_missing_shell_without_reinstalling_kimi(windows, monkeypatch):
    from management_portal import business_tasks
    runtime = updates.KimiRuntime()
    monkeypatch.setattr(updates, "STATE_ROOT", windows / "state")
    monkeypatch.setattr(updates, "runtimes", lambda: {"kimi": runtime})
    monkeypatch.setattr(runtime, "installed", lambda: ("2.3.3",))
    monkeypatch.setattr(runtime, "link", lambda: windows)
    stage = Mock(side_effect=AssertionError("must not reinstall existing Kimi"))
    monkeypatch.setattr(runtime, "stage", stage)
    monkeypatch.setattr(business_tasks, "start", lambda _, work: work())
    def prepare():
        bash = windows / "bash.exe"
        bash.touch()
        monkeypatch.setenv("KIMI_SHELL_PATH", str(bash))
    monkeypatch.setattr(shell, "prepare", prepare)
    assert not updates.runtime_available("kimi")
    updates.prepare_runtime("kimi")
    assert updates.preparation_status("kimi")["status"] == "ready"
    stage.assert_not_called()
