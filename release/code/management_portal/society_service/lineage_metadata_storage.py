"""Typed Lineage metadata and Project entry points, independent of graph bodies."""
from __future__ import annotations

from typing import Any

from .definition_collections import declarations, upsert, typed_values, decode

FIELDS = {
    "lineage_membership": "text", "project_id": "text", "title": "text", "description": "text",
    "leader_resident_id": "text",
    "revision": "integer", "layout_revision": "integer", "created_at": "instant",
    "updated_at": "instant", "graph_updated_at": "instant", "layout_updated_at": "instant",
    "created_by": "text", "deleted_at": "instant", "deleted_by": "text",
}
AUTOMATION_FIELDS = {"run_as_principal": "object"}
COLUMNS = {"lineages": ("lineage_id", *FIELDS, "runtime_revision", "runtime_updated_at", "run_as_principal_json"), "project_lineage_entries": ("project_id", "lineage_id")}


def create_schema(connection: Any) -> None:
    connection.execute(f"CREATE TABLE lineages (lineage_id TEXT PRIMARY KEY, {declarations(FIELDS)}, runtime_revision BIGINT, runtime_updated_at TIMESTAMPTZ, {declarations(AUTOMATION_FIELDS)})")
    create_entries(connection)


def create_entries(connection: Any) -> None:
    connection.execute("""CREATE TABLE project_lineage_entries (
        project_id TEXT PRIMARY KEY, lineage_id TEXT NOT NULL UNIQUE REFERENCES lineages(lineage_id))""")


def put(connection: Any, lineage_id: str, value: dict) -> None:
    from .lineage_definition_versions import check_definition_version

    check_definition_version(connection, lineage_id)
    existing = connection.execute("SELECT deleted_at FROM lineages WHERE lineage_id=%s", (lineage_id,)).fetchone()
    if existing and existing["deleted_at"]:
        raise ValueError("A deleted Lineage cannot be rewritten")
    upsert(connection, "lineages", {"lineage_id": lineage_id}, typed_values(value, {**FIELDS, **AUTOMATION_FIELDS}))
    level = (value.get("principal_hierarchy") or {}).get("level")
    if level == "user" and value.get("lineage_membership") != "personal":
        raise ValueError("User scope requires a personal Lineage")
    if level == "project":
        project_id = value["project_id"]
        owner = connection.execute("SELECT lineage_id FROM project_lineage_entries WHERE project_id=%s", (project_id,)).fetchone()
        if owner and owner["lineage_id"] != lineage_id:
            raise ValueError("A Project can have only one entry Lineage")
        upsert(connection, "project_lineage_entries", {"project_id": project_id}, {"lineage_id": lineage_id})
    else:
        connection.execute("DELETE FROM project_lineage_entries WHERE lineage_id=%s", (lineage_id,))


def read_many(connection: Any, lineage_ids: list[str] | None = None, *, include_deleted: bool = False) -> list[dict]:
    from .lineage_definition_contract import LINEAGE_DEFINITION_SCHEMA
    condition = "TRUE" if include_deleted else "l.deleted_at IS NULL"
    params = []
    if lineage_ids is not None:
        condition += " AND l.lineage_id=ANY(%s)"
        params.append(lineage_ids)
    rows = connection.execute(f"SELECT l.*,p.project_id AS entry_project_id FROM lineages l LEFT JOIN project_lineage_entries p USING(lineage_id) WHERE {condition} ORDER BY l.lineage_id", params).fetchall()
    result = []
    for row in rows:
        value = {"lineage_id": row["lineage_id"], "schema": LINEAGE_DEFINITION_SCHEMA, "schema_version": 1, **decode(row, {**FIELDS, **AUTOMATION_FIELDS})}
        for field in ("created_at", "updated_at", "graph_updated_at", "layout_updated_at"):
            value.setdefault(field, "")
        level = "project" if row["entry_project_id"] is not None else "user" if row["lineage_membership"] == "personal" else "lineage"
        value["principal_hierarchy"] = {"level": level, "scope_id": row["lineage_id"] if level == "user" else row["project_id"]}
        result.append(value)
    return result
