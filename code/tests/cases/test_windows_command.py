"""Batch dispatch contract, Job ownership, and opt-in native Windows evidence."""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from management_common import _process_windows as windows
from management_common import _windows_command as command
from management_common import process_runtime as runtime


ENV = {"SystemRoot": r"C:\Windows", "PATH": "selected-path", "COMSPEC": r"C:\untrusted\cmd.exe"}
BATCH = r"C:\Program Files\tools\npm.cmd"
CMD = r"C:\Windows\System32\cmd.exe"


@pytest.fixture
def no_lookup(monkeypatch):
    monkeypatch.setattr(command.shutil, "which", lambda *args, **kwargs: None)


@pytest.mark.parametrize("suffix", [".cmd", ".CMD", ".bat", ".BAT"])
def test_batch_dispatch_quotes_spaces_empty_and_unicode(no_lookup, suffix):
    program = BATCH[:-4] + suffix
    line, executable = command.prepare([program, "", "two words", "中文", r"a\b"], environment=ENV)
    assert executable == CMD
    assert line == CMD + ' /d /s /v:off /c ""' + program + '" "" "two words" "中文" "a\\b""'


def test_bare_command_resolves_before_extension_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(command.shutil, "which", lambda value, **kw: calls.append((value, kw)) or BATCH)
    line, executable = command.prepare(["npm", "--version"], environment=ENV)
    assert calls == [("npm", {"path": "selected-path"})]
    assert executable == CMD and BATCH in line


@pytest.mark.parametrize("value", ['a"b', "a&b", "%PATH%", "!value!", "a^b", "a|b", "a<b", "a>b", "(a)", "a\nb", "a\rb", "a\tb", "a\0b", "a\x01b", "end\\"])
def test_batch_ambiguous_values_rejected_before_job(monkeypatch, no_lookup, value):
    monkeypatch.setattr(windows.Job, "create", lambda *_: pytest.fail("invalid argv created a Job"))
    with pytest.raises(ValueError):
        windows.spawn_process([BATCH, value], env=ENV)


@pytest.mark.parametrize("program", [r"C:\a&b\tool.cmd", r"C:\a%b\tool.bat", r"C:\a!b\tool.cmd", r'C:\a"b\tool.cmd', "tool.cmd.", "tool.cmd "])
def test_batch_program_cannot_inject_or_hide_extension(no_lookup, program):
    with pytest.raises(ValueError):
        command.prepare([program], environment=ENV)


@pytest.mark.parametrize("argv", [[], [""], [b"tool.exe"], ["tool.exe", "a\0b"]])
def test_invalid_native_argv_is_explicit(no_lookup, argv):
    with pytest.raises(ValueError):
        command.prepare(argv, environment=ENV)


def test_native_argv_keeps_literal_special_characters(no_lookup):
    argv = [r"C:\Program Files\node.exe", 'a"b', "", "a&b", "%PATH%", "!value!", "two words", "end\\", "a\nb"]
    prepared, executable = command.prepare(argv, environment=ENV)
    assert prepared == argv and executable == argv[0]
    assert command.prepare(["alias", *argv[1:]], executable=argv[0], environment=ENV) == (["alias", *argv[1:]], argv[0])


def test_batch_requires_interpreter_and_bounded_length(monkeypatch, no_lookup):
    monkeypatch.delenv("SystemRoot", raising=False)
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    with pytest.raises(ValueError, match="SystemRoot"):
        command.prepare([BATCH], environment={})
    with pytest.raises(ValueError, match="limit"):
        command.prepare([BATCH, "a" * 8191], environment=ENV)
    with pytest.raises(ValueError, match="override"):
        command.prepare([BATCH], executable=BATCH, environment=ENV)


@pytest.mark.parametrize("system_root", ["Windows", r"\Windows", "C:Windows"])
def test_batch_rejects_drive_relative_interpreter(no_lookup, system_root):
    with pytest.raises(ValueError, match="SystemRoot"):
        command.prepare([BATCH], environment={"SystemRoot": system_root})


