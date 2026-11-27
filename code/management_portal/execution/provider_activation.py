"""Coordinate runtime replacement with execution admission.

Claims hold a shared transaction lock until their claimed row commits. An
updater holds the exclusive session lock only while recording recovery and
replacing the runtime. Existing work is interrupted and resumed, never drained.
PostgreSQL releases the lock if the updater exits.
"""
from contextlib import closing, contextmanager
import hashlib
import json
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


ACTIVATION_READY_CHANNEL = "tfi_provider_activation_ready"


def _pending_channel(database):
    # PostgreSQL channel identifiers are limited to 63 bytes.
    return "tfi_provider_pending_" + hashlib.sha256(database.schema.encode()).hexdigest()[:32]


def notify_pending_updates(providers):
    import psycopg

    database = portal_database(schema="execution_runtime")
    try:
        # Commit independently of any transaction surrounding the file writer.
        with psycopg.connect(database.dsn, autocommit=True, connect_timeout=30) as connection:
            connection.execute("SELECT pg_notify(%s, %s)", (_pending_channel(database), json.dumps(sorted(providers))))
    finally:
        database.close()


@contextmanager
def pending_update_listener():
    import psycopg
    from psycopg import sql

    database = portal_database(schema="execution_runtime")
    try:
        with psycopg.connect(database.dsn, autocommit=True, connect_timeout=30) as connection:
            connection.execute(sql.SQL("LISTEN {}").format(sql.Identifier(_pending_channel(database))))
            yield connection
    finally:
        database.close()


def publish_activation_ready(provider, candidate):
    """The switching owner emits this edge after validation and recovery, under its lock."""
    database = portal_database(schema="execution_runtime")
    try:
        with database.transaction() as connection:
            payload = json.dumps({"schema": database.schema, "provider": provider, "candidate": candidate})
            connection.execute("SELECT pg_notify(%s, %s)", (ACTIVATION_READY_CHANNEL, payload))
    finally:
        database.close()


def wait_for_execution_idle(is_busy, deadline):
    """Listen before sampling so a commit between registration and the read is covered."""
    import psycopg
    from psycopg import sql
    from .scheduler_changes import SCHEDULER_CHANGE_CHANNEL

    database = portal_database(schema="execution_runtime")
    try:
        with psycopg.connect(database.dsn, autocommit=True, connect_timeout=30) as connection:
            connection.execute(sql.SQL("LISTEN {}").format(sql.Identifier(SCHEDULER_CHANGE_CHANNEL)))
            if not is_busy():
                return True
            remaining = deadline - time.monotonic()
            if remaining > 0:
                with closing(connection.notifies(timeout=remaining)) as changes:
                    for change in changes:
                        if time.monotonic() >= deadline:
                            break
                        schema, _, kinds = change.payload.partition(":")
                        if schema == database.schema and "execution" in kinds.split(",") and not is_busy():
                            return True
            return False
    finally:
        database.close()
