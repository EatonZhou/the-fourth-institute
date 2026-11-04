"""Durable creation of file-backed objects.

The domain chooses initial contents and commits its registration. This module
owns operation identity, path reservations and proof of filesystem ownership.
Only a staged inode recorded before publication can be resumed at a target.
"""
from contextlib import contextmanager, nullcontext, ExitStack
from dataclasses import dataclass
from typing import Callable, ContextManager
from contextvars import ContextVar
import hashlib
import json
import os
from uuid import uuid4

from management_common.filesystem_paths import filesystem_path
from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_common.directory_paths import assert_missing_location, cleanup_location, cleanup_path, lexical_path, rename_directory_no_replace, remove_path
from management_common.portal_database import portal_database
from management_common.resource_lifecycle import current_lifecycle, LifecycleStore
from management_common.process_observation import process_identity, process_is_alive
from management_common.lifetime_execution_lease import execution_lease


_OPERATION = ContextVar('resource_creation_operation', default='')
_AUTHORITY = ContextVar('resource_creation_authority', default=None)


@contextmanager
def creation_authority(check):
    """Carry the delegating domain's execution fence through creation effects."""
    token = _AUTHORITY.set(check)
    try:
        yield
    finally:
        _AUTHORITY.reset(token)


def assert_creation_authority():
    check = _AUTHORITY.get()
    if check is not None:
        check()


@dataclass(frozen=True)
class SynchronousCreationCompletion:
    """Internal domain attestation, issued under original execution exclusion.

    Only the missing legacy receipt case may use this. It never represents a
    historical writer receipt and cannot override any existing writer metadata.
    """
    operation_id: str
    instruction_id: str


SynchronousCompletionVerifier = Callable[[dict, object], ContextManager[SynchronousCreationCompletion]]


class _CreationWriter:
    """Creation's existing journal owns the writer as well as its inodes.

    A free PG lock only serializes claims. An end receipt or observed Python
    death and exclusive execution admission (including native children) are
    required before another writer can touch its files.
    """
    def __init__(self, db, key, session, *, cleanup=False):
        self.db, self.key, self.session = db, key, session
        self.cleanup = cleanup
        self.table = f'"{db.schema}".resource_creations'
        self.receipt = dict(token=uuid4().hex, process=process_identity(), ended=False)
        self.claimed = False

    def claim(self, artifacts, connection, *, new=False, completion=None):
        previous = artifacts[0].get('writer', {}) if artifacts else {}
        proven = (isinstance(completion, SynchronousCreationCompletion)
                  and completion.operation_id == self.key and completion.instruction_id
                  and artifacts and all('writer' not in a for a in artifacts))
        if not new and not proven and previous.get('ended') is not True and process_is_alive(previous.get('process', {})):
            raise RuntimeError('Creation writer ownership is active or unknown')
        if not artifacts:
            raise ValueError('Creation requires at least one artifact')
        artifacts[0]['writer'] = self.receipt
        connection.execute(f'UPDATE {self.table} SET artifacts=%s::jsonb WHERE operation_id=%s',
                           (json.dumps(artifacts), self.key))
        self.claimed = True

    def check(self, connection=None):
        assert_creation_authority()
        try:
            self.session.execute('SELECT 1')
        except Exception as error:
            raise RuntimeError('Creation execution ownership lost') from error
        if connection is None:
            with self.db.read() as conn:
                return self.check(conn)
        row = connection.execute(f'SELECT artifacts,aborted FROM {self.table} WHERE operation_id=%s FOR UPDATE',
                                 (self.key,)).fetchone()
        if row and row['aborted'] and not self.cleanup:
            raise RuntimeError('Creation operation was cancelled')
        receipt = row['artifacts'][0].get('writer', {}) if row and row['artifacts'] else {}
        if receipt.get('token') != self.receipt['token'] or receipt.get('ended') is not False:
            raise RuntimeError('Creation execution ownership lost')

    def end(self):
        if self.claimed:
            # This receipt is allowed after session loss: the Python stack has
            # unwound and will perform no more file/domain effects. Never save
            # a stale artifact snapshot or replace a successor's receipt here.
            self.db.write(lambda conn: conn.execute(
                f"UPDATE {self.table} SET artifacts=jsonb_set(artifacts, '{{0,writer,ended}}', 'true') "
                "WHERE operation_id=%s AND artifacts->0->'writer'->>'token'=%s",
                (self.key, self.receipt['token'])))


