"""Directory aliases use unprivileged NTFS junctions on Windows."""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import secrets
import struct
import sys


def is_directory_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def remove_directory_link(path: Path, *, missing_ok=False) -> None:
    if hasattr(path, "is_junction") and path.is_junction():
        path.rmdir()
    elif path.is_symlink():
        path.unlink()
    elif path.exists() or not missing_ok:
        raise ValueError(f"Not a directory link: {path}")


def create_directory_link(path: Path, target: Path) -> None:
    if sys.platform == "win32":
        create_windows_junction(path, target)
    else:
        path.symlink_to(target, target_is_directory=True)


def replace_directory_link(source: Path, target: Path) -> None:
    # MoveFileEx cannot replace an existing directory junction. Preserve the
    # previous alias until the new one is installed, with rollback on failure.
    if sys.platform != "win32" or not is_directory_link(target):
        os.replace(source, target)
        return
    backup = target.with_name(f".{target.name}.{secrets.token_hex(8)}.bak")
    os.rename(target, backup)
    try:
        os.rename(source, target)
    except BaseException:
        os.rename(backup, target)
        raise
    remove_directory_link(backup)


def create_windows_junction(path: Path, target: Path) -> None:
    if not target.is_dir():
        raise ValueError("Directory link target must be a directory")
    if sys.platform != "win32":
        raise OSError("Windows junctions require Windows")
    from ctypes import wintypes

    resolved = str(target.resolve())
    substitute = ("\\??\\UNC\\" + resolved.lstrip("\\") if resolved.startswith("\\\\")
                  else "\\??\\" + resolved).encode("utf-16-le")
    display = resolved.encode("utf-16-le")
    buffer = substitute + b"\x00\x00" + display + b"\x00\x00"
    reparse = struct.pack("<IHHHHHH", 0xA0000003, 8 + len(buffer), 0,
                          0, len(substitute), len(substitute) + 2, len(display)) + buffer
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.restype = ctypes.c_void_p
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    path.mkdir()
    try:
        handle = api.CreateFileW(str(path), 0x40000000, 7, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value or not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            data = ctypes.create_string_buffer(reparse)
            count = wintypes.DWORD()
            if not api.DeviceIoControl(handle, 0x000900A4, data, len(reparse), None, 0,
                                       ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            api.CloseHandle(handle)
    except Exception:
        path.rmdir()
        raise

