"""Concurrency budget for local test processes of the instance (Dev and Release).

Each slot is one lock file; a holder keeps an exclusive ``flock`` on it for as
long as its test process runs. The kernel releases the lock when the holder
exits, so a crashed executor never leaks a slot.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import os
import time
from pathlib import Path
from typing import Callable

from management_common.file_lock import try_lock_descriptor
from management_portal.test_framework.paths import slot_root

CAPACITY = {
    # One pytest shard runs one pytest process with its own PostgreSQL.
    "pytest": 32,
    # One browser slot is one Playwright worker.
    "browser": 32,
    # Live tests act on the selected instance as test-actor, one process at a time.
    "live": 1,
}
DEFAULT_MAX_CONCURRENCY = 32
MAX_CONCURRENCY_CEILING = 32


def max_concurrency() -> int:
    """The instance's budget for framework-managed work across all test kinds."""
    raw = os.getenv("PORTAL_TEST_MAX_CONCURRENCY", str(DEFAULT_MAX_CONCURRENCY))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("PORTAL_TEST_MAX_CONCURRENCY must be an integer from 1 to 32") from exc
    if not 1 <= value <= MAX_CONCURRENCY_CEILING:
        raise ValueError("PORTAL_TEST_MAX_CONCURRENCY must be an integer from 1 to 32")
    return value


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
        with ExitStack() as closing:
            for descriptor in descriptors:
                closing.callback(os.close, descriptor)


def acquire(kind: str, want: int, *, stop: Callable[[], bool], poll_seconds: float = 1.0) -> SlotLease | None:
    """Take between one and ``want`` free slots, waiting until one is free; None once ``stop()``."""
    capacity = CAPACITY[kind]
    global_capacity = max_concurrency()
    want = max(1, min(int(want), capacity, global_capacity))
    root = slot_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    while True:
        descriptors: list[int] = []
        with ExitStack() as pending:
            for global_index in range(global_capacity):
                if len(descriptors) // 2 >= want:
                    break
                with ExitStack() as pair:
                    global_descriptor = _open_lock(root / f"global-{global_index}.lock")
                    pair.callback(os.close, global_descriptor)
                    if not try_lock_descriptor(global_descriptor):
                        continue
                    for kind_index in range(capacity):
                        with ExitStack() as candidate:
                            kind_descriptor = _open_lock(root / f"{kind}-{kind_index}.lock")
                            candidate.callback(os.close, kind_descriptor)
                            if not try_lock_descriptor(kind_descriptor):
                                continue
                            descriptors.extend((global_descriptor, kind_descriptor))
                            pair.enter_context(candidate.pop_all())
                            pending.enter_context(pair.pop_all())
                            break
            if descriptors:
                lease = SlotLease(kind, descriptors)
                # Until this transfer, any failure releases every acquired
                # pair as well as the candidate currently being examined.
                pending.pop_all()
                return lease
        if stop():
            return None
        time.sleep(poll_seconds)