@contextmanager
def _creation_writer(db, key, *, cleanup=False):
    # Both recovery and ordinary writers use this identity. Python end/death
    # receipts remain necessary but cannot override a surviving native tree.
    with execution_lease(key, namespace=db.schema, blocking=False) as acquired:
        if not acquired:
            raise RuntimeError('Creation writer ownership is active or unknown')
        with _creation_session(db, key, cleanup=cleanup) as writer:
            yield writer


@contextmanager
def _creation_session(db, key, *, cleanup=False):
    import psycopg
    lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
    with psycopg.connect(db.dsn, autocommit=True, connect_timeout=30) as session:
        session.execute("SET lock_timeout = '90s'")
        session.execute('SELECT pg_advisory_lock(%s)', (lock,))
        writer = _CreationWriter(db, key, session, cleanup=cleanup)
        try:
            yield writer
        finally:
            writer.end()


def creation_operation_id(operation_id=''):
    return operation_id or _OPERATION.get() or uuid4().hex


@contextmanager
def creation_operation(operation_id):
    token = _OPERATION.set(operation_id)
    try:
        yield
    finally:
        _OPERATION.reset(token)


def initialize_creation_schema(connection, schema):
    """Initialize the current resource creation schema for a fresh database."""
    table = f'"{schema}".resource_creations'
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (table + ':init',))
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is not None:
        columns = {row[0] for row in connection.execute(
            'SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND NOT attisdropped', (table,))}
        if not {'aborted', 'cleanup_complete'} <= columns:
            raise RuntimeError('Creation termination schema requires the offline maintenance window')
        from .directory_ownership_upgrade import upgrade_creations
        upgrade_creations(connection, schema)
        return
    connection.execute(f'''CREATE TABLE IF NOT EXISTS {table} (
        operation_id TEXT PRIMARY KEY, resource_id TEXT NOT NULL UNIQUE,
        actor TEXT NOT NULL, payload JSONB NOT NULL, scope_id TEXT NOT NULL,
        artifacts JSONB NOT NULL, completed BOOLEAN NOT NULL DEFAULT FALSE,
        aborted BOOLEAN NOT NULL DEFAULT FALSE,
        cleanup_complete BOOLEAN NOT NULL DEFAULT FALSE
    )''')
    from .directory_ownership_upgrade import upgrade_creations
    upgrade_creations(connection, schema)


def _identity(path):
    info = filesystem_path(path).lstat()
    if filesystem_path(path).is_symlink():
        raise FileExistsError('Creation path is a symbolic link: ' + str(path))
    return [info.st_dev, info.st_ino]


def _assert_identity(path, expected):
    for parent in path.parents:
        if filesystem_path(parent).is_symlink():
            raise FileExistsError('Creation parent was redirected: ' + str(parent))
    if not expected or _identity(path) != expected:
        raise FileExistsError('Creation path was replaced: ' + str(path))


def _remove_owned(path, identity):
    # Do not hand an inode claim to the generic path-only cleanup queue: the
    # path can be replaced before that worker executes it. Creation retains
    # both the claim and reservation until its own deletion succeeds.
    for parent in path.parents:
        if filesystem_path(parent).is_symlink():
            raise FileExistsError('Creation parent was redirected: ' + str(parent))
    if identity is None:
        if filesystem_path(path).exists() or filesystem_path(path).is_symlink():
            raise FileExistsError('Creation path has no ownership proof: ' + str(path))
        return
    # A missing public path may already have been claimed before interruption.
    # The deletion owner must recover that persisted effect before completion.
    remove_path(path, identity=identity)


