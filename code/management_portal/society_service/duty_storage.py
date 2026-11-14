"""Duty records: the holder, description, category and order of a duty's Function Block.

Applying a template to a Resident wraps the events, Effects and wires it adds
in a Function Block; the duty record says whose duty that block is. The
record rides on the block in the Lineage definition (``block["duty"]``), is
written in the same transaction as the graph and disappears with the block.
"""
from __future__ import annotations

from typing import Any

from management_common.json_columns import json_text, json_value

TABLE = "function_block_duties"
COLUMNS = {TABLE: ("function_block_id", "holder_id", "description", "category", "position", "parameters")}
CATEGORIES = ("fixed", "common", "custom")
# Created when absent: an installed store gains the table on its next start
# without touching existing tables.
SCHEMA_SQL = f"""
DO $function_block_duties$
BEGIN
    IF to_regclass('{TABLE}') IS NULL THEN
        CREATE TABLE {TABLE} (
            function_block_id TEXT PRIMARY KEY
                REFERENCES function_blocks(function_block_id) ON DELETE CASCADE,
            holder_id TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL CHECK (category IN {CATEGORIES}),
            position INTEGER NOT NULL DEFAULT 0,
            parameters JSONB NOT NULL DEFAULT '{{}}'
        );
    END IF;
END
$function_block_duties$
"""


def create_schema(connection: Any) -> None:
    connection.execute(SCHEMA_SQL)


def store_program(connection: Any, lineage_id: str, definition: dict) -> None:
    """Persist the duty records of a Lineage's blocks after the blocks are written."""
    rows = [
        (block["id"], block["duty"]["holder_id"], block["duty"].get("description", ""),
         block["duty"].get("category", "custom"), int(block["duty"].get("position", 0)),
         json_text(block["duty"].get("parameters") or {}, ensure_ascii=False))
        for block in definition.get("function_blocks") or [] if block.get("duty")
    ]
    connection.execute(
        f"DELETE FROM {TABLE} d USING function_blocks b WHERE b.function_block_id=d.function_block_id "
        "AND b.lineage_id=%s AND NOT (d.function_block_id=ANY(%s))",
        (lineage_id, [row[0] for row in rows]),
    )
    if rows:
        connection.executemany(
            f"INSERT INTO {TABLE} ({', '.join(COLUMNS[TABLE])}) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (function_block_id) DO UPDATE SET holder_id=excluded.holder_id, "
            "description=excluded.description, category=excluded.category, "
            "position=excluded.position, parameters=excluded.parameters",
            rows,
        )


def hydrate_programs(connection: Any, definitions: dict[str, dict]) -> None:
    """Attach each block's duty record in one query."""
    blocks = {block["id"]: block for value in definitions.values() for block in value.get("function_blocks") or []}
    if not blocks:
        return
    rows = connection.execute(
        f"SELECT {', '.join(COLUMNS[TABLE])} FROM {TABLE} WHERE function_block_id=ANY(%s)",
        (list(blocks),),
    ).fetchall()
    for row in rows:
        blocks[row["function_block_id"]]["duty"] = {
            "holder_id": row["holder_id"], "description": row["description"],
            "category": row["category"], "position": row["position"],
            "parameters": json_value(row["parameters"]) or {},
        }
