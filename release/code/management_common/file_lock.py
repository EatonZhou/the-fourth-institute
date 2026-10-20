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
