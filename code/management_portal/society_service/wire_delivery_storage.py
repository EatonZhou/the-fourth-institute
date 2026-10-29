"""Tables of Resident Route delivery budgets and their admission receipts."""
from __future__ import annotations

from typing import Any


COLUMNS = {
    "wire_delivery_budgets": ("lineage_id", "wire_id", "generation"),
    "wire_delivery_receipts": (
        "lineage_id",
        "wire_id",
        "message_id",
        "generation",
        "status",
        "accepted_message_id",
    ),
}


def create_schema(connection: Any) -> None:
    connection.execute("""CREATE TABLE wire_delivery_budgets (
        lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id), wire_id TEXT NOT NULL,
        generation BIGINT NOT NULL DEFAULT 0, PRIMARY KEY(lineage_id,wire_id))""")
    connection.execute("""CREATE TABLE wire_delivery_receipts (
        lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id), wire_id TEXT NOT NULL,
        message_id TEXT NOT NULL, generation BIGINT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('reserved','sent')),
        accepted_message_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(lineage_id,wire_id,message_id))""")
    connection.execute(
        "CREATE INDEX wire_delivery_count_idx ON wire_delivery_receipts(lineage_id,wire_id,generation,status)"
    )
