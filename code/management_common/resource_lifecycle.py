"""Durable ownership of newly allocated resources by a bounded operation.

This association grants no permissions. Callers still use each resource's
normal deletion entry. Existing IDs (reads, updates and retries) are not owned.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import time

from management_common.portal_database import portal_database

_CURRENT = ContextVar("resource_lifecycle", default="")


def current_lifecycle():
    return _CURRENT.get()


class LifecycleStore:
    def __init__(self):
        self.db = portal_database(schema="resource_lifecycle")
        with self.db.read() as db:
            ready = db.execute("SELECT to_regclass('scopes')").fetchone()[0]
        if ready:
            return
        def initialize(db):
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended('resource_lifecycle:init',0))")
            db.execute("CREATE TABLE IF NOT EXISTS scopes (id TEXT PRIMARY KEY, actor TEXT NOT NULL, "
                       "run_id TEXT NOT NULL, case_id TEXT NOT NULL, state TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS members (actor TEXT PRIMARY KEY, scope_id TEXT NOT NULL REFERENCES scopes(id))")
            db.execute("CREATE TABLE IF NOT EXISTS allocations (ordinal BIGSERIAL PRIMARY KEY, "
                       "resource_id TEXT UNIQUE NOT NULL, scope_id TEXT NOT NULL REFERENCES scopes(id), released BOOLEAN NOT NULL DEFAULT FALSE)")
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

    def lock_key(self, scope_id):
        with self.db.read() as db:
            return db.execute("SELECT hashtextextended(%s,0)", (scope_id,)).fetchone()[0]

    def seal(self, scope_id, *, timeout=90):
        # Never queue an exclusive lock behind a request: it may still need a
        # nested RPC's shared lock to finish. Wait without blocking that RPC.
        deadline = time.monotonic() + timeout
        key = self.lock_key(scope_id)
        while True:
            with self.db.advisory_lock(key, blocking=False) as acquired:
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

    def released(self, scope_id, resource_id):
        self.db.write(lambda db: db.execute("UPDATE allocations SET released=TRUE WHERE scope_id=%s AND resource_id=%s", (scope_id, resource_id)))

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
    if inherited and (not scope_id or scope_id == inherited):
        yield
        return
    if inherited:
        raise ValueError("Cannot switch resource lifecycle within a request")
    if not scope_id and not (discover and actor.startswith('resident_')):
        yield
        return
    store = LifecycleStore()
    scope_id = scope_id or store.for_actor(actor)
    if not scope_id:
        yield
        return
    # A dedicated lock connection neither occupies the data pool nor makes
    # allocation writes part of the request's transaction.
    with store.db.advisory_lock(store.lock_key(scope_id), shared=True):
        with store.db.read() as db:
            row = db.execute("SELECT actor,state FROM scopes WHERE id=%s", (scope_id,)).fetchone()
        if not row or row['state'] != 'open':
            raise ValueError("Resource lifecycle is closed")
        if actor != row['actor'] and store.for_actor(actor) != scope_id:
            raise PermissionError("Resource lifecycle does not belong to this caller")
        token = _CURRENT.set(scope_id)
        try:
            yield
        finally:
            _CURRENT.reset(token)


def record_allocation(resource_id):
    scope_id = current_lifecycle()
    # Workspaces belong to Residents and are removed by their lifecycle;
    # notices are durable operational history, not disposable test fixtures.
    if not scope_id or resource_id.partition('_')[0] in {'workspace', 'notice'}:
        return
    store = LifecycleStore()
    store.db.write(lambda db: db.execute(
        "INSERT INTO allocations(resource_id,scope_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (resource_id, scope_id)))
