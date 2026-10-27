"""Platform specific staging and activation decisions, without package installs."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_windows_activation_uses_installed_user_service_controller(monkeypatch):
    from management_portal.execution import provider_updates as updates
    from scripts import tfi_release_platform_windows as platform
    controller = object()
    monkeypatch.setattr(updates, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(platform, "service_controller", lambda: controller)
    assert updates._service_control() is controller


def test_windows_provider_services_are_instance_scoped(tmp_path, monkeypatch):
    import json
    import pytest
    from scripts import portal_service_units as units, tfi_release_platform_windows as platform
    from management_common import instance_config
    monkeypatch.setattr(units, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", str(tmp_path / "config/paths.json"))
    target = {"service_prefix": "fixture"}
    monkeypatch.setattr(instance_config, "read_installation", lambda root: {"target": target})
    monkeypatch.setattr(platform, "unit_directory", lambda: tmp_path)
    monkeypatch.setattr(platform, "unit_text", lambda target, component, components:
                        json.dumps({"name": "fixture-"+component, "root": str(tmp_path)}))
    checked = []
    monkeypatch.setattr(platform, "verify_service", lambda name, content: checked.append(name))
    for component in ("claude", "kimi"):
        file = tmp_path / ("fixture-"+component+".json")
        assert units.provider_runtime_services("portal-"+component+"-code.service") == ()
        file.write_text(platform.unit_text(target, component, (component,)))
        assert units.provider_runtime_services("portal-"+component+"-code.service") == ("fixture-"+component,)
        file.write_text(json.dumps({"name": "fixture-"+component, "root": "foreign"}))
        with pytest.raises(ValueError, match="another instance"):
            units.provider_runtime_services("portal-"+component+"-code.service")
    assert checked == ["fixture-claude", "fixture-kimi"]


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


def test_windows_claude_cold_start_registers_native_service_without_restart(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from scripts import portal_service_units as units, tfi_release_platform_windows as adapter
    from management_common import instance_config
    from management_portal.execution import provider_updates as updates
    target = {"service_prefix": "tfi-test"}
    monkeypatch.setattr(units, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path)
    monkeypatch.setenv("PORTAL_PATHS_CONFIG", str(tmp_path / "config/target.json"))
    monkeypatch.setattr(instance_config, "read_installation", lambda root: {"target": target})
    monkeypatch.setattr(adapter, "unit_directory", lambda: tmp_path)
    monkeypatch.setattr(adapter, "unit_text", lambda *args: "owned")
    calls = []
    monkeypatch.setattr(adapter, "register_services", lambda directory, definitions: calls.append(definitions))
    monkeypatch.setattr(adapter, "service_controller", lambda: SimpleNamespace(start=lambda name: calls.append(name)))
    monkeypatch.setattr(units, "_run", lambda *args: pytest.fail("Windows tried systemctl"))
    units.start_claude_service()
    assert calls == [{"tfi-test-claude": "owned"}, "tfi-test-claude"]

def test_windows_claude_command_uses_instance_sdk_and_clean_environment(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from scripts import tfi_release_install as installer
    sdk = tmp_path / "managed sdk"
    (sdk / "Scripts").mkdir(parents=True)
    (sdk / "Scripts/python.exe").touch()
    target = {"install_root": str(tmp_path), "data_roots": {"staging_root": str(tmp_path / "staging")}}
    monkeypatch.setattr(installer, "platform_adapter", lambda: SimpleNamespace(__name__="platform_windows"))
    monkeypatch.setattr(installer, "clean_environment", lambda *args, **kwargs: {"HOME": "isolated"})
    monkeypatch.setattr(installer, "runtime_python", lambda target: "unused-shared-python")
    monkeypatch.setattr(installer, "read_json", lambda path: {"deployment": {"providers": {"claude_runtime_current": str(sdk)}}})
    command, env = installer.component_command(target, "claude")
    assert command == [str(sdk / "Scripts/python.exe"), "-m", "management_portal.claude_code_daemon"]
    assert env == {"HOME": "isolated"}
