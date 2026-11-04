"""Durable ownership of newly allocated resources by a bounded operation.

This association grants no permissions. Callers still use each resource's
normal deletion entry. Existing IDs (reads, updates and retries) are not owned.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import time

from management_common.portal_database import portal_database
from management_common.lifetime_execution_lease import execution_lease
from management_common.json_columns import json_text, json_value
from management_common.database_time import iso_time

_CURRENT = ContextVar("resource_lifecycle", default="")


def current_lifecycle():
    return _CURRENT.get()


class LifecycleStore:
    def __init__(self):
        self.db = portal_database(schema="resource_lifecycle")
        with self.db.read() as db:
            ready = db.execute("SELECT to_regclass('scopes') IS NOT NULL AND to_regclass('allocation_corrections') IS NOT NULL").fetchone()[0]
        if ready:
            return
        def initialize(db):
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended('resource_lifecycle:init',0))")
            db.execute("CREATE TABLE IF NOT EXISTS scopes (id TEXT PRIMARY KEY, actor TEXT NOT NULL, "
                       "run_id TEXT NOT NULL, case_id TEXT NOT NULL, state TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS members (actor TEXT PRIMARY KEY, scope_id TEXT NOT NULL REFERENCES scopes(id))")
            db.execute("CREATE TABLE IF NOT EXISTS allocations (ordinal BIGSERIAL PRIMARY KEY, "
                       "resource_id TEXT UNIQUE NOT NULL, scope_id TEXT NOT NULL REFERENCES scopes(id), released BOOLEAN NOT NULL DEFAULT FALSE)")
            db.execute("CREATE TABLE IF NOT EXISTS allocation_corrections ("
                       "scope_id TEXT NOT NULL REFERENCES scopes(id), resource_id TEXT NOT NULL, "
                       "original_ordinal BIGINT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, "
                       "evidence JSONB NOT NULL, corrected_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), "
                       "PRIMARY KEY(scope_id,resource_id))")
        self.db.write(initialize)

    def open(self, key, *, actor, run_id, case_id):
        scope_id = hashlib.sha256(key.encode()).hexdigest()
        self.db.write(lambda db: db.execute(
            "INSERT INTO scopes VALUES (%s,%s,%s,%s,'open') ON CONFLICT DO NOTHING",
            (scope_id, actor, run_id, case_id)))
        with self.db.read() as db:
            row = db.execute("SELECT * FROM scopes WHERE id=%s", (scope_id,)).fetchone()
        if (row['actor'], row['run_id'], row['case_id'], row['state']) != (actor, run_id, case_id, 'open'):
            raise ValueError("Resource lifecycle is already closed or belongs to another operation")
        return scope_id

    def bind(self, scope_id, actor):
        def write(db):
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (actor,))
            previous = db.execute("SELECT s.id,s.state FROM scopes s JOIN members m ON m.scope_id=s.id WHERE m.actor=%s", (actor,)).fetchone()
            if previous and previous['id'] != scope_id and previous['state'] != 'complete':
                raise ValueError("Resident still belongs to an unfinished resource lifecycle")
            row = db.execute("SELECT state FROM scopes WHERE id=%s", (scope_id,)).fetchone()
            if not row or row['state'] != 'open':
                raise ValueError("Resource lifecycle is not open")
            db.execute("INSERT INTO members VALUES (%s,%s) ON CONFLICT (actor) DO UPDATE SET scope_id=excluded.scope_id", (actor, scope_id))
        self.db.write(write)

    def for_actor(self, actor):
        with self.db.read() as db:
            row = db.execute("SELECT scope_id FROM members WHERE actor=%s", (actor,)).fetchone()
        return row[0] if row else ''

    def scope(self, scope_id):
        with self.db.read() as db:
            row = db.execute('SELECT * FROM scopes WHERE id=%s', (scope_id,)).fetchone()
        return dict(row) if row else None

    def assert_open(self, scope_id, *, connection):
        """Fence an owner's commit against request_close in the same transaction."""
        if not scope_id:
            return
        row = connection.execute(f'SELECT state FROM "{self.db.schema}".scopes WHERE id=%s FOR SHARE',
                                 (scope_id,)).fetchone()
        if not row or row['state'] != 'open':
            raise ValueError('Resource lifecycle is closed')

    @contextmanager
    def _execution_lease(self, scope_id, *, shared, blocking=True):
        """One admission/quiescence mechanism for scopes, owners and sealing."""
        with self.db.advisory_lock(self.lock_key(scope_id), shared=shared,
                                   blocking=blocking) as acquired:
            if not acquired:
                yield False
                return
            with execution_lease(scope_id, namespace=self.db.schema,
                                 shared=shared, blocking=blocking) as running:
                yield running

    @contextmanager
    def owner_lease(self, scope_id):
        """Keep pre-registration effects visible to seal, including nested calls."""
        if not scope_id:
            yield
            return
        with self._execution_lease(scope_id, shared=True):
            with self.db.transaction() as connection:
                self.assert_open(scope_id, connection=connection)
            yield

    def for_run(self, run_id):
        with self.db.read() as db:
            return [dict(row) for row in db.execute('SELECT * FROM scopes WHERE run_id=%s AND state<>%s',
                                                   (run_id, 'complete')).fetchall()]

    def request_close(self, scope_id):
        """Reject new requests while existing holders finish or are terminated."""
        self.db.write(lambda db: db.execute("UPDATE scopes SET state='closing' WHERE id=%s AND state='open'", (scope_id,)))

    def closing_runs(self):
        with self.db.read() as db:
            return [row[0] for row in db.execute("SELECT DISTINCT run_id FROM scopes WHERE state='closing'").fetchall()]

    def lock_key(self, scope_id):
        with self.db.read() as db:
            return db.execute("SELECT hashtextextended(%s,0)", (scope_id,)).fetchone()[0]

    def seal(self, scope_id, *, timeout=90):
        # Never queue an exclusive lock behind a request: it may still need a
        # nested RPC's shared lock to finish. Wait without blocking that RPC.
        deadline = time.monotonic() + timeout
        while True:
            with self._execution_lease(scope_id, shared=False, blocking=False) as acquired:
                if acquired:
                    self.db.write(lambda db: db.execute(
                        "UPDATE scopes SET state='closing' WHERE id=%s AND state='open'", (scope_id,)))
                    return
            if time.monotonic() >= deadline:
                raise TimeoutError('Resource lifecycle still has active requests')
            time.sleep(0.05)

    def pending(self, scope_id):
        with self.db.read() as db:
            return [row[0] for row in db.execute("SELECT resource_id FROM allocations WHERE scope_id=%s AND NOT released ORDER BY ordinal DESC", (scope_id,)).fetchall()]

    def owns(self, scope_id, resource_id):
        """Allocation provenance survives deletion and successful cleanup."""
        with self.db.read() as db:
            return db.execute("SELECT 1 FROM allocations WHERE scope_id=%s AND resource_id=%s",
                              (scope_id, resource_id)).fetchone() is not None

    def actors(self, scope_id):
        """Residents assigned to this operation, including its new identities."""
        with self.db.read() as db:
            return {row[0] for row in db.execute(
                "SELECT actor FROM members WHERE scope_id=%s UNION "
                "SELECT resource_id FROM allocations WHERE scope_id=%s AND resource_id LIKE 'resident_%%'",
                (scope_id, scope_id)).fetchall()}

    def released(self, scope_id, resource_id):
        self.db.write(lambda db: db.execute("UPDATE allocations SET released=TRUE WHERE scope_id=%s AND resource_id=%s", (scope_id, resource_id)))

    def correct_association(self, scope_id, resource_id, *, actor, reason, validate):
        """The original scope actor corrects a proven dependency, never a deletion.

        The resource owner supplies current proof under its directory lock.
        Exclusive admission fences in-flight work; the original allocation is
        retained in an immutable audit before its erroneous association goes.
        Normal cleanup, not this operation, subsequently completes the scope.
        """
        if not reason or not reason.strip() or len(reason) > 2000:
            raise ValueError('A bounded correction reason is required')
        with self._execution_lease(scope_id, shared=False, blocking=False) as acquired:
            if not acquired:
                raise RuntimeError('Resource lifecycle still has active requests')
            with self.db.transaction() as db:
                scope = db.execute('SELECT actor,state FROM scopes WHERE id=%s FOR UPDATE', (scope_id,)).fetchone()
                if not scope or not actor or scope['actor'] != actor:
                    raise PermissionError('Only the original lifecycle actor may correct its association')
                def receipt(row):
                    return {'scope_id': scope_id, 'resource_id': resource_id, 'actor': row['actor'],
                            'reason': row['reason'], 'evidence': json_value(row['evidence']),
                            'original_ordinal': row['original_ordinal'],
                            'corrected_at': iso_time(row['corrected_at']), 'outcome': 'association_corrected'}
                prior = db.execute('SELECT * FROM allocation_corrections WHERE scope_id=%s AND resource_id=%s',
                                   (scope_id, resource_id)).fetchone()
                if prior:
                    return receipt(prior)
                if scope['state'] != 'closing':
                    raise ValueError('Correction requires a closed admission boundary')
                allocation = db.execute('SELECT ordinal,released FROM allocations WHERE scope_id=%s AND resource_id=%s FOR UPDATE',
                                        (scope_id, resource_id)).fetchone()
                if not allocation or allocation['released']:
                    raise ValueError('No pending allocation association to correct')
                evidence = validate(db)
                owner = evidence.get('dependency_owner_id')
                if not owner or owner == resource_id or db.execute(
                        'SELECT 1 FROM allocations WHERE scope_id=%s AND resource_id=%s', (scope_id, owner)).fetchone():
                    raise ValueError('The dependency owner must pre-exist this lifecycle')
                row = db.execute('INSERT INTO allocation_corrections(scope_id,resource_id,original_ordinal,actor,reason,evidence) '
                                 'VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',
                                 (scope_id, resource_id, allocation['ordinal'], actor, reason.strip(), json_text(evidence))).fetchone()
                db.execute('DELETE FROM allocations WHERE scope_id=%s AND resource_id=%s', (scope_id, resource_id))
                return receipt(row)

    def complete(self, scope_id):
        def write(db):
            if db.execute("SELECT 1 FROM allocations WHERE scope_id=%s AND NOT released LIMIT 1", (scope_id,)).fetchone():
                raise RuntimeError("Resource lifecycle still has unreleased resources")
            db.execute("UPDATE scopes SET state='complete' WHERE id=%s AND state='closing'", (scope_id,))
            db.execute("DELETE FROM members WHERE scope_id=%s AND EXISTS "
                       "(SELECT 1 FROM scopes WHERE id=%s AND state='complete')", (scope_id, scope_id))
        self.db.write(write)


