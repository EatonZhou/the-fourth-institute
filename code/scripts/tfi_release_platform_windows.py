"""Windows SCM definitions for isolated TFI services.

Registration requires an explicit service account and its password in the
installer process environment. The password is passed to CreateServiceW, never
put in a command line, installation state, or a service definition.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess

from scripts.tfi_instance_layout import code_root, installer_path


def safe_path(value):
    if not isinstance(value, str) or not Path(value).is_absolute() or any(c in value for c in "\0\n\r'\""):
        raise ValueError("Target paths must be absolute and contain no control characters")
    return Path(value).resolve()


def service_controller():
    from scripts.tfi_windows_user_services import WindowsServiceControl
    return WindowsServiceControl(unit_directory())


def validate_service_mode(target):
    mode = target.get("windows_service_mode", "scm")
    if mode not in {"user", "scm"}:
        raise ValueError("Unknown Windows service mode")
    if mode == "scm":
        account = target.get("windows_service_account", "")
        if not isinstance(account, str) or not account or account.lower() in {"localsystem", "localservice", "networkservice"}:
            raise ValueError("An explicit non-system windows_service_account is required")


def unit_name(target, component):
    return f"{target['service_prefix']}-{component}"


def unit_directory():
    configured = os.environ.get("TFI_WINDOWS_SERVICE_DIRECTORY")
    if configured:
        return safe_path(configured)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "TFI" / "services"


def unit_path(directory, name):
    return directory / f"{name}.json"


def unit_text(target, component, components):
    del components
    if target.get("windows_service_mode") == "user":
        return json.dumps({"mode": "user", "name": unit_name(target, component),
                           "root": target["install_root"], "python": target["python"],
                           "code": str(code_root(target)), "component": component},
                          sort_keys=True, ensure_ascii=False) + "\n"
    root = PureWindowsPath(target["install_root"])
    command = [target["python"], str(PureWindowsPath(installer_path(target))), "_service",
               "--root", str(root), "--component", component]
    return json.dumps({"name": unit_name(target, component),
                       "binary": subprocess.list2cmdline(command),
                       "account": target.get("windows_service_account", "")},
                      sort_keys=True, ensure_ascii=False) + "\n"


def _api():
    if os.name != "nt":
        raise OSError("Windows SCM is only available on Windows")
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    from ctypes import wintypes
    for name in ("OpenSCManagerW", "OpenServiceW", "CreateServiceW"):
        getattr(api, name).restype = ctypes.c_void_p
    api.OpenSCManagerW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, wintypes.DWORD]
    api.OpenServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, wintypes.DWORD]
    api.CreateServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p,
                                   ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
    api.QueryServiceConfigW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD)]
    api.CloseServiceHandle.argtypes = [ctypes.c_void_p]
    api.DeleteService.argtypes = [ctypes.c_void_p]
    return api


def _service_binary(name):
    from ctypes import wintypes

    api = _api()
    manager = api.OpenSCManagerW(None, None, 0x0001)
    if not manager:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        service = api.OpenServiceW(manager, name, 0x0001)
        if not service:
            error = ctypes.get_last_error()
            if error == 1060:
                return None
            raise ctypes.WinError(error)
        try:
            class Config(ctypes.Structure):
                _fields_ = [("service_type", wintypes.DWORD), ("start_type", wintypes.DWORD),
                            ("error_control", wintypes.DWORD), ("binary", wintypes.LPWSTR),
                            ("group", wintypes.LPWSTR), ("tag", wintypes.DWORD),
                            ("dependencies", wintypes.LPWSTR), ("account", wintypes.LPWSTR),
                            ("display", wintypes.LPWSTR)]
            buffer = ctypes.create_string_buffer(8192)
            needed = wintypes.DWORD()
            if not api.QueryServiceConfigW(service, buffer, len(buffer), ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.cast(buffer, ctypes.POINTER(Config)).contents.binary
        finally:
            api.CloseServiceHandle(service)
    finally:
        api.CloseServiceHandle(manager)


def verify_service(name, content):
    definition = json.loads(content)
    if definition.get("mode") == "user":
        from scripts.tfi_windows_user_services import validate_definition
        validate_definition(definition, name)
        return
    actual = _service_binary(name)
    if actual != definition["binary"]:
        raise ValueError(f"Windows service registration ownership mismatch: {name}")


def dependency_preflight(target):
    validate_service_mode(target)
    if target.get("windows_service_mode") == "user":
        return
    if not target.get("windows_service_account") or not os.environ.get("TFI_WINDOWS_SERVICE_PASSWORD"):
        raise ValueError("Dependency preflight failed: Windows service account and TFI_WINDOWS_SERVICE_PASSWORD are required")


def _create_service(definition, password):
    api = _api()
    manager = api.OpenSCManagerW(None, None, 0x0002)
    if not manager:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        handle = api.CreateServiceW(manager, definition["name"], definition["name"],
                                    0xF01FF, 0x10, 0x3, 0x1, definition["binary"],
                                    None, None, None, definition["account"], password)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        api.CloseServiceHandle(handle)
    finally:
        api.CloseServiceHandle(manager)


def _delete_service(name):
    api = _api()
    manager = api.OpenSCManagerW(None, None, 0x0001)
    if not manager:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        handle = api.OpenServiceW(manager, name, 0x10000)
        if not handle:
            if ctypes.get_last_error() == 1060:
                return
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not api.DeleteService(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            api.CloseServiceHandle(handle)
    finally:
        api.CloseServiceHandle(manager)


def command_unit_text(name, command, *, account, cwd, log_path, directory, python=None,
                      environment=None, env_files=()):
    """Define an arbitrary Portal child for the same SCM host as TFI components."""
    script = Path(__file__).with_name("tfi_release_windows_service.py")
    marker = unit_path(directory, name)
    launcher = [python or os.sys.executable, str(script), "_registered", "--definition", str(marker)]
    return json.dumps({"name": name, "binary": subprocess.list2cmdline(launcher),
                       "account": account, "command": [str(part) for part in command],
                       "cwd": str(cwd), "log_path": str(log_path),
                       "environment": environment or {}, "env_files": list(map(str, env_files))},
                      sort_keys=True, ensure_ascii=False) + "\n"


def register_services(directory, units, *, password_env="TFI_WINDOWS_SERVICE_PASSWORD"):
    if units and all(json.loads(value).get("mode") == "user" for value in units.values()):
        from scripts.tfi_windows_user_services import register
        return register(directory, units)
    if any(json.loads(value).get("mode") == "user" for value in units.values()):
        raise ValueError("Cannot mix Windows service modes in one registration")
    directory.mkdir(parents=True, exist_ok=True)
    new = [(name, content) for name, content in units.items()
           if not unit_path(directory, name).exists()]
    if new and not os.environ.get(password_env):
        raise ValueError(f"{password_env} is required for Windows service registration")
    created = []
    try:
        for name, content in units.items():
            path = unit_path(directory, name)
            if path.exists() or path.is_symlink():
                if path.is_symlink() or path.read_text(encoding="utf-8") != content:
                    raise ValueError(f"Refusing to replace another Windows service: {name}")
                verify_service(name, content)
                continue
            definition = json.loads(content)
            if not definition["account"] or definition["account"].lower() in {"localsystem", "localservice", "networkservice"}:
                raise ValueError("An explicit non-system Windows service account is required")
            if _service_binary(name) is not None:
                raise ValueError(f"Windows service identity already exists: {name}")
            _create_service(definition, os.environ[password_env])
            created.append(name)
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
    except Exception:
        for name in reversed(created):
            _delete_service(name)
            unit_path(directory, name).unlink(missing_ok=True)
        raise
    return created


def remove_services(directory, names):
    for name in names:
        path = unit_path(directory, name)
        if path.exists() or path.is_symlink():
            if path.is_symlink():
                raise ValueError(f"Refusing to remove symlinked Windows service marker: {name}")
            verify_service(name, path.read_text(encoding="utf-8"))
            if json.loads(path.read_text(encoding="utf-8")).get("mode") == "user":
                from scripts.tfi_windows_user_services import WindowsServiceControl
                WindowsServiceControl(directory).stop(name)
            else:
                _delete_service(name)
            path.unlink()
