"""Script admission, execution results and worker lease owned by script_runs."""
from contextlib import contextmanager
from copy import deepcopy
from functools import lru_cache
import os

from management_common.domain_database import DomainDatabase
from psycopg.types.json import Jsonb
from management_common.database_time import encode_times, decode_times

TEXT_FIELDS = ('lineage_id','trigger_path_id','effect_id','caller_id','status','admission','cwd','path','runtime',
               'sha256','unit','exclusive_key','started_at','finished_at','stderr_tail','error_code','trigger_type')
JSON_FIELDS = ('arguments','outputs','artifacts','identity')
COLUMNS = {**{k:'TEXT' for k in TEXT_FIELDS}, **{k:'JSONB' for k in JSON_FIELDS},
           'started_at':'TIMESTAMPTZ','finished_at':'TIMESTAMPTZ',
           'argv':'TEXT[]','exit_code':'BIGINT','memory_mb':'BIGINT','timeout_seconds':'BIGINT',
           'network':'BOOLEAN','cancel_requested':'BOOLEAN','notified':'BOOLEAN',
           'stdout_truncated':'BOOLEAN','stderr_truncated':'BOOLEAN','duration_seconds':'DOUBLE PRECISION'}
DDL = 'CREATE TABLE IF NOT EXISTS runs (script_run_id TEXT PRIMARY KEY,' + ','.join('"'+k+'" '+v for k,v in COLUMNS.items()) + ');' + """
CREATE INDEX IF NOT EXISTS script_lineage_idx ON runs(lineage_id,started_at);
CREATE INDEX IF NOT EXISTS script_status_idx ON runs(status,finished_at);
CREATE UNIQUE INDEX IF NOT EXISTS script_admission_idx ON runs(admission) WHERE admission<>'';
CREATE INDEX IF NOT EXISTS script_exclusive_idx ON runs(lineage_id,exclusive_key) WHERE status='running';
CREATE INDEX IF NOT EXISTS script_pending_idx ON runs(script_run_id) WHERE status='running' OR notified IS NOT TRUE;
CREATE INDEX IF NOT EXISTS script_retention_idx ON runs(finished_at) WHERE notified IS TRUE AND status<>'running';
CREATE TABLE IF NOT EXISTS worker_lease (singleton INTEGER PRIMARY KEY CHECK(singleton=1),holder TEXT NOT NULL,until_at TIMESTAMPTZ NOT NULL);
"""


@lru_cache(maxsize=8)
def _domain(dsn, namespace):
    return DomainDatabase('script_runs','execution',DDL)


def domain():
    return _domain(os.getenv('PORTAL_DATABASE_URL'),os.getenv('PORTAL_DATABASE_NAMESPACE'))


def _record(row):
    if row is None:
        return None
    return {k: v for k, v in decode_times(row, ('started_at', 'finished_at')).items()
            if v is not None or k == 'exit_code'}


def _values(row):
    unknown = set(row) - {'script_run_id', *COLUMNS}
    if unknown:
        raise ValueError(f'Unknown script run fields: {sorted(unknown)}')
    encoded = encode_times(row, ('started_at', 'finished_at'))
    return tuple(Jsonb(encoded.get(k)) if k in JSON_FIELDS else encoded.get(k) for k in COLUMNS)


def admit(record):
    """Serialize only competing admissions; no historical records are loaded."""
    admission, exclusive = record.get('admission'), record.get('exclusive_key')
    keys = []
    if admission:
        keys.append('admission:' + admission)
    if exclusive:
        keys.append('exclusive:' + record['lineage_id'] + ':' + exclusive)
    with domain().transaction() as c:
        for key in sorted(keys):
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':' || %s, 0))", (key,))
        if admission:
            existing = c.execute('SELECT * FROM runs WHERE admission=%s', (admission,)).fetchone()
            if existing:
                return _record(existing), 'replayed'
        if exclusive:
            existing = c.execute("SELECT * FROM runs WHERE lineage_id=%s AND exclusive_key=%s AND status='running' LIMIT 1",
                                 (record['lineage_id'], exclusive)).fetchone()
            if existing:
                return _record(existing), 'already_running'
        columns = ','.join('"' + k + '"' for k in COLUMNS)
        row = c.execute(f'INSERT INTO runs (script_run_id,{columns}) VALUES ({",".join(["%s"] * (len(COLUMNS)+1))}) RETURNING *',
                        (record['script_run_id'], *_values(record))).fetchone()
        return _record(row), 'created'


