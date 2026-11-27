"""Ordered provider acceptance timeouts. Only bounded public diagnostics cross to Lineage."""
from functools import lru_cache
import os

from management_common.database_time import iso_time
from management_common.domain_database import DomainDatabase

EVENT = "provider.validation_timed_out"


@lru_cache(maxsize=8)
def _domain(dsn, namespace):
    return DomainDatabase("provider_validation", "acceptance", """
        CREATE TABLE IF NOT EXISTS timeouts (
            sequence BIGSERIAL PRIMARY KEY, provider TEXT NOT NULL, candidate TEXT NOT NULL,
            stage TEXT NOT NULL, limit_seconds INTEGER NOT NULL, expired_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)


def domain():
    return _domain(os.getenv("PORTAL_DATABASE_URL"), os.getenv("PORTAL_DATABASE_NAMESPACE"))


def record(provider, candidate, stage, limit_seconds):
    with domain().transaction("append") as connection:
        connection.execute("INSERT INTO timeouts (provider,candidate,stage,limit_seconds) VALUES (%s,%s,%s,%s)",
                           (provider, candidate, stage, limit_seconds))


def latest_sequence():
    with domain().read() as connection:
        return connection.execute("SELECT COALESCE(MAX(sequence),0) AS latest FROM timeouts").fetchone()["latest"]


def after(sequence):
    with domain().read() as connection:
        rows = connection.execute("SELECT * FROM timeouts WHERE sequence>%s ORDER BY sequence LIMIT 100",
                                  (sequence,)).fetchall()
    return [{**dict(row), "expired_at": iso_time(row["expired_at"])} for row in rows]
