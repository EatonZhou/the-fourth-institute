"""Saved searches and frozen run inputs owned by the search domain."""
from __future__ import annotations

import json
import os
import threading
import logging
from collections.abc import Iterable, Mapping
from contextlib import contextmanager, nullcontext
from functools import lru_cache

from management_common.domain_database import DomainDatabase
from . import semantic_search_storage as storage
from .semantic_search_surfaces import RESOURCE_SCOPES

_identity_transaction = threading.local()


def _notify(identifiers):
    from management_portal.resources import get_resource_service
    try:
        for identifier in sorted(identifiers):
            get_resource_service().notify_source_changed(resource_type='search', resource_id=identifier, kind='updated')
    except Exception:
        logging.getLogger(__name__).warning('Search index notification failed; reconciliation will retry', exc_info=True)

DDL = storage.DDL


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase('semantic_search', 'searches', DDL)


def database():
    return _database(os.getenv('PORTAL_DATABASE_URL'), os.getenv('PORTAL_DATABASE_NAMESPACE'))


class SearchStore:
    def __init__(self, surface: str, *, limit: int | None, surfaces: tuple[str, ...] | None = None):
        self.surface, self.limit = surface, limit
        self.surfaces = surfaces or (surface,)
        self._local = threading.local()

    @contextmanager
    def locked(self):
        if getattr(self._local, 'connection', None) is not None:
            yield
            return
        self._local.changed = set()
        with database().transaction(self.surface) as connection:
            self._local.connection = connection
            try:
                yield
            finally:
                self._local.connection = None
        if self._local.changed:
            pending = getattr(_identity_transaction, 'changed', None)
            if pending is not None:
                pending.update(self._local.changed)
            else:
                _notify(self._local.changed)

    def load(self, *, strict: bool = False):
        with self._read() as connection:
            rows = connection.execute('SELECT * FROM jobs WHERE surface=ANY(%s) ORDER BY created_at,search_id', (list(self.surfaces),))
            return [storage.decode(row) for row in rows]

    def _read(self):
        connection = getattr(self._local, 'connection', None)
        return nullcontext(connection) if connection is not None else database().read()

    def get(self, search_id):
        with self._read() as connection:
            return storage.decode(connection.execute('SELECT * FROM jobs WHERE search_id=%s AND surface=ANY(%s)',
                                                     (search_id, list(self.surfaces))).fetchone())

    def _record(self, job):
        record = dict(job)
        record.setdefault('surface', self.surface)
        if record['surface'] not in self.surfaces:
            raise ValueError('Search belongs to another surface')
        return record

    def _changed(self, identifiers, removed):
        targets = {row[0] for row in removed}
        connection = self._local.connection
        if targets:
            connection.execute('DELETE FROM run_inputs WHERE search_id=ANY(%s)', (sorted(targets),))
        self._local.changed.update([*identifiers, *targets])

    def save(self, job):
        """Write one search under the store lock, preserving unrelated rows."""
        connection = getattr(self._local, 'connection', None)
        if connection is None:
            raise RuntimeError(f'{self.surface} must be locked before it is written')
        record = self._record(job)
        storage.save(connection, record)
        removed = [] if self.limit is None else connection.execute('''DELETE FROM jobs WHERE search_id IN
            (SELECT search_id FROM jobs WHERE surface=ANY(%s)
             ORDER BY created_at DESC NULLS LAST,search_id DESC OFFSET %s) RETURNING search_id''',
            (list(self.surfaces), self.limit)).fetchall()
        self._changed([record['search_id']], removed)

    def write(self, jobs):
        """Replace this store's set, for explicit identity migrations and maintenance."""
        connection = getattr(self._local, 'connection', None)
        if connection is None:
            raise RuntimeError(f'{self.surface} must be locked before it is written')
        kept = list(jobs) if self.limit is None else list(jobs)[-self.limit:]
        identifiers = [job['search_id'] for job in kept]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('Duplicate search identity')
        removed = connection.execute('DELETE FROM jobs WHERE surface=ANY(%s) AND NOT(search_id=ANY(%s)) RETURNING search_id', (list(self.surfaces), identifiers)).fetchall()
        for original in kept:
            storage.save(connection, self._record(original))
        self._changed(identifiers, [*removed, *((key,) for key in dropped_search_ids(jobs, kept))])


PAPER_SEARCHES = SearchStore('paper', limit=None)
KNOWLEDGE_SEARCHES = SearchStore('knowledge', limit=200, surfaces=RESOURCE_SCOPES)
BOARD_SEARCHES = SearchStore('wall', limit=300)


@contextmanager
def identity_lock():
    if getattr(_identity_transaction, 'changed', None) is not None:
        yield
        return
    changed = set()
    _identity_transaction.changed = changed
    try:
        with database().transaction('identity'):
            yield
    finally:
        _identity_transaction.changed = None
    # Index readers use separate connections. Publish only after the outer
    # identity transaction commits; exceptions discard pending notifications.
    if changed:
        _notify(changed)


def run_input(search_id, identity, create):
    with database().transaction('input:' + search_id) as connection:
        row = connection.execute('SELECT input_json FROM run_inputs WHERE search_id=%s', (search_id,)).fetchone()
        if row is not None and row[0].get('identity') == identity:
            return row[0]
        payload = create()
        connection.execute('INSERT INTO run_inputs VALUES (%s,%s::jsonb) ON CONFLICT(search_id) DO UPDATE SET input_json=excluded.input_json', (search_id, json.dumps(payload)))
        return payload


def stored_run_input(search_id):
    with database().read() as connection:
        row = connection.execute('SELECT input_json FROM run_inputs WHERE search_id=%s', (search_id,)).fetchone()
        return row[0] if row else None


def fingerprint():
    with database().read() as connection:
        return connection.execute("SELECT md5(COALESCE(string_agg(jobs::text, '' ORDER BY surface,search_id),'')) FROM jobs").fetchone()[0]


def delete_run_inputs(search_ids: Iterable[str]) -> int:
    targets = sorted(set(search_ids) - {''})
    if not targets:
        return 0
    with database().transaction() as connection:
        return connection.execute('DELETE FROM run_inputs WHERE search_id=ANY(%s)', (targets,)).rowcount


def dropped_search_ids(before: Iterable[Mapping], after: Iterable[Mapping]) -> set[str]:
    kept = {str(job.get('search_id') or '') for job in after}
    return {str(job.get('search_id') or '') for job in before} - kept - {''}
