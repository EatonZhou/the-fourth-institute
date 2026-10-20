"""Successful local Release publications, consumed as ordered runtime facts.

The publisher owns writes. Readers use this narrow API, which exposes no
publication commands or Lineage state. The log is operational evidence, not a
second release manifest. A repeat publication at the same commit is a new fact.
"""
from functools import lru_cache
import hashlib
import os
import re

from management_common.domain_database import DomainDatabase
from management_common.database_time import instant, iso_time

DDL = '''CREATE TABLE IF NOT EXISTS publications (
    sequence BIGSERIAL PRIMARY KEY,
    publication_key TEXT UNIQUE NOT NULL,
    commit_id TEXT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    previous_commit TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT ''
);
'''


@lru_cache(maxsize=8)
def _domain(dsn, namespace):
    return DomainDatabase('portal_release_events', 'publisher', DDL)


def domain():
    return _domain(os.getenv('PORTAL_DATABASE_URL'), os.getenv('PORTAL_DATABASE_NAMESPACE'))


def record(result):
    if result.get('status') != 'completed' or result.get('ok') is not True:
        raise ValueError('only a healthy completed local publication is a fact')
    commit = result['commit']
    if not re.fullmatch(r'[0-9a-f]{40}', commit) or result.get('active_commit') != commit:
        raise ValueError('publication commit must be the active Release commit')
    key = hashlib.sha256(f"{commit}:{result['started_at']}".encode()).hexdigest()
    # Serializing allocation and commit keeps sequence order equal to commit
    # order; a reader cannot skip a lower sequence still in flight.
    with domain().transaction('append') as connection:
        row = connection.execute('''INSERT INTO publications
            (publication_key,commit_id,completed_at,previous_commit,source)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT(publication_key) DO UPDATE
            SET publication_key=EXCLUDED.publication_key RETURNING sequence''',
            (key, commit, instant(result['completed_at']), result.get('parent_commit', ''),
             result.get('origin', ''))).fetchone()
    return int(row['sequence'])


def latest_sequence():
    with domain().read() as connection:
        row = connection.execute('SELECT COALESCE(MAX(sequence),0) AS sequence FROM publications').fetchone()
    return int(row['sequence'])


def after(sequence, limit=100, *, latest_only=False):
    order = 'DESC' if latest_only else 'ASC'
    with domain().read() as connection:
        rows = connection.execute(f'''SELECT sequence,publication_key,commit_id,completed_at,previous_commit,source FROM publications
            WHERE sequence>%s ORDER BY sequence {order} LIMIT %s''',
            (sequence, 1 if latest_only else limit)).fetchall()
    return [{'sequence': int(row['sequence']), 'publication_id': row['publication_key'],
             'commit': row['commit_id'], 'previous_commit': row['previous_commit'], 'source': row['source'],
             'completed_at': iso_time(row['completed_at'])} for row in rows]