@pytest.mark.parametrize("args,kwargs", [
    ((BATCH,), {}), (([BATCH],), {"shell": True}),
    (([BATCH], 0), {}), (([BATCH],), {"args": [BATCH]}), ((), {}),
])
def test_shell_strings_and_positional_options_rejected_before_job(monkeypatch, no_lookup, args, kwargs):
    monkeypatch.setattr(windows.Job, "create", lambda *_: pytest.fail("invalid command created a Job"))
    with pytest.raises((ValueError, TypeError)):
        windows.spawn_process(*args, env=ENV, **kwargs)


def _fake_launch(monkeypatch, *, exit_code=0, expire=False, backend=windows):
    events = []

    class Job:
        handle = 123
        running = True
        def active(self):
            events.append("count")
            return int(self.running)
        def terminate(self):
            events.append("terminate-tree")
            self.running = False
        def close(self):
            events.append("close-job")

    job = Job()

    class Process:
        pid = 345
        _handle = 456
        returncode = None
        stdin = stdout = stderr = None
        def poll(self):
            return self.returncode
        def wait(self, timeout=None):
            if expire and job.running:
                raise subprocess.TimeoutExpired(self.args, timeout)
            self.returncode = exit_code
            return self.returncode
        def communicate(self, data=None):
            return "out", "err"
        def kill(self):
            events.append("kill-root")

    def popen(argv, **kwargs):
        assert kwargs["executable"] == CMD
        assert kwargs["shell"] is False
        assert isinstance(argv, str) and " /d /s /v:off /c " in argv
        assert kwargs["creationflags"] & backend.CREATE_SUSPENDED
        events.append("spawn-suspended")
        return Process()

    monkeypatch.setattr(backend.Job, "create", lambda *_: events.append("create-job") or job)
    monkeypatch.setattr(backend.subprocess, "Popen", popen)
    monkeypatch.setattr(backend, "_api", lambda: SimpleNamespace(AssignProcessToJobObject=lambda *_: events.append("assign") or 1))
    monkeypatch.setattr(backend, "_resume", lambda _: events.append("resume"))
    return events, job


def test_dispatch_assigns_job_before_batch_can_run(monkeypatch, no_lookup):
    events, job = _fake_launch(monkeypatch)
    process = windows.spawn_process(args=[BATCH, "--version"], env=ENV)
    assert events == ["create-job", "spawn-suspended", "assign", "resume"]
    assert process.args == [BATCH, "--version"]
    windows.terminate_process_tree(process, force=True)
    assert "terminate-tree" in events and "count" in events and not job.running


@pytest.mark.parametrize("exit_code,expire", [(0, False), (7, False), (0, True)])
def test_run_batch_preserves_receipt_and_cleans_entire_job(monkeypatch, no_lookup, exit_code, expire):
    events, job = _fake_launch(monkeypatch, exit_code=exit_code, expire=expire)
    monkeypatch.setattr(runtime, "_backend", lambda: windows)
    argv = [BATCH, "--version"]
    if expire:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            runtime.run_process(argv, env=ENV, timeout=.01, text=True, capture_output=True)
        assert caught.value.cmd == argv and caught.value.output == b"out"
    elif exit_code:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            runtime.run_process(argv, env=ENV, check=True, text=True, capture_output=True)
        assert caught.value.cmd == argv and caught.value.returncode == 7
    else:
        result = runtime.run_process(argv, env=ENV, check=True, text=True, capture_output=True)
        assert result.args == argv and result.stdout == "out"
    assert events[:4] == ["create-job", "spawn-suspended", "assign", "resume"]
    assert "terminate-tree" in events and "count" in events and not job.running


