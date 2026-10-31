"""Current-user Windows component supervision; SCM remains an explicit option.

Definition files belong to the installer. Process identity and stop requests
belong to the instance. No password, elevation or global service is needed.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
from contextlib import contextmanager
from functools import lru_cache
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

DEFINITION_ENV = "TFI_SERVICE_DEFINITION"
RESTART_DELAY_SECONDS = 3
RESTART_DELAY_MAX_SECONDS = 60
RESTART_RESET_SECONDS = 60
REVIVE_INTERVAL_SECONDS = 60
REVIVE_DELAY_MAX_SECONDS = 1800
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _record_operation(path, operation):
    """Allow brief sharing/delete-pending windows, never assume access denied = absent."""
    deadline = time.monotonic() + 2
    while True:
        try:
            return operation()
        except PermissionError as exc:
            if not exc.filename:
                exc.filename = str(path)
            if (getattr(exc, "winerror", None) not in (5, 32, 33)
                    or time.monotonic() >= deadline):
                raise
            time.sleep(0.05)


@lru_cache(maxsize=1)
def _record_api():
    from ctypes import wintypes as w
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                               w.DWORD, w.DWORD, w.HANDLE]
    api.CreateFileW.restype = w.HANDLE
    api.CloseHandle.argtypes = [w.HANDLE]
    api.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    api.GetFileInformationByHandleEx.restype = w.BOOL
    return api


@contextmanager
def _open_record(path):
    if os.name != "nt":
        with path.open(encoding="utf-8") as stream:
            yield stream
        return
    import msvcrt
    from ctypes import wintypes as w
    api = _record_api()
    # Permit deletion while reading snapshots. Windows MoveFileEx may still
    # require readers to close before replacement; the writer retries briefly.
    # Private-file and pinned-resource handles intentionally have other policies.
    handle = api.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = (w.DWORD * 2)()
        if not api.GetFileInformationByHandleEx(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
        if attributes[0] & (0x400 | 0x10):
            raise ValueError("Service record must be a regular, non-reparse file")
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        api.CloseHandle(handle)
        raise
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        yield stream


def read_record(path):
    def read():
        with _open_record(path) as stream:
            return stream.read()
    return _record_operation(path, read)


def write_record(path, text):
    # Publish only closed, complete files. Never truncate a live process identity.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        _record_operation(path, lambda: temporary.replace(path))
    finally:
        remove_record(temporary)


def remove_record(path):
    _record_operation(path, lambda: path.unlink(missing_ok=True))


def validate_definition(value, name):
    from management_common.instance_config import read_installation
    codex = value.get("component") == "codex"
    pattern = r"portal-codex-app-server@[A-Za-z0-9_-]+-[a-f0-9]{10}\.service" if codex else r"tfi-[a-z0-9-]+"
    if value.get("name") != name or not re.fullmatch(pattern, name):
        raise ValueError("Invalid user service identity")
    if value.get("mode") != "user" or value.get("component") not in (
            "postgres", "nats", "resource", "execution", "society", "worker", "web", "codex", "claude", "kimi"):
        raise ValueError("Invalid user service component")
    for key in ("root", "code", "python"):
        path = Path(value[key])
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("User service paths must be absolute and not symlinks")
    root = Path(value["root"]).resolve()
    target = read_installation(root)["target"]
    if codex:
        from scripts.tfi_instance_layout import code_root
        from scripts.tfi_release_dependencies import runtime_python
        command = value.get("command", [])
        if (target.get("windows_service_mode") != "user"
                or Path(target["install_root"]).resolve() != root
                or Path(value["code"]).resolve() != code_root(target).resolve()
                or Path(value["python"]).resolve() != Path(runtime_python(target)).resolve()
                or not isinstance(command, list) or len(command) != 9
                or not all(isinstance(arg, str) and not any(c in arg for c in "\0\r\n") for arg in command)
                or command[:3] != [value["python"], "-m", "management_portal.codex_app_server_host"]
                or command[3::2] != ["--socket", "--binary", "--account-config"]
                or not all(Path(arg).is_absolute() for arg in command[4::2])):
            raise ValueError("Codex user service ownership mismatch")
        launch = Path(command[-1])
        account_id = launch.parent.name
        scope = hashlib.sha256(os.fsencode(Path(target["data_roots"]["instances_root"]).resolve())).hexdigest()[:10]
        if (launch != root / "runtime/provider-accounts/codex" / account_id / "launch.json"
                or Path(command[4]).resolve() != (Path(target["data_roots"]["instances_root"]) / "sockets" / f"codex-{account_id}.sock").resolve()
                or name != f"portal-codex-app-server@{account_id}-{scope}.service"):
            raise ValueError("Codex user service account ownership mismatch")
    else:
        from scripts.tfi_instance_layout import code_root
        if (name != target["service_prefix"] + "-" + value["component"]
                or target.get("windows_service_mode") != "user"
                or Path(target["install_root"]).resolve() != root
                or Path(value["code"]).resolve() != code_root(target).resolve()
                or Path(value["python"]).resolve() != Path(target["python"]).resolve()):
            raise ValueError("User service does not belong to this installation")
    return value


def process_stamp(pid):
    """Windows creation FILETIME prevents recycled PIDs from being trusted."""
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    handle = api.OpenProcess(0x1000 | 0x100000, False, pid)  # QUERY | SYNCHRONIZE
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # process no longer exists
            return None
        raise ctypes.WinError(error)
    try:
        status = api.WaitForSingleObject(handle, 0)
        if status == 0:  # A signalled process has exited, even with exit code 259.
            return None
        if status != 258:  # WAIT_TIMEOUT: still running
            raise ctypes.WinError(ctypes.get_last_error())
        times = [wintypes.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        api.CloseHandle(handle)


def state_path(value):
    identity = value["name"] if value["component"] == "codex" else value["component"]
    return Path(value["root"]) / "runtime" / (identity + ".process.json")


def register(directory, units):
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for name, content in units.items():
            validate_definition(json.loads(content), name)
            path = directory / (name + ".json")
            if path.is_symlink():
                raise ValueError("Symlinked service definition")
            if path.exists():
                if path.read_text(encoding="utf-8") != content:
                    raise ValueError("Service definition ownership mismatch: " + name)
                continue
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
            created.append(name)
        return created
    except Exception:
        for name in created:
            (directory / (name + ".json")).unlink()
        raise


class WindowsServiceControl:
    def __init__(self, directory):
        self.directory = Path(directory)

    @staticmethod
    def scm():
        from scripts.portal_service_control import ServiceControl
        return ServiceControl(platform="windows")

    def definition(self, name):
        if not re.fullmatch(r"[a-zA-Z0-9_@.-]+", name):
            raise ValueError("Invalid service name")
        path = self.directory / (name + ".json")
        if path.is_symlink():
            raise ValueError("Symlinked service definition")
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("mode") == "user":
                return validate_definition(value, name)
            from scripts.tfi_release_platform_windows import verify_service
            verify_service(name, path.read_text(encoding="utf-8"))
        return None

    def status(self, name):
        value = self.definition(name)
        if value is None:
            return self.scm().status(name)
        record = {}
        path = state_path(value)
        try:
            record = json.loads(read_record(path))
        except FileNotFoundError:
            pass
        active = bool(record.get("pid") and record.get("created") is not None
                      and process_stamp(record["pid"]) == record["created"]
                      and record.get("child_pid") and record.get("child_created") is not None
                      and process_stamp(record["child_pid"]) == record["child_created"])
        # Keep a living supervisor visible even while its child is stopping
        # or waiting to restart after a failure.
        supervisor = bool(record.get("pid") and record.get("created") is not None
                          and process_stamp(record["pid"]) == record["created"])
        if active:
            return {"service": name, "load": "loaded", "active": "active", "sub": "running", "pid": record["pid"]}
        if supervisor:
            try:
                stopping = read_record(path.with_suffix(".stop")) == str(record["created"])
            except FileNotFoundError:
                stopping = False
            return {"service": name, "load": "loaded", "active": "deactivating" if stopping else "activating",
                    "sub": "stopping" if stopping else "auto-restart", "pid": record["pid"]}
        # A supervisor removes its record on every exit it can observe. A
        # record that outlived its supervisor means the supervisor itself was
        # terminated: report it like systemd's failed unit, never as stopped.
        return {"service": name, "load": "loaded", "active": "failed" if record else "inactive",
                "sub": "failed" if record else "dead", "pid": 0}

    def start(self, *names):
        for name in names:
            value = self.definition(name)
            if value is None:
                self.scm().start(name)
                continue
            status = self.status(name)
            if status["active"] in {"active", "activating"}:
                continue  # A supervisor waiting to restart its child owns the start.
            if status["pid"]:
                raise RuntimeError("Wait for the component to stop: " + name)
            from management_common.file_lock import lock_stream
            with state_path(value).with_suffix(".start.lock").open("a+b") as lock, lock_stream(lock):
                status = self.status(name)
                if status["active"] == "active":
                    continue
                if status["pid"]:
                    raise RuntimeError("Wait for the component to stop: " + name)
                log = Path(value["root"]) / "runtime" / ((value["name"] if value["component"] == "codex" else value["component"]) + ".log")
                with log.open("ab") as output:
                    arguments = [value["python"], str(Path(value["code"]) / "scripts/tfi_windows_user_services.py"),
                                 "--definition", str(self.directory / (name + ".json"))]
                    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                    # A caller inside a component's kill-on-close Job (for
                    # example ExecutionService account hosting) must not make
                    # this supervisor die silently with that component.
                    try:
                        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=output,
                                                   stderr=output, close_fds=True,
                                                   creationflags=flags | CREATE_BREAKAWAY_FROM_JOB)
                    except PermissionError:
                        # An outer Job that forbids breakaway (some shells and
                        # task hosts); none of the instance's own Jobs do.
                        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=output,
                                                   stderr=output, close_fds=True, creationflags=flags)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if self.status(name)["active"] == "active":
                        break
                    if process.poll() is not None:
                        raise RuntimeError(f"{name} failed to start. See {log}")
                    time.sleep(0.1)
                else:
                    raise RuntimeError(f"{name} startup timed out. See {log}")

    def stop(self, *names):
        for name in names:
            value = self.definition(name)
            if value is None:
                self.scm().stop(name)
                continue
            path = state_path(value)
            if not self.status(name)["pid"]:
                continue
            try:
                record = json.loads(read_record(path))
            except FileNotFoundError:
                continue  # Supervisor finished between status and stop request.
            write_record(path.with_suffix(".stop"), str(record["created"]))
            deadline = time.monotonic() + 300
            while self.status(name)["pid"] and time.monotonic() < deadline:
                time.sleep(0.2)
            if self.status(name)["pid"]:
                raise RuntimeError("Component did not stop; data retained: " + name)

    def restart(self, name):
        self.stop(name)
        self.start(name)


def failed_units(target, directory):
    """Name every user unit of this installation whose supervisor was terminated."""
    from scripts import tfi_release_install as api
    from scripts.tfi_release_provider_services import owned_services
    control = WindowsServiceControl(directory)
    units = {api.unit_name(target, c): control for c in api.COMPONENTS}
    units.update((owned_services(target, directory) or {}).get("controllers", {}))
    return {name: unit for name, unit in units.items()
            if unit.definition(name) is not None and unit.status(name)["active"] == "failed"}


def revive_failed(definition, attempts, now=time.monotonic):
    """Start this installation's failed user units once; the caller repeats.

    A unit is failed only when its supervisor died without removing its
    record, so explicit stops and normal exits are never revived. The pass
    skips while an installer, upgrade or recovery owns the instance, and backs
    off per unit after a failed start. ``attempts`` carries that backoff.
    """
    from scripts import tfi_release_install as api
    definition = Path(definition)
    value = validate_definition(json.loads(definition.read_text(encoding="utf-8")), definition.stem)
    root = Path(value["root"])
    state = api.owned_state(root)
    if state.get("migration", {}).get("business_execution_allowed") is False:
        return []
    target = state["target"]
    if target.get("shared_code_root"):
        from scripts.tfi_application_upgrade import pending
        if pending(Path(target["shared_code_root"]).parent):
            return []
    failed = failed_units(target, definition.parent)
    for name in set(attempts) - set(failed):
        attempts.pop(name)
    due = [name for name in sorted(failed) if attempts.get(name, (0, 0))[1] <= now()]
    if not due:
        return []
    results = []
    try:
        with api.installation_lock(root):
            failed = failed_units(target, definition.parent)
            for name in due:
                if name not in failed:
                    continue
                try:
                    failed[name].start(name)
                except Exception as exc:
                    count = attempts.get(name, (0, 0))[0] + 1
                    attempts[name] = (count, now() + min(REVIVE_DELAY_MAX_SECONDS, REVIVE_INTERVAL_SECONDS * 2 ** count))
                    results.append({"service": name, "result": "failed", "error_type": type(exc).__name__})
                else:
                    attempts.pop(name, None)
                    results.append({"service": name, "result": "started"})
    except BlockingIOError:
        return []  # An installer command owns the instance; try next pass.
    return results


def supervise(path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import tfi_release_install as api
    from management_common.process_runtime import spawn_process, terminate_process_tree
    from scripts.tfi_instance_layout import database_root
    value = validate_definition(json.loads(path.read_text(encoding="utf-8")), path.stem)

    def launch():
        state = api.owned_state(Path(value["root"]))
        if state.get("migration", {}).get("business_execution_allowed") is False and value["component"] != "postgres":
            raise ValueError("Migration cutover has not been approved")
        target = state["target"]
        if value["component"] == "codex":
            command, env = value["command"], api.clean_environment(target)
        else:
            command, env = api.component_command(target, value["component"])
        # A native Python crash otherwise leaves no traceback in the component
        # log. The definition lets a core service revive this installation's
        # failed units (revive_failed) without guessing its service directory.
        env = {**env, "PYTHONFAULTHANDLER": "1", DEFINITION_ENV: str(path.resolve())}
        child = spawn_process(command, cwd=value["code"], env=env,
                              stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        return child, target, env

    process, target, env = launch()
    record_path = state_path(value)
    created = process_stamp(os.getpid())
    reason = "child_exit"
    def event(name, **details):
        print("@TFI_SERVICE " + json.dumps({
            "time": datetime.now(timezone.utc).isoformat(), "component": value["component"],
            "event": name, "supervisor_pid": os.getpid(), "child_pid": process.pid,
            **details,
        }), flush=True)
    stop = record_path.with_suffix(".stop")
    def stop_requested():
        try:
            return read_record(stop) == str(created)
        except FileNotFoundError:
            return False
    def publish():
        write_record(record_path, json.dumps({"pid": os.getpid(), "created": created,
                                             "child_pid": process.pid, "child_created": process_stamp(process.pid)}))
    try:
        publish()
        event("started")
        delay = 0
        while True:
            started = time.monotonic()
            while process.poll() is None:
                if stop_requested():
                    reason = "stop_requested"
                    event("stop_requested")
                    if value["component"] == "postgres":
                        subprocess.run([str(Path(target["pg_bin"]) / "pg_ctl.exe"), "stop", "-D",
                                        str(database_root(target)), "-m", "fast", "-w", "-t", "60"],
                                       env=env, check=True, timeout=210)
                    break
                time.sleep(0.2)
            if reason == "stop_requested" or process.returncode == 0:
                break
            # Restart=on-failure parity: a failed child restarts after a
            # backoff that resets once a generation stayed up for a minute.
            delay = RESTART_DELAY_SECONDS if time.monotonic() - started >= RESTART_RESET_SECONDS \
                else min(RESTART_DELAY_MAX_SECONDS, max(RESTART_DELAY_SECONDS, delay * 2))
            code = process.returncode
            event("child_failed", exit_code=code, exit_code_hex=f"0x{code & 0xffffffff:08X}", restart_in=delay)
            terminate_process_tree(process, timeout=30)
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline and not stop_requested():
                time.sleep(0.2)
            if stop_requested():
                reason = "stop_requested"
                event("stop_requested")
                break
            process, target, env = launch()
            publish()
            event("restarted")
    except BaseException as exc:
        reason = "supervisor_error"
        event("supervisor_error", error_type=type(exc).__name__)
        raise
    finally:
        terminate_process_tree(process, timeout=30)
        code = process.poll()
        event("exited", reason=reason, exit_code=code,
              exit_code_hex=f"0x{code & 0xffffffff:08X}" if code is not None else None)
        remove_record(record_path)
        remove_record(record_path.with_suffix(".stop"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", type=Path, required=True)
    supervise(parser.parse_args().definition)