def _sync(path):
    """Make prepared contents durable before committing publication evidence."""
    children = list(filesystem_path(path).rglob('*')) if filesystem_path(path).is_dir() else []
    for item in [*children, path, path.parent]:
        if not filesystem_path(item).is_symlink() and not (os.name == 'nt' and filesystem_path(item).is_dir()):
            # Windows FlushFileBuffers requires a write-capable handle. The
            # CRT reports EBADF when fsync receives a read-only descriptor.
            descriptor = os.open(filesystem_path(item), os.O_RDWR if os.name == 'nt' else os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


class Creation:
    def __init__(self, resource_id, *, actor, payload, operation_id=''):
        operation_id = creation_operation_id(operation_id)
        self.key = 'resource-create:' + hashlib.sha256((actor + '\0' + operation_id).encode()).hexdigest()
        self.resource_id = resource_id
        self.actor = actor
        self.payload = dict(payload)
        self.db = portal_database(schema='directory_lifecycle')
        self.table = f'"{self.db.schema}".resource_creations'
        self.queue = get_directory_cleanup_store()
        from .registration import ResourceRegistrationStore
        self.registrations = ResourceRegistrationStore()
        self.registrations._prepare_schema()
        self.lifecycle = LifecycleStore() if current_lifecycle() else None
        self._active = False
        self.db.write(lambda conn: initialize_creation_schema(conn, self.db.schema))

    @contextmanager
    def prepare(self, artifacts):
        """Persist intent before any filesystem effect; artifacts are (path, kind)."""
        from .directory_registrations import assert_empty_location, is_deleted
        self.scope_id = current_lifecycle()
        self.lifecycle = LifecycleStore() if self.scope_id else None
        lease = self.lifecycle.owner_lease(self.scope_id) if self.lifecycle else nullcontext()
        with lease, _creation_writer(self.db, self.key) as self.writer:
            with self.db.transaction() as conn:
                self._assert_scope_open(conn)
                self.queue.lock(conn)
                row = conn.execute(f'SELECT * FROM {self.table} WHERE operation_id=%s', (self.key,)).fetchone()
                if row:
                    if row['aborted']:
                        raise ValueError('Creation operation was aborted')
                    if (row['resource_id'], row['actor'], row['payload']) != (self.resource_id, self.actor, self.payload):
                        raise ValueError('Creation operation ID was already used with different arguments')
                    if not row['completed'] and row['scope_id'] != current_lifecycle():
                        raise ValueError('Unfinished creation belongs to another resource lifecycle')
                    self.artifacts = row['artifacts']
                    self.completed = row['completed']
                    if is_deleted(self.resource_id, connection=conn):
                        raise KeyError(self.resource_id)
                else:
                    if is_deleted(self.resource_id, connection=conn):
                        raise KeyError(self.resource_id)
                    self.artifacts = []
                    for path, kind in artifacts:
                        path = lexical_path(path)
                        for parent in (path, *path.parents):
                            if filesystem_path(parent).is_symlink():
                                raise FileExistsError('Creation path contains a symbolic link: ' + str(parent))
                        if kind == 'existing_directory':
                            self.queue.assert_available(path, connection=conn)
                            if not filesystem_path(path).is_dir():
                                raise FileNotFoundError('A registered directory must already exist')
                            artifact = {'identity': _identity(path), 'ready': True}
                        elif kind in {'file', 'directory'}:
                            assert_empty_location(path, connection=conn)
                            artifact = {}
                        else:
                            raise ValueError('Unknown creation artifact kind: ' + kind)
                        self.artifacts.append({**artifact, 'path': cleanup_location(path), 'kind': kind})
                    conn.execute(f'INSERT INTO {self.table} (operation_id,resource_id,actor,payload,scope_id,artifacts) '
                                 'VALUES (%s,%s,%s,%s::jsonb,%s,%s::jsonb)',
                                 (self.key, self.resource_id, self.actor, json.dumps(self.payload),
                                  current_lifecycle(), json.dumps(self.artifacts)))
                    self.completed = False
                if not self.completed:
                    for artifact in self.artifacts:
                        self.queue.reserve(cleanup_path(artifact['path']), operation_id=self.key, purpose='resource_creation', connection=conn)
                    self.writer.claim(self.artifacts, conn, new=row is None)
                    self.writer.check(conn)
                # Registration is the authoritative completion checkpoint.
                # Replaying it reads the original result; it acquires no file
                # authority and must not replace even an unknown writer receipt.
            self._active = True
            try:
                yield self
            finally:
                self._active = False

    @property
    def paths(self):
        return [cleanup_path(item['path']) for item in self.artifacts]

    def _save(self, connection):
        self.assert_owned(connection)
        connection.execute(f'UPDATE {self.table} SET artifacts=%s::jsonb WHERE operation_id=%s',
                           (json.dumps(self.artifacts), self.key))

    def assert_owned(self, connection=None):
        if not self._active:
            raise RuntimeError('Creation requires an active preparation lease')
        if self.completed:
            raise RuntimeError('Completed creation is read-only')
        self.writer.check(connection)

    def _assert_scope_open(self, connection):
        if self.lifecycle:
            self.lifecycle.assert_open(self.scope_id, connection=connection)
        parent_id = self.payload.get('parent_id')
        if parent_id:
            from .directory_registrations import is_deleted
            if is_deleted(parent_id, connection=connection):
                raise KeyError('Creation parent was deleted: ' + parent_id)

    def publish(self, initialize):
        """Build each artifact in private staging, then publish without replacing."""
        if not self._active:
            raise RuntimeError('Creation requires an active preparation lease')
        if self.completed:
            return
        with self.db.transaction() as conn:
            self.assert_owned(conn)
            self._assert_scope_open(conn)
        for index, artifact in enumerate(self.artifacts):
            self.assert_owned()
            target = cleanup_path(artifact['path'])
            if artifact['kind'] == 'existing_directory':
                _assert_identity(target, artifact['identity'])
                continue
            for parent in target.parents:
                if filesystem_path(parent).is_symlink():
                    raise FileExistsError('Creation parent was redirected: ' + str(parent))
            if filesystem_path(target).exists() or filesystem_path(target).is_symlink():
                _assert_identity(target, artifact.get('identity'))
                if not artifact.get('ready'):
                    raise FileExistsError('Unprepared creation target: ' + str(target))
                continue
            assert_missing_location(target)
            stage = cleanup_path(artifact['stage']) if artifact.get('stage') else None
            if stage and artifact.get('identity') and (artifact.get('ready') or filesystem_path(stage).exists() or filesystem_path(stage).is_symlink()):
                _assert_identity(stage, artifact['identity'])
            if not artifact.get('ready'):
                # An interrupted initializer is never merged into a new attempt.
                # Its known inode can be cleaned; an unproven path is untouched.
                with self.db.transaction() as conn:
                    self.assert_owned(conn)
                    self.queue.lock(conn)
                    if stage and artifact.get('identity'):
                        if artifact.get('git_dependency'):
                            from .creation_git import GitWorktreeCreation
                            GitWorktreeCreation(artifact, lambda: self.db.write(self._save), self.key, check=self.assert_owned).cleanup()
                        self.assert_owned(conn)
                        _remove_owned(stage, artifact['identity'])
                        self.queue.release(artifact['stage_operation'], connection=conn)
                    elif stage:
                        # A crash between exclusive mkdir/open and saving the
                        # inode leaves no safe proof. Keep this path reserved
                        # and recorded instead of deleting or silently losing it.
                        artifact.setdefault('unproven_stages', []).append({
                            'path': artifact['stage'], 'operation_id': artifact['stage_operation'],
                        })
                    stage = target.parent / ('.tfi-create-' + uuid4().hex)
                    artifact.update(stage=cleanup_location(stage), identity=None, ready=False,
                                    stage_operation=self.key + ':stage:' + uuid4().hex)
                    self.queue.reserve(stage, operation_id=artifact['stage_operation'], purpose='resource_creation', connection=conn)
                    self._save(conn)
                filesystem_path(target.parent).mkdir(parents=True, exist_ok=True)
                if artifact['kind'] == 'directory':
                    filesystem_path(stage).mkdir()
                else:
                    filesystem_path(stage).touch(exist_ok=False)
                artifact['identity'] = _identity(stage)
                self.db.write(self._save)
                initialize(index, stage)
                self.assert_owned()
                if artifact['kind'] == 'directory':
                    _assert_identity(stage, artifact['identity'])
                _sync(stage)
                # Atomic-writing initializers may replace the staging file.
                artifact['identity'] = _identity(stage)
                artifact['ready'] = True
                self.db.write(self._save)
            _assert_identity(stage, artifact['identity'])
            self.assert_owned()
            rename_directory_no_replace(stage, target)
            if os.name != 'nt':
                descriptor = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)

    @contextmanager
    def registration(self, connection):
        """Enlist completion, reservation release and scope in the owner transaction."""
        from .directory_registrations import is_deleted
        if not self._active:
            raise RuntimeError('Creation requires an active preparation lease')
        self._assert_scope_open(connection)
        self.assert_owned(connection)
        self.queue.lock(connection)
        if is_deleted(self.resource_id, connection=connection):
            raise KeyError(self.resource_id)
        if self.completed:
            raise RuntimeError('Completed creation must read its existing owner')
        for artifact in self.artifacts:
            path = cleanup_path(artifact['path'])
            self.queue.assert_available(path, connection=connection, operation_id=self.key)
            _assert_identity(path, artifact.get('identity'))
        self.queue.release(self.key, connection=connection)
        for artifact in self.artifacts:
            if artifact.get('stage_operation'):
                self.queue.release(artifact['stage_operation'], connection=connection)
        yield
        self.assert_owned(connection)
        self.registrations.complete(self.resource_id, connection=connection)
        if self.scope_id:
            connection.execute(f'INSERT INTO "{self.lifecycle.db.schema}".allocations(resource_id,scope_id) '
                               'VALUES (%s,%s) ON CONFLICT DO NOTHING', (self.resource_id, self.scope_id))
        connection.execute(f'UPDATE {self.table} SET completed=TRUE WHERE operation_id=%s', (self.key,))


