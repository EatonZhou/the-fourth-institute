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
            "postgres", "nats", "resource", "execution", "society", "worker", "web", "codex"):
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
                or command[3::2] != ["--socket", "--binary", "--account-home"]
                or not all(Path(arg).is_absolute() for arg in command[4::2])):
            raise ValueError("Codex user service ownership mismatch")
        home = Path(command[-1]).resolve()
        scope = hashlib.sha256(os.fsencode(Path(target["data_roots"]["instances_root"]).resolve())).hexdigest()[:10]
        if (home.parent != (Path(target["data_roots"]["accounts_root"]) / "codex").resolve()
                or name != f"portal-codex-app-server@{home.name}-{scope}.service"):
            raise ValueError("Codex user service account ownership mismatch")
    elif name != target["service_prefix"] + "-" + value["component"]:
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
        return None

    def status(self, name):
        value = self.definition(name)
        if value is None:
            return self.scm().status(name)
        record = {}
        try:
            record = json.loads(read_record(state_path(value)))
        except FileNotFoundError:
            pass
        active = bool(record.get("pid") and record.get("created") is not None
                      and process_stamp(record["pid"]) == record["created"]
                      and record.get("child_pid") and record.get("child_created") is not None
                      and process_stamp(record["child_pid"]) == record["child_created"])
        # Keep a living supervisor visible even while its child is stopping.
        supervisor = bool(record.get("pid") and record.get("created") is not None
                          and process_stamp(record["pid"]) == record["created"])
        return {"service": name, "load": "loaded", "active": "active" if active else "inactive",
                "sub": "running" if active else "stopping" if supervisor else "dead",
                "pid": record["pid"] if supervisor else 0}

    def start(self, *names):
        for name in names:
            value = self.definition(name)
            if value is None:
                self.scm().start(name)
                continue
            status = self.status(name)
            if status["active"] == "active":
                continue
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
                    process = subprocess.Popen(
                        [value["python"], str(Path(value["code"]) / "scripts/tfi_windows_user_services.py"),
                         "--definition", str(self.directory / (name + ".json"))],
                        stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                        close_fds=True)
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


def supervise(path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import tfi_release_install as api
    from management_common.process_runtime import spawn_process, terminate_process_tree
    from scripts.tfi_instance_layout import database_root
    value = validate_definition(json.loads(path.read_text(encoding="utf-8")), path.stem)
    state = api.owned_state(Path(value["root"]))
    if state.get("migration", {}).get("business_execution_allowed") is False and value["component"] != "postgres":
        raise ValueError("Migration cutover has not been approved")
    target = state["target"]
    if value["component"] == "codex":
        command, env = value["command"], api.clean_environment(target)
    else:
        command, env = api.component_command(target, value["component"])
    # A native Python crash otherwise leaves no traceback in the component log.
    process = spawn_process(command, cwd=value["code"], env={**env, "PYTHONFAULTHANDLER": "1"},
                            stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    record_path = state_path(value)
    created = process_stamp(os.getpid())
    reason = "child_exit"
    def event(name, **details):
        print("@TFI_SERVICE " + json.dumps({
            "time": datetime.now(timezone.utc).isoformat(), "component": value["component"],
            "event": name, "supervisor_pid": os.getpid(), "child_pid": process.pid,
            **details,
        }), flush=True)
    try:
        write_record(record_path, json.dumps({"pid": os.getpid(), "created": created,
                                             "child_pid": process.pid, "child_created": process_stamp(process.pid)}))
        event("started")
        stop = record_path.with_suffix(".stop")
        while process.poll() is None:
            try:
                requested = read_record(stop) == str(created)
            except FileNotFoundError:
                requested = False
            if requested:
                reason = "stop_requested"
                event("stop_requested")
                if value["component"] == "postgres":
                    subprocess.run([str(Path(target["pg_bin"]) / "pg_ctl.exe"), "stop", "-D",
                                    str(database_root(target)), "-m", "fast", "-w", "-t", "60"],
                                   env=env, check=True, timeout=210)
                break
            time.sleep(0.2)
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
