"""Durable creation of file-backed objects.

The domain chooses initial contents and commits its registration. This module
owns operation identity, path reservations and proof of filesystem ownership.
Only a staged inode recorded before publication can be resumed at a target.
"""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import hashlib
import json
import os
from uuid import uuid4

from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_common.directory_paths import assert_missing_location, cleanup_location, cleanup_path, lexical_path, rename_directory_no_replace, remove_path
from management_common.portal_database import portal_database
from management_common.resource_lifecycle import current_lifecycle, LifecycleStore


_OPERATION = ContextVar('resource_creation_operation', default='')


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
    """Fresh schema DDL; also callable by the offline directory migration."""
    table = f'"{schema}".resource_creations'
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (table + ':init',))
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is not None:
        columns = {row[0] for row in connection.execute(
            'SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND NOT attisdropped', (table,))}
        if not {'aborted', 'cleanup_complete'} <= columns:
            raise RuntimeError('Creation termination schema requires the offline maintenance window')
        return
    connection.execute(f'''CREATE TABLE IF NOT EXISTS {table} (
        operation_id TEXT PRIMARY KEY, resource_id TEXT NOT NULL UNIQUE,
        actor TEXT NOT NULL, payload JSONB NOT NULL, scope_id TEXT NOT NULL,
        artifacts JSONB NOT NULL, completed BOOLEAN NOT NULL DEFAULT FALSE,
        aborted BOOLEAN NOT NULL DEFAULT FALSE,
        cleanup_complete BOOLEAN NOT NULL DEFAULT FALSE
    )''')


def migrate_creation_schema(connection, schema):
    """Offline upgrade of the creation owner's termination state."""
    table = f'"{schema}".resource_creations'
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is not None:
        connection.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS aborted BOOLEAN NOT NULL DEFAULT FALSE')
        connection.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS cleanup_complete BOOLEAN NOT NULL DEFAULT FALSE')
    initialize_creation_schema(connection, schema)


def _identity(path):
    info = path.lstat()
    if path.is_symlink():
        raise FileExistsError('Creation path is a symbolic link: ' + str(path))
    return [info.st_dev, info.st_ino]


def _assert_identity(path, expected):
    for parent in path.parents:
        if parent.is_symlink():
            raise FileExistsError('Creation parent was redirected: ' + str(parent))
    if not expected or _identity(path) != expected:
        raise FileExistsError('Creation path was replaced: ' + str(path))


def _remove_owned(path, identity):
    # Do not hand an inode claim to the generic path-only cleanup queue: the
    # path can be replaced before that worker executes it. Creation retains
    # both the claim and reservation until its own deletion succeeds.
    for parent in path.parents:
        if parent.is_symlink():
            raise FileExistsError('Creation parent was redirected: ' + str(parent))
    if identity is None:
        if path.exists() or path.is_symlink():
            raise FileExistsError('Creation path has no ownership proof: ' + str(path))
        return
    # A missing public path may already have been claimed before interruption.
    # The deletion owner must recover that persisted effect before completion.
    remove_path(path, identity=identity)


