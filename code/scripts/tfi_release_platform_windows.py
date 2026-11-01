"""Windows definitions for one instance's registered components.

Current-user supervision (tfi_windows_user_services) is the default. An SCM
registration requires an explicit service account and its password in the
registering process environment. The password is passed to CreateServiceW,
never put in a command line, the instance or a service definition. Definitions
live in the instance's runtime/service-units (instance_services owns that).
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess

from management_common.code_environment import runtime_python


def service_controller(instance=None):
    from management_common.instance_manifest import load
    from scripts.instance_services import definition_directory
    from scripts.tfi_windows_user_services import WindowsServiceControl
    return WindowsServiceControl(definition_directory(instance or load()))


def unit_path(directory, name):
    return directory / f"{name}.json"


def manifest_unit_text(instance, component, *, mode='user', account=''):
    code = instance.service_code(component)
    python = str(runtime_python(code))
    if mode == "user":
        return json.dumps({"mode": "user", "name": instance.service_name(component),
                           "root": str(instance.root), "python": python,
                           "code": str(code), "component": component,
                           "environment": instance.service_environment(component)},
                          sort_keys=True, ensure_ascii=False) + "\n"
    root = PureWindowsPath(instance.root)
    command = [python, str(PureWindowsPath(code / 'scripts/tfi_release_windows_service.py')), "_component",
               "--root", str(root), "--component", component]
    return json.dumps({"name": instance.service_name(component),
                       "binary": subprocess.list2cmdline(command),
                       "account": account},
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
    """Ownership of an installed definition; startup separately requires the current text."""
    definition = json.loads(content)
    if definition.get("mode") == "user":
        from scripts.tfi_windows_user_services import owned_definition
        owned_definition(definition, name)
        return
    actual = _service_binary(name)
    if actual != definition["binary"]:
        raise ValueError(f"Windows service registration ownership mismatch: {name}")


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
    """User definitions of the same instance are replaced (instance_services has
    verified their ownership); an SCM registration is never silently replaced."""
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
