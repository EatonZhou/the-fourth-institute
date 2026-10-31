"""A stopped, finished or crashed desktop component leaves an actionable, secret-free log."""
import json
from types import SimpleNamespace

import pytest

from scripts import tfi_release_install as api, tfi_windows_user_services as services
from management_common import process_runtime


def supervised(tmp_path, monkeypatch, component, returncodes):
    """Run supervise() over scripted child generations; returns the parsed events."""
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
    clock = [0.0]
    monkeypatch.setattr(services.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(services.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    spawned = []
    def spawn(command, **kwargs):
        assert kwargs["env"]["PYTHONFAULTHANDLER"] == "1"
        assert kwargs["env"][services.DEFINITION_ENV] == str(definition.resolve())
        process = SimpleNamespace(pid=123 + len(spawned), returncode=returncodes[len(spawned)])
        process.poll = lambda: process.returncode
        spawned.append(process)
        return process
    monkeypatch.setattr(process_runtime, "spawn_process", spawn)
    monkeypatch.setattr(process_runtime, "terminate_process_tree",
                        lambda process, **kwargs: setattr(process, "returncode", process.returncode or 0))
    return value, definition, spawned


def events(capsys):
    output = capsys.readouterr().out
    assert "secret-argument" not in output and "secret-value" not in output
    return [json.loads(line.removeprefix("@TFI_SERVICE ")) for line in output.splitlines()]


@pytest.mark.parametrize("component", ["web", "codex"])
@pytest.mark.parametrize("outcome", ["finished", "stop", "supervisor_error"])
def test_supervision_records_exit_without_command_or_environment(tmp_path, monkeypatch, capsys, outcome, component):
    value, definition, _ = supervised(tmp_path, monkeypatch, component, [0 if outcome == "finished" else None])
    if outcome == "stop":
        services.state_path(value).with_suffix(".stop").write_text("42")
    if outcome == "supervisor_error":
        monkeypatch.setattr(services.time, "sleep", lambda seconds: (_ for _ in ()).throw(RuntimeError("poll failed")))
        with pytest.raises(RuntimeError, match="poll failed"):
            services.supervise(definition)
    else:
        services.supervise(definition)
    logged = events(capsys)
    assert logged[0]["event"] == "started"
    assert logged[-1]["event"] == "exited"
    assert logged[-1]["reason"] == {"finished": "child_exit", "stop": "stop_requested", "supervisor_error": "supervisor_error"}[outcome]
    assert logged[-1]["exit_code_hex"] == "0x00000000"
    assert not services.state_path(value).exists()
    assert not services.state_path(value).with_suffix(".stop").exists()


def test_failed_child_restarts_with_backoff_until_stopped(tmp_path, monkeypatch, capsys):
    """A crashed component restarts like systemd Restart=on-failure, with growing delays.

    :purpose: Windows user services had no restart after a child crash.
    """
    value, definition, spawned = supervised(tmp_path, monkeypatch, "codex", [0xc0000005, 1, None])
    stop = services.state_path(value).with_suffix(".stop")
    records = []
    original = services.write_record
    def write(path, text):
        original(path, text)
        if path == services.state_path(value):
            records.append(json.loads(text)["child_pid"])
            if len(records) == 3:
                stop.write_text("42")  # The third generation runs until stopped.
    monkeypatch.setattr(services, "write_record", write)
    services.supervise(definition)
    logged = events(capsys)
    assert [e["event"] for e in logged] == ["started", "child_failed", "restarted", "child_failed",
                                            "restarted", "stop_requested", "exited"]
    failures = [e for e in logged if e["event"] == "child_failed"]
    assert [e["exit_code_hex"] for e in failures] == ["0xC0000005", "0x00000001"]
    assert [e["restart_in"] for e in failures] == [3, 6]
    assert records == [123, 124, 125]
    assert logged[-1]["reason"] == "stop_requested"
    assert len(spawned) == 3
    assert not services.state_path(value).exists()


def test_stop_during_restart_delay_ends_supervision(tmp_path, monkeypatch, capsys):
    value, definition, spawned = supervised(tmp_path, monkeypatch, "web", [1])
    stop = services.state_path(value).with_suffix(".stop")
    monkeypatch.setattr(services.time, "sleep", lambda seconds: stop.write_text("42"))
    services.supervise(definition)
    logged = events(capsys)
    assert [e["event"] for e in logged] == ["started", "child_failed", "stop_requested", "exited"]
    assert len(spawned) == 1
    assert not services.state_path(value).exists()


def test_long_running_generation_resets_backoff(tmp_path, monkeypatch, capsys):
    value, definition, _ = supervised(tmp_path, monkeypatch, "web", [None, None, 0])
    clock = [0.0]
    monkeypatch.setattr(services.time, "monotonic", lambda: clock[0])
    def sleep(seconds):
        clock[0] += seconds
    monkeypatch.setattr(services.time, "sleep", sleep)
    generations = []
    original = services.write_record
    def write(path, text):
        original(path, text)
        generations.append(path)
    monkeypatch.setattr(services, "write_record", write)
    import itertools
    polls = itertools.count()
    real_spawn = process_runtime.spawn_process
    def spawn(command, **kwargs):
        process = real_spawn(command, **kwargs)
        if process.returncode is None:
            # Fail after 120 s of uptime, which is longer than the reset window.
            def poll(process=process, start=clock[0]):
                if clock[0] - start >= 120:
                    process.returncode = 2
                return process.returncode
            process.poll = poll
        next(polls)
        return process
    monkeypatch.setattr(process_runtime, "spawn_process", spawn)
    services.supervise(definition)
    failures = [e for e in events(capsys) if e["event"] == "child_failed"]
    assert [e["restart_in"] for e in failures] == [3, 3]
