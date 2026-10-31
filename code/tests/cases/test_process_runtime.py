"""Process ownership: native POSIX trees and Windows API contract failures."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from management_common import process_runtime as runtime
from management_common import _process_windows as windows


def wait_file(path):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return int(path.read_text())
        time.sleep(.01)
    raise AssertionError("child did not start")


def tree_command(path, *, exit_parent=False):
    child = "import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path(" + repr(str(path)) + ").write_text(str(os.getpid())); time.sleep(60)"
    parent = ("import subprocess,sys,time; from pathlib import Path; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); "
              "p=Path(" + repr(str(path)) + ");\nwhile not p.exists(): time.sleep(.01)\n" +
              ("print('done', flush=True)" if exit_parent else "time.sleep(60)"))
    return [sys.executable, "-c", parent]


@pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="POSIX native test")
@pytest.mark.parametrize("exit_parent", [False, True])
def test_owned_tree_kills_grandchild_even_after_parent_exit(tmp_path, exit_parent):
    path = tmp_path / "child.pid"
    process = runtime.spawn_process(tree_command(path, exit_parent=exit_parent), stdout=subprocess.DEVNULL)
    try:
        pid = wait_file(path)
        if exit_parent:
            process.wait(timeout=5)
        runtime.terminate_process_tree(process, timeout=.1)
        assert not runtime.process_alive(pid)
        runtime.terminate_process_tree(process, timeout=.1)
    finally:
        runtime.terminate_process_tree(process, timeout=.1, force=True)


@pytest.mark.parametrize("expire", [False, True])
def test_run_process_cleans_inherited_pipe_and_timeout_tree(tmp_path, expire):
    path = tmp_path / "pipe-holder.pid"
    command = tree_command(path, exit_parent=not expire)
    if expire:
        with pytest.raises(subprocess.TimeoutExpired):
            runtime.run_process(command, capture_output=True, timeout=1)
    else:
        result = runtime.run_process(command, capture_output=True, timeout=5, text=True)
        assert result.returncode == 0 and result.stdout == "done\n"
    pid = wait_file(path)
    # Windows Job termination is asynchronous; observe the process object's
    # final signal within a bounded interval, rather than one racing poll.
    deadline = time.monotonic() + 2
    while runtime.process_alive(pid) and time.monotonic() < deadline:
        time.sleep(.01)
    assert not runtime.process_alive(pid)


def test_run_process_preserves_error_and_text_contract():
    with pytest.raises(subprocess.CalledProcessError) as caught:
        runtime.run_process([sys.executable, "-c", "import sys; print('out'); print('err',file=sys.stderr); sys.exit(7)"],
                            capture_output=True, text=True, check=True)
    assert (caught.value.returncode, caught.value.stdout, caught.value.stderr) == (7, "out\n", "err\n")
    result = runtime.run_process([sys.executable, "-c", "import sys; print(sys.stdin.read())"],
                                 input="hello", capture_output=True, text=True, check=True)
    assert result.stdout == "hello\n"


def test_unknown_platform_rejected_without_spawn(monkeypatch):
    monkeypatch.setattr(sys, "platform", "plan9")
    with pytest.raises(NotImplementedError, match="plan9"):
        runtime.spawn_process(["never-start"])
    with pytest.raises(NotImplementedError):
        runtime.process_alive(1)


def test_shutdown_handlers_use_available_signals_and_restore(monkeypatch):
    calls = []
    monkeypatch.setattr(signal, "signal", lambda key, callback: calls.append((key, callback)) or "old")
    callback = lambda *_: None
    previous = runtime.install_shutdown_handlers(callback, reload=callback, restart=callback)
    runtime.restore_shutdown_handlers(previous)
    assert set(previous) >= {signal.SIGINT, signal.SIGTERM}
    assert all(value == "old" for _, value in calls[len(previous):])


class FakeJob:
    handle = 40
    def __init__(self, counts):
        self.counts = iter(counts)
        self.last = 0
        self.terminated = False
        self.closed = False
    def active(self):
        self.last = next(self.counts, self.last)
        return self.last
    def terminate(self):
        self.terminated = True
    def close(self):
        self.closed = True


def test_windows_tree_count_is_authoritative_after_leader_exit():
    job = FakeJob([1, 0])
    process = SimpleNamespace(_portal_job=job, poll=lambda: 0, wait=lambda **_: 0)
    windows.terminate_process_tree(process, timeout=.1)
    assert job.terminated and job.closed and process._portal_tree_stopped


def test_windows_does_not_claim_tree_exit_if_job_stays_active():
    job = FakeJob([1])
    process = SimpleNamespace(_portal_job=job, poll=lambda: 0, wait=lambda **_: 0)
    with pytest.raises(TimeoutError, match="active processes"):
        windows.terminate_process_tree(process, timeout=0, force=True)
    assert job.terminated and not job.closed


def test_windows_reopens_named_job_instead_of_using_recycled_pid(monkeypatch):
    job = FakeJob([0])
    names = []
    monkeypatch.setattr(windows.Job, "open", lambda name: names.append(name) or job)
    windows.terminate_process_tree(991, tree_id="run-identity", force=True)
    assert names == ["run-identity"] and job.terminated and job.closed


def test_windows_rejects_unowned_pid():
    with pytest.raises(ValueError, match="tree_id"):
        windows.terminate_process_tree(991)


def test_windows_assigns_suspended_child_before_resume(monkeypatch):
    events = []
    job = FakeJob([0])
    class Process:
        pid = 567
        _handle = 99
    def popen(*args, **kwargs):
        assert kwargs["creationflags"] & windows.CREATE_SUSPENDED
        assert {key.upper(): value for key, value in kwargs["env"].items()}["SYSTEMROOT"] == "C:/Windows"
        assert "SECRET_PROVIDER_KEY" not in kwargs["env"]
        events.append("spawn-suspended")
        return Process()
    monkeypatch.setattr(windows.Job, "create", lambda *args: job)
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    monkeypatch.setenv("SECRET_PROVIDER_KEY", "must-not-inherit")
    monkeypatch.setattr(windows.subprocess, "Popen", popen)
    monkeypatch.setattr(windows, "_api", lambda: SimpleNamespace(AssignProcessToJobObject=lambda *args: events.append("assign") or 1))
    monkeypatch.setattr(windows, "_resume", lambda pid: events.append("resume"))
    process = windows.spawn_process(["child"], env={"PATH": "selected-path"})
    assert events == ["spawn-suspended", "assign", "resume"]
    process._portal_job_finalizer()


def test_windows_assignment_failure_kills_suspended_child(monkeypatch):
    events = []
    job = FakeJob([0])
    process = SimpleNamespace(pid=567, _handle=99, kill=lambda: events.append("kill"), wait=lambda **kwargs: events.append("wait"))
    monkeypatch.setattr(windows.Job, "create", lambda *args: job)
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *args, **kwargs: process)
    def failed(*args):
        raise PermissionError("Job assignment denied")
    monkeypatch.setattr(windows, "_api", lambda: SimpleNamespace(AssignProcessToJobObject=failed))
    monkeypatch.setattr(windows, "_resume", lambda _: pytest.fail("unowned child resumed"))
    with pytest.raises(PermissionError):
        windows.spawn_process(["child"])
    assert events == ["kill", "wait"] and job.closed


@pytest.mark.parametrize("inherited,same_tree", [(False, False), (True, False), (True, True)])
def test_windows_successor_must_leave_callers_tree(monkeypatch, inherited, same_tree):
    events = []
    job = FakeJob([0])
    class Process:
        pid = 567
        _handle = 99
        def kill(self):
            events.append("kill")
        def wait(self, **kwargs):
            events.append("wait")
    def popen(*args, **kwargs):
        assert kwargs["creationflags"] & windows.CREATE_BREAKAWAY_FROM_JOB
        assert kwargs["creationflags"] & windows.CREATE_SUSPENDED
        return Process()
    def in_job(handle, ancestor, result):
        assert ancestor is None
        result._obj.value = inherited
        return 1
    monkeypatch.setattr(windows.Job, "create", lambda *args: job)
    monkeypatch.setattr(windows.subprocess, "Popen", popen)
    monkeypatch.setattr(windows, "_api", lambda: SimpleNamespace(
        IsProcessInJob=in_job, AssignProcessToJobObject=lambda *args: events.append("assign") or 1))
    monkeypatch.setattr(windows, "_in_current_job", lambda pid: same_tree)
    monkeypatch.setattr(windows, "_resume", lambda _: events.append("resume"))
    if inherited and same_tree:
        with pytest.raises(PermissionError, match="Host Job"):
            windows.spawn_process(["successor"], kill_on_owner_exit=False)
        assert events == ["kill", "wait"] and job.closed
    else:
        process = windows.spawn_process(["successor"], kill_on_owner_exit=False)
        assert events == ["assign", "resume"]
        process._portal_job_finalizer()


@pytest.mark.parametrize("kill_on_close", [False, True])
def test_windows_job_allows_explicit_but_not_silent_breakaway(monkeypatch, kill_on_close):
    limits = []
    monkeypatch.setattr(windows.ctypes, "get_last_error", lambda: 0, raising=False)
    monkeypatch.setattr(windows, "_api", lambda: SimpleNamespace(
        CreateJobObjectW=lambda *args: 40,
        SetInformationJobObject=lambda handle, kind, value, size: limits.append(value._obj.BasicLimitInformation.LimitFlags) or 1,
        CloseHandle=lambda *args: 1))
    job = windows.Job.create("successor", kill_on_close)
    assert limits == [windows.BREAKAWAY_OK | (windows.KILL_ON_JOB_CLOSE if kill_on_close else 0)]
    job.close()


@pytest.mark.parametrize("member", [False, True])
def test_windows_job_membership_retries_a_growing_process_list(monkeypatch, member):
    capacities = []
    def query(job, kind, result, size, returned):
        assert job is None and kind == 3
        info = result._obj
        capacities.append(len(info.pids))
        if len(capacities) == 1:
            info.assigned = 130
            return 0
        info.count = 2
        info.pids[0], info.pids[1] = 10, 567 if member else 20
        return 1
    monkeypatch.setattr(windows, "_api", lambda: SimpleNamespace(QueryInformationJobObject=query))
    monkeypatch.setattr(windows.ctypes, "get_last_error", lambda: 234, raising=False)
    assert windows._in_current_job(567) is member
    assert capacities == [64, 130]


def test_windows_job_membership_denied_does_not_mean_detached(monkeypatch):
    monkeypatch.setattr(windows, "_api", lambda: SimpleNamespace(QueryInformationJobObject=lambda *args: 0))
    monkeypatch.setattr(windows.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(windows.ctypes, "WinError", lambda code: PermissionError(code), raising=False)
    with pytest.raises(PermissionError):
        windows._in_current_job(567)


@pytest.mark.parametrize("state,alive", [(0, False), (258, True)])
def test_windows_liveness_uses_handle_state_not_exit_code(monkeypatch, state, alive):
    closed = []
    monkeypatch.setattr(windows, "_api", lambda: SimpleNamespace(
        OpenProcess=lambda *args: 123, WaitForSingleObject=lambda *args: state,
        CloseHandle=lambda handle: closed.append(handle)))
    assert windows.process_alive(456) is alive
    assert closed == [123]


def test_windows_permission_denied_does_not_mean_dead(monkeypatch):
    monkeypatch.setattr(windows.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(windows, "_api", lambda: SimpleNamespace(OpenProcess=lambda *args: 0))
    assert windows.process_alive(456)


def test_windows_dispatch_does_not_import_posix_backend(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert runtime._backend() is windows
    assert runtime.spawn_options() == {"creationflags": windows.CREATE_NEW_PROCESS_GROUP}


def test_macos_liveness_uses_ps_without_proc(monkeypatch):
    from management_common import _process_posix as posix
    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(posix.os, "kill", lambda *args: None)
    monkeypatch.setattr(posix, "_linux_stat", lambda _: pytest.fail("macOS accessed /proc"))
    def ps(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "Z\n", "")
    monkeypatch.setattr(posix.subprocess, "run", ps)
    assert not runtime.process_alive(42)
    assert calls == [["ps", "-p", "42", "-o", "stat="]]


def test_posix_refuses_to_kill_callers_process_group():
    if sys.platform == "win32":
        pytest.skip("POSIX group safety")
    with pytest.raises(ValueError, match="unowned process group"):
        runtime.terminate_process_tree(os.getpgrp())
