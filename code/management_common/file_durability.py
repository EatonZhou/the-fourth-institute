"""Filesystem namespace durability, separate from flushing file contents."""
from __future__ import annotations

import os
from pathlib import Path


def sync_directory(path: Path) -> bool:
    """Flush a POSIX directory; return False when the OS has no CRT equivalent.

    Windows callers must still flush/fsync file contents before replacement.
    This does not promise namespace persistence across a Windows power loss.
    Errors on supported platforms propagate instead of claiming durability.
    """
    if os.name == "nt":
        return False
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True