def _sync(path):
    """Make prepared contents durable before committing publication evidence."""
    children = list(path.rglob('*')) if path.is_dir() else []
    for item in [*children, path, path.parent]:
        if not item.is_symlink() and not (os.name == 'nt' and item.is_dir()):
            descriptor = os.open(item, os.O_RDONLY)
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
        lock = int.from_bytes(hashlib.sha256(self.key.encode()).digest()[:8], 'big', signed=True)
        self.scope_id = current_lifecycle()
        self.lifecycle = LifecycleStore() if self.scope_id else None
        lease = self.lifecycle.owner_lease(self.scope_id) if self.lifecycle else nullcontext()
        with lease, self.db.advisory_lock(lock):
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
                else:
                    if is_deleted(self.resource_id, connection=conn):
                        raise KeyError(self.resource_id)
                    self.artifacts = []
                    for path, kind in artifacts:
                        path = lexical_path(path)
                        for parent in (path, *path.parents):
                            if parent.is_symlink():
                                raise FileExistsError('Creation path contains a symbolic link: ' + str(parent))
                        if kind == 'adopted':
                            self.queue.assert_available(path, connection=conn)
                            if not path.is_dir():
                                raise FileNotFoundError('An adopted directory must already exist')
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
            self._active = True
            try:
                yield self
            finally:
                self._active = False

    @property
    def paths(self):
        return [cleanup_path(item['path']) for item in self.artifacts]

    def _save(self, connection):
        connection.execute(f'UPDATE {self.table} SET artifacts=%s::jsonb WHERE operation_id=%s',
                           (json.dumps(self.artifacts), self.key))

    def _assert_scope_open(self, connection):
        if self.lifecycle:
            self.lifecycle.assert_open(self.scope_id, connection=connection)

    def publish(self, initialize):
        """Build each artifact in private staging, then publish without replacing."""
        if not self._active:
            raise RuntimeError('Creation requires an active preparation lease')
        with self.db.transaction() as conn:
            self._assert_scope_open(conn)
        if self.completed:
            return
        for index, artifact in enumerate(self.artifacts):
            target = cleanup_path(artifact['path'])
            if artifact['kind'] == 'adopted':
                _assert_identity(target, artifact['identity'])
                continue
            for parent in target.parents:
                if parent.is_symlink():
                    raise FileExistsError('Creation parent was redirected: ' + str(parent))
            if target.exists() or target.is_symlink():
                _assert_identity(target, artifact.get('identity'))
                if not artifact.get('ready'):
                    raise FileExistsError('Unprepared creation target: ' + str(target))
                continue
            assert_missing_location(target)
            stage = cleanup_path(artifact['stage']) if artifact.get('stage') else None
            if stage and artifact.get('identity') and (artifact.get('ready') or stage.exists() or stage.is_symlink()):
                _assert_identity(stage, artifact['identity'])
            if not artifact.get('ready'):
                # An interrupted initializer is never merged into a new attempt.
                # Its known inode can be cleaned; an unproven path is untouched.
                with self.db.transaction() as conn:
                    self.queue.lock(conn)
                    if stage and artifact.get('identity'):
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
                target.parent.mkdir(parents=True, exist_ok=True)
                if artifact['kind'] == 'directory':
                    stage.mkdir()
                else:
                    stage.touch(exist_ok=False)
                artifact['identity'] = _identity(stage)
                self.db.write(self._save)
                initialize(index, stage)
                if artifact['kind'] == 'directory':
                    _assert_identity(stage, artifact['identity'])
                _sync(stage)
                # Atomic-writing initializers may replace the staging file.
                artifact['identity'] = _identity(stage)
                artifact['ready'] = True
                self.db.write(self._save)
            _assert_identity(stage, artifact['identity'])
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


def _finish_creations(*, scope_id=None):
    db = portal_database(schema='directory_lifecycle')
    table = f'"{db.schema}".resource_creations'
    queue = get_directory_cleanup_store()
    with db.read() as conn:
        if conn.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None:
            return []
        scope_filter = ' AND scope_id=%s' if scope_id is not None else ''
        keys = [row[0] for row in conn.execute(
            f'SELECT operation_id FROM {table} WHERE NOT cleanup_complete' + scope_filter,
            (scope_id,) if scope_id is not None else ())]
    errors = []
    for key in keys:
        lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
        with db.advisory_lock(lock):
            # Commit the tombstone before deletion, including on cleanup failure.
            with db.transaction() as conn:
                row = conn.execute(f'SELECT * FROM {table} WHERE operation_id=%s FOR UPDATE', (key,)).fetchone()
                if row['cleanup_complete']:
                    continue
                if not row['completed']:
                    conn.execute(f'UPDATE {table} SET aborted=TRUE WHERE operation_id=%s', (key,))
            try:
                with db.transaction() as conn:
                    queue.lock(conn)
                    for artifact in row['artifacts']:
                        if not row['completed'] and artifact['kind'] != 'adopted':
                            _remove_owned(cleanup_path(artifact['path']), artifact.get('identity'))
                            if artifact.get('stage'):
                                _remove_owned(cleanup_path(artifact['stage']), artifact.get('identity'))
                        for stage in artifact.get('unproven_stages', []):
                            _remove_owned(cleanup_path(stage['path']), None)
                            queue.release(stage['operation_id'], connection=conn)
                        if artifact.get('stage_operation'):
                            queue.release(artifact['stage_operation'], connection=conn)
                    queue.release(key, connection=conn)
                    conn.execute(f'UPDATE {table} SET cleanup_complete=TRUE WHERE operation_id=%s', (key,))
            except (OSError, ValueError) as error:
                errors.append(f"{row['resource_id']}: {error}")
    return errors
