"""Order durable execution admission against directory move reservations.

Callers acquire Resident state / Lineage execution authority first. Admission
holds the shared gate through durable submit or native launch. A mover commits
its reservations before taking the exclusive gate to discover existing users;
it must not acquire Resident state or Lineage authority while holding that gate.
Reservation transactions end before external execution calls or termination.
"""
from contextlib import contextmanager
import hashlib

from management_common.directory_cleanup_store import get_directory_cleanup_store
from .state_store import get_lineage_data_store


@contextmanager
def authority(*, shared=False):
    database = get_lineage_data_store().database
    key = int.from_bytes(hashlib.sha256(
        (database.schema + ':directory-execution-admission').encode()).digest()[:8], 'big', signed=True)
    with database.advisory_lock(key, shared=shared):
        yield


@contextmanager
def admit(paths):
    queue = get_directory_cleanup_store()
    with authority(shared=True):
        with queue.database.transaction() as connection:
            for path in paths:
                if path:
                    queue.assert_available(path, connection=connection)
        yield
