"""Rows of pending Resident delivery publications.

Admission writes a row in its own transaction; ``resident_delivery_work``
drains them.
"""
from __future__ import annotations


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
