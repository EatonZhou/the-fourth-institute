"""Pulse items their owners published for every Pulse reader.

The Portal-Pulse Resident's summaries and index stay files in its Lineage
Folder; only the publications Portal records live here.
"""
from __future__ import annotations

import os
from functools import lru_cache

from psycopg.types.json import Jsonb

from management_common.domain_database import DomainDatabase
from management_common.database_time import encode_times, decode_times


DDL = """
CREATE TABLE IF NOT EXISTS publications (
    publication_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    source_period TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    text TEXT NOT NULL,
    project TEXT NOT NULL,
    evidence_paths JSONB NOT NULL,
    published_at TIMESTAMPTZ NOT NULL
);
"""
FIELDS = ('publication_id', 'owner', 'source_period', 'source_key', 'source_item_id',
          'text', 'project', 'evidence_paths', 'published_at')
# Publish and unpublish read and change publications under this lock.
LOCK = 'publications'


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase('portal_pulse', 'publications', DDL)


def database():
    return _database(os.getenv('PORTAL_DATABASE_URL'), os.getenv('PORTAL_DATABASE_NAMESPACE'))


def items(connection) -> list[dict]:
    rows = connection.execute(f'SELECT {",".join(FIELDS)} FROM publications ORDER BY published_at, publication_id')
    return [decode_times(row, ('published_at',)) for row in rows]


def add(connection, item: dict) -> None:
    """Insert one publication; an existing publication_id is kept as it is."""
    item = encode_times(item, ('published_at',))
    connection.execute(
        f'INSERT INTO publications({",".join(FIELDS)}) VALUES ({",".join(["%s"] * len(FIELDS))}) '
        'ON CONFLICT(publication_id) DO NOTHING',
        tuple(Jsonb(item[field]) if field == 'evidence_paths' else item[field] for field in FIELDS),
    )


def remove(connection, publication_id: str) -> None:
    connection.execute('DELETE FROM publications WHERE publication_id=%s', (publication_id,))
