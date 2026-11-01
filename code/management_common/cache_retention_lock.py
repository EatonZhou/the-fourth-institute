"""Per-artifact leases let long cache builders coexist with expiry scans."""
from contextlib import contextmanager
from hashlib import sha256

from .directory_paths import cleanup_location
from .portal_database import portal_database


@contextmanager
def cache_lease(path, *, blocking=True, shared=False):
    database = portal_database(schema='directory_retention')
    identity = database.schema + ':cache:' + cleanup_location(path.absolute())
    key = int.from_bytes(sha256(identity.encode()).digest()[:8], 'big', signed=True)
    # Dedicated session locks disappear when a builder dies. They neither hold
    # a database transaction across compilation nor leave permanent lock files.
    with database.advisory_lock(key, blocking=blocking, shared=shared) as acquired:
        yield acquired
