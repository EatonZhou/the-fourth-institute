"""Real Windows SCM host for one isolated TFI component."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import json
import argparse
import sys
import threading

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

def run_service(target, component):
    from scripts.tfi_release_install import component_command
    root = Path(target["install_root"])
    command, environment = component_command(target, component)
    return run_command_service(f"{target['service_prefix']}-{component}", command,
                               cwd=root / "code", log=root / "runtime" / f"{component}.log",
                               environment=environment)


def run_registered_service(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Service definition must not be a symlink")
    definition = json.loads(path.read_text(encoding="utf-8"))
    if path.name != f"{definition['name']}.json" or not definition.get("command"):
        raise ValueError("Invalid registered service definition")
    return run_command_service(definition["name"], definition["command"],
                               cwd=Path(definition["cwd"]), log=Path(definition["log_path"]),
                               environment=_registered_environment(definition))


def _registered_environment(definition):
    from scripts.portal_native_service_child import load_environment_files
    environment = os.environ.copy()
    environment.update(definition.get("environment", {}))
    return load_environment_files(definition.get("env_files", ()), environment)


def run_command_service(name, command, *, cwd, log, environment=None):
    if os.name != "nt":
        raise OSError("SCM service host requires Windows")
    log = Path(log)
    stop = threading.Event()
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    service_handle = None
    result = {"code": 0}

    class Status(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("state", wintypes.DWORD),
                    ("controls", wintypes.DWORD), ("win32_exit", wintypes.DWORD),
                    ("service_exit", wintypes.DWORD), ("checkpoint", wintypes.DWORD),
                    ("wait_hint", wintypes.DWORD)]

    handler_type = ctypes.WINFUNCTYPE(wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, ctypes.c_void_p)
    main_type = ctypes.WINFUNCTYPE(None, wintypes.DWORD, ctypes.POINTER(ctypes.c_wchar_p))

    class Entry(ctypes.Structure):
        _fields_ = [("name", ctypes.c_wchar_p), ("main", main_type)]

    api.RegisterServiceCtrlHandlerExW.restype = ctypes.c_void_p
    api.RegisterServiceCtrlHandlerExW.argtypes = [ctypes.c_wchar_p, handler_type, ctypes.c_void_p]
    api.SetServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(Status)]
    api.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(Entry)]

    def report(state, *, code=0, wait_hint=0):
        if not service_handle:
            return
        status = Status(0x10, state, 0x5 if state == 4 else 0, code, 0, 0, wait_hint)
        if not api.SetServiceStatus(service_handle, ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())

    @handler_type
    def control(code, _event_type, _event_data, _context):
        if code in (1, 5):  # SERVICE_CONTROL_STOP / SHUTDOWN
            stop.set()
            report(3, wait_hint=30000)  # STOP_PENDING
        return 0

    @main_type
    def service_main(_argc, _argv):
        nonlocal service_handle
        service_handle = api.RegisterServiceCtrlHandlerExW(name, control, None)
        if not service_handle:
            result["code"] = ctypes.get_last_error()
            return
        report(2, wait_hint=30000)  # START_PENDING
        child = None
        try:
            from management_common.process_runtime import spawn_process, terminate_process_tree
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("ab") as output:
                child = spawn_process(command, cwd=cwd, env=environment,
                                      stdin=subprocess.DEVNULL, stdout=output,
                                      stderr=subprocess.STDOUT, close_fds=True,
                                      tree_id=name, kill_on_owner_exit=True)
                report(4)  # RUNNING
                while not stop.wait(0.5):
                    if child.poll() is not None:
                        result["code"] = child.returncode or 1
                        break
                report(3, wait_hint=30000)
                terminate_process_tree(child, timeout=30, tree_id=name)
        except Exception as exc:
            result["code"] = 1
            with log.with_suffix(".error.log").open("a", encoding="utf-8") as output:
                output.write(f"{type(exc).__name__}: {exc}\n")
            if child is not None:
                try:
                    terminate_process_tree(child, timeout=5, force=True, tree_id=name)
                except Exception:
                    pass
        finally:
            report(1, code=result["code"])  # STOPPED

    table = (Entry * 2)()
    table[0] = Entry(name, service_main)
    table[1] = Entry()
    if not api.StartServiceCtrlDispatcherW(table):
        raise ctypes.WinError(ctypes.get_last_error())
    return result["code"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("_registered",))
    parser.add_argument("--definition", required=True)
    args = parser.parse_args()
    return run_registered_service(args.definition)


if __name__ == "__main__":
    raise SystemExit(main())
