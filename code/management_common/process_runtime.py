"""Owned child-process trees; platform differences live behind this boundary.

Use spawn_process, not spawn_options alone, when tree cleanup is required.
Windows children are assigned to a Job before their first instruction executes.
Call terminate_process_tree even after the leader exits to release descendants.
"""
from __future__ import annotations

import importlib
import signal
import subprocess
import sys
import threading


class ProcessCleanupError(RuntimeError):
    """Process creation failed and rollback could not prove the tree empty."""


def _backend():
    if sys.platform in {"linux", "darwin"}:
        return importlib.import_module("management_common._process_posix")
    if sys.platform == "win32":
        return importlib.import_module("management_common._process_windows")
    raise NotImplementedError(f"Unsupported process platform: {sys.platform}")


def spawn_options() -> dict:
    return _backend().spawn_options()


def spawn_process(*args, tree_id=None, kill_on_owner_exit=True, **kwargs):
    """Create an owned tree; Windows requires argv and keyword Popen options.

    Windows .cmd/.bat commands use an explicit restricted cmd.exe dispatch.
    Quotes, shell metacharacters, controls and trailing backslashes in batch
    arguments are rejected before spawn. Use [native.exe, script, *argv] when
    arbitrary literal arguments are required; shell=True is not supported there.
    """
    return _backend().spawn_process(*args, tree_id=tree_id,
                                   kill_on_owner_exit=kill_on_owner_exit, **kwargs)


def terminate_process_tree(process, timeout=15, force=False, *, tree_id=None):
    if timeout < 0:
        raise ValueError("timeout must be nonnegative")
    return _backend().terminate_process_tree(process, timeout=timeout, force=force, tree_id=tree_id)


def process_alive(pid):
    return _backend().process_alive(pid)


def process_tree_alive(process=None, *, tree_id=None):
    """Read owned group/Job liveness, including descendants of an exited root.

    Permission and identity errors propagate; callers must not turn an
    unavailable observation into proof of an empty tree. No signal is sent.
    """
    return _backend().process_tree_alive(process, tree_id=tree_id)


def install_shutdown_handlers(callback, *, reload=None, restart=None):
    _backend()  # Refuse unknown platforms before changing process state.
    handlers = {signal.SIGINT: callback, signal.SIGTERM: callback}
    for name, handler in (("SIGBREAK", callback), ("SIGHUP", reload), ("SIGUSR1", restart)):
        if handler is not None and hasattr(signal, name):
            handlers[getattr(signal, name)] = handler
    previous = {}
    try:
        for signum, handler in handlers.items():
            previous[signum] = signal.signal(signum, handler)
    except BaseException:
        restore_shutdown_handlers(previous)
        raise
    return previous


def restore_shutdown_handlers(previous):
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def run_process(*args, input=None, capture_output=False, timeout=None, check=False, **kwargs):
    """subprocess.run with tree cleanup on success, timeout and exceptions."""
    if input is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used")
        kwargs["stdin"] = subprocess.PIPE
    if capture_output:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output")
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process = spawn_process(*args, **kwargs)
    output, errors = [], []
    def communicate():
        try:
            output.append(process.communicate(input))
        except BaseException as error:
            errors.append(error)
    reader = threading.Thread(target=communicate, name="process-output", daemon=True)
    try:
        reader.start()
        expired = None
        try:
            # communicate alone waits for inherited pipes even after the leader
            # exits. Reap the leader independently, then clean up its tree.
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            expired = error
        finally:
            terminate_process_tree(process, force=True)
        reader.join(15)
        if reader.is_alive():
            raise TimeoutError("Process output did not close after tree termination")
        if errors:
            raise errors[0]
        stdout, stderr = output[0]
        if expired is not None:
            # Match subprocess.TimeoutExpired's byte-output contract in text mode.
            encoding = getattr(process.stdout, "encoding", None) or "utf-8"
            expired.output = stdout.encode(encoding) if isinstance(stdout, str) else stdout
            expired.stderr = stderr.encode(encoding) if isinstance(stderr, str) else stderr
            raise expired
        completed = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        if check:
            completed.check_returncode()
        return completed
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not reader.is_alive():
                stream.close()
