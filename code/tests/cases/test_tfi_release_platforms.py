"""Pure platform contracts; native Windows and macOS installation need their own hosts."""
from __future__ import annotations

import json
import plistlib
import struct
import subprocess

import pytest

from scripts import tfi_release_install as install
from scripts import tfi_release_platform_linux as linux
from scripts import tfi_release_platform_macos as macos
from scripts import tfi_release_platform_windows as windows
from scripts.portal_service_control import ServiceControl


def test_linux_unit_quotes_install_root_with_spaces(tmp_path):
    root = tmp_path / "TFI release"
    target = {"install_root": str(root), "python": "/opt/Python Env/bin/python",
              "service_prefix": "tfi-space", "run_id": "run-1"}
    assert linux.safe_path(str(root)) == root.resolve()
    unit = linux.unit_text(target, "web", ("web",))
    assert f'WorkingDirectory="{root / "code"}"' in unit
    assert '"/opt/Python Env/bin/python"' in unit


def test_macos_launchd_definition_and_registration_are_passive(tmp_path):
    root = tmp_path / "TFI release"
    target = {"install_root": str(root), "python": "/opt/Python Env/bin/python",
              "service_prefix": "tfi-space", "run_id": "run-1"}
    name = macos.unit_name(target, "web")
    content = macos.unit_text(target, "web", ("web",))
    job = plistlib.loads(content.encode())
    assert job["ProgramArguments"][:2] == [target["python"], str(root / "runtime/installer.py")]
    assert job["WorkingDirectory"] == str(root / "code")
    assert job["RunAtLoad"] is True
    assert macos.register_services(tmp_path / "agents", {name: content}) == [name]
    assert macos.register_services(tmp_path / "agents", {name: content}) == []
    macos.remove_services(tmp_path / "agents", [name])
    assert not macos.unit_path(tmp_path / "agents", name).exists()


def test_windows_service_command_quotes_python_and_root():
    target = {"install_root": r"C:\Program Files\TFI Portal",
              "python": r"C:\Python Env\python.exe", "service_prefix": "tfi-space",
              "windows_service_account": r"HOST\tfi-user"}
    definition = json.loads(windows.unit_text(target, "web", ("web",)))
    assert definition["name"] == "tfi-space-web"
    assert definition["binary"].startswith('"C:\\Python Env\\python.exe"')
    assert '"C:\\Program Files\\TFI Portal\\runtime\\installer.py"' in definition["binary"]
    assert '"C:\\Program Files\\TFI Portal"' in definition["binary"]
    assert definition["account"] == r"HOST\tfi-user"


def test_windows_registration_rolls_back_only_new_services(tmp_path, monkeypatch):
    target = {"install_root": r"C:\TFI", "python": r"C:\Python\python.exe",
              "service_prefix": "tfi-test", "windows_service_account": r"HOST\tfi-user"}
    units = {windows.unit_name(target, component): windows.unit_text(target, component, ())
             for component in ("postgres", "nats")}
    created, deleted = [], []
    monkeypatch.setenv("TFI_WINDOWS_SERVICE_PASSWORD", "test-only")
    monkeypatch.setattr(windows, "_service_binary", lambda _name: None)
    def create(definition, _password):
        if definition["name"].endswith("nats"):
            raise OSError("second registration failed")
        created.append(definition["name"])
    monkeypatch.setattr(windows, "_create_service", create)
    monkeypatch.setattr(windows, "_delete_service", deleted.append)
    with pytest.raises(OSError, match="second registration"):
        windows.register_services(tmp_path, units)
    assert deleted == created == ["tfi-test-postgres"]
    assert not windows.unit_path(tmp_path, "tfi-test-postgres").exists()


def test_native_binary_probe_checks_claimed_architecture(tmp_path):
    elf = bytearray(64)
    elf[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", elf, 18, 62)
    path = tmp_path / "postgres"
    path.write_bytes(elf)
    assert install.binary_architectures(path) == {"x86_64"}
    pe = bytearray(128)
    pe[:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 64)
    pe[64:68] = b"PE\0\0"
    struct.pack_into("<H", pe, 68, 0xAA64)
    path.write_bytes(pe)
    assert install.binary_architectures(path) == {"aarch64"}
    path.write_bytes(b"not a native binary")
    assert install.binary_architectures(path) == set()


def test_windows_stop_waits_for_pending_service():
    observed, commands = ["start_pending", "stop_pending", "stopped"], []
    control = ServiceControl(platform="win32")
    def status(_name):
        return {"load": "loaded", "active": "inactive", "sub": observed.pop(0), "pid": 11}
    control.status = status
    control.run = lambda *args, **_kw: commands.append(args) or subprocess.CompletedProcess(args, 0, "", "")
    control.stop("tfi-test-web")
    assert commands == [("sc.exe", "stop", "tfi-test-web")]
