"""Telepus switch controls only its fixed user units; never touches live systemd."""
import subprocess

import pytest

from management_common.operation_errors import OperationError
from management_portal.telepus_runtime import TelepusRuntime, UNITS


class Systemd:
    def __init__(self, missing=()):
        self.enabled = True
        self.missing = set(missing)
        self.calls = []

    def __call__(self, command, **_kwargs):
        self.calls.append(command)
        if command[2] == "show":
            blocks = []
            for unit in UNITS:
                installed = unit not in self.missing
                blocks.append("\n".join((f"Id={unit}", f"LoadState={'loaded' if installed else 'not-found'}",
                                         f"ActiveState={'active' if installed and self.enabled else 'inactive'}",
                                         f"UnitFileState={'enabled' if installed and self.enabled else 'disabled'}")))
            return subprocess.CompletedProcess(command, 0, "\n\n".join(blocks), "")
        assert command[:3] == ["systemctl", "--user", "enable" if self.enabled is False else "disable"]
        self.enabled = command[2] == "enable"
        return subprocess.CompletedProcess(command, 0, "", "")


def test_switch_stops_and_disables_all_telepus_units_then_starts_them():
    systemd = Systemd()
    runtime = TelepusRuntime(systemd)
    assert runtime.status()["complete"]
    off = runtime.set_enabled(False)
    assert not off["enabled"] and not off["running"]
    assert [command for command in systemd.calls if command[2] != "show"] == [
        ["systemctl", "--user", "disable", "--now", *UNITS]]
    on = runtime.set_enabled(True)
    assert on["complete"]
    assert [command for command in systemd.calls if command[2] != "show"][-1] == [
        "systemctl", "--user", "enable", "--now", *UNITS]


def test_missing_unit_blocks_enable_but_does_not_block_disabling_installed_units():
    systemd = Systemd(missing=(UNITS[1],))
    runtime = TelepusRuntime(systemd)
    with pytest.raises(OperationError, match="缺少 Telepus 服务"):
        runtime.set_enabled(True)
    assert all(command[2] == "show" for command in systemd.calls)
    status = runtime.set_enabled(False)
    assert not status["enabled"]
    assert systemd.calls[-2] == ["systemctl", "--user", "disable", "--now", UNITS[0], UNITS[2]]


def test_unavailable_systemd_does_not_attempt_mutation():
    def unavailable(_command, **_kwargs):
        raise FileNotFoundError("systemctl")

    runtime = TelepusRuntime(unavailable)
    assert runtime.status()["available"] is False
    with pytest.raises(OperationError, match="systemctl"):
        runtime.set_enabled(False)
