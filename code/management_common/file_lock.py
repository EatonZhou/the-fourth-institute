"""Cross-process exclusive locks with identical caller lifetimes on each OS.

Locks never add sentinel bytes or move the caller's file position. Keep the
file descriptor open until release; for replaceable state, lock a separate
stable file. Windows byte locks require a writable, seekable descriptor.
"""
from __future__ import annotations

import errno
import os
import time
from contextlib import contextmanager
from typing import IO, Iterator


def open_lock_file(path):
    """Open a stable lock file without following links, allowing its parent to move.

    A Windows CRT open denies deletion sharing and prevents root relocation
    while lifecycle locks are held. Sharing the name does not release the byte
    lock: contenders at the new name still see the same file. Callers must never
    unlink or replace a live lock file.
    """
    if os.name != "nt":
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    else:
        import ctypes
        import msvcrt
        from ctypes import wintypes as w

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        api.CreateFileW.restype = w.HANDLE
        api.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        api.GetFileInformationByHandleEx.restype = w.BOOL
        api.CloseHandle.argtypes = [w.HANDLE]
        handle = api.CreateFileW(str(path), 0xC0000000, 7, None, 4, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            attributes = (w.DWORD * 2)()
            if not api.GetFileInformationByHandleEx(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
                raise ctypes.WinError(ctypes.get_last_error())
            if attributes[0] & (0x400 | 0x10):
                raise ValueError("Lock must be a regular file, not a reparse point")
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            api.CloseHandle(handle)
            raise
    return os.fdopen(descriptor, "r+b")


def _windows_lock(descriptor: int, mode: int) -> None:
    import msvcrt

    position = os.lseek(descriptor, 0, os.SEEK_CUR)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        # The CRT permits locking beyond EOF. Never modify empty state files.
        msvcrt.locking(descriptor, mode, 1)
    finally:
        os.lseek(descriptor, position, os.SEEK_SET)


def try_lock_descriptor(descriptor: int) -> bool:
    """Try once; False means contention, other errors remain errors."""
    if os.name == "nt":
        import msvcrt

        try:
            _windows_lock(descriptor, msvcrt.LK_NBLCK)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise
    else:
        import fcntl

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True


def unlock_descriptor(descriptor: int) -> None:
    """Release an acquired lock without closing the descriptor."""
    if os.name == "nt":
        import msvcrt

        _windows_lock(descriptor, msvcrt.LK_UNLCK)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def lock_stream(stream: IO[object]) -> Iterator[None]:
    """Block until acquired, then release on every exit from the body."""
    descriptor = stream.fileno()
    if os.name == "nt":
        # LK_LOCK fails after a fixed retry window, unlike flock(LOCK_EX).
        while not try_lock_descriptor(descriptor):
            time.sleep(0.05)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        yield
    finally:
        unlock_descriptor(descriptor)
