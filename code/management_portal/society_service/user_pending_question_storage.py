"""Low-level schema and transactional rows for the pending-question index."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

TABLE = "user_pending_requests"
REPAIR_TABLE = "user_pending_request_repairs"
READY_KEY = "user_pending_requests"
CLEANUP_KEY = "user_pending_requests_cleanup_after"
# Created when absent; an existing table is not locked on process start.
SCHEMA_SQL = """
DO $user_pending_requests$
BEGIN
    IF to_regclass('user_pending_requests') IS NULL THEN
        CREATE TABLE user_pending_requests (
            user_id TEXT NOT NULL,
            source_resident_id TEXT NOT NULL,
            source_message_id TEXT NOT NULL,
            resident_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (user_id, source_resident_id, source_message_id)
        );
        CREATE INDEX user_pending_requests_source_idx
            ON user_pending_requests(source_resident_id, source_message_id);
        -- A store without Conversation history has nothing to backfill.
        IF to_regclass('conversation_events') IS NOT NULL
                AND to_regclass('lineage_meta') IS NOT NULL THEN
            IF NOT EXISTS (SELECT 1 FROM conversation_events LIMIT 1) THEN
                INSERT INTO lineage_meta(key, value) VALUES ('user_pending_requests', 'ready')
                    ON CONFLICT (key) DO UPDATE SET value='ready';
            END IF;
        END IF;
    END IF;
    IF to_regclass('user_pending_request_repairs') IS NULL THEN
        CREATE TABLE user_pending_request_repairs (
            resident_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            PRIMARY KEY (resident_id, message_id)
        );
    END IF;
END
$user_pending_requests$
"""
COLUMNS = "user_id, source_resident_id, source_message_id, resident_id, message_id, kind, created_at"


def create_schema(connection: Any) -> None:
    """Create the index when absent; created in a store without history, it is ready at once."""

    connection.execute(SCHEMA_SQL)


def mark_ready(connection: Any) -> None:
    connection.execute(
        "INSERT INTO lineage_meta(key, value) VALUES (%s, 'ready') ON CONFLICT (key) DO UPDATE SET value='ready'",
        (READY_KEY,),
    )


class Pending:
    """What one message write adds to the index: its rows, or a repair when they could not be worked out."""

    def __init__(self, resident_id: str, message_id: str, rows: list[tuple[Any, ...]] | None) -> None:
        self.resident_id, self.message_id, self.rows = resident_id, message_id, rows

    def write(self, connection: Any) -> None:
        if self.rows is None:
            connection.execute(
                f"INSERT INTO {REPAIR_TABLE}(resident_id, message_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (self.resident_id, self.message_id),
            )
            return
        record(connection, self.rows)


def record(connection: Any, rows: Iterable[tuple[Any, ...]]) -> None:
    for row in rows:
        connection.execute(
            f"INSERT INTO {TABLE} ({COLUMNS}) VALUES (%s,%s,%s,%s,%s,%s,COALESCE(%s::timestamptz, now())) "
            "ON CONFLICT (user_id, source_resident_id, source_message_id) "
            "DO UPDATE SET kind=EXCLUDED.kind, resident_id=EXCLUDED.resident_id",
            row,
        )


TABLE_COLUMNS = {
    TABLE: tuple(part.strip() for part in COLUMNS.split(",")),
    REPAIR_TABLE: ("resident_id", "message_id"),
}
INDEXES = frozenset({"user_pending_requests_source_idx"})

