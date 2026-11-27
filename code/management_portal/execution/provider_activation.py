"""Coordinate runtime replacement with execution admission.

Claims hold a shared transaction lock until their claimed row commits. An
updater holds the exclusive session lock only while recording recovery and
replacing the runtime. Existing work is interrupted and resumed, never drained.
PostgreSQL releases the lock if the updater exits.
"""
from contextlib import contextmanager
import time

from management_common.portal_database import portal_database


def admitting_providers(connection, providers):
    providers = sorted(set(providers))
    if not providers:
        return set()
    return {row["provider"] for row in connection.execute(
        "SELECT provider, pg_try_advisory_xact_lock_shared("
        "hashtextextended(current_schema() || ':provider-runtime:' || provider, 0)) AS admitted "
        "FROM unnest(%s::text[]) AS provider", (providers,),
    ) if row["admitted"]}


@contextmanager
def activation_window(provider, *, deadline=None):
    """Try immediately, or join PostgreSQL's wait queue until the monotonic deadline."""
    import psycopg

    database = portal_database(schema="execution_runtime")
    # Waiting here must not consume the execution pool's connections. Closing
    # this dedicated session also releases its lock on failure or cancellation.
    try:
        with psycopg.connect(database.dsn, connect_timeout=30) as connection:
            key = connection.execute(
                "SELECT hashtextextended(%s || ':provider-runtime:' || %s, 0)",
                (database.schema, provider),
            ).fetchone()[0]
            if deadline is None:
                reserved = connection.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]
            else:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    yield False
                    return
                connection.execute("SELECT set_config('lock_timeout', %s, true), "
                                   "set_config('statement_timeout', '0', true)", (str(remaining_ms),))
                try:
                    connection.execute("SELECT pg_advisory_lock(%s)", (key,))
                except psycopg.errors.LockNotAvailable:
                    connection.rollback()
                    yield False
                    return
                reserved = True
            connection.commit()
            yield reserved
    finally:
        database.close()
