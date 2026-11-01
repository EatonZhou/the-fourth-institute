"""Durable file-first directory moves with owner-enlisted registration commits.

The journal contains operation snapshots, never current object locations. Owners
supply their registration mutation and dependency reconciliation. Recovery before
commit restores the source/removes the partial copy; after commit it only retries
dependencies, then enqueues old-directory cleanup.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import os
import shutil
from uuid import uuid4

from .directory_cleanup_store import get_directory_cleanup_store
from .directory_paths import (
    cleanup_location, cleanup_path, lexical_path, remove_path, validate_removal,
    rename_directory_no_replace, validate_filename,
)
from .operation_errors import OperationError

_LOCKED = ContextVar('directory_move_locks', default=frozenset())


def initialize_move_schema(connection, schema):
    connection.execute(f'''CREATE TABLE IF NOT EXISTS "{schema}".moves (
        operation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, actor TEXT NOT NULL,
        source TEXT NOT NULL, target TEXT NOT NULL, mode TEXT NOT NULL,
        staging TEXT NOT NULL DEFAULT '', file_identity TEXT NOT NULL DEFAULT '',
        staging_identity TEXT NOT NULL DEFAULT '',
        phase TEXT NOT NULL CHECK(phase IN ('prepared','files_ready','committed','completed','aborted','cancelled')),
        last_error TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    )''')
    columns = {row[0] for row in connection.execute(
        'SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s',
        (schema, 'moves')).fetchall()}
    if not {'staging', 'file_identity', 'staging_identity'} <= columns:
        raise RuntimeError('Directory move safety requires the offline maintenance migration')


def migrate_move_safety_schema(connection, schema):
    """Offline only; never infer ownership of a legacy move's current paths."""
    table = f'"{schema}".moves'
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None:
        initialize_move_schema(connection, schema)
        return
    columns = {row[0] for row in connection.execute(
        'SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s',
        (schema, 'moves')).fetchall()}
    if {'staging', 'file_identity', 'staging_identity'} <= columns:
        return
    if not {'staging', 'file_identity'} <= columns:
        pending = connection.execute(f"SELECT operation_id FROM {table} "
            "WHERE phase NOT IN ('completed','aborted','cancelled')").fetchall()
        if pending:
            raise RuntimeError('Resolve legacy directory moves before safety migration: ' +
                               ', '.join(row['operation_id'] for row in pending))
    connection.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS staging TEXT NOT NULL DEFAULT ''")
    connection.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS file_identity TEXT NOT NULL DEFAULT ''")
    connection.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS staging_identity TEXT NOT NULL DEFAULT ''")


def _directory_identity(path):
    """The published entry must be the very directory captured before rename."""
    if path.is_symlink() or not path.is_dir():
        return ''
    status = path.lstat()
    return f'{status.st_dev}:{status.st_ino}'


def relocate_pending_cleanup(connection, relocate):
    """Enlist deletion intents created after move preparation in its commit.

    Pre-existing pending paths block preparation. Owners may unregister an
    artifact while files are being prepared, so still-pending snapshots must
    follow those files. Completed receipts remain historical evidence.
    """
    from .directory_cleanup_store import DirectoryCleanupStore
    queue = DirectoryCleanupStore(connection=connection)
    queue.lock(connection)
    rows = connection.execute(f'SELECT cleanup_id,location FROM {queue.table} '
                              'WHERE completed_at IS NULL FOR UPDATE').fetchall()
    for row in rows:
        location = relocate(row['location'])
        if location != row['location']:
            connection.execute(f'UPDATE {queue.table} SET location=%s WHERE cleanup_id=%s',
                               (location, row['cleanup_id']))