def finish_scope_creations(scope_id):
    """Terminate a sealed scope's intents, then remove only proven artifacts.

    This includes unknown stages left by a subsequently completed creation.
    Society's request snapshots do not own filesystem effects and need no
    reverse dependency here. The original scope fence also blocks their retry.
    """
    lifecycle = LifecycleStore()
    scope = lifecycle.scope(scope_id)
    if not scope or scope['state'] not in {'closing', 'complete'}:
        raise ValueError('Creation cleanup requires a closing lifecycle')
    return _finish_creations(scope_id=scope_id)


def finish_instance_creations():
    """Finish all file intents after the installer has stopped every client."""
    return _finish_creations()


def finish_deleted_creations():
    """Retry aborted effects and effects whose object or explicit parent was deleted.

    The original Creation lease fences an active initializer. Tombstones are
    committed before cleanup, and retries retain every unproven inode claim.
    """
    return _finish_creations(deleted_only=True)


def _finish_creations(*, scope_id=None, deleted_only=False, operation_keys=None, unregister=None, validate=None,
                     synchronous_completion: SynchronousCompletionVerifier | None = None):
    db = portal_database(schema='directory_lifecycle')
    table = f'"{db.schema}".resource_creations'
    queue = get_directory_cleanup_store()
    with db.read() as conn:
        if conn.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None:
            return []
        filters, values = ['NOT cleanup_complete'], []
        if scope_id is not None:
            filters.append('scope_id=%s')
            values.append(scope_id)
        if operation_keys is not None:
            filters.append('operation_id=ANY(%s)')
            values.append(sorted(operation_keys))
        rows = conn.execute(
            f'SELECT operation_id,resource_id,payload,aborted FROM {table} WHERE ' + ' AND '.join(filters),
            tuple(values)).fetchall()
        if deleted_only:
            from .directory_registrations import is_deleted
            rows = [row for row in rows if row['aborted'] or is_deleted(row['resource_id'], connection=conn)
                    or (row['payload'].get('parent_id')
                        and is_deleted(row['payload']['parent_id'], connection=conn))]
        keys = [row['operation_id'] for row in rows]
    errors = []
    for key in keys:
        try:
            with _creation_writer(db, key, cleanup=True) as writer, ExitStack() as completion_scope:
                # Commit the tombstone before deletion, including on cleanup failure.
                with db.transaction() as conn:
                    row = dict(conn.execute(f'SELECT * FROM {table} WHERE operation_id=%s FOR UPDATE', (key,)).fetchone())
                    if validate is not None:
                        validate(row, conn)
                    if row['cleanup_complete']:
                        continue
                    assert_creation_authority()
                    if (row['completed'] and not row['aborted'] and unregister is None
                            and not any(a.get('unproven_stages') for a in row['artifacts'])):
                        # Registration already released these reservations and
                        # transferred the published files to their domain owner.
                        # Only this journal's bookkeeping remains. Do not claim
                        # or end a writer, touch files, or unregister identities.
                        conn.execute(f'UPDATE {table} SET cleanup_complete=TRUE WHERE operation_id=%s', (key,))
                        continue
                    completion = None
                    if (synchronous_completion is not None and row['artifacts']
                            and all('writer' not in a for a in row['artifacts'])):
                        completion = completion_scope.enter_context(synchronous_completion(row, conn))
                    writer.claim(row['artifacts'], conn, completion=completion)
                    writer.check(conn)
                    if unregister is not None and not row['aborted']:
                        queue.lock(conn)
                        # Registration released the original reservation. Take it
                        # back before unregistering; no path-only worker may race
                        # this owner's recorded inode claims.
                        def retain(path, **kwargs):
                            from management_common.directory_paths import contains
                            if not any(contains(cleanup_path(a['path']), path) for a in row['artifacts']):
                                raise ValueError('Unregistration escaped Creation artifacts')
                        for artifact in row['artifacts']:
                            queue.reserve(cleanup_path(artifact['path']), operation_id=key,
                                          purpose='resource_creation', connection=conn)
                        unregister(row, conn, retain)
                        writer.check(conn)
                    if not row['completed'] or unregister is not None:
                        row['aborted'] = True
                        conn.execute(f'UPDATE {table} SET aborted=TRUE WHERE operation_id=%s', (key,))
                with db.transaction() as conn:
                    writer.check(conn)
                    queue.lock(conn)
                    for artifact in row['artifacts']:
                        writer.check(conn)
                        if ((not row['completed'] or row['aborted'])
                                and (artifact['kind'] != 'existing_directory' or row['completed'])):
                            if artifact.get('git_dependency'):
                                from .creation_git import GitWorktreeCreation
                                def save_artifacts():
                                    writer.check(conn)
                                    conn.execute(f'UPDATE {table} SET artifacts=%s::jsonb WHERE operation_id=%s',
                                                 (json.dumps(row['artifacts']), key))
                                GitWorktreeCreation(artifact, save_artifacts, key, check=lambda: writer.check(conn)).cleanup()
                            writer.check(conn)
                            _remove_owned(cleanup_path(artifact['path']), artifact.get('identity'))
                            if artifact.get('stage'):
                                writer.check(conn)
                                _remove_owned(cleanup_path(artifact['stage']), artifact.get('identity'))
                        for stage in artifact.get('unproven_stages', []):
                            writer.check(conn)
                            _remove_owned(cleanup_path(stage['path']), None)
                            queue.release(stage['operation_id'], connection=conn)
                        if artifact.get('stage_operation'):
                            queue.release(artifact['stage_operation'], connection=conn)
                    writer.check(conn)
                    queue.release(key, connection=conn)
                    conn.execute(f'UPDATE {table} SET cleanup_complete=TRUE WHERE operation_id=%s', (key,))
        except Exception as error:
            errors.append(f"{key}: {error}")
    return errors


