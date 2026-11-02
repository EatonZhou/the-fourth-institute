from __future__ import annotations

import hashlib
import threading
import weakref
from functools import wraps
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from management_portal.shared_state import exclusive_file_lock


_MUTATIONS_GUARD = threading.Lock()
_MUTATIONS = weakref.WeakValueDictionary()


@contextmanager
def mutation_projection_scope(resource_id: str) -> Iterator[None]:
    """Order one resource's domain commit and projection in ResourceService.

    The service owns the index. Reentrancy lets its adapters use the same
    boundary for direct source reads; other resources remain independent.
    """
    with _MUTATIONS_GUARD:
        lock = _MUTATIONS.get(resource_id)
        if lock is None:
            lock = threading.RLock()
            _MUTATIONS[resource_id] = lock
    with lock:
        yield


def serialized_mutation(function):
    @wraps(function)
    def wrapped(service, resource_id, *args, **kwargs):
        with mutation_projection_scope(str(resource_id).strip()):
            return function(service, resource_id, *args, **kwargs)
    return wrapped


@contextmanager
def resource_mutation_lock(
    state_root: str | Path,
    resource_id: str,
) -> Iterator[None]:
    """Serialize one resource without blocking mutations of other resources."""

    clean_id = str(resource_id or "").strip()
    if not clean_id:
        raise ValueError("resource_id is required for mutation locking")
    digest = hashlib.sha256(clean_id.encode("utf-8")).hexdigest()
    lock_path = Path(state_root) / ".resource-locks" / f"{digest}.lock"
    with exclusive_file_lock(lock_path):
        yield
