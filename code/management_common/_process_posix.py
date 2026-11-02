"""Linux/macOS process groups. Never imported by the Windows runtime."""
from __future__ import annotations

import os
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time


def spawn_options():
    return {"start_new_session": True}


def spawn_process(*args, tree_id=None, kill_on_owner_exit=True, lifetime_registered=None, **kwargs):
    if kwargs.get("start_new_session") is False or kwargs.get("process_group") not in (None, -1):
        raise ValueError("Owned process trees require a new session")
    kwargs.update(spawn_options())
    if lifetime_registered is not None:
        return _spawn_registered(args, kwargs, lifetime_registered)
    process = subprocess.Popen(*args, **kwargs)
    process._portal_tree_stopped = False
    try:
        process._portal_lifetime_receipt = lifetime_receipt(process.pid)
    except BaseException:
        _rollback_spawn(process)
        raise
    return process


# The child cannot execute caller code until its identity is durably recorded
# in every inherited lease. EOF (including parent SIGKILL) exits without effects.
# This exec gate uses no preexec_fn in the multithreaded service process.
_EXEC_GATE = '''import os, sys, json
fd = int(sys.argv[1])
error_fd = int(sys.argv[2])
os.set_inheritable(error_fd, False)
permit = os.read(fd, 1)
os.close(fd)
if permit != b'1': os._exit(125)
executable, command = json.loads(sys.argv[3])
try:
    os.execvpe(executable, command, os.environ)
except OSError as error:
    os.write(error_fd, json.dumps([error.errno, str(error)]).encode())
    os._exit(127)
'''


def _spawn_registered(args, kwargs, register):
    if len(args) > 1 or (args and 'args' in kwargs):
        raise TypeError('Pass process options as keywords after argv')
    command = args[0] if args else kwargs.pop('args')
    original = command
    if isinstance(command, (str, bytes, os.PathLike)):
        command = [os.fsdecode(command)]
    else:
        command = [os.fsdecode(value) for value in command]
    executable = kwargs.pop('executable', None)
    if kwargs.pop('shell', False):
        command = [os.fsdecode(executable or '/bin/sh'), '-c', *command]
        executable = command[0]
    executable = os.fsdecode(executable or command[0])
    if kwargs.get('preexec_fn') is not None:
        raise ValueError('Execution leases require effects after the native launch gate')
    read_fd, write_fd = os.pipe()
    error_read, error_write = os.pipe()
    process = None
    try:
        kwargs['pass_fds'] = (*kwargs.get('pass_fds', ()), read_fd, error_write)
        process = subprocess.Popen([sys.executable, '-I', '-S', '-c', _EXEC_GATE,
                                    str(read_fd), str(error_write), json.dumps([executable, command])], **kwargs)
        os.close(error_write)
        error_write = None
        process.args = original
        process._portal_tree_stopped = False
        process._portal_lifetime_receipt = lifetime_receipt(process.pid)
        register(process._portal_lifetime_receipt)
        os.write(write_fd, b'1')
        error = os.read(error_read, 65536)
        if error:
            number, message = json.loads(error)
            raise OSError(number, message, executable)
        return process
    except BaseException:
        if process is not None:
            _rollback_spawn(process)
        raise
    finally:
        os.close(read_fd)
        os.close(write_fd)
        os.close(error_read)
        if error_write is not None:
            os.close(error_write)


def _rollback_spawn(process):
    try:
        terminate_process_tree(process, force=True)
    except BaseException as error:
        from .process_runtime import ProcessCleanupError
        raise ProcessCleanupError('POSIX process creation rollback is unconfirmed') from error


def lifetime_receipt(pid):
    receipt = dict(version=1, platform=sys.platform, host=socket.gethostname(), pgid=pid)
    if sys.platform == 'linux':
        receipt.update(boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                       namespace=os.readlink('/proc/self/ns/pid'), start=_linux_stat(pid)[19])
    else:
        import psutil
        receipt['start'] = psutil.Process(pid).create_time()
    return receipt


