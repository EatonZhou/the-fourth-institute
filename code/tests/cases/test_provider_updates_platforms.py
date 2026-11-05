"""Platform specific staging and activation decisions, without package installs."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A selected instance whose Provider programs live in the case's directory."""
    from tests.cases.provider_service_test_support import provider_instance, real_provider_links
    links = real_provider_links()
    root, runtimes = provider_instance(monkeypatch, tmp_path, components=("claude-code", "kimi-code"))
    yield SimpleNamespace(root=root, runtimes=runtimes)
    assert real_provider_links() == links


@pytest.fixture
def windows(isolated, tmp_path, monkeypatch):
    """Simulate Windows service ownership over a private definition directory."""
    from scripts import instance_services, portal_service_units as units, tfi_release_platform_windows as adapter
    from tests.cases.provider_service_test_support import FakeControl
    directory = tmp_path / "service-units"
    directory.mkdir()
    monkeypatch.setattr(units, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(instance_services, "platform_name", lambda: "windows")
    monkeypatch.setattr(instance_services, "definition_directory", lambda instance: directory)
    control = FakeControl(directory, adapter.unit_path)
    monkeypatch.setattr(instance_services, "controller", lambda instance: control)
    return SimpleNamespace(directory=directory, control=control, adapter=adapter, units=units, **vars(isolated))


def test_windows_activation_uses_the_instance_service_controller(isolated, monkeypatch):
    from management_portal.execution import provider_updates as updates
    from scripts import instance_services
    controller = object()
    monkeypatch.setattr(updates, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(instance_services, "controller", lambda instance: controller
                        if instance.root == isolated.root else pytest.fail("another instance"))
    assert updates._service_control() is controller


def test_windows_provider_services_are_instance_scoped(windows):
    from management_common.instance_manifest import load
    instance = load(windows.root)
    for component in ("claude-code", "kimi-code"):
        unit = "portal-" + component + ".service"
        name = instance.service_name(component)
        path = windows.adapter.unit_path(windows.directory, name)
        assert windows.units.provider_runtime_services(unit) == ()
        path.write_text(windows.adapter.manifest_unit_text(instance, component), encoding="utf-8")
        assert windows.units.provider_runtime_services(unit) == ()
        windows.control.active.add(name)
        assert windows.units.provider_runtime_services(unit) == (name,)
        definition = json.loads(path.read_text(encoding="utf-8"))
        definition["environment"]["PORTAL_INSTANCE_ROOT"] = "C:/another/instance"
        path.write_text(json.dumps(definition), encoding="utf-8")
        with pytest.raises(ValueError, match="another instance"):
            windows.units.provider_runtime_services(unit)


def test_windows_npm_and_venv_paths(isolated, monkeypatch):
    from management_common import code_environment
    from management_portal.execution import provider_updates as updates

    monkeypatch.setattr(updates, "sys", SimpleNamespace(platform="win32", executable="C:\\Python\\python.exe"))
    monkeypatch.setattr(code_environment, "os", SimpleNamespace(name="nt"))
    runtimes = isolated.runtimes
    assert updates.npm_binary(runtimes / "codex-cli-1.2.3", "codex") == runtimes / "codex-cli-1.2.3/codex.cmd"
    assert updates.venv_python(runtimes / "claude-1.2.3") == runtimes / "claude-1.2.3/Scripts/python.exe"
    assert updates.CodexRuntime().link() == runtimes / "codex-cli-current"
    assert updates.CodexRuntime().link_target(runtimes / "codex-cli-1.2.3") == runtimes / "codex-cli-1.2.3"


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
    monkeypatch.setattr(updates.KimiRuntime, "services", property(lambda self: ("owned-kimi",)))
    calls = []

    def restart(service):
        calls.append(service)
        if len(calls) == 1:
            raise RuntimeError("mock service failure")

    monkeypatch.setattr(updates, "_service_control", lambda: SimpleNamespace(
        restart=restart, status=lambda service: {"active": "active"}))
    with pytest.raises(RuntimeError, match="服务启用失败"):
        updates._activate("kimi", updates.KimiRuntime(), new)
    assert link.resolve() == old
    assert len(calls) == 2


def test_dsh_stage_includes_commonjs_transport(isolated, monkeypatch):
    from management_portal.execution import provider_updates as updates

    monkeypatch.setattr(updates, "run_command", lambda *args, **kwargs: (
        '"~4.0.5"' if args[:2] == ("npm", "view") else ""))
    root = updates.DshRuntime().stage(("0.2.1-rc.2", "0.2.1-rc.2"))
    assert root.is_relative_to(isolated.runtimes)
    source = updates.ROOT / "management_common/local_ipc.cjs"
    assert (root / "local_ipc.cjs").read_text().strip() == (
        "module.exports = require(" + json.dumps(str(source)) + ");"
    )


def test_engineering_package_includes_dsh_wrapper_and_common_transport():
    from management_portal.distribution.engineering import source_files
    from management_portal.execution import provider_updates as updates

    names = {path.as_posix() for path in source_files(updates.ROOT)}
    assert "management_common/local_ipc.cjs" in names
    assert "management_portal/dsh_runtime/local_ipc.cjs" in names
    assert "management_portal/dsh_runtime/bridge.mjs" in names


def test_windows_claude_cold_start_defines_native_service_without_restart(windows, monkeypatch):
    from management_common.instance_manifest import load
    from tests.cases.provider_service_test_support import installed_claude_sdk
    installed_claude_sdk(monkeypatch)
    document = json.loads((windows.root / "instance.json").read_text())
    document["services"]["components"].remove("claude-code")
    (windows.root / "instance.json").write_text(json.dumps(document))
    registered = []

    def register(directory, definitions, **_):
        for name, text in definitions.items():
            windows.adapter.unit_path(directory, name).write_text(text, encoding="utf-8")
        registered.append(sorted(definitions))
        return list(definitions)

    monkeypatch.setattr(windows.adapter, "register_services", register)
    monkeypatch.setattr(windows.units, "_run", lambda *args, **kwargs: pytest.fail("Windows tried systemctl"))
    windows.units.start_claude_service()
    name = load(windows.root).service_name("claude-code")
    assert registered == [[name]]
    assert [call for call in windows.control.calls if call[0] in {"start", "stop", "restart"}] == [("start", name)]
    # Repeating the cold start finds the running daemon and leaves it alone.
    windows.units.start_claude_service()
    assert registered == [[name]]


def test_windows_claude_command_uses_instance_sdk(isolated, monkeypatch):
    from management_portal import service_runtime as instance_runtime
    from management_common.instance_manifest import load
    sdk = isolated.runtimes / "claude-agent-sdk-current"
    (sdk / "Scripts").mkdir(parents=True)
    (sdk / "Scripts/python.exe").touch()
    package = isolated.runtimes / "claude-app-server-current/management_portal"
    package.mkdir(parents=True)
    (package / "claude_code_daemon.py").touch()
    monkeypatch.setattr(instance_runtime, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(instance_runtime, "clean_environment", lambda *args, **kwargs: {"PORTAL_INSTANCE_ROOT": "selected"})
    command, env = instance_runtime.component_command(load(isolated.root), "claude-code")
    assert command == [str(sdk / "Scripts/python.exe"), "-m", "management_portal.claude_code_daemon"]
    assert env == {"PORTAL_INSTANCE_ROOT": "selected"}
