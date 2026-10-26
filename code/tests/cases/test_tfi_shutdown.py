"""Instance shutdown shares a deadline across ordered services and bus calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from unittest.mock import patch

import pytest

from scripts import tfi_shutdown as shutdown


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_ordered_services_share_one_deadline_and_kill_all_live_units():
    clock, commands = Clock(), []
    units = ["tfi-test-web.service", "tfi-test-worker.service", "tfi-test-execution.service"]
    killed = False

    def run(argv, **kw):
        nonlocal killed
        commands.append((argv, kw["timeout"], clock.now))
        assert 0 < kw["timeout"] <= 20 - clock.now
        output = ""
        if "list-units" in argv:
            output = json.dumps([{"unit": unit} for unit in units] + [{"unit": "foreign-web.service"}])
        elif "show" in argv:
            output = "\n\n".join(f"Id={unit}\nActiveState={'inactive' if killed else 'active'}\nMainPID={0 if killed else 42}\nControlPID=0" for unit in units)
        elif "kill" in argv:
            killed = True
        return subprocess.CompletedProcess(argv, 0, output, "")

    with patch.object(shutdown.time, "monotonic", clock.monotonic), patch.object(shutdown.time, "sleep", clock.sleep):
        result = shutdown.stop_units("tfi-test", runner=run)
    assert result["ok"] and set(result["forced"]) == set(units)
    assert 15 <= clock.now < 16
    stops = [call for call in commands if "stop" in call[0]]
    assert len(stops) == 1 and "--no-block" in stops[0][0]
    assert set(stops[0][0][-3:]) == set(units)
    assert all("foreign-web.service" not in call[0] for call in commands)


def test_unresponsive_service_manager_is_bounded_by_remaining_deadline():
    def run(argv, **kw):
        assert 0 < kw["timeout"] <= 0.2
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    with pytest.raises(TimeoutError, match="overall deadline"):
        shutdown.stop_units("tfi-test", timeout=0.2, runner=run)


def test_processes_remaining_after_sigkill_are_reported_as_failure():
    clock = Clock()

    def run(argv, **kw):
        output = (json.dumps([{"unit": "tfi-test-web.service"}]) if "list-units" in argv else
                  "Id=tfi-test-web.service\nActiveState=deactivating\nMainPID=42\nControlPID=0")
        return subprocess.CompletedProcess(argv, 0, output, "")

    with patch.object(shutdown.time, "monotonic", clock.monotonic), patch.object(shutdown.time, "sleep", clock.sleep):
        with pytest.raises(TimeoutError):
            shutdown.stop_units("tfi-test", timeout=1, runner=run)
    assert clock.now == 1


def test_application_stop_preserves_database_and_nats():
    calls = []
    units = ["tfi-test-web.service", "tfi-test-postgres.service", "tfi-test-nats.service"]

    def run(argv, **kw):
        calls.append(argv)
        output = json.dumps([{"unit": unit} for unit in units]) if "list-units" in argv else ""
        if "show" in argv:
            output = "Id=tfi-test-web.service\nActiveState=inactive\nMainPID=0\nControlPID=0"
        return subprocess.CompletedProcess(argv, 0, output, "")

    assert shutdown.stop_units("tfi-test", application_only=True, runner=run)["ok"]
    stop = next(call for call in calls if "stop" in call)
    assert stop[-1] == units[0] and len(stop) == 5


def test_running_accounts_recorded_before_stop_and_disabled_after_exit(tmp_path):
    calls = []
    account = "tfi-test-codex-app-server@one.service"

    def run(argv, **kw):
        calls.append(argv)
        output = ""
        if "list-units" in argv:
            output = json.dumps([{"unit": account, "active": "active"}])
        elif "stop" in argv:
            assert json.loads(record.read_text()) == [account]
        elif "show" in argv:
            output = f"Id={account}\nActiveState=inactive\nMainPID=0\nControlPID=0"
        return subprocess.CompletedProcess(argv, 0, output, "")

    record = tmp_path / "restart.json"
    assert shutdown.stop_units("tfi-test", runner=run, restart_record=record)["ok"]
    assert calls[-1] == ["systemctl", "--user", "disable", account]


def test_missing_unit_observation_cannot_report_success():
    def run(argv, **kw):
        output = json.dumps([{"unit": "tfi-test-web.service"}]) if "list-units" in argv else ""
        return subprocess.CompletedProcess(argv, 0, output, "")
    with pytest.raises(RuntimeError, match="Incomplete shutdown"):
        shutdown.stop_units("tfi-test", runner=run)


def test_explicit_extra_service_shares_the_same_stop_transaction():
    units = ["tfi-test-web.service", "aclink-standalone.service"]
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        output = json.dumps([{"unit": unit} for unit in units]) if "list-units" in argv else ""
        if "show" in argv:
            output = "\n\n".join(f"Id={unit}\nActiveState=inactive\nMainPID=0\nControlPID=0" for unit in units)
        return subprocess.CompletedProcess(argv, 0, output, "")
    assert shutdown.stop_units("tfi-test", extra_units=[units[1]], runner=run)["ok"]
    stops = [argv for argv in calls if "stop" in argv]
    assert len(stops) == 1 and set(stops[0][-2:]) == set(units)
    with pytest.raises(ValueError, match="explicit service"):
        shutdown.stop_units("tfi-test", extra_units=["foreign-*.service"], runner=run)


@pytest.mark.skipif(os.getenv("PORTAL_TEST_SYSTEMD_SHUTDOWN") != "1",
                    reason="Opt-in isolated user-systemd services; no Portal units touched")
def test_real_ordered_services_with_270_second_timeouts_stop_with_one_budget(tmp_path):
    from scripts.tfi_instance_manager import supervisor_environment
    env = supervisor_environment()
    prefix = "tfi-stop-test-" + uuid.uuid4().hex
    units = [prefix + "-" + name + ".service" for name in ("execution", "worker", "web")]

    def control(*args, check=True):
        return subprocess.run(args, env=env, text=True, capture_output=True, timeout=5, check=check)

    try:
        for index, unit in enumerate(units):
            marker = tmp_path / f"ready-{index}"
            program = ("import os,signal,time; from pathlib import Path; "
                       "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                       f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(90)")
            args = ["systemd-run", "--user", "--collect", "--unit=" + unit,
                    "--property=TimeoutStopSec=270", "--property=KillMode=control-group"]
            if index:
                args += ["--property=After=" + units[index - 1]]
            control(*args, sys.executable, "-c", program)
            deadline = time.monotonic() + 4
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists(), "Isolated fixture process did not start"
        started = time.monotonic()
        result = shutdown.stop_units(prefix, timeout=2, env=env)
        assert result["ok"] and set(result["forced"]) == set(units)
        assert time.monotonic() - started < 2
        for marker in tmp_path.glob("ready-*"):
            stat = Path("/proc") / marker.read_text() / "stat"
            assert not stat.exists() or stat.read_text().rsplit(")", 1)[1].split()[0] == "Z"
    finally:
        control("systemctl", "--user", "kill", "--kill-whom=all", "--signal=SIGKILL", *units, check=False)
        control("systemctl", "--user", "stop", "--no-block", *units, check=False)
        control("systemctl", "--user", "reset-failed", *units, check=False)
