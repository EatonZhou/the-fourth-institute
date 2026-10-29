"""Provider service activation uses the OS capability without changing rollback."""
from types import SimpleNamespace

import pytest

from management_portal.execution import account_login, provider_updates as updates


@pytest.mark.parametrize("platform,executable", [("linux", "systemctl"), ("darwin", "launchctl"), ("win32", "sc.exe")])
def test_provider_control_uses_platform_service_owner(monkeypatch, tmp_path, platform, executable):
    from scripts import portal_service_control, portal_service_units
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        output = "LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=42\n"
        if platform == "darwin":
            output = "state = running\npid = 42"
        elif platform == "win32":
            output = "STATE : 4 RUNNING\nPID : 42"
        return SimpleNamespace(returncode=0, stdout=output)
    monkeypatch.setattr(portal_service_control.sys, "platform", platform)
    monkeypatch.setattr(updates, "run_process", run)
    monkeypatch.setattr(portal_service_units, "_run", lambda *command, **kwargs: run(command, **kwargs))
    if platform == "win32":
        # Windows Provider services belong to the installed Windows service
        # controller: a service without a TFI user-service definition is
        # queried through the SCM with the shared service-control runner.
        monkeypatch.setenv("TFI_WINDOWS_SERVICE_DIRECTORY", str(tmp_path))
        monkeypatch.setattr(portal_service_control, "_run", lambda *command, **kwargs: run(command, **kwargs))
    result = updates._service_control().status("example.service")
    assert result["active"] == "active" and result["pid"] == 42
    assert calls[0][0][0] == executable
    if platform == "darwin":
        assert calls[0][1]["timeout"] == 180
    elif platform == "win32":
        assert calls[0][0][:3] == ("sc.exe", "queryex", "example.service")
    else:
        assert calls[0][0][:3] == ("systemctl", "--user", "show")


def test_login_stops_tree_even_if_leader_already_exited(monkeypatch):
    calls = []
    process = SimpleNamespace(poll=lambda: 0)
    monkeypatch.setattr(account_login, "terminate_process_tree", lambda child, **kwargs: calls.append(child))
    account_login._terminate(process)
    assert calls == [process]
