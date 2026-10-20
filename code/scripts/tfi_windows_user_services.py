"""Current-user Windows component supervision; SCM remains an explicit option.

Definition files belong to the installer. Process identity and stop requests
belong to the instance. No password, elevation or global service is needed.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def validate_definition(value, name):
    if value.get("name") != name or not re.fullmatch(r"tfi-[a-z0-9-]+", name):
        raise ValueError("Invalid user service identity")
    if value.get("mode") != "user" or value.get("component") not in (
            "postgres", "nats", "resource", "execution", "society", "worker", "web"):
        raise ValueError("Invalid user service component")
    for key in ("root", "code", "python"):
        path = Path(value[key])
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("User service paths must be absolute and not symlinks")
    if name != json.loads((Path(value["root"]) / "installation.json").read_text(encoding="utf-8"))["target"]["service_prefix"] + "-" + value["component"]:
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
    return Path(value["root"]) / "runtime" / (value["component"] + ".process.json")


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
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
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
            record = json.loads(state_path(value).read_text(encoding="utf-8"))
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
                if self.status(name)["active"] == "active":
                    continue
                log = Path(value["root"]) / "runtime" / (value["component"] + ".log")
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
            record = json.loads(path.read_text(encoding="utf-8"))
            path.with_suffix(".stop").write_text(str(record["created"]), encoding="utf-8")
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
    command, env = api.component_command(target, value["component"])
    process = spawn_process(command, cwd=value["code"], env=env,
                            stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    record_path = state_path(value)
    created = process_stamp(os.getpid())
    try:
        api.write_json(record_path, {"pid": os.getpid(), "created": created,
                                    "child_pid": process.pid, "child_created": process_stamp(process.pid)})
        stop = record_path.with_suffix(".stop")
        while process.poll() is None:
            if stop.exists() and stop.read_text(encoding="utf-8") == str(created):
                if value["component"] == "postgres":
                    subprocess.run([str(Path(target["pg_bin"]) / "pg_ctl.exe"), "stop", "-D",
                                    str(database_root(target)), "-m", "fast", "-w", "-t", "60"],
                                   env=env, check=True, timeout=210)
                break
            time.sleep(0.2)
    finally:
        terminate_process_tree(process, timeout=30)
        record_path.unlink(missing_ok=True)
        record_path.with_suffix(".stop").unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", type=Path, required=True)
    supervise(parser.parse_args().definition)