def test_bootstrap_installs_use_common_dispatch_without_installing(monkeypatch, tmp_path):
    scope = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/bootstrap_portal"))
    calls = []
    scope["run"].__globals__["run_process"] = lambda *args, **kwargs: calls.append((args, kwargs))
    scope["run"]([BATCH, "install"], cwd=tmp_path, dry_run=True)
    assert not calls
    scope["run"]([BATCH, "install"], cwd=tmp_path)
    assert calls == [(([BATCH, "install"],), {"cwd": tmp_path, "check": True})]


def test_original_snapshot_lacks_explicit_batch_dispatch(monkeypatch, no_lookup):
    """Optional negative replay: set only in the review command, never in CI."""
    path = os.environ.get("PORTAL_BATCH_BEFORE")
    if not path:
        pytest.skip("original snapshot supplied only for negative replay")
    spec = importlib.util.spec_from_file_location("management_common._batch_before", path)
    before = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(before)
    _fake_launch(monkeypatch, backend=before)
    # The old implementation reaches Popen without selecting cmd.exe. Keep the
    # failed contract as evidence while avoiding any real process creation.
    with pytest.raises(KeyError, match="executable") as caught:
        before.spawn_process([BATCH, "--version"], env=ENV)
    print("Original snapshot fails explicit dispatch contract:", repr(caught.value))


@pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows cmd.exe and Job API")
@pytest.mark.parametrize("suffix", [".cmd", ".bat"])
def test_native_batch_literal_safe_argv(tmp_path, suffix):
    folder = tmp_path / "directory with spaces"
    folder.mkdir()
    script = folder / "capture.py"
    script.write_text("import json,sys; print(json.dumps(sys.argv[1:]))", encoding="utf-8")
    wrapper = folder / ("wrapper" + suffix)
    wrapper.write_text('@"%PORTAL_TEST_PYTHON%" "%PORTAL_TEST_SCRIPT%" %*\n', encoding="utf-8")
    environment = dict(os.environ, PORTAL_TEST_PYTHON=sys.executable, PORTAL_TEST_SCRIPT=str(script))
    argv = ["", "two words", "中文", r"a\b"]
    result = runtime.run_process([str(wrapper), *argv], env=environment, capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(result.stdout) == argv


@pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows CreateProcess and Job API")
def test_native_executable_preserves_unrestricted_argv():
    argv = ['a"b', "", "a&b", "%PATH%", "!value!", "two words", "end\\", "a\nb"]
    result = runtime.run_process([sys.executable, "-c", "import json,sys; print(json.dumps(sys.argv[1:]))", *argv],
                                 capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(result.stdout) == argv


@pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows cmd.exe and Job API")
@pytest.mark.parametrize("expire", [False, True])
def test_native_batch_cleans_descendants_after_exit_or_timeout(tmp_path, expire):
    from tests.cases.test_process_runtime import tree_command, wait_file
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "tree.py"
    script.write_text(tree_command(pid_file, exit_parent=not expire)[2], encoding="utf-8")
    wrapper = tmp_path / "tree.cmd"
    wrapper.write_text('@"%PORTAL_TEST_PYTHON%" "%PORTAL_TEST_SCRIPT%"\n', encoding="utf-8")
    environment = dict(os.environ, PORTAL_TEST_PYTHON=sys.executable, PORTAL_TEST_SCRIPT=str(script))
    if expire:
        with pytest.raises(subprocess.TimeoutExpired):
            runtime.run_process([str(wrapper)], env=environment, timeout=3, capture_output=True)
    else:
        runtime.run_process([str(wrapper)], env=environment, timeout=10, capture_output=True, check=True)
    pid = wait_file(pid_file)
    # Job termination is asynchronous: the process object's final signal can
    # follow its removal from the Job. A surviving child still fails this bound.
    deadline = time.monotonic() + 2
    while runtime.process_alive(pid) and time.monotonic() < deadline:
        time.sleep(.01)
    assert not runtime.process_alive(pid)
