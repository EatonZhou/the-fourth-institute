"""Typed storage helpers and chat-fragment relations.

Writes reconcile stable IDs, so editing one item leaves other items and their
indexes alone. Public dictionaries are read models, not stored documents.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from management_common.database_time import instant, iso_time
from management_common.json_columns import json_text, json_value


FRAGMENT_FIELDS = {
    "content": "text", "created_at": "instant", "created_by": "text",
    "updated_at": "instant", "updated_by": "text",
}
ATTACHMENT_FIELDS = {
    "file_id": "text", "kind": "text", "name": "text", "path": "text",
    "preview_url": "text", "content_type": "text", "size": "integer",
}


def column(field: str, kind: str) -> str:
    return field + "_json" if kind in {"object", "array"} else field


def encode(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == 'instant':
        return instant(value)
    expected = {"text": str, "integer": int, "object": dict, "array": list, "boolean": bool}[kind]
    if not isinstance(value, expected) or (kind == "integer" and isinstance(value, bool)):
        raise ValueError(f"Expected {kind}, got {type(value).__name__}")
    return json_text(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) if kind in {"object", "array"} else value


def decode(row: Mapping[str, Any], fields: Mapping[str, str]) -> dict[str, Any]:
    return {
        field: json_value(row[column(field, kind)]) if kind in {"object", "array"} else iso_time(row[column(field, kind)]) if kind == "instant" else row[column(field, kind)]
        for field, kind in fields.items() if row[column(field, kind)] is not None
    }


COLUMN_TYPES = {"integer": "BIGINT", "boolean": "BOOLEAN", "instant": "TIMESTAMPTZ", "object": "JSONB", "array": "JSONB"}


def declarations(fields: Mapping[str, str]) -> str:
    return ", ".join(f"{column(field, kind)} {COLUMN_TYPES.get(kind, 'TEXT')}" for field, kind in fields.items())


def _upsert_statement(table: str, keys, values) -> str:
    """Internal SQL identifiers come exclusively from the closed table contracts."""
    names = [*keys, *values]
    conflict = ", ".join(keys)
    if values:
        action = "UPDATE SET " + ", ".join(f"{k}=excluded.{k}" for k in values)
        action += " WHERE (" + ", ".join(f"{table}.{k}" for k in values) + ") IS DISTINCT FROM (" + ", ".join(f"excluded.{k}" for k in values) + ")"
    else:
        action = "NOTHING"
    return f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('%s' for _ in names)}) ON CONFLICT ({conflict}) DO {action}"


def upsert(connection: Any, table: str, keys: Mapping[str, Any], values: Mapping[str, Any]) -> None:
    connection.execute(_upsert_statement(table, keys, values), [*keys.values(), *values.values()])


def upsert_many(connection: Any, table: str, keys: tuple[str, ...], rows: list[Mapping[str, Any]]) -> None:
    """Pipeline one typed relation while retaining unchanged-row and transaction semantics."""
    if not rows:
        return
    values = tuple(name for name in rows[0] if name not in keys)
    names = (*keys, *values)
    if any(set(row) != set(names) for row in rows):
        raise ValueError(f"Inconsistent {table} batch columns")
    connection.executemany(_upsert_statement(table, keys, values), [
        [row[name] for name in names] for row in rows
    ])


def typed_values(item: Mapping[str, Any], fields: Mapping[str, str]) -> dict[str, Any]:
    return {column(field, kind): encode(item.get(field), kind) for field, kind in fields.items()}


def validate_fields(item: Mapping[str, Any], allowed: set[str], domain: str) -> None:
    unknown = set(item) - allowed
    if unknown:
        raise ValueError(f"Unknown {domain} fields: {', '.join(sorted(unknown))}")


COLUMNS = {
    "lineage_fragments": ("fragment_id", "lineage_id", *FRAGMENT_FIELDS),
    "lineage_fragment_attachments": ("fragment_id", "attachment_id", "position", *ATTACHMENT_FIELDS),
}


def create_schema(connection: Any) -> None:
    connection.execute(f"""CREATE TABLE lineage_fragments (
        fragment_id TEXT PRIMARY KEY, lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id),
        {declarations(FRAGMENT_FIELDS)})""")
    connection.execute("CREATE INDEX lineage_fragments_lineage_idx ON lineage_fragments(lineage_id)")
    connection.execute(f"""CREATE TABLE lineage_fragment_attachments (
        fragment_id TEXT NOT NULL REFERENCES lineage_fragments(fragment_id) ON DELETE CASCADE,
        attachment_id TEXT NOT NULL, position INTEGER NOT NULL,
        {declarations(ATTACHMENT_FIELDS)}, PRIMARY KEY(fragment_id,attachment_id))""")


def put(connection: Any, lineage_id: str, value: Mapping[str, Any]) -> None:
    for collection, table, id_column, fields, derived in (
        ("fragments", "lineage_fragments", "fragment_id", FRAGMENT_FIELDS, {"attachments"}),
    ):
        items = value.get(collection) or []
        ids = [item["id"] for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate {collection} ID")
        connection.execute(f"DELETE FROM {table} WHERE lineage_id=%s AND NOT ({id_column}=ANY(%s))", (lineage_id, ids))
        for item in items:
            validate_fields(item, {"id", *fields, *derived}, collection)
            upsert(connection, table, {id_column: item["id"]}, {"lineage_id": lineage_id, **typed_values(item, fields)})
            if collection == "fragments":
                attachments = item.get("attachments") or []
                attachment_ids = [a["id"] for a in attachments]
                if len(attachment_ids) != len(set(attachment_ids)):
                    raise ValueError("Duplicate fragment attachment")
                connection.execute("DELETE FROM lineage_fragment_attachments WHERE fragment_id=%s AND NOT (attachment_id=ANY(%s))", (item["id"], attachment_ids))
                for position, attachment in enumerate(attachments):
                    validate_fields(attachment, {"id", *ATTACHMENT_FIELDS}, "fragment attachment")
                    upsert(connection, "lineage_fragment_attachments", {"fragment_id": item["id"], "attachment_id": attachment["id"]}, {"position": position, **typed_values(attachment, ATTACHMENT_FIELDS)})


def read_many(connection: Any, lineage_ids: list[str]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    result = {lid: {"fragments": []} for lid in lineage_ids}
    if not lineage_ids:
        return result
    fragments = {}
    for row in connection.execute("SELECT * FROM lineage_fragments WHERE lineage_id=ANY(%s) ORDER BY created_at,fragment_id", (lineage_ids,)).fetchall():
        fragment = {"id": row["fragment_id"], **decode(row, FRAGMENT_FIELDS), "attachments": []}
        fragments[row["fragment_id"]] = fragment
        result[row["lineage_id"]]["fragments"].append(fragment)
    if fragments:
        for row in connection.execute("SELECT * FROM lineage_fragment_attachments WHERE fragment_id=ANY(%s) ORDER BY fragment_id,position,attachment_id", (list(fragments),)).fetchall():
            fragments[row["fragment_id"]]["attachments"].append({"id": row["attachment_id"], **decode(row, ATTACHMENT_FIELDS)})
    return result
