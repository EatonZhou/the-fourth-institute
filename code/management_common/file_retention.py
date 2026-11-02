"""File writers declare their category at the write/read boundary."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from functools import wraps

from .directory_retention import RetentionStore, enqueue_directory_cleanup, utc_now
from .directory_paths import cleanup_location
from .process_observation import process_identity, process_is_alive
from .lifetime_execution_lease import execution_lease


CACHE_CAPACITIES = {'status': 32 * 1024**2, 'usage': 256 * 1024**2,
                    'presentation': 512 * 1024**2, 'devices': 32 * 1024**2,
                    'article': 2 * 1024**3, 'paper_ccf': 512 * 1024**2,
                    'paper_text': 512 * 1024**2, 'paper_pdf': 2 * 1024**3}


def cache_operation(*, group):
    """Keep a cache path (the first argument) leased for a complete operation."""
    def decorate(function):
        @wraps(function)
        def run(path, *args, **kwargs):
            with cache_file(path, group=group):
                return function(path, *args, **kwargs)
        return run
    return decorate


@contextmanager
def cache_file(path, *, group, owner_id=None):
    """Serialize cache access with eviction; each cache owns its byte budget."""
    store = RetentionStore()
    location = cleanup_location(path.absolute())
    from .cache_retention_lock import cache_lease
    with cache_lease(path, shared=True):
        store.declare(path.absolute(), category='cache', owner_id=owner_id or 'cache:' + sha256(location.encode()).hexdigest(),
                      cache_group=group)
        try:
            yield
        finally:
            if path.exists():
                size = _size(path)
                with store.database.transaction() as connection:
                    store.queue.lock(connection)
                    connection.execute(f'UPDATE {store.table} SET last_access=%s,bytes=%s WHERE location=%s',
                                       (utc_now(), size, location))


def _size(path):
    import os
    if not path.is_dir() or path.is_symlink():
        return path.lstat().st_size
    return sum((Path(root) / name).lstat().st_size
               for root, _directories, files in os.walk(path, followlinks=False) for name in files)


def provider_session_ended(path, *, owner_id, ended_at=None):
    """Only call for an instance-owned session after the Provider confirmed end."""
    store = RetentionStore()
    at = ended_at or utc_now()
    with store.database.transaction() as connection:
        store.declare(path, category='provider_session', owner_id=owner_id, finished_at=at, connection=connection)
        store.finish(owner_id, at=at, connection=connection)


def remove_generated(path, *, owner_id):
    """An owner has confirmed runtime work ended; queue even occupied files."""
    from uuid import uuid4
    from .directory_cleanup_store import get_directory_cleanup_store
    queue = get_directory_cleanup_store()
    with queue.database.transaction() as connection:
        identifier = enqueue_directory_cleanup(path.absolute(), owner_id=owner_id, operation_id='runtime-end:' + uuid4().hex,
                                   actor='directory-retention', connection=connection)
    queue.run_pending()
    return queue.status(cleanup_id=identifier)[0]


_TEMPORARY_PURPOSE = 'temporary-directory-v1:'


def _temporary_lock_key(store, operation):
    return int.from_bytes(sha256((store.database.schema + ':' + operation).encode()).digest()[:8],
                          'big', signed=True)


def _temporary_metadata(row):
    import json
    return json.loads(row['purpose'][len(_TEMPORARY_PURPOSE):])


def _forget_uncreated_temporary(store, row):
    """Cancel a declaration, with no filesystem effect or deletion intent."""
    with store.database.transaction() as connection:
        store.queue.lock(connection)
        connection.execute(f'DELETE FROM {store.table} WHERE location=%s AND owner_id=%s',
                           (row['location'], _temporary_metadata(row)['owner_id']))
        store.queue.release(row['operation_id'], connection=connection)


def _end_temporary(store, row):
    with execution_lease(row['operation_id'], namespace=store.database.schema, blocking=False) as acquired:
        if not acquired:
            return None
        return _cleanup_temporary(store, row)


def _cleanup_temporary(store, row):
    import os
    from .directory_paths import cleanup_path
    path = cleanup_path(row['location'])
    metadata = _temporary_metadata(row)
    if metadata['identity'] is None:
        # mkdir and the identity commit cannot be atomic across DB/filesystem.
        # An interrupted declaration never proves an existing entry is ours.
        if os.path.lexists(path):
            raise FileExistsError('Unproven temporary allocation retained: ' + str(path))
        _forget_uncreated_temporary(store, row)
        return None
    with store.database.transaction() as connection:
        identifier = store.enqueue_cleanup(path, owner_id=metadata['owner_id'],
            operation_id=row['operation_id'], actor='temporary-directory', connection=connection)
    return store.queue.run_reserved(cleanup_id=identifier, operation_id=row['operation_id'],
                                   identity=metadata['identity'])


def recover_temporary_directories():
    """Acquire abandoned allocation leases; never infer end from file age.

    Only this helper's versioned reservations authorize recovery. Other runtime
    artifacts keep their own end protocol. Reservations also fence generic
    deletion while a writer is alive or identity-checked cleanup is pending.
    """
    from .directory_paths import cleanup_path
    store = RetentionStore()
    report = {'active': [], 'cleanup': [], 'errors': []}
    with store.database.read() as connection:
        rows = connection.execute(f'SELECT * FROM {store.queue.reservations} WHERE starts_with(purpose,%s)',
                                  (_TEMPORARY_PURPOSE,)).fetchall()
    for candidate in rows:
        operation = candidate['operation_id']
        try:
            with store.database.advisory_lock(_temporary_lock_key(store, operation), blocking=False) as acquired:
                if not acquired:
                    report['active'].append(str(cleanup_path(candidate['location'])))
                    continue
                # Another recovery or normal exit can finish between scan/lock.
                with store.database.read() as connection:
                    row = connection.execute(f'SELECT * FROM {store.queue.reservations} '
                                             'WHERE operation_id=%s AND location=%s',
                                             (operation, candidate['location'])).fetchone()
                if row:
                    metadata = _temporary_metadata(row)
                    if metadata.get('ended') is not True and process_is_alive(metadata.get('process_owner')):
                        report['active'].append(str(cleanup_path(row['location'])))
                        continue
                    outcome = _end_temporary(store, row)
                    if outcome:
                        report['cleanup'].append(outcome)
                        if outcome['last_error']:
                            report['errors'].append(outcome['last_error'])
                    elif metadata['identity'] is not None:
                        report['active'].append(str(cleanup_path(row['location'])))
        except Exception as error:
            report['errors'].append(f'{operation}: {error}')
    return report


@contextmanager
def temporary_directory(*, prefix, dir=None):
    """Declare before exclusive creation; record owner identity and explicit end.

    The reservation journals allocation identity without a new schema. End is
    either this context's durable exit receipt or proven owner process death,
    together with exclusive execution admission after native users end.
    Occupied files retain the reservation and the shared cleanup retry receipt.
    """
    import json
    import os
    import tempfile
    from uuid import uuid4
    from . import secure_paths as fs
    from .resource_lifecycle import current_lifecycle, LifecycleStore
    from .directory_paths import lexical_path
    if not isinstance(prefix, str) or '/' in prefix or '\\' in prefix:
        raise ValueError('Temporary directory prefix must be a file name prefix')
    operation = 'temporary:' + uuid4().hex
    path = lexical_path(Path(dir or tempfile.gettempdir()) / (prefix + uuid4().hex))
    lifecycle = current_lifecycle()
    scope = LifecycleStore().scope(lifecycle) if lifecycle else None
    owner = scope['run_id'] if scope else operation
    store = RetentionStore()
    # Nested transactions would roll back a supposedly durable predeclaration.
    current = store.database._current_connection()
    if current is not None and current.in_transaction:
        raise RuntimeError('Temporary allocation must precede the owner transaction')
    metadata = {'owner_id': owner, 'identity': None,
                'process_owner': process_identity(), 'ended': False}
    row = {'operation_id': operation, 'location': cleanup_location(path),
           'purpose': _TEMPORARY_PURPOSE + json.dumps(metadata)}
    with store.database.advisory_lock(_temporary_lock_key(store, operation)):
        reserved = False
        try:
            with execution_lease(operation, namespace=store.database.schema):
                with fs.open_directory(path.parent) as parent:
                    with store.database.transaction() as connection:
                        store.queue.lock(connection)
                        if os.path.lexists(path):
                            raise FileExistsError(str(path))
                        store.declare(path, category='runtime_tmp', owner_id=owner, connection=connection)
                        store.queue.reserve(path, operation_id=operation, purpose=row['purpose'], connection=connection)
                    reserved = True
                    try:
                        with fs.private_child(parent, path.name, create=True) as directory:
                            fs.sync_directory(parent)
                            info = os.fstat(directory)
                            metadata['identity'] = [info.st_dev, info.st_ino]
                            row['purpose'] = _TEMPORARY_PURPOSE + json.dumps(metadata)
                            store.database.write(lambda connection: connection.execute(
                                f'UPDATE {store.queue.reservations} SET purpose=%s WHERE operation_id=%s AND location=%s',
                                (row['purpose'], operation, row['location'])))
                    except FileExistsError:
                        # Exclusive creation lost to an existing/racing entry. It has
                        # never become our allocation and must never enter cleanup.
                        _forget_uncreated_temporary(store, row)
                        reserved = False
                        raise
                yield str(path)
        finally:
            if reserved:
                metadata['ended'] = True
                row['purpose'] = _TEMPORARY_PURPOSE + json.dumps(metadata)
                store.database.write(lambda connection: connection.execute(
                    f'UPDATE {store.queue.reservations} SET purpose=%s WHERE operation_id=%s AND location=%s',
                    (row['purpose'], operation, row['location'])))
                _end_temporary(store, row)
