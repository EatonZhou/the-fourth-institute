"""A stopped or crashed desktop component leaves an actionable, secret-free log."""
import json
from types import SimpleNamespace

import pytest

from scripts import tfi_release_install as api, tfi_windows_user_services as services
from management_common import process_runtime


@pytest.mark.parametrize("component", ["web", "codex"])
@pytest.mark.parametrize("outcome", ["crash", "stop", "supervisor_error"])
def test_supervision_records_exit_without_command_or_environment(tmp_path, monkeypatch, capsys, outcome, component):
    value = dict(root=str(tmp_path), code=str(tmp_path), component=component, name="account-one", command=["python", "secret-argument"])
    (tmp_path / "runtime").mkdir()
    definition = tmp_path / "web.json"
    definition.write_text(json.dumps(value))
    monkeypatch.setattr(services, "validate_definition", lambda value, name: value)
    monkeypatch.setattr(api, "owned_state", lambda root: {"target": {}})
    monkeypatch.setattr(api, "clean_environment", lambda target: {"TOKEN": "secret-value"})
    monkeypatch.setattr(api, "component_command", lambda *args: (["python", "secret-argument"], {"TOKEN": "secret-value"}))
    monkeypatch.setattr(services, "process_stamp", lambda pid: 42)
    monkeypatch.setattr(services.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    process = SimpleNamespace(pid=123, returncode=0xc0000005 if outcome == "crash" else None)
    process.poll = lambda: process.returncode
    def spawn(command, **kwargs):
        assert kwargs["env"]["PYTHONFAULTHANDLER"] == "1"
        return process
    monkeypatch.setattr(process_runtime, "spawn_process", spawn)
    monkeypatch.setattr(process_runtime, "terminate_process_tree", lambda *args, **kwargs: setattr(process, "returncode", process.returncode or 0))
    if outcome == "stop":
        services.state_path(value).with_suffix(".stop").write_text("42")
    if outcome == "supervisor_error":
        monkeypatch.setattr(services.time, "sleep", lambda seconds: (_ for _ in ()).throw(RuntimeError("poll failed")))
        with pytest.raises(RuntimeError, match="poll failed"):
            services.supervise(definition)
    else:
        services.supervise(definition)
    output = capsys.readouterr().out
    assert "secret-argument" not in output and "secret-value" not in output
    events = [json.loads(line.removeprefix("@TFI_SERVICE ")) for line in output.splitlines()]
    assert events[0]["event"] == "started"
    assert events[-1]["event"] == "exited"
    assert events[-1]["reason"] == {"crash": "child_exit", "stop": "stop_requested", "supervisor_error": "supervisor_error"}[outcome]
    assert events[-1]["exit_code_hex"] == ("0xC0000005" if outcome == "crash" else "0x00000000")
    assert not services.state_path(value).exists()
    assert not services.state_path(value).with_suffix(".stop").exists()
