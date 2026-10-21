"""Service manager adapters keep the same Portal control contract."""
from __future__ import annotations

import subprocess

import pytest

from scripts.portal_service_control import ServiceControl
from scripts.portal_source_layout import INSTANCE_LAYOUT
from scripts import portal_services


def test_linux_contract_and_health():
    commands = []

    def run(*args, check=True):
        commands.append(args)
        output = "LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=42\n" if "show" in args else ""
        return subprocess.CompletedProcess(args, 0, output, "")

    control = ServiceControl(platform="linux", runner=run)
    control.start("management-portal.service")
    control.restart("management-portal.service")
    control.stop("management-portal.service")
    assert control.health("management-portal.service", lambda: {"ok": True})["ok"]
    assert commands == [
        ("systemctl", "--user", "start", "management-portal.service"),
        ("systemctl", "--user", "restart", "management-portal.service"),
        ("systemctl", "--user", "stop", "management-portal.service"),
        ("systemctl", "--user", "show", "management-portal.service", "-p", "LoadState", "-p", "ActiveState", "-p", "SubState", "-p", "MainPID"),
    ]


def test_macos_launchd_contract(tmp_path):
    service = "management-portal.service"
    commands = []
    plist = tmp_path / "com.tfi.portal.management-portal.plist"
    plist.touch()
    registered = False

    def run(*args, check=True):
        nonlocal registered
        commands.append(args)
        if args[1] == "print":
            return subprocess.CompletedProcess(args, 0 if registered else 113,
                                               "state = running\n pid = 44\n" if registered else "", "")
        if args[1] == "bootstrap":
            registered = True
        if args[1] == "bootout":
            registered = False
        return subprocess.CompletedProcess(args, 0, "", "")

    control = ServiceControl(platform="darwin", runner=run, agent_root=tmp_path, uid=501)
    control.start(service)
    assert control.status(service)["pid"] == 44
    control.restart(service)
    control.stop(service)
    assert control.status(service)["active"] == "inactive"
    assert ("launchctl", "bootstrap", "gui/501", str(plist)) in commands
    assert ("launchctl", "kickstart", "-k", "gui/501/com.tfi.portal.management-portal") in commands
    assert ("launchctl", "bootout", "gui/501/com.tfi.portal.management-portal") in commands
    with pytest.raises(NotImplementedError):
        control.reload(service)


def test_windows_scm_contract_and_instance_group():
    commands = []
    active = set()

    def run(*args, check=True):
        commands.append(args)
        service = args[-1]
        if args[1] == "start":
            active.add(service)
        if args[1] == "stop":
            active.discard(service)
        state = "4  RUNNING" if service in active else "1  STOPPED"
        return subprocess.CompletedProcess(args, 0, f"STATE : {state}\nPID : 45\n", "")

    control = ServiceControl(platform="win32", runner=run)
    members = INSTANCE_LAYOUT["dev"]["services"]
    control.start(INSTANCE_LAYOUT["dev"]["target"])
    assert tuple(command[-1] for command in commands if command[1] == "start") == members
    assert control.status(INSTANCE_LAYOUT["dev"]["target"])["active"] == "active"
    control.stop(INSTANCE_LAYOUT["dev"]["target"])
    assert tuple(command[-1] for command in commands if command[1] == "stop") == tuple(reversed(members))
    assert control.health(members[0])["ok"] is False


def test_macos_stop_waits_for_launchd_process_exit(tmp_path, monkeypatch):
    from scripts import portal_service_control as module
    states = iter(["state = running\n pid = 44\n", "state = exiting\n pid = 44\n", ""])
    sleeps = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    def run(*args, check=True):
        if args[1] == "print":
            output = next(states)
            return subprocess.CompletedProcess(args, 0 if output else 113, output, "")
        assert args[1] == "bootout"
        return subprocess.CompletedProcess(args, 0, "", "")
    ServiceControl(platform="darwin", runner=run, agent_root=tmp_path, uid=501).stop("fixture.service")
    assert sleeps == [0.2]


def test_service_stop_timeout_never_claims_a_live_pid_has_exited(monkeypatch):
    control = ServiceControl(platform="darwin", uid=501)
    monkeypatch.setattr(control, "status", lambda _: {"active": "inactive", "pid": 44})
    with pytest.raises(TimeoutError, match="macos service did not stop"):
        control._wait_stopped("fixture.service", timeout=0)


def test_portal_services_exposes_health_for_each_instance():
    args = portal_services._parser().parse_args(["health", "--instance", "release"])
    assert args.command == "health"
    assert args.instance == "release"


def test_native_start_requires_registered_services(monkeypatch):
    events = []
    missing_service = INSTANCE_LAYOUT["dev"]["services"][0]

    def register(instance, platform):
        events.append(("register", instance, platform))

    def run(*args, check=True):
        events.append(args)
        assert args == ("sc.exe", "queryex", missing_service)
        return subprocess.CompletedProcess(args, 1060, "Service does not exist (1060)", "")

    control = ServiceControl(platform="win32", runner=run)
    monkeypatch.setattr(portal_services, "_controller", lambda: control)
    monkeypatch.setattr(portal_services.portal_service_units, "install_native_units", register)
    monkeypatch.setattr(portal_services.portal_dev_generation, "activate",
                        lambda: events.append(("activate",)))
    monkeypatch.setattr(portal_services, "_wait_healthy",
                        lambda instance: pytest.fail("Missing service must prevent health probing"))
    with pytest.raises(RuntimeError, match="Standalone TFI"):
        portal_services.start("dev")
    assert events == []

