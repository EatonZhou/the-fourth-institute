from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from management_portal.shared_state import exclusive_file_lock


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
