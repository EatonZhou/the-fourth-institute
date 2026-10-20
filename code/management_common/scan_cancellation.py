"""Cooperative cancellation for filesystem scans owned by a worker."""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import Event


class ScanCancelled(BaseException):
    """Unwind a scan without treating cancellation as a corrupt source."""


_stop_event: ContextVar[Event | None] = ContextVar("scan_stop_event", default=None)


@contextmanager
def cancellable_scan(stop: Event):
    token = _stop_event.set(stop)
    try:
        check_scan_cancelled()
        yield
    finally:
        _stop_event.reset(token)


def check_scan_cancelled() -> None:
    stop = _stop_event.get()
    if stop is not None and stop.is_set():
        raise ScanCancelled()


def walk_paths(root: Path):
    """Yield descendants, checking even directories with no matching files.

    Like Path.rglob, do not descend through directory symlinks. Checking the
    walk itself matters: filtering a glob's matches cannot interrupt a large
    tree that contains no matches.
    """
    check_scan_cancelled()
    for directory, names, files in os.walk(root, followlinks=False):
        check_scan_cancelled()
        for name in (*names, *files):
            check_scan_cancelled()
            yield Path(directory) / name
