"""Platform specific staging and activation decisions, without package installs."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_windows_npm_and_venv_paths(tmp_path, monkeypatch):
    from management_portal.execution import provider_updates as updates

    monkeypatch.setattr(updates, "sys", SimpleNamespace(platform="win32", executable="C:\\Python\\python.exe"))
    monkeypatch.setattr(updates, "RUNTIMES", tmp_path)
    assert updates.npm_binary(tmp_path / "codex-cli-1.2.3", "codex") == tmp_path / "codex-cli-1.2.3/codex.cmd"
    assert updates.venv_python(tmp_path / "claude-1.2.3") == tmp_path / "claude-1.2.3/Scripts/python.exe"
    assert updates.CodexRuntime().link() == tmp_path / "codex-cli-current"
    assert updates.CodexRuntime().link_target(tmp_path / "codex-cli-1.2.3") == tmp_path / "codex-cli-1.2.3"


def test_windows_junction_switch_and_rollback(tmp_path, monkeypatch):
    from management_portal.execution import provider_updates as updates

    monkeypatch.setattr(updates, "sys", SimpleNamespace(platform="win32"))
    old = tmp_path / "old version"
    new = tmp_path / "new version"
    old.mkdir()
    new.mkdir()
    link = tmp_path / "current version"
    link.symlink_to(old, target_is_directory=True)
    # Simulate NTFS junction directory semantics on the Linux test host.
    monkeypatch.setattr(updates, "_windows_junction", lambda path, target: path.symlink_to(target, target_is_directory=True))
    monkeypatch.setattr(updates, "_active_executions", lambda provider: 0)
    monkeypatch.setattr(updates.KimiRuntime, "link", lambda self: link)
    calls = []

    def restart(service):
        calls.append(service)
        if len(calls) == 1:
            raise RuntimeError("mock service failure")

    monkeypatch.setattr(updates, "_service_control", lambda: SimpleNamespace(
        restart=restart, status=lambda service: {"active": "active"}))
    import pytest
    with pytest.raises(RuntimeError, match="mock service failure"):
        updates._activate("kimi", updates.KimiRuntime(), new)
    assert link.resolve() == old
    assert len(calls) == 2


def test_dsh_stage_includes_commonjs_transport(tmp_path, monkeypatch):
    from management_portal.execution import provider_updates as updates

    monkeypatch.setattr(updates, "RUNTIMES", tmp_path)
    monkeypatch.setattr(updates, "run_command", lambda *args, **kwargs: (
        '"~4.0.5"' if args[:2] == ("npm", "view") else ""))
    root = updates.DshRuntime().stage(("0.2.1-rc.2", "0.2.1-rc.2"))
    source = updates.ROOT / "management_common/local_ipc.cjs"
    assert (root / "local_ipc.cjs").read_text().strip() == (
        "module.exports = require(" + __import__("json").dumps(str(source)) + ");"
    )


def test_engineering_package_includes_dsh_wrapper_and_common_transport():
    from management_portal.distribution.engineering import source_files
    from management_portal.execution import provider_updates as updates

    names = {path.as_posix() for path in source_files(updates.ROOT)}
    assert "management_common/local_ipc.cjs" in names
    assert "management_portal/dsh_runtime/local_ipc.cjs" in names
    assert "management_portal/dsh_runtime/bridge.mjs" in names
