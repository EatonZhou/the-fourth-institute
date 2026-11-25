"""Service manager adapters keep the same Portal control contract."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import psutil
import pytest

from scripts.portal_service_control import ServiceControl
from scripts import portal_services


def test_linux_contract_and_status():
    commands = []

    def run(*args, check=True):
        commands.append(args)
        output = "LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=42\n" if "show" in args else ""
        return subprocess.CompletedProcess(args, 0, output, "")

    control = ServiceControl(platform="linux", runner=run)
    control.start("management-portal.service")
    control.restart("management-portal.service")
    control.stop("management-portal.service")
    assert control.status("management-portal.service")["pid"] == 42
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


def test_windows_scm_contract():
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
    control.start("tfi-example-web", "tfi-example-worker")
    assert [command[-1] for command in commands if command[1] == "start"] == ["tfi-example-web", "tfi-example-worker"]
    assert control.status("tfi-example-web")["active"] == "active"
    control.stop("tfi-example-worker")
    assert [command[-1] for command in commands if command[1] == "stop"] == ["tfi-example-worker"]
    assert control.status("tfi-example-worker")["pid"] == 0


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


@pytest.mark.parametrize("mode", ["stop", "restart", "child still running"])
def test_macos_codex_drains_native_child_before_unloading(tmp_path, monkeypatch, mode):
    service = "tfi-fixture-codex-app-server@codex_one.service"
    label = ServiceControl.label(service)
    (tmp_path / f"{label}.plist").touch()
    child = SimpleNamespace(pid=45, create_time=lambda: 1001.0)
    events = []
    registered = True

    def run(*args, check=True):
        nonlocal registered
        operation = args[1]
        if operation == "print":
            return subprocess.CompletedProcess(args, 0 if registered else 113,
                                               "state = running\n pid = 44\n" if registered else "", "")
        events.append(operation)
        if operation == "bootout":
            registered = False
        return subprocess.CompletedProcess(args, 0, "", "")

    def terminate():
        events.append("terminate host")
        # The launcher may exit before the native process finishes draining.
        raise psutil.NoSuchProcess(44)

    host = SimpleNamespace(pid=44, children=lambda *, recursive: [child], terminate=terminate,
                           create_time=lambda: 1000.0)
    monkeypatch.setattr(psutil, "Process", lambda pid: {44: host}[pid])

    def wait_procs(processes, *, timeout):
        assert processes == [host, child]
        assert timeout > 0
        events.append("wait for native child")
        return ([host], [child]) if mode == "child still running" else (processes, [])

    monkeypatch.setattr(psutil, "wait_procs", wait_procs)
    settings = dict(platform="darwin", runner=run, agent_root=tmp_path, uid=501,
                    shutdown_state=tmp_path / "state")
    control = ServiceControl(**settings)
    expected = ["terminate host", "wait for native child"]
    if mode == "child still running":
        with pytest.raises(TimeoutError, match="did not drain.*45"):
            control.restart(service)
    else:
        getattr(control, mode)(service)
        expected.append("bootout")
        if mode == "restart":
            expected.append("bootstrap")
    assert events == expected
    if mode == "child still running":
        # The update controller and launcher can both exit while the Codex
        # child still holds its session lock. A retry must still see the child.
        registered = False
        recycled_pid = SimpleNamespace(pid=44, create_time=lambda: 2000.0)
        monkeypatch.setattr(psutil, "Process", lambda pid: {44: recycled_pid, 45: child}[pid])

        def still_running(processes, *, timeout):
            assert processes == [child]
            return [], processes

        monkeypatch.setattr(psutil, "wait_procs", still_running)
        with pytest.raises(TimeoutError, match="did not drain.*45"):
            ServiceControl(**settings).start(service)
        assert events == expected

        monkeypatch.setattr(psutil, "wait_procs", lambda processes, *, timeout: (processes, []))
        ServiceControl(**settings).start(service)
        assert events == [*expected, "bootstrap"]
    assert not list(settings["shutdown_state"].glob("*.shutdown.json"))


def test_portal_services_exposes_health_for_each_deployment():
    args = portal_services._parser().parse_args(["health", "--deployment", "release"])
    assert args.command == "health"
    assert args.deployment == "release"