class DirectoryMoveStore:
    def __init__(self):
        self.cleanup = get_directory_cleanup_store()
        self.database = self.cleanup.database
        self.table = f'"{self.database.schema}".moves'
        self.database.write(lambda c: initialize_move_schema(c, self.database.schema))

    @contextmanager
    def operation_lock(self, operation_id):
        # Session lock spans the file operation without keeping a DB transaction
        # open; another recovery worker cannot delete an in-progress copy.
        if operation_id in _LOCKED.get():
            yield
            return
        key = int.from_bytes(hashlib.sha256(operation_id.encode()).digest()[:8], 'big', signed=True)
        with self.database.advisory_lock(key):
            token = _LOCKED.set(_LOCKED.get() | {operation_id})
            try:
                yield
            finally:
                _LOCKED.reset(token)

    def get(self, operation_id, *, connection=None):
        if connection is None:
            with self.database.read() as connection:
                return self.get(operation_id, connection=connection)
        row = connection.execute(f'SELECT * FROM {self.table} WHERE operation_id=%s', (operation_id,)).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return dict(row)

    def _phase(self, operation_id, phase, *, connection=None, error=''):
        def write(c):
            c.execute(f"UPDATE {self.table} SET phase=%s,last_error=%s,updated_at=clock_timestamp() WHERE operation_id=%s AND phase<>'cancelled'",
                      (phase, str(error)[:4000], operation_id))
        return write(connection) if connection is not None else self.database.write(write)

    def prepare(self, source, target, *, owner_id, origin, actor, terminate, operation_id=None):
        if origin != 'created':
            raise ValueError('Only created directories can be moved by Portal')
        source, target = lexical_path(source), lexical_path(target)
        validate_filename(target)
        operation_id = operation_id or 'directory-move_' + uuid4().hex
        with self.operation_lock(operation_id):
            try:
                row = self.get(operation_id)
            except KeyError:
                pass
            else:
                if (row['owner_id'], row['actor'], row['source'], row['target']) != (
                        owner_id, actor, cleanup_location(source), cleanup_location(target)):
                    raise ValueError('Directory move operation ID was already used with different arguments')
                # The caller's update result is not a journal receipt. Never
                # replay that mutation or pretend to reconstruct its result.
                raise OperationError('Directory move already exists: ' + row['phase'],
                    code='directory.move_operation_exists', http_status=409,
                    commit_state='committed' if row['phase'] in {'committed', 'completed', 'cancelled'} else 'not_committed',
                    details={'operation_id': operation_id, 'phase': row['phase']})
            return self._prepare(source, target, owner_id=owner_id, actor=actor,
                                 terminate=terminate, operation_id=operation_id)

    def _prepare(self, source, target, *, owner_id, actor, terminate, operation_id):
        validate_removal(source)
        validate_removal(target)
        if source == target or source in target.parents or target in source.parents:
            raise ValueError('Move source and target must be disjoint')
        if source.is_symlink() or not source.is_dir():
            raise ValueError('Directory move requires a real source directory')
        with self.operation_lock(operation_id):
            with self.database.transaction() as connection:
                self.cleanup.reserve(source, operation_id=operation_id, purpose='move-source', connection=connection)
                self.cleanup.reserve(target, operation_id=operation_id, purpose='move-target', connection=connection)
                if os.path.lexists(target):
                    raise FileExistsError(target)
                parent = target.parent
                while not parent.exists():
                    parent = parent.parent
                mode = 'rename' if source.stat().st_dev == parent.stat().st_dev else 'copy'
                staging = target.parent / ('.tfi-move-' + hashlib.sha256(operation_id.encode()).hexdigest())
                if mode == 'copy':
                    self.cleanup.reserve(staging, operation_id=operation_id, purpose='move-staging', connection=connection)
                    if os.path.lexists(staging):
                        raise FileExistsError(staging)
                connection.execute(f'INSERT INTO {self.table}(operation_id,owner_id,actor,source,target,mode,phase,staging,file_identity) '
                                   "VALUES (%s,%s,%s,%s,%s,%s,'prepared',%s,%s)",
                                   (operation_id, owner_id, actor, cleanup_location(source), cleanup_location(target), mode,
                                    cleanup_location(staging) if mode == 'copy' else '',
                                    _directory_identity(source) if mode == 'rename' else ''))
            try:
                if mode == 'copy':
                    terminate(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                if mode == 'rename':
                    rename_directory_no_replace(source, target)
                else:
                    staging.mkdir(mode=0o700)
                    identity = _directory_identity(staging)
                    self.database.write(lambda c: c.execute(
                        f'UPDATE {self.table} SET staging_identity=%s WHERE operation_id=%s', (identity, operation_id)))
                    with (staging / 'operation').open('x', encoding='utf-8') as stream:
                        stream.write(operation_id)
                        stream.flush()
                        os.fsync(stream.fileno())
                    payload = staging / 'directory'
                    shutil.copytree(source, payload, symlinks=True)
                    identity = _directory_identity(payload)
                    self.database.write(lambda c: c.execute(
                        f'UPDATE {self.table} SET file_identity=%s WHERE operation_id=%s', (identity, operation_id)))
                    rename_directory_no_replace(payload, target)
                self._phase(operation_id, 'files_ready')
                error = self._remove_staging(self.get(operation_id))
                if error:
                    raise FileExistsError(error)
            except BaseException:
                self.abort(operation_id)
                raise
        return operation_id

    def commit(self, operation_id, *, connection, update):
        """Owner registrations and the journal's cleanup intent share this commit."""
        self.cleanup.lock(connection)
        row = self.get(operation_id, connection=connection)
        if row['phase'] != 'files_ready':
            raise ValueError('Directory files are not ready for registration commit')
        source, target = cleanup_path(row['source']), cleanup_path(row['target'])
        if row['mode'] != 'external' and _directory_identity(target) != row['file_identity']:
            raise FileExistsError('Move destination was replaced before registration commit')
        self.cleanup.assert_available(target, connection=connection, operation_id=operation_id)
        self.cleanup.release(operation_id, connection=connection)
        result = update(connection, source, target)
        if row['mode'] != 'external':
            # Keep the old files and name until dependency recovery completes.
            # The committed journal is the durable intent to enqueue cleanup.
            self.cleanup.reserve(source, operation_id=operation_id, purpose='move-dependencies', connection=connection)
        self._phase(operation_id, 'committed', connection=connection)
        return result

    def move(self, source, target, *, owner_id, origin, actor, terminate, database, update, dependencies, operation_id=None):
        operation_id = operation_id or 'directory-move_' + uuid4().hex
        with self.operation_lock(operation_id):
            self.prepare(source, target, owner_id=owner_id, origin=origin, actor=actor,
                         terminate=terminate, operation_id=operation_id)
            try:
                result = database.write(lambda c: self.commit(operation_id, connection=c, update=update))
            except BaseException:
                self.abort(operation_id)
                raise
            self.finish(operation_id, dependencies=dependencies)
        return operation_id, result

    def register_external_move(self, source, target, *, owner_id, actor, database, update, dependencies):
        """The owner moved an adopted directory; Portal only updates registration."""
        source, target = lexical_path(source), lexical_path(target)
        if not target.is_dir() or target.is_symlink() or source == target:
            raise ValueError('External relocation requires an existing real destination')
        operation_id = 'directory-move_' + uuid4().hex
        with self.operation_lock(operation_id):
            with self.database.transaction() as connection:
                self.cleanup.reserve(target, operation_id=operation_id, purpose='external-move', connection=connection)
                connection.execute(f'INSERT INTO {self.table}(operation_id,owner_id,actor,source,target,mode,phase) '
                    "VALUES (%s,%s,%s,%s,%s,'external','files_ready')",
                    (operation_id, owner_id, actor, cleanup_location(source), cleanup_location(target)))
            try:
                result = database.write(lambda c: self.commit(operation_id, connection=c, update=update))
            except BaseException:
                self.abort(operation_id)
                raise
            self.finish(operation_id, dependencies=dependencies)
        return operation_id, result

    def finish(self, operation_id, *, dependencies):
        with self.operation_lock(operation_id):
            row = self.get(operation_id)
            if row['phase'] == 'completed':
                return row
            if row['phase'] != 'committed':
                raise ValueError('Directory registration has not committed')
            try:
                dependencies(cleanup_path(row['source']), cleanup_path(row['target']))
            except Exception as exc:
                self._phase(operation_id, 'committed', error=exc)
                raise OperationError(str(exc), code='directory.move_dependencies_pending',
                                     commit_state='committed', details={'operation_id': operation_id}) from exc
            with self.database.transaction() as connection:
                self.cleanup.lock(connection)
                # Owner deletion can cancel while dependency I/O is running.
                if self.get(operation_id, connection=connection)['phase'] != 'committed':
                    return self.get(operation_id, connection=connection)
                if row['mode'] != 'external':
                    self.cleanup.enqueue(cleanup_path(row['source']), owner_id=row['owner_id'],
                        operation_id=operation_id, actor=row['actor'], connection=connection)
                self.cleanup.release(operation_id, connection=connection)
                connection.execute(f"UPDATE {self.table} SET phase='completed',last_error='',updated_at=clock_timestamp() "
                                   "WHERE operation_id=%s AND phase='committed'", (operation_id,))
            return self.get(operation_id)

    def _remove_staging(self, row):
        if not row['staging']:
            return ''
        staging = cleanup_path(row['staging'])
        if not os.path.lexists(staging) and not row['staging_identity']:
            return ''
        # A marker can be copied to a replacement directory. Only the inode
        # recorded after exclusive mkdir proves ownership, including crashes
        # before the marker was written. Legacy/unproven stages stay untouched.
        if not row['staging_identity'] or (os.path.lexists(staging) and _directory_identity(staging) != row['staging_identity']):
            return 'Move staging directory ownership cannot be verified; preserved: ' + str(staging)
        remove_path(staging, identity=tuple(map(int, row['staging_identity'].split(':'))))
        return ''

    def abort(self, operation_id):
        with self.operation_lock(operation_id):
            return self._abort(operation_id)

    def _abort(self, operation_id):
        row = self.get(operation_id)
        if row['phase'] not in {'prepared', 'files_ready'}:
            return
        source, target = cleanup_path(row['source']), cleanup_path(row['target'])
        owned_target = bool(row['file_identity']) and _directory_identity(target) == row['file_identity']
        if row['mode'] == 'rename' and owned_target:
            if os.path.lexists(source):
                raise FileExistsError('Both sides of an uncommitted rename exist')
            rename_directory_no_replace(target, source)
        elif row['mode'] == 'copy' and row['file_identity']:
            identity = tuple(map(int, row['file_identity'].split(':')))
            if owned_target or not os.path.lexists(target):
                remove_path(target, identity=identity)
            else:
                # A target that won the no-replace race was never ours. A
                # previous abort may nevertheless have claimed our own copy
                # before another host entry appeared at the public name.
                from .owned_path_deletion import OwnedPathDeletionStore
                OwnedPathDeletionStore().resume(target, identity)
        elif row['mode'] == 'rename' and _directory_identity(source) != row['file_identity']:
            raise FileExistsError('Original move directory is missing or replaced; recovery requires inspection')
        error = self._remove_staging(row)
        with self.database.transaction() as connection:
            self.cleanup.release(operation_id, connection=connection)
            if error:
                self.cleanup.reserve(cleanup_path(row['staging']), operation_id=operation_id,
                                     purpose='move-unproven-staging', connection=connection)
            self._phase(operation_id, 'aborted', connection=connection, error=error)

    def recover(self, *, dependencies):
        with self.database.read() as connection:
            ids = [r['operation_id'] for r in connection.execute(
                f"SELECT operation_id FROM {self.table} WHERE phase NOT IN ('completed','aborted','cancelled') ORDER BY created_at",
            ).fetchall()]
        results = []
        for operation_id in ids:
            with self.operation_lock(operation_id):
                row = self.get(operation_id)
                try:
                    if row['phase'] in {'prepared', 'files_ready'}:
                        self.abort(operation_id)
                    elif row['phase'] == 'committed':
                        self.finish(operation_id, dependencies=dependencies)
                except Exception as exc:
                    self._phase(operation_id, row['phase'], error=exc)
                results.append(self.get(operation_id))
        return results


def cancel_owner_moves(connection, owner_id):
    """A deleted owner has no remaining session/path dependencies to publish."""
    from .portal_database import _database_schema
    table = f'"{_database_schema("directory_lifecycle")}".moves'
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0]:
        queue = get_directory_cleanup_store()
        queue.lock(connection)
        operations = connection.execute(f"UPDATE {table} SET phase='cancelled',updated_at=clock_timestamp() "
                           "WHERE owner_id=%s AND phase='committed' RETURNING *", (owner_id,)).fetchall()
        for row in operations:
            if row['mode'] != 'external':
                queue.enqueue(cleanup_path(row['source']), owner_id=row['owner_id'],
                    operation_id=row['operation_id'], actor=row['actor'], connection=connection)
            queue.release(row['operation_id'], connection=connection)
