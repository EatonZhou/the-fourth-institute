"""Article build lifecycle using disposable Python children, never real TeX."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_common import process_runtime
from management_portal import article_build


@pytest.fixture
def build(tmp_path, monkeypatch):
    article_id = "article_111111111111"
    processes = []
    original = subprocess.Popen

    def track(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    # Track real children so a regression cannot leave the test's helpers alive.
    monkeypatch.setattr(subprocess, "Popen", track)
    monkeypatch.setattr(article_build, "get_resource_service", lambda: SimpleNamespace(
        is_valid_id=lambda value, kind: value == article_id and kind == "article"))
    article_build._begin_article_build(article_id)
    try:
        yield article_id, tmp_path, processes
    finally:
        for process in processes:
            process_runtime.terminate_process_tree(process, timeout=1, force=True)
        article_build._finish_article_build(article_id)


def run(build, code, timeout=5, **kwargs):
    article_id, root, _ = build
    return article_build._run_article_build_command(
        article_id, [sys.executable, "-u", "-c", code],
        cwd=root, timeout_seconds=timeout, **kwargs)


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "fixture did not become ready"
        time.sleep(.01)


def descendant_code(root, *, leader_exits):
    # The descendant inherits stdout; EOF alone must not own build completion.
    marker = root / "descendant.pid"
    child = (
        "import os,time; from pathlib import Path; "
        f"Path({str(marker)!r}).write_text(str(os.getpid())); "
        "print('descendant-ready', flush=True); time.sleep(30)"
    )
    return (
        "import subprocess,sys,time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable, '-u', '-c', {child!r}]); "
        f"marker=Path({str(marker)!r})\n"
        "while not marker.exists(): time.sleep(.01)\n"
        "print('leader-ready', flush=True)\n"
        + ("" if leader_exits else "time.sleep(30)\n")
    )


@pytest.mark.parametrize("returncode", [0, 7])
def test_result_keeps_output_cwd_environment_and_exit_code(build, returncode):
    code = (
        "import os,sys; from pathlib import Path; "
        "print(os.environ['ARTICLE_TEST_VALUE']); "
        "print(Path.cwd().name); print('diagnostic', file=sys.stderr); "
        f"sys.exit({returncode})"
    )
    completed = run(build, code, env={**os.environ, "ARTICLE_TEST_VALUE": "编译✓"})
    assert completed.returncode == returncode
    assert completed.stdout == f"编译✓\n{build[1].name}\ndiagnostic\n"
    assert completed.stderr is None
    assert build[0] not in article_build._ACTIVE_ARTICLE_BUILDS


def test_normal_leader_exit_reaps_descendant_with_inherited_output(build):
    completed = run(build, descendant_code(build[1], leader_exits=True), timeout=1)
    assert completed.returncode == 0
    assert "leader-ready\n" in completed.stdout
    assert not process_runtime.process_alive(int((build[1] / "descendant.pid").read_text()))


def test_stop_helper_reaps_descendant_after_leader_exit(build):
    process = process_runtime.spawn_process(
        [sys.executable, "-u", "-c", descendant_code(build[1], leader_exits=True)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process.wait(timeout=5)
    child = int((build[1] / "descendant.pid").read_text())
    assert process_runtime.process_alive(child)
    article_build._terminate_article_process(process)
    assert not process_runtime.process_alive(child)


def test_timeout_preserves_partial_output_once_and_cleans_tree(build):
    code = descendant_code(build[1], leader_exits=False)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run(build, code, timeout=1)
    assert caught.value.cmd == [sys.executable, "-u", "-c", code]
    assert caught.value.timeout == 1
    assert caught.value.stdout.count("leader-ready\n") == 1
    assert not process_runtime.process_alive(int((build[1] / "descendant.pid").read_text()))
    assert build[0] not in article_build._ACTIVE_ARTICLE_BUILDS


def test_stop_retains_partial_output_and_rejects_concurrent_same_article(build):
    result = []

    def worker():
        try:
            result.append(run(build, descendant_code(build[1], leader_exits=False)))
        except BaseException as error:
            result.append(error)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        wait_for(lambda: (build[1] / "descendant.pid").exists())
        with pytest.raises(RuntimeError, match="already running"):
            article_build._begin_article_build(build[0])
        assert article_build.stop_article_compile(build[0]) == {
            "id": build[0], "running": True, "stop_requested": True}
        thread.join(8)
        assert not thread.is_alive()
        assert len(result) == 1 and isinstance(result[0], article_build.ArticleCompilationStopped)
        assert "descendant-ready\n" in result[0].output
        assert not process_runtime.process_alive(int((build[1] / "descendant.pid").read_text()))
        assert build[0] not in article_build._ACTIVE_ARTICLE_BUILDS
        assert build[0] in article_build._STOP_REQUESTED_ARTICLE_BUILDS
    finally:
        for process in build[2]:
            process_runtime.terminate_process_tree(process, timeout=1, force=True)
        thread.join(5)


def test_stop_before_spawn_is_not_lost(build):
    article_build.stop_article_compile(build[0])
    with pytest.raises(article_build.ArticleCompilationStopped):
        run(build, "import time; time.sleep(30)")
    assert build[0] not in article_build._ACTIVE_ARTICLE_BUILDS


def test_spawn_failure_keeps_stop_and_running_state_for_outer_owner(build):
    with pytest.raises(FileNotFoundError):
        article_build._run_article_build_command(
            build[0], [str(build[1] / "missing-executable")], cwd=build[1], timeout_seconds=1)
    assert build[0] not in article_build._ACTIVE_ARTICLE_BUILDS
    assert build[0] in article_build._RUNNING_ARTICLE_BUILDS


@pytest.mark.parametrize("outcome", ["normal", "stop", "timeout", "wait_error", "cleanup_error"])
def test_owned_runtime_contract_without_posix_process_identity(build, monkeypatch, outcome):
    """A Job-backed consumer needs only wait/returncode, never pid or killpg.

    This is a platform contract double, not a native Windows/macOS execution.
    """
    cleanup_calls = []
    streams = []

    class OwnedProcess:
        returncode = None

        def wait(self, timeout):
            if outcome == "wait_error":
                raise OSError("wait unavailable")
            if outcome == "stop":
                # Repeated requests only set the shared flag; one owner cleans.
                article_build.stop_article_compile(build[0])
                article_build.stop_article_compile(build[0])
                raise subprocess.TimeoutExpired("fixture", timeout)
            if outcome == "timeout":
                time.sleep(timeout)
                raise subprocess.TimeoutExpired("fixture", timeout)
            self.returncode = 0
            return 0

    process = OwnedProcess()

    def spawn(command, **kwargs):
        assert "start_new_session" not in kwargs
        streams.append(kwargs["stdout"])
        streams[0].write("partial\n")
        return process

    def terminate(owned, **kwargs):
        assert owned is process
        cleanup_calls.append(owned)
        if outcome == "cleanup_error":
            raise PermissionError("tree cleanup unconfirmed")
        process.returncode = 0 if outcome == "normal" else 1
        streams[0].write("shutdown\n")

    monkeypatch.setattr(process_runtime, "_backend", lambda: SimpleNamespace(
        spawn_process=spawn, terminate_process_tree=terminate))
    if outcome == "normal":
        assert run(build, "fixture").stdout == "partial\nshutdown\n"
    elif outcome == "stop":
        with pytest.raises(article_build.ArticleCompilationStopped) as caught:
            run(build, "fixture")
        assert caught.value.output == "partial\nshutdown\n"
    elif outcome == "timeout":
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            run(build, "fixture", timeout=1)
        assert caught.value.stdout == "partial\nshutdown\n"
    else:
        expected = "wait unavailable" if outcome == "wait_error" else "tree cleanup unconfirmed"
        with pytest.raises(OSError, match=expected):
            run(build, "fixture")
    assert len(cleanup_calls) == 1
    assert streams[0].closed
    assert build[0] not in article_build._ACTIVE_ARTICLE_BUILDS