def get(identifier):
    with domain().read() as c:
        return _record(c.execute('SELECT * FROM runs WHERE script_run_id=%s', (identifier,)).fetchone())


@contextmanager
def edit(identifier):
    """Only the selected run is locked; external launch/stop stays outside."""
    with domain().transaction() as c:
        row = _record(c.execute('SELECT * FROM runs WHERE script_run_id=%s FOR UPDATE', (identifier,)).fetchone())
        before = deepcopy(row)
        yield row
        if row is not None and row != before:
            assignments = ','.join('"' + k + '"=%s' for k in COLUMNS)
            c.execute(f'UPDATE runs SET {assignments} WHERE script_run_id=%s', (*_values(row), identifier))


def list_runs(arguments):
    fields = [k for k in ('lineage_id', 'effect_id', 'status', 'path') if arguments.get(k)]
    predicate = ' AND '.join(k + '=%s' for k in fields) or 'TRUE'
    values = tuple(arguments[k] for k in fields)
    limit = min(200, max(1, int(arguments.get('limit', 50))))
    with domain().read() as c:
        total = c.execute(f'SELECT count(*) AS total FROM runs WHERE {predicate}', values).fetchone()['total']
        rows = c.execute(f'SELECT * FROM runs WHERE {predicate} ORDER BY started_at DESC,script_run_id DESC LIMIT %s', (*values, limit)).fetchall()
    return {'runs': [_record(row) for row in rows], 'total': total}


def pending(after='', limit=100):
    with domain().read() as c:
        return [_record(row) for row in c.execute(
            "SELECT * FROM runs WHERE (status='running' OR notified IS NOT TRUE) AND script_run_id>%s ORDER BY script_run_id LIMIT %s",
            (after, limit))]


def expired(limit=100):
    with domain().read() as c:
        return [row['script_run_id'] for row in c.execute(
            "SELECT script_run_id FROM runs WHERE notified IS TRUE AND status<>'running' AND finished_at < CURRENT_TIMESTAMP-INTERVAL '60 days' ORDER BY finished_at LIMIT %s", (limit,))]


def delete_expired(identifier):
    with domain().transaction() as c:
        c.execute("DELETE FROM runs WHERE script_run_id=%s AND notified IS TRUE AND status<>'running' AND finished_at < CURRENT_TIMESTAMP-INTERVAL '60 days'", (identifier,))


def acquire_lease(holder):
    with domain().transaction() as c:
        return c.execute("""INSERT INTO worker_lease VALUES(1,%s,clock_timestamp()+INTERVAL '30 seconds')
            ON CONFLICT(singleton) DO UPDATE SET holder=excluded.holder,until_at=excluded.until_at
            WHERE worker_lease.holder=excluded.holder OR worker_lease.until_at<=clock_timestamp()
            RETURNING holder""", (holder,)).fetchone() is not None


def renew_lease(holder):
    with domain().transaction() as c:
        return c.execute("UPDATE worker_lease SET until_at=clock_timestamp()+INTERVAL '30 seconds' WHERE singleton=1 AND holder=%s AND until_at>clock_timestamp() RETURNING holder", (holder,)).fetchone() is not None


def release_lease(holder):
    with domain().transaction() as c:
        c.execute('DELETE FROM worker_lease WHERE singleton=1 AND holder=%s', (holder,))
