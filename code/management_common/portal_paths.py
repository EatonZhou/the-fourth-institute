"""Installation data directories shared by Portal services and the migrator.

Relative values are anchored to the source installation, never to the process cwd.
The local configuration lives outside the data directories so it survives a move.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePath, PureWindowsPath
import stat
import sys
import tempfile

from management_common.product_environment import read_product_env
from management_common.site_compatibility import compatibility_value


PORTAL_ROOT = Path(__file__).resolve().parents[1]
KEYS = ("share_root", "staging_root", "instances_root", "secrets_dir")
ENV_KEYS = {
    "share_root": "PORTAL_SHARE_ROOT",
    "staging_root": "PORTAL_AGENT_STAGING_ROOT",
    "instances_root": "PORTAL_INSTANCES_ROOT",
    "secrets_dir": "PORTAL_SECRETS_DIR",
}


def _anchor_directory(path: PurePath, root: PurePath) -> PurePath:
    # A drive-relative path can consult that drive's process cwd. A rooted
    # path without a drive also leaves part of the location implicit.
    if isinstance(path, PureWindowsPath) and (path.drive or path.root) and not path.is_absolute():
        raise ValueError("Windows directory must be fully qualified or relative to the installation")
    return path if path.is_absolute() else root / path


def resolve_directory(value: str) -> Path:
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "\0\n\r"):
        raise ValueError("Directory must be a nonempty path")
    path = Path(value).expanduser()
    return _anchor_directory(path, PORTAL_ROOT).resolve(strict=False)


CONFIG_PATH = resolve_directory(os.environ.get(
    "PORTAL_PATHS_CONFIG", str(Path.home() / ".config" / "tfi" / "portal_paths.json")))


def read_config() -> dict[str, str]:
    source = CONFIG_PATH
    if "PORTAL_PATHS_CONFIG" not in os.environ:
        configured = compatibility_value('paths_config')
        if configured is not None:
            source = resolve_directory(configured)
    from management_common.instance_config import installed_root, read_installation
    root = installed_root(source)
    if root is not None:
        data = read_installation(root)["target"]["data_roots"]
        if set(data) != set(KEYS):
            raise ValueError("Invalid instance data roots")
        for key in KEYS:
            resolve_directory(data[key])
        return data
    if not source.exists():
        return {}
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != set(KEYS):
        raise ValueError("Invalid Portal paths configuration")
    for key in KEYS:
        resolve_directory(data[key])
    return data


def _defaults() -> dict[str, str]:
    # Site-specific directories are persisted before an existing installation
    # adopts these portable defaults. No installation borrows another host's paths.
    return {key: "portal_data/" + name for key, name in (
        ("share_root", "share"), ("staging_root", "staging"),
        ("instances_root", "instances"),
        ("secrets_dir", "secrets"),
    )}


def path_values() -> dict[str, str]:
    saved = read_config()
    defaults = _defaults()
    if saved:
        return saved
    return {
        key: (read_product_env(name, defaults[key]) if key == "share_root"
              else os.environ.get(name, defaults[key]))
        for key, name in ENV_KEYS.items()
    }


def paths() -> dict[str, Path]:
    return {key: resolve_directory(value) for key, value in path_values().items()}


def path(key: str) -> Path:
    if key not in KEYS:
        raise KeyError(key)
    return paths()[key]


def write_config(values: dict[str, str]) -> None:
    from management_common.instance_config import installed_root
    if installed_root(CONFIG_PATH) is not None:
        raise ValueError("Installed instance paths are owned by config/target.json; use tfi_instance_config plan/apply")
    if set(values) != set(KEYS):
        raise ValueError("Every Portal data directory must be configured")
    for key in KEYS:
        resolve_directory(values[key])
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{CONFIG_PATH.name}.", suffix=".tmp", dir=CONFIG_PATH.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            _preserve_config_permissions(temporary, CONFIG_PATH)
            stream.write(json.dumps(values, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Windows requires the temporary handle to be closed before replacement.
        os.replace(temporary, CONFIG_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def _preserve_config_permissions(temporary: Path, target: Path) -> None:
    try:
        existing = target.stat()
    except FileNotFoundError:
        return
    if os.name != "nt":
        created = temporary.stat()
        if (created.st_uid, created.st_gid) != (existing.st_uid, existing.st_gid):
            os.chown(temporary, existing.st_uid, existing.st_gid)
        temporary.chmod(stat.S_IMODE(existing.st_mode))
        if sys.platform == "darwin":
            _copy_macos_acl(target, temporary)
            return
        # Linux POSIX ACLs are extended attributes. Copy them explicitly so an
        # ACL failure does not silently widen a previously restricted config.
        if hasattr(os, "listxattr"):
            name = "system.posix_acl_access"
            if name in os.listxattr(target):
                os.setxattr(temporary, name, os.getxattr(target, name))
            elif name in os.listxattr(temporary):
                os.removexattr(temporary, name)
        return
    _copy_windows_dacl(target, temporary)


def _copy_macos_acl(source: Path, destination: Path) -> None:
    import ctypes

    copyfile = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True).copyfile
    copyfile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32]
    copyfile.restype = ctypes.c_int
    if copyfile(os.fsencode(source), os.fsencode(destination), None, 1) != 0:  # COPYFILE_ACL
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def _copy_windows_dacl(source: Path, destination: Path) -> None:
    """Preserve owner, group and DACL before writing any contents.

    Do not substitute chmod: Windows mode bits do not describe access control.
    Missing ACL read/write rights fail the save before publication.
    """
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = api.GetFileSecurityW
    get_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    get_security.restype = wintypes.BOOL
    set_security = api.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
    set_security.restype = wintypes.BOOL
    get_control = api.GetSecurityDescriptorControl
    get_control.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    get_control.restype = wintypes.BOOL

    security_information = 0x00000007  # OWNER | GROUP | DACL
    required = wintypes.DWORD()
    get_security(str(source), security_information, None, 0, ctypes.byref(required))
    if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(required.value)
    if not get_security(str(source), security_information, descriptor, required, ctypes.byref(required)):
        raise ctypes.WinError(ctypes.get_last_error())
    control, revision = wintypes.WORD(), wintypes.DWORD()
    if not get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)):
        raise ctypes.WinError(ctypes.get_last_error())
    # Preserve whether inherited ACL entries may be recomputed.
    protection = 0x80000000 if control.value & 0x1000 else 0x20000000
    if not set_security(str(destination), security_information | protection, descriptor):
        raise ctypes.WinError(ctypes.get_last_error())
