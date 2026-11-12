"""Bounded native parsing workers; the parent alone owns the incremental index."""
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
import hashlib
import json
import multiprocessing
import os
import sqlite3

from scripts.provider_usage_incremental import IncrementalCache, anchors, stamp
from scripts.provider_usage_native import record_path


APPEND_FORMATS = frozenset(('codex', 'claude', 'kimi', 'dsh', 'requests'))


def source_key(source):
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


def parse_source(job):
    """Return normalized observations only, never transcript bodies or DB handles."""
    from scripts.provider_weekly_usage import SOURCES
    source, signature, old, append = job
    path = record_path(source['path'])
    if stamp(path) != signature:
        raise OSError('Source changed before parallel read; retry required')
    cursor = {'offset': old[1] if append else 0,
              'parser': json.loads(old[2]) if append else {}}
    gaps = set(json.loads(old[4])) if append else set()
    gaps.discard('pending_partial_json_line')
    reader_source = {**source, '_cursor': cursor} if source['format'] in APPEND_FORMATS else source
    events = []
    for event in SOURCES[source['format']](reader_source, gaps).read():
        if event:
            at = datetime.fromisoformat(event['at'].replace('Z', '+00:00')).timestamp()
            events.append((at, json.dumps(event)))
    anchor = anchors(path, signature[0][2]) if source['format'] not in ('antigravity', 'opencode_sqlite') else ''
    if stamp(path) != signature:
        raise OSError('Source changed during parallel read; retry required')
    return events, cursor, sorted(gaps), anchor


class ParallelIncrementalCache(IncrementalCache):
    def __init__(self, *args, workers=8, **kwargs):
        if type(workers) is not int or not 1 <= workers <= 16:
            raise ValueError('workers must be between 1 and 16')
        super().__init__(*args, **kwargs)
        available = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count() or 1
        self.workers = min(workers, available)
        self._pool = None
        self._prepared = {}

    def close(self):
        try:
            if self._pool is not None:
                self._pool.shutdown(wait=True, cancel_futures=True)
        finally:
            super().close()

    def _job(self, source):
        path = record_path(source['path'])
        signature = stamp(path)
        if signature[0] is None:
            raise FileNotFoundError(path)
        old = self.db.execute('SELECT stamp,offset,state,anchor,gaps FROM sources WHERE key=?',
                              (source_key(source),)).fetchone()
        if old and json.loads(old[0]) == signature:
            return None, json.loads(old[4])
        previous = json.loads(old[0])[0] if old else None
        append = bool(old and source['format'] in APPEND_FORMATS
                      and previous[:2] == signature[0][:2] and signature[0][2] > previous[2]
                      and anchors(path, previous[2]) == old[3])
        return (source, signature, old, append), None

    def _commit(self, job, result):
        source, signature, old, append = job
        events, cursor, gaps, anchor = result
        key = source_key(source)
        # Parsing holds no SQLite write lock; recheck both inputs before commit.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.db.execute('SELECT stamp,offset,state,anchor,gaps FROM sources WHERE key=?',
                                      (key,)).fetchone()
            if current != old or stamp(record_path(source['path'])) != signature:
                raise OSError('Source or checkpoint changed before commit; retry required')
            if not append:
                self.db.execute('DELETE FROM events WHERE source=?', (key,))
            self.db.executemany('INSERT INTO events(source,at,payload) VALUES(?,?,?)',
                                ((key, at, payload) for at, payload in events))
            self.db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?)',
                            (key, json.dumps(signature), cursor['offset'], json.dumps(cursor['parser']),
                             anchor, json.dumps(gaps)))
        self.stats['raw_files_read'] += 1
        self.stats['appended_files'] += int(append)
        self.stats['jsonl_bytes_read'] += cursor.get('bytes_read', 0)
        return gaps

    def prefetch(self, sources):
        """Yield in source order, keeping at most twice the worker count in flight."""
        self._prepared.clear()
        pending, iterator, exhausted = deque(), iter(sources), False
        while pending or not exhausted:
            while not exhausted and len(pending) < 2 * self.workers:
                try:
                    source = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                try:
                    job, gaps = self._job(source)
                    if job is not None:
                        if self._pool is None:
                            # Spawn avoids inheriting live SQLite/PostgreSQL connections.
                            self._pool = ProcessPoolExecutor(max_workers=self.workers,
                                mp_context=multiprocessing.get_context('spawn'))
                        future = self._pool.submit(parse_source, job)
                    else:
                        future = None
                    pending.append((source, job, future, gaps, None))
                except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
                    pending.append((source, None, None, None, error))
            if not pending:
                continue
            source, job, future, gaps, error = pending.popleft()
            if error is None:
                try:
                    if future is not None:
                        gaps = self._commit(job, future.result())
                    else:
                        self.stats['cache_hits'] += 1
                except Exception as failure:
                    error = failure
            self._prepared[source_key(source)] = (gaps, error)
            yield source

    def read(self, source, reader_type, gaps, since, until):
        key = source_key(source)
        prepared = self._prepared.pop(key, None)
        if prepared is None:
            yield from super().read(source, reader_type, gaps, since, until)
            return
        local_gaps, error = prepared
        if error is not None:
            raise error
        gaps.update(local_gaps)
        for row in self.db.execute('SELECT payload FROM events WHERE source=? AND at>=? AND at<? ORDER BY seq',
                                   (key, since.timestamp(), until.timestamp())):
            yield json.loads(row[0])
