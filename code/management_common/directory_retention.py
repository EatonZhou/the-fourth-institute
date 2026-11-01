"""Ownership and retention of generated files without a separate domain object.

Domain objects keep their own path truth. This store owns only feature artifacts;
on expiry it unregisters them and passes a deletion snapshot to the shared queue.
No scheduler or worker is started here.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .directory_cleanup_store import get_directory_cleanup_store
from .directory_paths import cleanup_location, cleanup_path
from .portal_database import portal_database


# None means a domain event (or capacity), never a wall-clock guess.
POLICIES = {
    'runtime_tmp': {'days': 0, 'clock': 'finished_at'},
    'test_evidence': {'days': 7, 'clock': 'finished_at'},
    'cache': {'days': None, 'clock': 'capacity_lru'},
    'workbench_build': {'days': 7, 'clock': 'replaced_at'},
    'background': {'days': 7, 'clock': 'finished_at'},
    'provider_session': {'days': 30, 'clock': 'finished_at'},
    'unused_upload': {'days': 7, 'clock': 'created_at'},
    'object_data': {'days': None, 'clock': 'object_deleted'},
}

DDL = """
CREATE TABLE IF NOT EXISTS artifacts (
 location TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('created','adopted')),
 category TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL,
 eligible_at TIMESTAMPTZ, last_access TIMESTAMPTZ NOT NULL,
 bytes BIGINT NOT NULL CHECK(bytes >= 0), cache_group TEXT NOT NULL DEFAULT '',
 test_run_id TEXT NOT NULL DEFAULT ''
);
"""


def utc_now():
    return datetime.now(timezone.utc)


def relocate_artifacts(connection, relocate):
    """Enlist current artifact locations in the enclosing directory move.

    The move owner supplies its one location transformation. Retention owns
    the table and preserves deadlines, ownership and test-run provenance.
    No table means no declarations; do not open another initialization
    transaction while the caller holds the shared path lock.
    """
    from .directory_cleanup_store import DirectoryCleanupStore
    table = f'"{portal_database(schema="directory_retention").schema}".artifacts'
    queue = DirectoryCleanupStore(connection=connection)
    queue.lock(connection)
    if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None:
        return
    rows = connection.execute(f'SELECT location FROM {table} FOR UPDATE').fetchall()
    for row in rows:
        location = relocate(row['location'])
        if location != row['location']:
            queue.assert_available(cleanup_path(location), connection=connection)
            connection.execute(f'UPDATE {table} SET location=%s WHERE location=%s',
                               (location, row['location']))


class RetentionStore:
    def __init__(self, *, connection=None):
        self.database = portal_database(schema='directory_retention')
        self.table = f'"{self.database.schema}".artifacts'
        if connection is not None:
            from .directory_cleanup_store import DirectoryCleanupStore
            self.queue = DirectoryCleanupStore(connection=connection)
            if connection.execute('SELECT to_regclass(%s)', (self.table,)).fetchone()[0] is None:
                raise RuntimeError('Initialize the retention schema before enlisted use')
            return
        self.queue = get_directory_cleanup_store()
        with self.database.read() as connection:
            exists = connection.execute('SELECT to_regclass(%s)', (self.table,)).fetchone()[0]
        if not exists:
            def initialize(connection):
                connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (self.table,))
                connection.executescript(DDL)
            self.database.write(initialize)

    def declare(self, path, *, category, owner_id, origin='created', created_at=None,
                finished_at=None, bytes=0, cache_group='', connection=None):
        if category not in POLICIES or origin not in {'created', 'adopted'} or not owner_id:
            raise ValueError('File category, origin and directory owner are required')
        created_at = created_at or utc_now()
        clock = POLICIES[category]['clock']
        start = created_at if clock == 'created_at' else finished_at if clock == 'finished_at' else None
        expiry = start + timedelta(days=POLICIES[category]['days']) if start is not None else None
        location = cleanup_location(path)
        from .resource_lifecycle import current_lifecycle, LifecycleStore
        scope_id = current_lifecycle()
        scope = LifecycleStore().scope(scope_id) if scope_id else None
        test_run_id = scope['run_id'] if scope else ''
        with nullcontext(connection) if connection is not None else self.database.transaction() as conn:
            self.queue.assert_available(path, connection=conn)
            prior = conn.execute(f'SELECT owner_id,category,origin FROM {self.table} WHERE location=%s', (location,)).fetchone()
            if prior:
                if (prior['owner_id'], prior['category'], prior['origin']) != (owner_id, category, origin):
                    raise ValueError('Artifact already belongs to another owner or category')
                return  # Retries never extend an existing retention deadline.
            conn.execute(f'INSERT INTO {self.table} VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                         (location, owner_id, origin, category, created_at, expiry, created_at, bytes, cache_group, test_run_id))

    def finish(self, owner_id, *, at=None, connection=None):
        """The writer confirms work has ended; live Provider sessions never age out."""
        at = at or utc_now()
        with nullcontext(connection) if connection is not None else self.database.transaction() as conn:
            self.queue.lock(conn)
            for category, policy in POLICIES.items():
                if policy['clock'] == 'finished_at':
                    conn.execute(f'UPDATE {self.table} SET eligible_at=%s WHERE owner_id=%s AND category=%s AND eligible_at IS NULL',
                                 (at + timedelta(days=policy['days']), owner_id, category))

    def used(self, path, *, owner_id, connection=None):
        """An accepted upload becomes object data, owned by its consuming object."""
        if not owner_id:
            raise ValueError('Consumed upload requires its object owner')
        with nullcontext(connection) if connection is not None else self.database.transaction() as conn:
            self.queue.assert_available(path, connection=conn)
            registered = conn.execute(f'SELECT category FROM {self.table} WHERE location=%s FOR UPDATE',
                                      (cleanup_location(path),)).fetchone()
            if registered is None:
                raise ValueError('Upload retention registration is missing; complete the offline migration first')
            if registered['category'] not in {'unused_upload', 'object_data'}:
                raise ValueError('Only an original upload can be consumed')
            conn.execute(f"UPDATE {self.table} SET owner_id=%s,category='object_data',eligible_at=NULL WHERE location=%s AND category='unused_upload'",
                         (owner_id, cleanup_location(path)))

    def touch(self, path, *, bytes=None, at=None):
        with self.database.transaction() as conn:
            self.queue.assert_available(path, connection=conn)
            conn.execute(f'UPDATE {self.table} SET last_access=%s,bytes=coalesce(%s,bytes) WHERE location=%s',
                         (at or utc_now(), bytes, cleanup_location(path)))

    def _release(self, row, connection):
        if row['origin'] == 'created':
            self.queue.enqueue(cleanup_path(row['location']), owner_id=row['owner_id'],
                               operation_id='retention:' + uuid4().hex, actor='directory-retention', connection=connection)
        connection.execute(f'DELETE FROM {self.table} WHERE location=%s', (row['location'],))

    def release_owner(self, owner_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self.database.transaction() as conn:
            self.queue.lock(conn)
            rows = conn.execute(f'SELECT * FROM {self.table} WHERE owner_id=%s FOR UPDATE', (owner_id,)).fetchall()
            for row in rows:
                self._release(row, conn)
            return len(rows)

    def release_run(self, run_id):
        """After the run's writers stop, remove its generated fixtures/artifacts.

        Evidence keeps seven days. Shared caches keep their capacity policy;
        shared builds use authoritative references after application deletion.
        """
        if not run_id:
            raise ValueError('Test run ID required')
        with self.database.transaction() as connection:
            self.queue.lock(connection)
            rows = connection.execute(f'''SELECT * FROM {self.table} WHERE test_run_id=%s
                AND category NOT IN ('test_evidence','workbench_build','cache') FOR UPDATE''', (run_id,)).fetchall()
            for row in rows:
                self._release(row, connection)
            return len(rows)

    def release_path(self, path, *, connection=None):
        with nullcontext(connection) if connection is not None else self.database.transaction() as conn:
            self.queue.lock(conn)
            row = conn.execute(f'SELECT * FROM {self.table} WHERE location=%s FOR UPDATE',
                               (cleanup_location(path),)).fetchone()
            if row:
                self._release(row, conn)
            return bool(row)

    def sweep(self, *, now=None, cache_capacities=None, owner_id=None, categories=None):
        now = now or utc_now()
        count = 0
        with self.database.transaction() as conn:
            self.queue.lock(conn)
            rows = conn.execute(f'SELECT * FROM {self.table} WHERE eligible_at<=%s '
                                'AND (%s::text IS NULL OR owner_id=%s) '
                                'AND (%s::text[] IS NULL OR category=ANY(%s::text[])) FOR UPDATE',
                                (now, owner_id, owner_id, categories, categories)).fetchall()
            for row in rows:
                self._release(row, conn)
                count += 1
            for group, capacity in (cache_capacities or {}).items():
                if capacity < 0:
                    raise ValueError('Cache capacity cannot be negative')
                rows = conn.execute(f"SELECT * FROM {self.table} WHERE category='cache' AND cache_group=%s AND origin='created' ORDER BY last_access,location FOR UPDATE", (group,)).fetchall()
                size = sum(row['bytes'] for row in rows)
                for row in rows:
                    if size <= capacity:
                        break
                    from .cache_retention_lock import cache_lease
                    with cache_lease(cleanup_path(row['location']), blocking=False) as acquired:
                        if acquired:
                            self._release(row, conn)
                            size -= row['bytes']
                            count += 1
        return {'queued': count}