def lifetime_alive(receipt):
    pid = receipt.get('pgid')
    if receipt.get('host') != socket.gethostname() or type(pid) is not int or pid <= 1 or not receipt.get('start'):
        return True
    if sys.platform == 'linux':
        if (not receipt.get('boot') or not receipt.get('namespace')
                or not isinstance(receipt['start'], str) or not receipt['start'].isdigit()):
            return True
        if receipt['boot'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
            return False
        if receipt['namespace'] != os.readlink('/proc/self/ns/pid'):
            return True
        current = _linux_stat(pid)
        if current and current[19] != receipt['start']:
            # The kernel cannot reuse a group ID while that group still has
            # members. This newly allocated PID therefore proves the old end.
            return False
    else:
        import psutil
        try:
            if psutil.Process(pid).create_time() != receipt['start']:
                return False
        except psutil.NoSuchProcess:
            pass
    return _group_alive(pid)


def _linux_stat(pid):
    try:
        # comm may itself contain spaces and closing parentheses.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if sys.platform == "linux":
        try:
            fields = _linux_stat(pid)
            return bool(fields and fields[0] not in {"Z", "X"})
        except PermissionError:
            return True
    result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True,
                            text=True, timeout=15)
    if result.returncode not in (0, 1):
        result.check_returncode()
    return bool(result.stdout.strip() and not result.stdout.strip().startswith("Z"))


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    # killpg(0) includes zombies. Do not report a dead group as an active tree
    # while waiting for an external reaper (notably container PID 1).
    if sys.platform == "linux":
        observed = False
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = _linux_stat(entry.name)
            except PermissionError:
                return True  # Cannot prove the group empty.
            if fields and int(fields[2]) == pgid:
                observed = True
                if fields[0] not in {"Z", "X"}:
                    return True
        # The kernel reported an existing group. An empty/hidden proc view
        # cannot certify its end; only an observed zombie-only group can.
        return not observed
    result = subprocess.run(["ps", "-axo", "pgid=,stat="], capture_output=True, text=True,
                            check=True, timeout=15)
    states = [parts[1] for line in result.stdout.splitlines()
              if len(parts := line.split()) >= 2 and int(parts[0]) == pgid]
    return not states or any(not state.startswith('Z') for state in states)


def process_tree_alive(process, *, tree_id=None):
    pid = process if isinstance(process, int) else getattr(process, "pid", None)
    if type(pid) is not int or pid <= 1:
        raise ValueError("An owned process-group identity is required")
    receipt = getattr(process, '_portal_lifetime_receipt', None)
    if receipt is not None:
        return lifetime_alive(receipt)
    return _group_alive(pid)


def terminate_process_tree(process, timeout=15, force=False, *, tree_id=None):
    if process is None:
        return
    managed = not isinstance(process, int)
    if managed and getattr(process, "_portal_tree_stopped", False):
        return
    receipt = getattr(process, '_portal_lifetime_receipt', None)
    if receipt is not None and not lifetime_alive(receipt):
        process.wait(timeout=max(timeout, .1))
        process._portal_tree_stopped = True
        return
    pid = process.pid if managed else process
    if not isinstance(pid, int) or pid <= 1 or pid == os.getpgrp():
        raise ValueError("Refusing to signal an unowned process group")
    # If the leader still exists, reject a caller that did not isolate it.
    try:
        if os.getpgid(pid) != pid:
            raise ValueError("Process is not the leader of an owned process group")
    except ProcessLookupError:
        pass
    def send(signum):
        try:
            os.killpg(pid, signum)
        except ProcessLookupError:
            pass
    def wait_empty(seconds):
        deadline = time.monotonic() + seconds
        while True:
            if managed:
                process.poll()  # Reap our leader as soon as it exits.
            if not _group_alive(pid):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(.02)
    send(signal.SIGKILL if force else signal.SIGTERM)
    if not wait_empty(timeout):
        if not force:
            send(signal.SIGKILL)
        if not wait_empty(max(timeout, .1)):
            raise TimeoutError(f"Process group {pid} did not exit")
    if managed:
        process.wait(timeout=max(timeout, .1))
        process._portal_tree_stopped = True
