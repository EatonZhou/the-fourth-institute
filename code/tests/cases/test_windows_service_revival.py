"""Windows user units report a terminated supervisor as failed and get revived.

This provides parity with systemd Restart= and launchd KeepAlive for desktop installs.
"""
import json
from types import SimpleNamespace

import pytest

from management_common.instance_manifest import load
from scripts import instance_services, tfi_windows_user_services as services
from tests.cases.instance_test_support import write_instance


@pytest.fixture
def unit(tmp_path, monkeypatch):
    (tmp_path / "runtime").mkdir()
    directory = tmp_path / "units"
    directory.mkdir()
    value = {"mode": "user", "name": "tfi-one-codex", "component": "web", "root": str(tmp_path),
             "code": str(tmp_path), "python": "python"}
    (directory / "tfi-one-codex.json").write_text(json.dumps(value))
    monkeypatch.setattr(services, "owned_definition", lambda value, name: value)
    monkeypatch.setattr(services, "validate_definition", lambda value, name: value)
    alive = {}
    monkeypatch.setattr(services, "process_stamp", lambda pid: alive.get(pid))
    control = services.WindowsServiceControl(directory)
    return control, value, alive


def record(value, **fields):
    services.write_record(services.state_path(value), json.dumps(
        {"pid": 10, "created": 1, "child_pid": 11, "child_created": 2, **fields}))


def test_status_distinguishes_running_restarting_stopping_failed_and_stopped(unit):
    control, value, alive = unit
    assert control.status("tfi-one-codex")["active"] == "inactive"
    record(value)
    alive.update({10: 1, 11: 2})
    assert control.status("tfi-one-codex") == {"service": "tfi-one-codex", "load": "loaded",
                                               "active": "active", "sub": "running", "pid": 10}
    del alive[11]
    assert control.status("tfi-one-codex")["active"] == "activating"
    assert control.status("tfi-one-codex")["sub"] == "auto-restart"
    services.write_record(services.state_path(value).with_suffix(".stop"), "1")
    assert control.status("tfi-one-codex")["active"] == "deactivating"
    alive.clear()
    # The supervisor died without removing its record: never "stopped".
    assert control.status("tfi-one-codex") == {"service": "tfi-one-codex", "load": "loaded",
                                               "active": "failed", "sub": "failed", "pid": 0}
    alive[10] = 99  # A recycled PID with another creation time is not the supervisor.
    assert control.status("tfi-one-codex")["active"] == "failed"


def test_start_leaves_a_restarting_supervisor_alone(unit, monkeypatch):
    control, value, alive = unit
    record(value)
    alive[10] = 1
    monkeypatch.setattr(services.subprocess, "Popen", lambda *a, **k: pytest.fail("second supervisor"))
    control.start("tfi-one-codex")


@pytest.mark.parametrize("outer_job_allows_breakaway", [True, False])
def test_start_breaks_away_from_the_callers_job(unit, monkeypatch, outer_job_allows_breakaway):
    """A supervisor started from a component must not die with that component's Job."""
    control, value, alive = unit
    record(value)  # failed: supervisor gone
    monkeypatch.setattr(services.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(services.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    calls = []

    def popen(arguments, **kwargs):
        calls.append(kwargs["creationflags"])
        if kwargs["creationflags"] & services.CREATE_BREAKAWAY_FROM_JOB and not outer_job_allows_breakaway:
            raise PermissionError(5, "Access is denied")
        alive.update({20: 3, 21: 4})
        record(value, pid=20, created=3, child_pid=21, child_created=4)
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(services.subprocess, "Popen", popen)
    control.start("tfi-one-codex")
    assert calls[0] & services.CREATE_BREAKAWAY_FROM_JOB
    assert len(calls) == (1 if outer_job_allows_breakaway else 2)
    assert control.status("tfi-one-codex")["active"] == "active"


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = write_instance(tmp_path / "instance")
    directory = root / "runtime/service-units"
    directory.mkdir(parents=True)
    definition = directory / "tfi-fixture-execution.json"
    definition.write_text(json.dumps({"name": "tfi-fixture-execution", "root": str(root)}))
    monkeypatch.setattr(services, "validate_definition", lambda value, name: value)
    units = {}
    monkeypatch.setattr(services, "failed_units", lambda instance, directory: {
        name: control for name, control in units.items() if control.failed})
    return definition, units, root


class Unit:
    def __init__(self, error=None):
        self.failed, self.error, self.starts = True, error, 0

    def start(self, name):
        self.starts += 1
        if self.error:
            raise self.error
        self.failed = False


def test_revival_starts_failed_units_and_backs_off_failed_starts(installation):
    definition, units, _ = installation
    units.update({"codex": Unit(), "kimi": Unit(RuntimeError("no runtime"))})
    attempts, clock = {}, [0.0]
    assert services.revive_failed(definition, attempts, now=lambda: clock[0]) == [
        {"service": "codex", "result": "started"},
        {"service": "kimi", "result": "failed", "error_type": "RuntimeError"}]
    assert attempts["kimi"] == (1, 120)
    clock[0] = 60
    assert services.revive_failed(definition, attempts, now=lambda: clock[0]) == []
    clock[0] = 120
    services.revive_failed(definition, attempts, now=lambda: clock[0])
    assert units["kimi"].starts == 2 and attempts["kimi"] == (2, 120 + 240)
    units["kimi"].failed = False  # Started some other way: forget its backoff.
    services.revive_failed(definition, attempts, now=lambda: clock[0])
    assert attempts == {}


def test_revival_waits_while_a_lifecycle_operation_owns_the_instance(installation):
    definition, units, root = installation
    units["codex"] = Unit()
    with instance_services.configuration_lock(root):
        assert services.revive_failed(definition, {}) == []
    assert units["codex"].starts == 0
    assert services.revive_failed(definition, {}) == [{"service": "codex", "result": "started"}]


def test_failed_units_cover_registered_core_and_provider_components(tmp_path, monkeypatch):
    root = write_instance(tmp_path / "instance")
    document = json.loads((root / "instance.json").read_text())
    document["services"]["components"] += ["codex-app-server@a", "codex-app-server@b"]
    (root / "instance.json").write_text(json.dumps(document))
    instance = load(root)
    statuses = {instance.service_name("postgres"): "active", instance.service_name("web"): "failed",
                instance.service_name("codex-app-server@a"): "failed",
                instance.service_name("codex-app-server@b"): "inactive", "tfi-other-web": "failed"}

    class Control:
        def definition(self, name):
            return {} if name in statuses else None

        def status(self, name):
            return {"active": statuses[name]}
    monkeypatch.setattr(services, "WindowsServiceControl", lambda directory: Control())
    assert sorted(services.failed_units(instance, tmp_path)) == sorted([
        instance.service_name("web"), instance.service_name("codex-app-server@a")])
