"""Coordinate runtime replacement with execution admission.

Claims hold a shared transaction lock until their claimed row commits. An
updater holds the exclusive session lock only while recording recovery and
replacing the runtime. Existing work is interrupted and resumed, never drained.
PostgreSQL releases the lock if the updater exits.
"""
from contextlib import contextmanager

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
def activation_window(provider):
    database = portal_database(schema="execution_runtime")
    try:
        with database.connect() as connection:
            key = connection.execute(
                "SELECT hashtextextended(current_schema() || ':provider-runtime:' || %s, 0) AS key",
                (provider,),
            ).fetchone()["key"]
            reserved = connection.execute(
                "SELECT pg_try_advisory_lock(%s) AS reserved", (key,),
            ).fetchone()["reserved"]
            connection.commit()
            try:
                yield bool(reserved)
            finally:
                if reserved:
                    connection.rollback()
                    connection.execute("SELECT pg_advisory_unlock(%s)", (key,))
                    connection.commit()
    finally:
        database.close()
