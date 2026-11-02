"""Durable file-first directory moves with owner-enlisted registration commits.

The journal contains operation snapshots, never current object locations. Owners
supply their registration mutation and dependency reconciliation. Recovery before
commit restores the source/removes the partial copy; after commit it only retries
dependencies, then enqueues old-directory cleanup.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import logging
import os
import shutil
import threading
from uuid import uuid4

from .directory_cleanup_store import get_directory_cleanup_store
from .directory_retention import enqueue_directory_cleanup
from .directory_paths import (
    cleanup_location, cleanup_path, lexical_path, remove_path, validate_removal,
    rename_directory_no_replace, validate_filename,
)
from .operation_errors import OperationError
from .process_observation import process_identity, process_is_alive

_LOCKED = ContextVar('directory_move_locks', default={})
_WRITER_PREFIX = 'directory-move-writer-v1:'


class MoveWriterUnavailable(OperationError):
    """The previous writer has not ended, or this execution lost authority."""

    code = 'directory.move_writer_unavailable'
    http_status = 409


def _receipt(purpose):
    if not purpose.startswith(_WRITER_PREFIX):
        return None
    try:
        value = json.loads(purpose[len(_WRITER_PREFIX):])
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _writer_has_ended(receipt, operation_id):
    return bool(receipt and receipt.get('operation_id') == operation_id
                and isinstance(receipt.get('token'), str) and receipt['token']
                and receipt.get('kind') == 'move-source'
                and (receipt.get('ended') is True or not process_is_alive(receipt.get('process'))))


class _Writer:
    def __init__(self, operation_id, session):
        self.operation_id = operation_id
        self.session = session
        self.process = process_identity()
        self.thread = threading.current_thread()
        self.context = ContextVar('directory_move_execution')
        self.context_token = self.context.set(self)
        self.active = True
        self.location = None
        self.receipt = {'operation_id': operation_id, 'token': uuid4().hex,
                        'process': self.process, 'ended': False, 'kind': 'move-source'}

    def check_execution(self):
        if (not self.active or self.thread is not threading.current_thread()
                or self.process != process_identity()):
            raise MoveWriterUnavailable('Directory move belongs to a different execution')
        # ContextVar values are copied by copy_context/create_task. A Token can
        # only be reset in its originating Context, even on the same thread.
        try:
            self.context.reset(self.context_token)
        except (ValueError, RuntimeError) as exc:
            raise MoveWriterUnavailable('Directory move context was copied') from exc
        self.context_token = self.context.set(self)

    def check_session(self):
        self.check_execution()
        try:
            self.session.execute('SELECT 1')
        except Exception as exc:
            raise MoveWriterUnavailable('Directory move writer lost its PostgreSQL session') from exc

    def purpose(self, *, ended=False):
        return _WRITER_PREFIX + json.dumps({**self.receipt, 'ended': ended}, sort_keys=True)


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

    def _execution_key(self, operation_id):
        return self.database.dsn, self.database.schema, operation_id

    def _writer(self, operation_id):
        writer = _LOCKED.get().get(self._execution_key(operation_id))
        if writer is None:
            raise MoveWriterUnavailable('Directory move has no owning execution')
        writer.check_session()
        return writer

    def _assert_writer(self, operation_id, connection):
        writer = self._writer(operation_id)
        if writer.location is not None:
            row = connection.execute(f'SELECT purpose FROM {self.cleanup.reservations} '
                'WHERE operation_id=%s AND location=%s', (operation_id, writer.location)).fetchone()
            if row is None or row['purpose'] != writer.purpose():
                raise MoveWriterUnavailable('Directory move writer receipt changed')
        return writer

    def _check_writer(self, operation_id):
        self._writer(operation_id)  # reject copied/forked execution before using any DB connection
        with self.database.read() as connection:
            return self._assert_writer(operation_id, connection)

    @contextmanager
    def operation_lock(self, operation_id, *, connection=None):
        key = self._execution_key(operation_id)
        if key in _LOCKED.get():
            self._check_writer(operation_id)
            yield
            return
        # Own the actual session, not just a boolean returned by advisory_lock.
        # Session loss fences later effects, but is never proof of writer end.
        import psycopg
        lock_key = int.from_bytes(hashlib.sha256(operation_id.encode()).digest()[:8], 'big', signed=True)
        with psycopg.connect(self.database.dsn, autocommit=True, connect_timeout=30) as session:
            if not session.execute('SELECT pg_try_advisory_lock(%s)', (lock_key,)).fetchone()[0]:
                raise MoveWriterUnavailable('Directory move writer is active')
            writer = _Writer(operation_id, session)
            token = _LOCKED.set({**_LOCKED.get(), key: writer})
            try:
                def claim(c):
                    self.cleanup.lock(c)
                    writer.check_session()
                    try:
                        row = self.get(operation_id, connection=c)
                    except KeyError:
                        return
                    receipts = c.execute(f'SELECT location,purpose FROM {self.cleanup.reservations} '
                        'WHERE operation_id=%s AND purpose LIKE %s',
                        (operation_id, _WRITER_PREFIX + '%')).fetchall()
                    if not receipts and row['phase'] in {'completed', 'aborted', 'cancelled'}:
                        return
                    previous = _receipt(receipts[0]['purpose']) if len(receipts) == 1 else None
                    if not _writer_has_ended(previous, operation_id):
                        raise MoveWriterUnavailable('Previous directory move writer has no proven end')
                    writer.location = receipts[0]['location']
                    c.execute(f'UPDATE {self.cleanup.reservations} SET purpose=%s '
                              'WHERE operation_id=%s AND location=%s AND purpose=%s',
                              (writer.purpose(), operation_id, writer.location, receipts[0]['purpose']))
                claim(connection) if connection is not None else self.database.write(claim)
                try:
                    yield
                finally:
                    # All synchronous copying, callbacks and rollback have now
                    # unwound. A fresh connection may attest end after lease loss;
                    # it must never publish phases or perform filesystem cleanup.
                    def end(c):
                        writer.check_execution()
                        self.cleanup.lock(c)
                        if writer.location is None:
                            return
                        changed = c.execute(f'UPDATE {self.cleanup.reservations} SET purpose=%s '
                            'WHERE operation_id=%s AND location=%s AND purpose=%s',
                            (writer.purpose(ended=True), operation_id, writer.location, writer.purpose())).rowcount
                        if changed:
                            row = self.get(operation_id, connection=c)
                            if row['phase'] in {'completed', 'aborted', 'cancelled'}:
                                c.execute(f'DELETE FROM {self.cleanup.reservations} WHERE operation_id=%s '
                                          "AND purpose<>'move-unproven-staging'", (operation_id,))
                    try:
                        end(connection) if connection is not None else self.database.write(end)
                    except Exception:
                        # Losing the end write leaves a conservative live receipt.
                        logging.getLogger(__name__).exception('Could not persist directory move writer end')
            finally:
                writer.active = False
                writer.context.reset(writer.context_token)
                _LOCKED.reset(token)

    def _reserve_writer(self, source, operation_id, connection):
        writer = self._writer(operation_id)
        self.cleanup.reserve(source, operation_id=operation_id, purpose=writer.purpose(), connection=connection)
        writer.location = cleanup_location(source)

    def _record_identity(self, operation_id, column, identity):
        def write(c):
            self.cleanup.lock(c)
            self._assert_writer(operation_id, c)
            c.execute(f'UPDATE {self.table} SET {column}=%s WHERE operation_id=%s', (identity, operation_id))
        self.database.write(write)

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
            self.cleanup.lock(c)
            self._assert_writer(operation_id, c)
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
                self._reserve_writer(source, operation_id, connection)
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
                self._assert_writer(operation_id, connection)
                connection.execute(f'INSERT INTO {self.table}(operation_id,owner_id,actor,source,target,mode,phase,staging,file_identity) '
                                   "VALUES (%s,%s,%s,%s,%s,%s,'prepared',%s,%s)",
                                   (operation_id, owner_id, actor, cleanup_location(source), cleanup_location(target), mode,
                                    cleanup_location(staging) if mode == 'copy' else '',
                                    _directory_identity(source) if mode == 'rename' else ''))
            try:
                self._check_writer(operation_id)
                if mode == 'copy':
                    terminate(source)
                self._check_writer(operation_id)
                target.parent.mkdir(parents=True, exist_ok=True)
                self._check_writer(operation_id)
                if mode == 'rename':
                    rename_directory_no_replace(source, target)
                else:
                    staging.mkdir(mode=0o700)
                    identity = _directory_identity(staging)
                    self._record_identity(operation_id, 'staging_identity', identity)
                    self._check_writer(operation_id)
                    with (staging / 'operation').open('x', encoding='utf-8') as stream:
                        stream.write(operation_id)
                        stream.flush()
                        os.fsync(stream.fileno())
                    payload = staging / 'directory'
                    self._check_writer(operation_id)
                    shutil.copytree(source, payload, symlinks=True)
                    self._check_writer(operation_id)
                    identity = _directory_identity(payload)
                    self._record_identity(operation_id, 'file_identity', identity)
                    self._check_writer(operation_id)
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
        with self.operation_lock(operation_id, connection=connection):
            return self._commit(operation_id, connection=connection, update=update)

    def _commit(self, operation_id, *, connection, update):
        self.cleanup.lock(connection)
        writer = self._assert_writer(operation_id, connection)
        row = self.get(operation_id, connection=connection)
        if row['phase'] != 'files_ready':
            raise ValueError('Directory files are not ready for registration commit')
        source, target = cleanup_path(row['source']), cleanup_path(row['target'])
        if row['mode'] != 'external' and _directory_identity(target) != row['file_identity']:
            raise FileExistsError('Move destination was replaced before registration commit')
        self.cleanup.assert_available(target, connection=connection, operation_id=operation_id)
        # Registration writers must be able to register the destination, while
        # the source reservation remains the durable writer/dependency boundary.
        connection.execute(f'DELETE FROM {self.cleanup.reservations} WHERE operation_id=%s AND location<>%s',
                           (operation_id, writer.location))
        self._assert_writer(operation_id, connection)
        result = update(connection, source, target)
        self._assert_writer(operation_id, connection)
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
                self._reserve_writer(source, operation_id, connection)
                self.cleanup.reserve(target, operation_id=operation_id, purpose='external-move', connection=connection)
                self._assert_writer(operation_id, connection)
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
                self._check_writer(operation_id)
                dependencies(cleanup_path(row['source']), cleanup_path(row['target']))
            except MoveWriterUnavailable:
                raise
            except Exception as exc:
                self._phase(operation_id, 'committed', error=exc)
                raise OperationError(str(exc), code='directory.move_dependencies_pending',
                                     commit_state='committed', details={'operation_id': operation_id}) from exc
            with self.database.transaction() as connection:
                self.cleanup.lock(connection)
                self._assert_writer(operation_id, connection)
                # Owner deletion can cancel while dependency I/O is running.
                if self.get(operation_id, connection=connection)['phase'] != 'committed':
                    return self.get(operation_id, connection=connection)
                if row['mode'] != 'external':
                    enqueue_directory_cleanup(cleanup_path(row['source']), owner_id=row['owner_id'],
                        operation_id=operation_id, actor=row['actor'], connection=connection)
                self._assert_writer(operation_id, connection)
                connection.execute(f"UPDATE {self.table} SET phase='completed',last_error='',updated_at=clock_timestamp() "
                                   "WHERE operation_id=%s AND phase='committed'", (operation_id,))
            return self.get(operation_id)

    def _remove_staging(self, row):
        self._check_writer(row['operation_id'])
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
        self._check_writer(row['operation_id'])
        remove_path(staging, identity=tuple(map(int, row['staging_identity'].split(':'))))
        return ''

    def abort(self, operation_id):
        with self.operation_lock(operation_id):
            return self._abort(operation_id)

    def _abort(self, operation_id):
        row = self.get(operation_id)
        if row['phase'] not in {'prepared', 'files_ready'}:
            return
        self._check_writer(operation_id)
        source, target = cleanup_path(row['source']), cleanup_path(row['target'])
        owned_target = bool(row['file_identity']) and _directory_identity(target) == row['file_identity']
        if row['mode'] == 'rename' and owned_target:
            if os.path.lexists(source):
                raise FileExistsError('Both sides of an uncommitted rename exist')
            self._check_writer(operation_id)
            rename_directory_no_replace(target, source)
        elif row['mode'] == 'copy' and row['file_identity']:
            identity = tuple(map(int, row['file_identity'].split(':')))
            if owned_target or not os.path.lexists(target):
                self._check_writer(operation_id)
                remove_path(target, identity=identity)
            else:
                # A target that won the no-replace race was never ours. A
                # previous abort may nevertheless have claimed our own copy
                # before another host entry appeared at the public name.
                from .owned_path_deletion import OwnedPathDeletionStore
                self._check_writer(operation_id)
                OwnedPathDeletionStore().resume(target, identity)
        elif row['mode'] == 'rename' and _directory_identity(source) != row['file_identity']:
            raise FileExistsError('Original move directory is missing or replaced; recovery requires inspection')
        error = self._remove_staging(row)
        with self.database.transaction() as connection:
            self.cleanup.lock(connection)
            self._assert_writer(operation_id, connection)
            if error:
                connection.execute(f'UPDATE {self.cleanup.reservations} SET purpose=%s '
                                   'WHERE operation_id=%s AND location=%s',
                                   ('move-unproven-staging', operation_id, row['staging']))
            self._phase(operation_id, 'aborted', connection=connection, error=error)

    def recover(self, *, dependencies):
        with self.database.read() as connection:
            ids = [r['operation_id'] for r in connection.execute(
                f"SELECT operation_id FROM {self.table} m WHERE phase NOT IN ('completed','aborted','cancelled') "
                f'OR EXISTS (SELECT 1 FROM {self.cleanup.reservations} r WHERE r.operation_id=m.operation_id '
                'AND r.purpose LIKE %s) ORDER BY created_at', (_WRITER_PREFIX + '%',)
            ).fetchall()]
        results = []
        for operation_id in ids:
            try:
                with self.operation_lock(operation_id):
                    row = self.get(operation_id)
                    try:
                        if row['phase'] in {'prepared', 'files_ready'}:
                            self.abort(operation_id)
                        elif row['phase'] == 'committed':
                            self.finish(operation_id, dependencies=dependencies)
                    except MoveWriterUnavailable:
                        raise
                    except Exception as exc:
                        self._phase(operation_id, row['phase'], error=exc)
            except MoveWriterUnavailable:
                # A free session lock never proves that a writer has stopped.
                pass
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
                enqueue_directory_cleanup(cleanup_path(row['source']), owner_id=row['owner_id'],
                    operation_id=row['operation_id'], actor=row['actor'], connection=connection)
            receipts = connection.execute(f'SELECT purpose FROM {queue.reservations} '
                'WHERE operation_id=%s AND purpose LIKE %s',
                (row['operation_id'], _WRITER_PREFIX + '%')).fetchall()
            if len(receipts) == 1:
                receipt = _receipt(receipts[0]['purpose'])
                if _writer_has_ended(receipt, row['operation_id']):
                    queue.release(row['operation_id'], connection=connection)
