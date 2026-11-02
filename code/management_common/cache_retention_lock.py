"""Per-artifact leases let long cache builders coexist with expiry scans."""
from contextlib import contextmanager
from hashlib import sha256

from .directory_paths import cleanup_location
from .lifetime_execution_lease import execution_lease
from .portal_database import portal_database


@contextmanager
def cache_lease(path, *, blocking=True, shared=False):
    database = portal_database(schema='directory_retention')
    identity = database.schema + ':cache:' + cleanup_location(path.absolute())
    key = int.from_bytes(sha256(identity.encode()).digest()[:8], 'big', signed=True)
    # Keep PG ordering, but only the actual execution lease proves that a
    # filesystem user ended. Losing this session must not admit eviction.
    with database.advisory_lock(key, blocking=blocking, shared=shared) as acquired:
        if not acquired:
            yield False
            return
        with execution_lease(identity, namespace=database.schema,
                             blocking=blocking, shared=shared) as running:
            yield running
