"""Native Codex account hosts are registered components defined through the instance."""
from __future__ import annotations

import json
import plistlib
from types import SimpleNamespace

import pytest


@pytest.fixture(params=["darwin", "win32"])
def native(request, tmp_path, monkeypatch):
    from scripts import instance_services, portal_service_units as units
    from scripts import tfi_release_platform_macos, tfi_release_platform_windows
    from management_portal.execution import account_hosting
    from tests.cases.provider_service_test_support import FakeControl, provider_instance, real_provider_links
    links = real_provider_links()
    root, _ = provider_instance(monkeypatch, tmp_path)
    platform = request.param
    adapter = tfi_release_platform_windows if platform == "win32" else tfi_release_platform_macos
    directory = tmp_path / "definitions"
    monkeypatch.setattr(units, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(instance_services, "platform_name", lambda: "windows" if platform == "win32" else "macos")
    monkeypatch.setattr(instance_services, "definition_directory", lambda instance: directory)
    control = FakeControl(directory, adapter.unit_path)
    monkeypatch.setattr(instance_services, "controller", lambda instance: control)
    events = []

    def register(target, definitions, **_):
        target.mkdir(parents=True, exist_ok=True)
        created = [name for name in definitions if not adapter.unit_path(target, name).exists()]
        for name, text in definitions.items():
            adapter.unit_path(target, name).write_text(text, encoding="utf-8")
            events.append(("register", name))
        return created

    def remove(target, names):
        for name in names:
            adapter.unit_path(target, name).unlink()
            events.append(("remove", name))

    monkeypatch.setattr(adapter, "register_services", register)
    monkeypatch.setattr(adapter, "remove_services", remove)
    monkeypatch.setattr(account_hosting, "ensure_account_home", lambda *args: tmp_path / "account")
    monkeypatch.setattr(account_hosting, "write_codex_launch", lambda account: None)
    yield SimpleNamespace(root=root, platform=platform, adapter=adapter, directory=directory,
                          control=control, events=events, units=units)
    assert real_provider_links() == links


def test_native_codex_lifecycle_uses_registered_service(native):
    from management_common.instance_manifest import load
    units = native.units
    state = units.start_codex_instance("sample")
    instance = load(native.root)
    component = "codex-app-server@sample"
    assert component in instance.document["services"]["components"]
    name = instance.service_name(component) + ("" if native.platform == "win32" else ".service")
    assert state["active"] == "active" and state["unit"] == name
    assert native.events == [("register", name)] and ("start", name) in native.control.calls
    text = native.adapter.unit_path(native.directory, name).read_text(encoding="utf-8")
    if native.platform == "darwin":
        definition = plistlib.loads(text.encode())
        command, environment = definition["ProgramArguments"], definition["EnvironmentVariables"]
    else:
        definition = json.loads(text)
        assert definition["mode"] == "user" and definition["component"] == component
        command = [definition["python"], "-m", "management_portal.service_runtime", "--component", component]
        environment = definition["environment"]
    # Only the instance selection travels; programs and accounts come from its manifest.
    assert command[1:] == ["-m", "management_portal.service_runtime", "--component", component]
    assert environment == {"PORTAL_INSTANCE_ROOT": str(native.root)}
    units.verify_native_codex_services([name])
    with pytest.raises(ValueError, match="not a registered account host"):
        units.verify_native_codex_services([name.replace("sample", "other")])
    units.stop_codex_instance("sample")
    assert native.control.status(name)["active"] == "inactive"
    units.remove_codex_instance("sample")
    assert native.events[-1] == ("remove", name)
    assert component not in load(native.root).document["services"]["components"]


def test_native_start_failure_rolls_back_new_registration(native, monkeypatch):
    from management_common.instance_manifest import load

    def fail(*names):
        raise RuntimeError("mock launch failed")

    monkeypatch.setattr(native.control, "start", fail)
    with pytest.raises(RuntimeError, match="mock launch failed"):
        native.units.start_codex_instance("sample")
    assert [event for event, _ in native.events] == ["register", "remove"]
    assert "codex-app-server@sample" not in load(native.root).document["services"]["components"]


def test_foreign_definition_is_never_replaced(native):
    from management_common.host_instances import add_service_component
    from scripts.instance_services import unit_name
    instance = add_service_component(native.root, "codex-app-server@sample")
    name = unit_name(instance, "codex-app-server@sample")
    path = native.adapter.unit_path(native.directory, name)
    path.parent.mkdir(parents=True)
    if native.platform == "darwin":
        path.write_bytes(plistlib.dumps({"EnvironmentVariables": {"PORTAL_INSTANCE_ROOT": "/another"}}))
    else:
        path.write_text(json.dumps({"mode": "user", "environment": {"PORTAL_INSTANCE_ROOT": "/another"}}))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="another instance"):
        native.units.start_codex_instance("sample")
    assert path.read_bytes() == before and native.events == []
    assert not any(call[0] == "start" for call in native.control.calls)


def test_windows_scm_host_reads_registered_codex_command(tmp_path, monkeypatch):
    from scripts import tfi_release_windows_service as host

    definition = tmp_path / "portal-codex-app-server@test.service.json"
    command = [str(tmp_path / "Python With Spaces.exe"), "-m", "management_portal.codex_app_server_host",
               "--socket", str(tmp_path / "codex.sock"), "--binary", str(tmp_path / "Codex With Spaces.cmd"),
               "--account-config", str(tmp_path / "launch.json")]
    definition.write_text(json.dumps({"name": definition.stem, "command": command,
                                      "cwd": str(tmp_path), "log_path": str(tmp_path / "host.log")}))
    received = []
    monkeypatch.setattr(host, "run_command_service", lambda *args, **kwargs: received.append((args, kwargs)) or 0)
    assert host.run_registered_service(definition) == 0
    assert received[0][0] == (definition.stem, command)
    assert received[0][1]["cwd"] == tmp_path
