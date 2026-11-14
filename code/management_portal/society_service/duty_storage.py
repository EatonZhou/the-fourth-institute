"""Duty records: the holder, description, category, order and prompt of a duty's Function Block.

Applying a template to a Resident wraps the events, Effects and wires it adds
in a Function Block; the duty record says whose duty that block is. The
record rides on the block in the Lineage definition (``block["duty"]``), is
written in the same transaction as the graph and disappears with the block.
A duty's prompt and its memory level (``block["duty"]["memory"]``) live in
the same row, so adding or removing the duty adds or removes the prompt.
"""
from __future__ import annotations

from typing import Any

from management_common.json_columns import json_text, json_value

TABLE = "function_block_duties"
COLUMNS = {TABLE: ("function_block_id", "holder_id", "description", "category", "position", "parameters",
                   "memory_level", "memory_prompt")}
CATEGORIES = ("fixed", "common", "custom")
# Created when absent: an installed store gains the table on its next start
# without touching existing tables. The memory columns came with the second
# step; a table created before them gains them once.
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
            parameters JSONB NOT NULL DEFAULT '{{}}',
            memory_level TEXT NOT NULL DEFAULT '',
            memory_prompt TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX {TABLE}_holder_idx ON {TABLE}(holder_id);
    ELSIF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name='{TABLE}' AND column_name='memory_prompt'
    ) THEN
        ALTER TABLE {TABLE}
            ADD COLUMN memory_level TEXT NOT NULL DEFAULT '',
            ADD COLUMN memory_prompt TEXT NOT NULL DEFAULT '';
        CREATE INDEX {TABLE}_holder_idx ON {TABLE}(holder_id);
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
         json_text(block["duty"].get("parameters") or {}, ensure_ascii=False),
         (block["duty"].get("memory") or {}).get("level", ""),
         (block["duty"].get("memory") or {}).get("prompt", ""))
        for block in definition.get("function_blocks") or [] if block.get("duty")
    ]
    connection.execute(
        f"DELETE FROM {TABLE} d USING function_blocks b WHERE b.function_block_id=d.function_block_id "
        "AND b.lineage_id=%s AND NOT (d.function_block_id=ANY(%s))",
        (lineage_id, [row[0] for row in rows]),
    )
    if rows:
        connection.executemany(
            f"INSERT INTO {TABLE} ({', '.join(COLUMNS[TABLE])}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (function_block_id) DO UPDATE SET holder_id=excluded.holder_id, "
            "description=excluded.description, category=excluded.category, "
            "position=excluded.position, parameters=excluded.parameters, "
            "memory_level=excluded.memory_level, memory_prompt=excluded.memory_prompt",
            rows,
        )


def _duty(row: Any) -> dict:
    duty = {
        "holder_id": row["holder_id"], "description": row["description"],
        "category": row["category"], "position": row["position"],
        "parameters": json_value(row["parameters"]) or {},
    }
    if row["memory_prompt"]:
        duty["memory"] = {"level": row["memory_level"] or "trigger", "prompt": row["memory_prompt"]}
    return duty


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
        blocks[row["function_block_id"]]["duty"] = _duty(row)


def held_by(connection: Any, holder_id: str) -> list[dict]:
    """A holder's duties in live Lineages, first duty first, without loading definitions."""
    rows = connection.execute(
        f"SELECT {', '.join('d.' + column for column in COLUMNS[TABLE])}, b.lineage_id, b.title "
        f"FROM {TABLE} d JOIN function_blocks b ON b.function_block_id=d.function_block_id "
        "JOIN lineage_nodes n ON n.node_id=b.function_block_id AND n.deleted_at IS NULL "
        "JOIN lineages l ON l.lineage_id=b.lineage_id AND l.deleted_at IS NULL "
        "WHERE d.holder_id=%s ORDER BY b.lineage_id, d.position, b.created_at, d.function_block_id",
        (holder_id,),
    ).fetchall()
    return [{"function_block_id": row["function_block_id"], "lineage_id": row["lineage_id"],
             "title": row["title"], **_duty(row)} for row in rows]
