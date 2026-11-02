"""File writers declare their category at the write/read boundary."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from functools import wraps

from .directory_retention import RetentionStore, enqueue_directory_cleanup, utc_now
from .directory_paths import cleanup_location


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


@contextmanager
def temporary_directory(*, prefix, dir=None):
    """TemporaryDirectory with durable end cleanup, including occupied files."""
    import tempfile
    from pathlib import Path
    from uuid import uuid4
    from .resource_lifecycle import current_lifecycle, LifecycleStore
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=dir)).absolute()
    lifecycle = current_lifecycle()
    scope = LifecycleStore().scope(lifecycle) if lifecycle else None
    owner = scope['run_id'] if scope else 'temporary:' + uuid4().hex
    store = RetentionStore()
    store.declare(path, category='runtime_tmp', owner_id=owner)
    try:
        yield str(path)
    finally:
        # Release this one directory, not a sibling temporary allocation owned
        # by the same test Run which may still be executing concurrently.
        store.release_path(path)
        store.queue.run_pending()
