"""Same-host execution leases, independent of database session lifetime.

The selected instance runtime and database schema name define the registry.
Each holder owns a unique exclusive OS file lock; shared holders can coexist.
Only registration, inspection and removal hold a shard's coordination lock.
The 64 control inodes (and directories) are bounded current infrastructure and
must never be removed while the instance runs. They are not artifact history.

Holder files are opened/probed only under that guard, never waited upon. Release
closes then unlinks under the same guard (including on Windows); nobody can hold
an opened old inode across removal and reacquire it later. Each scan reclaims
unlocked crash remnants throughout its shard, even for unrelated identities.
Unknown filesystem/locking errors propagate, never authorizing cleanup.

process_runtime carries active leases across native launch. POSIX inherits the
locked open description; durable tree receipts also cover descendants which
close inherited descriptors. Windows uses named Jobs, never CRT inheritance.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import json
import os
import time
from uuid import uuid4

from . import file_lock
from .instance_manifest import instance_runtime_root


_ACTIVE = ContextVar('execution_lease_holders', default=())


def _reclaim(path):
    """Called only under the shard guard; unknown native state retains ownership."""
    from .process_runtime import native_lifetime_alive
    with path.open('r+b') as probe:
        if not file_lock.try_lock_descriptor(probe.fileno()):
            return False
        for line in probe:
            if native_lifetime_alive(json.loads(line)):
                return False
    path.unlink()
    return True


@contextmanager
def inherit_execution_leases():
    """Register native holders while their delegating contexts still own leases.

    Each spawn gets independently locked holders. This pins admission even on
    Windows, where duplicating a CRT descriptor does not transfer its byte lock.
    These are entries in the existing lease registry, not a process registry.
    """
    pinned = []
    try:
        for path, holder in _ACTIVE.get():
            # Opening the stable guard afresh also avoids sharing its flock
            # open description with a fork child.
            with path.parent.with_suffix('.lock').open('a+b') as guard, file_lock.lock_stream(guard):
                if holder.closed:
                    raise RuntimeError('Execution lease context has already ended')
                identity, mode, _, _ = path.name.split('.')
                pinned.append(_new_holder(path.parent, identity, mode == 'r'))

        def record(receipt):
            # None revokes a launch reservation only after process_runtime has
            # confirmed spawn rollback. It is never an observation of death.
            for path, holder in pinned:
                with path.parent.with_suffix('.lock').open('a+b') as guard, file_lock.lock_stream(guard):
                    # Windows byte locks are mandatory even across two handles
                    # in one process. Write through the handle owning the lock.
                    if receipt is None:
                        holder.seek(0)
                        holder.truncate()
                    else:
                        holder.seek(0, os.SEEK_END)
                        holder.write(json.dumps(receipt).encode() + b'\n')
                    holder.flush()
                    os.fsync(holder.fileno())
        yield tuple(holder.fileno() for _, holder in pinned), record, tuple(path for path, _ in pinned)
    finally:
        first_error = None
        for path, holder in pinned:
            try:
                try:
                    with path.parent.with_suffix('.lock').open('a+b') as guard, file_lock.lock_stream(guard):
                        holder.close()
                        if path.exists():
                            _reclaim(path)
                finally:
                    # A failed guard or uncertain observation must retain the
                    # receipt, never a parent-side lock through its traceback.
                    holder.close()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error


def reap_native_leases(paths):
    """Reclaim stopped process holders promptly, including normal run_process."""
    for path in paths:
        with path.parent.with_suffix('.lock').open('a+b') as guard, file_lock.lock_stream(guard):
            if path.exists():
                _reclaim(path)


def _registry(namespace, identity):
    digest = sha256(identity.encode()).hexdigest()
    root = instance_runtime_root() / 'execution-leases' / sha256(namespace.encode()).hexdigest()
    shard = root / f'{int(digest[:2], 16) % 64:02x}'
    shard.mkdir(parents=True, exist_ok=True)
    return shard, digest


def _register(shard, identity, shared):
    # Every caller holds the stable shard guard, including normal release.
    conflict = False
    for path in shard.iterdir():
        parts = path.name.split('.')
        if (len(parts) != 4 or len(parts[0]) != 64 or parts[1] not in {'r', 'w'}
                or len(parts[2]) != 32 or parts[3] != 'lease'):
            raise RuntimeError(f'Unknown execution lease entry: {path}')
        if not _reclaim(path) and parts[0] == identity and (not shared or parts[1] == 'w'):
            conflict = True
    if conflict:
        return None
    return _new_holder(shard, identity, shared)


def _new_holder(shard, identity, shared):
    path = shard / f'{identity}.{"r" if shared else "w"}.{uuid4().hex}.lease'
    holder = path.open('x+b')
    try:
        if not file_lock.try_lock_descriptor(holder.fileno()):
            raise RuntimeError(f'New execution lease is already locked: {path}')
    except BaseException:
        holder.close()
        path.unlink()
        raise
    return path, holder


@contextmanager
def execution_lease(identity, *, namespace, shared=False, blocking=True):
    """Yield acquisition; shared users conflict only with exact-identity writers."""
    shard, digest = _registry(namespace, identity)
    # This file is stable even when the shard has no active holders.
    with shard.with_suffix('.lock').open('a+b') as guard:
        registered = None
        while registered is None:
            if file_lock.try_lock_descriptor(guard.fileno()):
                try:
                    registered = _register(shard, digest, shared)
                finally:
                    file_lock.unlock_descriptor(guard.fileno())
            if registered is not None or not blocking:
                break
            time.sleep(0.05)
        if registered is None:
            yield False
            return
        path, holder = registered
        token = _ACTIVE.set((*_ACTIVE.get(), (path, holder)))
        try:
            yield True
        finally:
            _ACTIVE.reset(token)
            try:
                with shard.with_suffix('.lock').open('a+b') as release_guard, file_lock.lock_stream(release_guard):
                    holder.close()
                    # Closing our reference must not unlock/unlink a native
                    # child's still-locked open description or active Job.
                    if path.exists():
                        _reclaim(path)
            finally:
                # If guard acquisition fails, leave an unlocked remnant for
                # readback; never remove a holder without owning the guard.
                holder.close()
