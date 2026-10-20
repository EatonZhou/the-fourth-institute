"""Portal access audit: an append-only log of account and sign-in events.

Rows are only ever inserted; nothing in Portal updates or deletes them, and a
Project rename leaves them as they were written.
"""
from __future__ import annotations

import os
from functools import lru_cache

from psycopg.types.json import Jsonb

from management_common.database_time import instant, iso_time
from management_common.domain_database import DomainDatabase


DDL = """
CREATE TABLE IF NOT EXISTS access_audit (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    details JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS access_audit_at_idx ON access_audit(at, event_id);
"""


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase('portal_identity', 'access_audit', DDL)


def database():
    return _database(os.getenv('PORTAL_DATABASE_URL'), os.getenv('PORTAL_DATABASE_NAMESPACE'))


def append(connection, event: dict) -> None:
    connection.execute(
        'INSERT INTO access_audit(at, actor, action, target, details) VALUES (%s,%s,%s,%s,%s)',
        (instant(event['at']), event['actor'], event['action'], event['target'], Jsonb(event['details'])),
    )


def recent(limit: int) -> list[dict]:
    """The newest events first, each ``at`` returned as a UTC ISO time."""
    with database().read() as connection:
        rows = connection.execute(
            'SELECT at, actor, action, target, details FROM access_audit ORDER BY at DESC, event_id DESC LIMIT %s',
            (limit,),
        )
        return [{**dict(row), 'at': iso_time(row['at'])} for row in rows]
