"""Host-wide concurrency budget shared by every Test executor (Dev and Release).

Each slot is one lock file; a holder keeps an exclusive ``flock`` on it for as
long as its test process runs. The kernel releases the lock when the holder
exits, so a crashed executor never leaks a slot.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
import time
from pathlib import Path
from typing import Callable

from management_common.file_lock import try_lock_descriptor
from management_portal.test_framework.paths import user_state_root

CAPACITY = {
    # One pytest shard runs one pytest process with its own PostgreSQL.
    "pytest": 32,
    # One browser slot is one Playwright worker.
    "browser": 32,
    # Live tests act on the shared DEV as test-actor, one process at a time.
    "live": 1,
    # One TAU slot is one active module group.
    "tau": 32,
}
DEFAULT_MAX_CONCURRENCY = 4
MAX_CONCURRENCY_CEILING = 32


def max_concurrency() -> int:
    """Host-wide budget for framework-managed work across all test kinds."""
    raw = os.getenv("PORTAL_TEST_MAX_CONCURRENCY", str(DEFAULT_MAX_CONCURRENCY))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("PORTAL_TEST_MAX_CONCURRENCY must be an integer from 1 to 32") from exc
    if not 1 <= value <= MAX_CONCURRENCY_CEILING:
        raise ValueError("PORTAL_TEST_MAX_CONCURRENCY must be an integer from 1 to 32")
    return value


def slot_root() -> Path:
    return Path(os.getenv(
        "PORTAL_TEST_SLOT_ROOT",
        str(user_state_root() / "test-slots"),
    )).expanduser()


def _open_lock(path: Path) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
                   | getattr(os, "O_BINARY", 0), 0o600)


@contextmanager
def exclusive(key: str, *, stop: Callable[[], bool], poll_seconds: float = 1.0):
    """Serialize a shared test identity without consuming a global test slot."""
    root = slot_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = hashlib.sha256(key.encode()).hexdigest()
    descriptor = _open_lock(root / f"exclusive-{name}.lock")
    try:
        while True:
            if try_lock_descriptor(descriptor):
                break
            if stop():
                yield False
                return
            time.sleep(poll_seconds)
        yield True
    finally:
        os.close(descriptor)


class SlotLease:
    def __init__(self, kind: str, descriptors: list[int]):
        self.kind = kind
        self._descriptors = descriptors

    @property
    def count(self) -> int:
        return len(self._descriptors) // 2

    def release(self) -> None:
        descriptors, self._descriptors = self._descriptors, []
        for descriptor in descriptors:
            os.close(descriptor)


def acquire(kind: str, want: int, *, stop: Callable[[], bool], poll_seconds: float = 1.0) -> SlotLease | None:
    """Take between one and ``want`` free slots, waiting until one is free; None once ``stop()``."""
    capacity = CAPACITY[kind]
    global_capacity = max_concurrency()
    want = max(1, min(int(want), capacity, global_capacity))
    root = slot_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    while True:
        descriptors: list[int] = []
        for global_index in range(global_capacity):
            if len(descriptors) // 2 >= want:
                break
            global_descriptor = _open_lock(root / f"global-{global_index}.lock")
            try:
                locked = try_lock_descriptor(global_descriptor)
            except BaseException:
                os.close(global_descriptor)
                raise
            if not locked:
                os.close(global_descriptor)
                continue
            for kind_index in range(capacity):
                kind_descriptor = _open_lock(root / f"{kind}-{kind_index}.lock")
                try:
                    locked = try_lock_descriptor(kind_descriptor)
                except BaseException:
                    os.close(kind_descriptor)
                    os.close(global_descriptor)
                    raise
                if not locked:
                    os.close(kind_descriptor)
                    continue
                descriptors.extend((global_descriptor, kind_descriptor))
                break
            else:
                os.close(global_descriptor)
        if descriptors:
            return SlotLease(kind, descriptors)
        if stop():
            return None
        time.sleep(poll_seconds)
