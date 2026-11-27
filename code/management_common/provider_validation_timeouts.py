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
        CREATE TABLE IF NOT EXISTS pending_checks (
            attempt_id UUID PRIMARY KEY, provider TEXT NOT NULL, candidate TEXT NOT NULL,
            stage TEXT NOT NULL, limit_seconds INTEGER NOT NULL, due_at TIMESTAMPTZ NOT NULL
        );
    """)


def domain():
    return _domain(os.getenv("PORTAL_DATABASE_URL"), os.getenv("PORTAL_DATABASE_NAMESPACE"))


def register(attempt_id, provider, candidate, limit_seconds):
    with domain().transaction("append") as connection:
        connection.execute("""
            INSERT INTO pending_checks (attempt_id, provider, candidate, stage, limit_seconds, due_at)
            VALUES (%s, %s, %s, 'setup', %s, clock_timestamp() + %s * interval '1 second')
        """, (attempt_id, provider, candidate, limit_seconds, limit_seconds))


def set_validation_stage(attempt_id, stage):
    with domain().transaction("append") as connection:
        connection.execute("""
            UPDATE pending_checks SET stage = %s WHERE attempt_id = %s AND due_at > clock_timestamp()
        """, (stage, attempt_id))


def finish_validation(attempt_id):
    # A late completion cannot erase the supervisor's outstanding wake.
    with domain().transaction("append") as connection:
        connection.execute("""
            DELETE FROM pending_checks WHERE attempt_id = %s AND due_at > clock_timestamp()
        """, (attempt_id,))


def collect_due():
    """Commit each elapsed deadline once and return the next one to the external scheduler."""
    with domain().transaction("append") as connection:
        baseline = connection.execute("SELECT COALESCE(MAX(sequence), 0) AS n FROM timeouts").fetchone()["n"]
        connection.execute("""
            WITH expired AS (
                DELETE FROM pending_checks WHERE due_at <= clock_timestamp()
                RETURNING provider, candidate, stage, limit_seconds, due_at
            )
            INSERT INTO timeouts (provider, candidate, stage, limit_seconds, expired_at)
            SELECT provider, candidate, stage, limit_seconds, due_at FROM expired ORDER BY due_at, provider
        """)
        due_at = connection.execute("SELECT MIN(due_at) AS due FROM pending_checks").fetchone()["due"]
        return baseline, due_at


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
