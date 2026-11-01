"""Pure platform contracts; native Windows and macOS installation need their own hosts."""
from __future__ import annotations

import json
from pathlib import PureWindowsPath
import plistlib
import struct
import subprocess

import pytest

from management_common.code_environment import runtime_python
from management_common.instance_manifest import load
from scripts import tfi_release_install as install
from scripts import tfi_release_platform_linux as linux
from scripts import tfi_release_platform_macos as macos
from scripts import tfi_release_platform_windows as windows
from scripts.portal_service_control import ServiceControl
from tests.cases.instance_test_support import write_instance


@pytest.fixture
def instance(tmp_path):
    return load(write_instance(tmp_path / "TFI instance"))


def test_linux_unit_quotes_paths_and_selects_only_the_instance(instance):
    unit = linux.manifest_unit_text(instance, "web")
    code = instance.service_code("web")
    assert f"WorkingDirectory={code}" in unit
    assert json.dumps(str(runtime_python(code))) in unit
    assert json.dumps(f"PORTAL_INSTANCE_ROOT={instance.root}") in unit
    assert json.dumps("PORTAL_DEPLOYMENT=release") in unit
    assert "HOME=" not in unit and "XDG_" not in unit


def test_request_path_units_run_ahead_of_agent_commands_and_tests(instance):
    """处理请求的服务单元 CPUWeight 为 1000，优先于 agent 命令和测试；Worker 保持默认"""
    # Agent shells keep the default weight; test runs use 20 (management_portal.test_framework.executor).
    for component in install.CORE_COMPONENTS:
        weighted = "\nCPUWeight=1000\n" in linux.manifest_unit_text(instance, component)
        assert weighted is (component != "worker"), component


def test_linux_registration_replaces_and_restores_on_failure(instance, tmp_path, monkeypatch):
    """归属由 instance_services 核对；写入失败时恢复原定义。"""
    commands = []
    monkeypatch.setattr(linux.subprocess, "run", lambda command, **kwargs: commands.append(command))
    directory = tmp_path / "units"
    directory.mkdir()
    current = linux.manifest_unit_text(instance, "web")
    (directory / "tfi-fixture-web.service").write_text(current.replace("RestartSec=3", "RestartSec=9"))
    assert linux.register_services(directory, {"tfi-fixture-web.service": current,
                                               "tfi-fixture-nats.service": "nats"}) == ["tfi-fixture-nats.service"]
    assert (directory / "tfi-fixture-web.service").read_text() == current
    assert commands == [["systemctl", "--user", "daemon-reload"]]
    (directory / "tfi-fixture-worker.service").symlink_to(directory / "elsewhere")
    with pytest.raises(ValueError, match="linked"):
        linux.register_services(directory, {"tfi-fixture-web.service": "changed",
                                            "tfi-fixture-worker.service": "worker"})
    assert (directory / "tfi-fixture-web.service").read_text() == current


def test_macos_launchd_definition_and_registration_are_passive(instance, tmp_path):
    name = instance.service_name("web") + ".service"
    content = macos.manifest_unit_text(instance, "web")
    job = plistlib.loads(content.encode())
    code = instance.service_code("web")
    assert job["ProgramArguments"] == [str(runtime_python(code)), "-m", "management_portal.service_runtime",
                                       "--component", "web"]
    assert job["WorkingDirectory"] == str(code)
    assert job["EnvironmentVariables"] == {"PORTAL_INSTANCE_ROOT": str(instance.root), "PORTAL_DEPLOYMENT": "release"}
    assert job["StandardOutPath"] == str(instance.directory("runtime") / "web.log")
    assert job["RunAtLoad"] is True
    assert macos.register_services(tmp_path / "agents", {name: content}) == [name]
    assert macos.register_services(tmp_path / "agents", {name: content}) == []
    macos.remove_services(tmp_path / "agents", [name])
    assert not macos.unit_path(tmp_path / "agents", name).exists()


def test_windows_scm_command_quotes_python_and_root(instance):
    definition = json.loads(windows.manifest_unit_text(instance, "web", mode="scm", account=r"HOST\tfi-user"))
    assert definition["name"] == "tfi-fixture-web"
    assert definition["binary"].startswith(str(runtime_python(instance.service_code("web"))) + " ")
    # A root with a space is one quoted Windows argument.
    assert '"' + str(PureWindowsPath(instance.root)) + '"' in definition["binary"]
    assert definition["account"] == r"HOST\tfi-user"


def test_windows_registration_rolls_back_only_new_services(instance, tmp_path, monkeypatch):
    units = {instance.service_name(component): windows.manifest_unit_text(instance, component, mode="scm",
                                                                          account=r"HOST\tfi-user")
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
    assert deleted == created == ["tfi-fixture-postgres"]
    assert not windows.unit_path(tmp_path, "tfi-fixture-postgres").exists()


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
