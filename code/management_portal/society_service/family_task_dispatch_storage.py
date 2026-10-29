"""The table of first-request snapshots for Family task admission."""
from __future__ import annotations


COLUMNS = {"family_task_dispatches": ("lineage_id", "message_id", "intent_json")}


def create_schema(connection):
    connection.execute("""CREATE TABLE family_task_dispatches (
        lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id),
        message_id TEXT NOT NULL, intent_json JSONB NOT NULL,
        PRIMARY KEY(lineage_id,message_id))""")
