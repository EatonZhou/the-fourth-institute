"""Durable filesystem effects; object owners keep registration authority.

Enqueue with the owner's transaction. Reservations survive crashes and block
reuse until cleanup commits. No worker thread is started by this store: Portal
System's scheduled RunScript drains it alongside the declared retention work.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from functools import lru_cache
from hashlib import sha256
from uuid import uuid4

from .directory_paths import DirectoryComparison, cleanup_location, cleanup_path, lexical_path, remove_path
from .portal_database import _database_schema, portal_database, portal_database_url
from .database_time import decode_times


class DirectoryPendingCleanup(FileExistsError):
    def __init__(self, cleanup_id: str):
        self.cleanup_id = cleanup_id
        super().__init__('Directory is reserved for pending cleanup: ' + cleanup_id)


def initialize_directory_schema(connection, schema):
    table = f'"{schema}".cleanup'
    reservations = f'"{schema}".path_reservations'
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (table + ':init',))
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is not None:
        if connection.execute('SELECT to_regclass(%s)', (reservations,)).fetchone()[0] is None:
            raise RuntimeError('Directory reservations require the offline maintenance window')
        return
    connection.execute(f'''CREATE TABLE {table} (
        cleanup_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        location TEXT NOT NULL,
        actor TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        completed_at TIMESTAMPTZ,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        UNIQUE(operation_id, location)
    )''')
    connection.execute(f'CREATE INDEX cleanup_ready_idx ON {table}(available_at) WHERE completed_at IS NULL')
    initialize_reservations_schema(connection, schema)


def initialize_reservations_schema(connection, schema):
    connection.execute(f'''CREATE TABLE "{schema}".path_reservations (
        operation_id TEXT NOT NULL,
        location TEXT NOT NULL,
        purpose TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(operation_id,location)
    )''')


class DirectoryCleanupStore:
    def __init__(self, *, connection=None):
        self.database = portal_database(schema='directory_lifecycle')
        self.table = f'"{self.database.schema}".cleanup'
        self.reservations = f'"{self.database.schema}".path_reservations'
        if connection is None:
            self.database.write(self._initialize)
        else:
            self._initialize(connection)

    def _initialize(self, connection):
        initialize_directory_schema(connection, self.database.schema)

    def lock(self, connection):
        # Serialize short path decisions, never the physical filesystem walk.
        # Durable reservations protect overlapping paths between transactions.
        connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                           (self.table + ':paths',))

    def pending_at(self, path, *, connection=None, include_descendants=True):
        target = lexical_path(path)
        with nullcontext(connection) if connection is not None else self.database.read() as conn:
            rows = conn.execute(f'SELECT cleanup_id,location FROM {self.table} '
                                'WHERE completed_at IS NULL ORDER BY created_at,cleanup_id').fetchall()
        comparison = DirectoryComparison()
        return [row['cleanup_id'] for row in rows
                if (comparison.overlaps(target, cleanup_path(row['location'])) if include_descendants
                    else comparison.contains(cleanup_path(row['location']), target))]

    def assert_available(self, path, *, connection, operation_id=None):
        self._assert_available(path, connection=connection, operation_id=operation_id)

    def assert_execution_available(self, path, *, connection):
        """Reserve execution locations inside the affected directory.

        A parent binding grants access; it does not make every child directory
        the current execution location. Registration mutations still check
        overlap in both directions, including ancestor replacement.
        """
        self._assert_available(path, connection=connection, for_execution=True)

    def _assert_available(self, path, *, connection, operation_id=None, for_execution=False):
        self.lock(connection)
        pending = self.pending_at(path, connection=connection, include_descendants=not for_execution)
        if pending:
            raise DirectoryPendingCleanup(pending[0])
        target = lexical_path(path)
        rows = connection.execute(f'SELECT operation_id,location,purpose FROM {self.reservations}').fetchall()
        comparison = DirectoryComparison()
        for row in rows:
            reserved = cleanup_path(row['location'])
            conflict = (comparison.contains(reserved, target)
                        if for_execution
                        else comparison.overlaps(target, reserved))
            if row['operation_id'] != operation_id and conflict:
                raise FileExistsError('Directory is reserved by operation: ' + row['operation_id'])

    def reserve(self, path, *, operation_id, purpose, connection):
        if not operation_id or not purpose:
            raise ValueError('Directory reservation requires operation identity and purpose')
        self.assert_available(path, connection=connection, operation_id=operation_id)
        return bool(connection.execute(f'INSERT INTO {self.reservations}(operation_id,location,purpose) VALUES (%s,%s,%s) '
                                       'ON CONFLICT(operation_id,location) DO NOTHING',
                                       (operation_id, cleanup_location(path), purpose)).rowcount)

    def release(self, operation_id, *, connection):
        self.lock(connection)
        connection.execute(f'DELETE FROM {self.reservations} WHERE operation_id=%s', (operation_id,))

    def enqueue(self, path, *, owner_id, operation_id, actor, connection):
        if not owner_id or not operation_id:
            raise ValueError('Directory cleanup requires owner and operation identity')
        self.lock(connection)
        location = cleanup_location(path)
        row = connection.execute(f'''INSERT INTO {self.table}
            (cleanup_id,owner_id,operation_id,location,actor) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT(operation_id,location) DO UPDATE SET operation_id=EXCLUDED.operation_id
            RETURNING cleanup_id,owner_id,actor''',
            ('cleanup_' + uuid4().hex, owner_id, operation_id, location, actor)).fetchone()
        if row['owner_id'] != owner_id or row['actor'] != actor:
            raise ValueError('Cleanup operation identity already belongs to another request')
        return row['cleanup_id']

    def status(self, *, owner_id=None, cleanup_id=None, connection=None):
        with nullcontext(connection) if connection is not None else self.database.read() as conn:
            rows = conn.execute(f'SELECT * FROM {self.table} WHERE '
                                '(%s::text IS NULL OR owner_id=%s) AND (%s::text IS NULL OR cleanup_id=%s) '
                                'ORDER BY created_at,cleanup_id',
                                (owner_id, owner_id, cleanup_id, cleanup_id)).fetchall()
        return [{**decode_times(row, ('created_at', 'available_at', 'completed_at')),
                 'status': 'completed' if row['completed_at'] else ('retry' if row['attempts'] else 'pending')}
                for row in rows]

    def _worker_lease(self, cleanup_id):
        key = int.from_bytes(sha256((self.table + ':worker:' + cleanup_id).encode()).digest()[:8],
                             'big', signed=True)
        return self.database.advisory_lock(key, blocking=False)

    @contextmanager
    def _claim_pending(self, cleanup_id):
        """Commit an overlap reservation while a crash-released session owns the walk."""
        selected = None
        with ExitStack() as leases:
            with self.database.transaction() as connection:
                self.lock(connection)
                candidates = connection.execute(f'SELECT * FROM {self.table} '
                    'WHERE completed_at IS NULL AND available_at <= clock_timestamp() '
                    'AND (%s::text IS NULL OR cleanup_id=%s) '
                    'ORDER BY available_at,cleanup_id FOR UPDATE SKIP LOCKED', (cleanup_id, cleanup_id)).fetchall()
                reserved = connection.execute(f'SELECT * FROM {self.reservations}').fetchall()
                comparison = DirectoryComparison()
                # A move may temporarily vacate its source before committing
                # new locations. Never complete an expiry snapshot against
                # that temporary absence, or delete a copy still in flight.
                # Skip blocked entries instead of starving unrelated cleanup.
                for row in candidates:
                    operation = 'cleanup-worker:' + row['cleanup_id']
                    if any(item['operation_id'] != operation and
                           comparison.overlaps(cleanup_path(row['location']), cleanup_path(item['location']))
                           for item in reserved):
                        continue
                    with ExitStack() as attempt:
                        if not attempt.enter_context(self._worker_lease(row['cleanup_id'])):
                            continue
                        own = [item for item in reserved if item['operation_id'] == operation]
                        if own and (len(own) != 1 or own[0]['location'] != row['location']
                                    or own[0]['purpose'] != 'directory-cleanup'):
                            raise ValueError('Cleanup worker reservation identity changed')
                        connection.execute(f'INSERT INTO {self.reservations}(operation_id,location,purpose) '
                                           'VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                                           (operation, row['location'], 'directory-cleanup'))
                        leases.enter_context(attempt.pop_all())
                        selected = row
                        break
            # A crashed worker leaves the row pending and its reservation intact.
            # The next worker can adopt it only after obtaining the same session lease.
            yield selected

    def _finish_attempt(self, row, error, *, reservation, release_on_error):
        with self.database.transaction() as connection:
            self.lock(connection)
            if error is not None:
                connection.execute(f'UPDATE {self.table} SET attempts=attempts+1,last_error=%s,'
                    "available_at=clock_timestamp()+least(3600,30*power(2,least(attempts,7)))*interval '1 second' "
                    'WHERE cleanup_id=%s', (str(error)[:4000], row['cleanup_id']))
            else:
                connection.execute(f'UPDATE {self.table} SET attempts=attempts+1,last_error=\'\','
                                   'completed_at=clock_timestamp() WHERE cleanup_id=%s', (row['cleanup_id'],))
            if error is None or release_on_error:
                self.release(reservation, connection=connection)
            return self.status(cleanup_id=row['cleanup_id'], connection=connection)[0]

    def run_pending(self, *, limit=100, cleanup_id=None):
        outcomes = []
        for _ in range(limit):
            with self._claim_pending(cleanup_id) as row:
                if row is None:
                    break
                error = None
                try:
                    remove_path(cleanup_path(row['location']))
                except (OSError, ValueError) as failure:
                    error = failure
                outcomes.append(self._finish_attempt(row, error,
                    reservation='cleanup-worker:' + row['cleanup_id'], release_on_error=True))
        return outcomes

    def run_reserved(self, *, cleanup_id, operation_id, identity):
        """Delete an identity-proven allocation while retaining its reservation.

        The allocation owner must hold its session lease. Ordinary workers skip
        this reserved path; the owner retries it here after recovering that lease.
        Keep the same queue backoff and receipt used by ordinary cleanup.
        """
        from .owned_path_deletion import OwnedPathDeletionStore
        deletion = OwnedPathDeletionStore()
        with self._worker_lease(cleanup_id) as acquired:
            with self.database.transaction() as connection:
                self.lock(connection)
                row = connection.execute(f'SELECT *,available_at<=clock_timestamp() AS ready FROM {self.table} '
                                         'WHERE cleanup_id=%s FOR UPDATE', (cleanup_id,)).fetchone()
                if row is None or row['operation_id'] != operation_id:
                    raise ValueError('Reserved cleanup requires its allocation operation')
                reservations = connection.execute(f'SELECT * FROM {self.reservations}').fetchall()
                comparison = DirectoryComparison()
                if not any(item['operation_id'] == operation_id and item['location'] == row['location']
                           for item in reservations):
                    raise ValueError('Allocation cleanup reservation is missing')
                if row['completed_at']:
                    self.release(operation_id, connection=connection)
                if row['completed_at'] or not acquired or not row['ready'] or any(
                        item['operation_id'] != operation_id and
                        comparison.overlaps(cleanup_path(item['location']), cleanup_path(row['location']))
                        for item in reservations):
                    return self.status(cleanup_id=cleanup_id, connection=connection)[0]
            error = None
            try:
                deletion.remove(cleanup_path(row['location']), identity)
            except (OSError, ValueError) as failure:
                error = failure
            return self._finish_attempt(row, error, reservation=operation_id, release_on_error=False)


@lru_cache(maxsize=16)
def _store(dsn, schema):
    return DirectoryCleanupStore()


def get_directory_cleanup_store():
    return _store(portal_database_url(), _database_schema('directory_lifecycle'))