@contextmanager
def lifecycle_scope(scope_id='', *, actor='', discover=False):
    inherited = current_lifecycle()
    if inherited and scope_id and scope_id != inherited:
        raise ValueError("Cannot switch resource lifecycle within a request")
    scope_id = scope_id or inherited
    if not scope_id and not (discover and actor.startswith('resident_')):
        yield
        return
    store = LifecycleStore()
    scope_id = scope_id or store.for_actor(actor)
    if not scope_id:
        yield
        return
    # Even an explicitly inherited/copied context gets its own actual holder;
    # a parent can exit while a background thread is still using its files.
    with store.owner_lease(scope_id):
        with store.db.read() as db:
            row = db.execute("SELECT actor,state FROM scopes WHERE id=%s", (scope_id,)).fetchone()
        if not row or row['state'] != 'open':
            raise ValueError("Resource lifecycle is closed")
        if not inherited and actor != row['actor'] and store.for_actor(actor) != scope_id:
            raise PermissionError("Resource lifecycle does not belong to this caller")
        token = _CURRENT.set(scope_id)
        try:
            yield
        finally:
            _CURRENT.reset(token)


def record_allocation(resource_id, *, owner_id=''):
    scope_id = current_lifecycle()
    # Additional Workspaces survive unbinding and need explicit deletion.
    # Notices are durable operational history, not disposable test fixtures.
    if not scope_id or resource_id.partition('_')[0] == 'notice':
        return
    store = LifecycleStore()
    if owner_id and not store.owns(scope_id, owner_id):
        return  # A lazy identity for an existing owner's root is a dependency.
    store.db.write(lambda db: db.execute(
        "INSERT INTO allocations(resource_id,scope_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (resource_id, scope_id)))
