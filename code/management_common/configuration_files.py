"""Preserve configuration file ownership and native access controls."""
from pathlib import Path
import os
import stat
import sys

def preserve_config_permissions(temporary: Path, target: Path) -> None:
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


def atomic_json(path: Path, value: dict) -> None:
    import json
    import tempfile
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Configuration must not be a symbolic link')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            if path.exists():
                preserve_config_permissions(temporary, path)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
