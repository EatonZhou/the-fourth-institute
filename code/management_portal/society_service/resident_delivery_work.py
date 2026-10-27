"""Durable, coalesced publication of accepted inputs and Resident control state.

Admission commits this work with the Job. The coordinator (including its
restart scan) owns projection, watcher wakeups and exact-turn dispatch.
"""
from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)
TABLE = "resident_delivery_work"
COLUMNS = ("resident_id", "message_id", "revision")


def create_schema(connection):
    connection.execute(f"""CREATE TABLE {TABLE} (
        resident_id TEXT NOT NULL REFERENCES residents(resident_id),
        message_id TEXT NOT NULL, revision BIGINT NOT NULL,
        PRIMARY KEY(resident_id, message_id))""")


def enqueue(connection, resident_id, message_id, revision):
    if not message_id:
        raise ValueError("Delivery publication requires message_id")
    connection.execute(
        f"INSERT INTO {TABLE} VALUES (%s,%s,%s) ON CONFLICT (resident_id,message_id) "
        "DO UPDATE SET revision=excluded.revision",
        (resident_id, message_id, revision),
    )
    # The receipt is queryable before its asynchronous chat projection. Never
    # reset an existing running or terminal input on replay or Send now.
    from .resident_conversation_stream import get_repository
    get_repository().record_accepted_input(connection, resident_id, message_id)


def enqueue_state(connection, resident_id, revision):
    """An empty message key denotes state publication, never a new delivery."""
    connection.execute(
        f"INSERT INTO {TABLE} VALUES (%s,'',%s) ON CONFLICT (resident_id,message_id) "
        "DO UPDATE SET revision=excluded.revision", (resident_id, revision),
    )


def pending_ids():
    from .state_store import get_lineage_data_store
    with get_lineage_data_store().database.read() as connection:
        return [row["resident_id"] for row in connection.execute(
            f"SELECT DISTINCT resident_id FROM {TABLE} ORDER BY resident_id"
        ).fetchall()]


def drain(resident_id):
    """Retry idempotent effects outside state locks; acknowledge only this revision."""
    from . import resident_catalog, resident_execution_work, lineage_message_events
    from . import resident_input_queue_domain
    from .state_store import get_lineage_data_store
    store = get_lineage_data_store()
    with store.database.read() as connection:
        rows = connection.execute(
            f"SELECT message_id,revision FROM {TABLE} WHERE resident_id=%s ORDER BY revision,message_id",
            (resident_id,),
        ).fetchall()
    if not rows:
        return True
    cutoff = max(row["revision"] for row in rows)
    try:
        record = resident_catalog.get_catalog().get(resident_id)
    except KeyError:
        record = None
    if record is not None and not record.get("deleted_at"):
        job = resident_execution_work.read(record)
        if job.get("stop_requested_at") and resident_execution_work.is_active(job):
            from .lifecycle import notify_execution_cancellation
            notify_execution_cancellation(resident_execution_work.submitted_execution_id(job), str(job.get("actor") or ""))
        settled = True
        if resident_execution_work.send_now_inputs(job):
            settled = resident_input_queue_domain.reconcile_queued_turn_inputs(resident_id, str(job.get("execution_id") or ""))
        resident_execution_work.project_latest_conversation_state(record, job)
        resident_execution_work.notify_changed(record)
        for row in rows:
            if row["message_id"]:
                lineage_message_events.wake_watchers(record, row["message_id"])
        # Checkpoint retention runs during admission with identity-only records.
        # Filesystem cleanup belongs to this durable, hydrated publication step;
        # a cleanup failure retains the work without rejecting an accepted input.
        from . import resident_media_domain
        resident_media_domain.cleanup_expired_input_images(record)
        if not settled:
            return False
    store.database.write(lambda connection: connection.execute(
        f"DELETE FROM {TABLE} WHERE resident_id=%s AND revision<=%s", (resident_id, cutoff),
    ))
    return True


def recover():
    for resident_id in pending_ids():
        try:
            drain(resident_id)
        except Exception:
            LOGGER.exception("Resident delivery publication remains pending for %s", resident_id)
