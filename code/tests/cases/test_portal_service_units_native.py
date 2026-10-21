"""Native Codex service definitions share the release SCM/launchd adapters."""
from __future__ import annotations

import json
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_native_codex_lifecycle_uses_registered_service(tmp_path, monkeypatch, platform):
    from scripts import portal_service_units as units
    from scripts import portal_service_control
    from scripts import tfi_release_platform_windows, tfi_release_platform_macos
    from management_common import site_config

    binary = tmp_path / "Codex Binary.exe"
    binary.write_bytes(b"test")
    monkeypatch.setattr(units, "sys", SimpleNamespace(platform=platform, executable=str(tmp_path / "Python.exe")))
    monkeypatch.setattr(units, "instance_root", lambda: tmp_path / "Portal Instance")
    monkeypatch.setattr(site_config, "read_site_config", lambda: {"deployment": {"providers": {"codex_binary": str(binary)}}})
    monkeypatch.setattr(units.shutil, "which", lambda name: str(binary))
    if platform == "win32":
        from management_portal.execution import provider_updates
        monkeypatch.setattr(provider_updates, "sys", units.sys)
        monkeypatch.setattr(provider_updates, "RUNTIMES", tmp_path / "runtimes")
        binary = provider_updates.CodexRuntime().link() / "codex.cmd"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"test")
    from management_common import portal_paths
    original_path = portal_paths.path_values
    monkeypatch.setattr(portal_paths, "path_values", lambda: {**original_path(), "accounts_root": str(tmp_path / "accounts")})
    monkeypatch.setenv("PORTAL_WINDOWS_SERVICE_ACCOUNT", "local\\portal")
    monkeypatch.setenv("PORTAL_WINDOWS_SERVICE_PASSWORD", "mock-secret")
    monkeypatch.setattr(tfi_release_platform_macos, "unit_directory", lambda: tmp_path / "agents")
    events = []
    definitions = {}

    class Controller:
        def start(self, name):
            events.append(("start", name))

        def stop(self, name):
            events.append(("stop", name))

        def status(self, name):
            return {"load": "loaded", "active": "active", "sub": "running", "pid": 123}

    def controller(*args, **kwargs):
        return Controller()

    controller.label = lambda service: "com.tfi.portal." + service.removesuffix(".service")
    monkeypatch.setattr(portal_service_control, "ServiceControl", controller)
    adapter = tfi_release_platform_windows if platform == "win32" else tfi_release_platform_macos

    def register(directory, units_to_register, **kwargs):
        definitions.update(units_to_register)
        events.append(("register", next(iter(units_to_register))))
        return list(units_to_register)

    monkeypatch.setattr(adapter, "register_services", register)
    monkeypatch.setattr(adapter, "remove_services", lambda directory, names: events.append(("remove", names[0])))

    state = units.start_codex_instance("sample")
    assert state["active"] == "active" and state["pid"] == 123
    name, definition = next(iter(definitions.items()))
    assert name.startswith("portal-codex-app-server@sample-")
    if platform == "darwin":
        command = plistlib.loads(definition.encode())["ProgramArguments"]
    else:
        data = json.loads(definition)
        command = data["command"]
        assert data["account"] == "local\\portal"
        assert "mock-secret" not in definition
        assert "tfi_release_windows_service.py" in data["binary"]
    assert command[:3] == [str(tmp_path / "Python.exe"), "-m", "management_portal.codex_app_server_host"]
    assert command[command.index("--binary") + 1] == str(binary)
    assert command[command.index("--account-home") + 1].endswith("accounts/codex/sample")
    assert events[:2] == [("register", name), ("start", name)]
    units.stop_codex_instance("sample")
    units.remove_codex_instance("sample")
    assert events[-2:] == [("stop", name), ("remove", name)]


def test_native_start_failure_rolls_back_new_registration(tmp_path, monkeypatch):
    from scripts import portal_service_units as units
    from scripts import portal_service_control, tfi_release_platform_macos as adapter
    from management_common import site_config

    binary = tmp_path / "codex"
    binary.write_bytes(b"test")
    monkeypatch.setattr(units, "sys", SimpleNamespace(platform="darwin", executable=str(binary)))
    monkeypatch.setattr(units, "instance_root", lambda: tmp_path / "instance")
    monkeypatch.setattr(site_config, "read_site_config", lambda: {"deployment": {}})
    monkeypatch.setattr(units.shutil, "which", lambda name: str(binary))
    from management_common import portal_paths
    original_path = portal_paths.path_values
    monkeypatch.setattr(portal_paths, "path_values", lambda: {**original_path(), "accounts_root": str(tmp_path / "accounts")})
    monkeypatch.setattr(adapter, "unit_directory", lambda: tmp_path / "agents")
    events = []
    monkeypatch.setattr(adapter, "register_services", lambda d, u: list(u))
    monkeypatch.setattr(adapter, "remove_services", lambda d, n: events.append("removed"))

    class Controller:
        def start(self, name):
            raise RuntimeError("mock launch failed")

        def stop(self, name):
            events.append("stopped")

    def controller(*args, **kwargs):
        return Controller()

    controller.label = lambda service: "com.tfi.portal." + service.removesuffix(".service")
    monkeypatch.setattr(portal_service_control, "ServiceControl", controller)
    with pytest.raises(RuntimeError, match="mock launch failed"):
        units.start_codex_instance("sample")
    assert events == ["stopped", "removed"]


def test_windows_scm_host_reads_registered_codex_command(tmp_path, monkeypatch):
    from scripts import tfi_release_windows_service as host

    definition = tmp_path / "portal-codex-app-server@test.service.json"
    command = [str(tmp_path / "Python With Spaces.exe"), "-m", "management_portal.codex_app_server_host",
               "--socket", str(tmp_path / "codex.sock"), "--binary", str(tmp_path / "Codex With Spaces.cmd"),
               "--account-home", str(tmp_path / "account home")]
    definition.write_text(json.dumps({"name": definition.stem, "command": command,
                                      "cwd": str(tmp_path), "log_path": str(tmp_path / "host.log")}))
    received = []
    monkeypatch.setattr(host, "run_command_service", lambda *args, **kwargs: received.append((args, kwargs)) or 0)
    assert host.run_registered_service(definition) == 0
    assert received[0][0] == (definition.stem, command)
    assert received[0][1]["cwd"] == tmp_path