def abort_creations(*, actor, operation_ids, unregister):
    """Terminate selected intents under their leases, then retry proven effects.

    The domain unregisters its identities in the abort transaction. This owner
    keeps reservations until every artifact and dependency is removed.
    """
    keys = {'resource-create:' + hashlib.sha256((actor + '\0' + operation).encode()).hexdigest()
            for operation in operation_ids}
    errors = _finish_creations(operation_keys=keys, unregister=unregister)
    if errors:
        raise RuntimeError('Creation cleanup pending: ' + '; '.join(errors))


def get_resource_creation(resource_id):
    """Read the journal by allocated identity, without reconstructing its caller."""
    db = portal_database(schema='directory_lifecycle')
    with db.read() as conn:
        if conn.execute("SELECT to_regclass('resource_creations')").fetchone()[0] is None:
            raise KeyError(resource_id)
        row = conn.execute('SELECT * FROM resource_creations WHERE resource_id=%s',
                           (resource_id,)).fetchone()
    if row is None:
        raise KeyError(resource_id)
    return dict(row)


def cancel_resource_creation(resource_id, *, validate, record_cancellation,
                             synchronous_completion: SynchronousCompletionVerifier | None = None):
    """A domain delegates cancellation; all writer/inode effects stay here.

    validate runs again under the ordinary creation writer lock. The internal
    domain verifier may attest missing legacy metadata only, under that row lock.
    Existing writer evidence, completed registration and native admission remain
    mandatory. This callback is never exposed as caller-authored proof or a flag.
    Domain cancellation and the aborted flag commit before acquiring cleanup
    authority. Missing writer proof keeps cleanup pending, never authorization.
    """
    row = get_resource_creation(resource_id)
    def unfinished(current, connection):
        if current['completed']:
            raise ValueError('Registered creation cannot be cancelled')
        validate(current, connection)
    db = portal_database(schema='directory_lifecycle')
    with db.transaction() as conn:
        current = dict(conn.execute('SELECT * FROM resource_creations WHERE operation_id=%s FOR UPDATE',
                                   (row['operation_id'],)).fetchone())
        unfinished(current, conn)
        if not current['aborted']:
            record_cancellation(current, conn)
            conn.execute('UPDATE resource_creations SET aborted=TRUE WHERE operation_id=%s',
                         (row['operation_id'],))
    errors = _finish_creations(operation_keys={row['operation_id']},
        validate=unfinished,
        synchronous_completion=synchronous_completion)
    current = get_resource_creation(resource_id)
    return dict(resource_id=resource_id, cancelled=current['aborted'],
                cleanup_complete=current['cleanup_complete'], errors=errors)


def resource_creation_status(row):
    """Public observation, never an admission grant; cancellation checks again."""
    writer = row['artifacts'][0].get('writer', {}) if row['artifacts'] else {}
    return dict(resource_id=row['resource_id'], completed=row['completed'],
        cancelled=row['aborted'], cleanup_complete=row['cleanup_complete'],
        writer_evidence=('missing' if not writer else
                         'ended' if writer.get('ended') is True else 'requires_quiescence_check'))
