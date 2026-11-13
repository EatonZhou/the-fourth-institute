"""Append-only account refresh evidence, independent of Home presentation caches.

Every completed monitor attempt is retained, including equal values and failures.
checked_at is not a new Provider observation: each window keeps its source time.
No raw adapter responses, credentials, exception messages or model catalogs enter
this store. History is not exposed through an unauthenticated web endpoint.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import math
import threading

from management_common.portal_database import portal_database
from management_common.database_time import instant, iso_time
from management_common.json_columns import json_text, json_value


FIELDS = {
    'root': ('status', 'checked_at', 'error_code', 'login_observation', 'login_observed_at'),
    'window': ('id', 'label', 'source', 'status', 'quota_group', 'remaining_percent',
               'used_percent', 'window_duration_mins', 'resets_at', 'observed_at', 'stale'),
    'balance': ('status', 'amount', 'currency', 'metric_kind', 'updated_at', 'stale'),
    'credits': ('balance', 'has_credits', 'unlimited', 'observed_at', 'stale'),
}


def _fields(value, keys):
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in keys if key in value and (
        value[key] is None or isinstance(value[key], (str, bool, int))
        or isinstance(value[key], float) and math.isfinite(value[key]))}


def evidence(sample):
    result = _fields(sample, FIELDS['root'])
    result['rate_limits'] = [_fields(w, FIELDS['window']) for w in sample.get('rate_limits') or []
                             if isinstance(w, dict)]
    for key in ('balance', 'credits'):
        if isinstance(sample.get(key), dict):
            result[key] = _fields(sample[key], FIELDS[key])
    if sample.get('status') == 'unavailable':
        for item in [*result['rate_limits'], result.get('balance'), result.get('credits')]:
            if isinstance(item, dict):
                item['stale'] = True
    return result


class AccountObservationStore:
    def __init__(self):
        self.database = portal_database(schema='execution_account_observations')
        self._ready = False
        self._lock = threading.Lock()

    @contextmanager
    def connection(self):
        if not self._ready:
            with self._lock:
                if not self._ready:
                    with self.database.connect() as c:
                        c.execute("SET LOCAL lock_timeout = '1s'")
                        c.execute("SET LOCAL statement_timeout = '2s'")
                        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(), 0))')
                        c.execute('''CREATE TABLE IF NOT EXISTS observations (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            provider TEXT NOT NULL, account_id TEXT NOT NULL,
                            started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ NOT NULL,
                            forced BOOLEAN NOT NULL, sample JSONB NOT NULL
                        )''')
                        c.execute('''CREATE INDEX IF NOT EXISTS observations_account_time
                            ON observations(provider, account_id, finished_at, id)''')
                    self._ready = True
        with self.database.connect() as c:
            c.execute("SET LOCAL lock_timeout = '1s'")
            c.execute("SET LOCAL statement_timeout = '2s'")
            yield c

    def append(self, provider, account_id, sample, *, started_at, finished_at, forced=False):
        with self.connection() as c:
            return c.execute('''INSERT INTO observations
                (provider,account_id,started_at,finished_at,forced,sample)
                VALUES(%s,%s,%s,%s,%s,%s) RETURNING id''',
                (provider, account_id, instant(started_at), instant(finished_at), bool(forced),
                 json_text(evidence(sample)))).fetchone()['id']

    def read(self, provider, account_id, *, since, until=None, after_id=0, limit=1000):
        if type(limit) is not int or not 1 <= limit <= 1000 or type(after_id) is not int or after_id < 0:
            raise ValueError('Invalid history page')
        start = instant(since)
        end = instant(until or datetime.now(timezone.utc))
        if start is None or end is None or start >= end:
            raise ValueError('Invalid history interval')
        with self.connection() as c:
            rows = c.execute('''SELECT id,provider,account_id,started_at,finished_at,forced,sample
                FROM observations WHERE provider=%s AND account_id=%s
                AND finished_at >= %s AND finished_at < %s AND id > %s ORDER BY id LIMIT %s''',
                (provider, account_id, start, end, after_id, limit)).fetchall()
        return [{**dict(row), 'started_at': iso_time(row['started_at']),
                 'finished_at': iso_time(row['finished_at']), 'sample': json_value(row['sample'])}
                for row in rows]
