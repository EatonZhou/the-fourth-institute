"""Resident usage relationships. WorkspaceStore owns paths and lifecycle."""
from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping
from management_portal.resources.workspace_store import canonical_path
from . import identity_contract

BINDING_FIELDS = ("workspace_id", "purpose")
DERIVED_FIELDS = frozenset({"workspace_path", "workspace_root", "default_cwd",
    "workspace_paths", "owned_workspace"})
WORKSPACE_SCHEMA_SQL = (
    """CREATE TABLE resident_workspace_bindings (
        resident_id TEXT NOT NULL REFERENCES residents(resident_id),
        workspace_id TEXT NOT NULL CHECK (workspace_id ~ '^workspace_[0-9a-f]{12}$'),
        purpose TEXT NOT NULL,
        PRIMARY KEY (resident_id, workspace_id)
    )""",
    "CREATE INDEX resident_workspace_id_idx ON resident_workspace_bindings(workspace_id)",
    "CREATE UNIQUE INDEX residents_owned_workspace_idx ON residents(owned_workspace_id)",
)


def normalize_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(binding) - set(BINDING_FIELDS) - {"path"}
    if unknown:
        raise ValueError("Unknown Workspace binding fields: " + ", ".join(sorted(unknown)))
    value = {field: binding.get(field, "") for field in BINDING_FIELDS}
    identity_contract.validate_id(value["workspace_id"], "workspace")
    if not isinstance(value["purpose"], str):
        raise ValueError("Workspace purpose must be text")
    if "path" in binding and binding["path"] != canonical_path(binding["path"]):
        raise ValueError("Workspace binding path must be canonical")
    return value


def normalize_bindings(bindings):
    values = [normalize_binding(binding) for binding in bindings]
    if len({value["workspace_id"] for value in values}) != len(values):
        raise ValueError("Duplicate Workspace binding")
    return sorted(values, key=lambda value: value["workspace_id"])


def replace_bindings(connection, resident_id, bindings):
    identity_contract.validate_id(resident_id, "resident")
    values = normalize_bindings(bindings)
    old = bindings_for_residents(connection, [resident_id])[resident_id]
    before = {item["workspace_id"]: item for item in old}
    after = {item["workspace_id"]: item for item in values}
    removed = list(before.keys() - after.keys())
    if removed:
        connection.execute("DELETE FROM resident_workspace_bindings WHERE resident_id=%s AND workspace_id=ANY(%s)",
                           (resident_id, removed))
    changed = [item for wid, item in after.items() if before.get(wid) != item]
    if changed:
        connection.executemany("INSERT INTO resident_workspace_bindings(resident_id,workspace_id,purpose) VALUES (%s,%s,%s) "
                               "ON CONFLICT(resident_id,workspace_id) DO UPDATE SET purpose=excluded.purpose",
                               [(resident_id, item["workspace_id"], item["purpose"]) for item in changed])


def bindings_for_residents(connection, resident_ids):
    ids = list(dict.fromkeys(resident_ids))
    result = {rid: [] for rid in ids}
    if ids:
        for row in connection.execute("SELECT resident_id,workspace_id,purpose FROM resident_workspace_bindings "
                                      "WHERE resident_id=ANY(%s) ORDER BY resident_id,workspace_id", (ids,)).fetchall():
            result[row["resident_id"]].append({field: row[field] for field in BINDING_FIELDS})
    return result


def project_records(records, bindings_by_resident, workspace_paths, lineage_folders):
    result = []
    for record in records:
        value = copy.deepcopy(dict(record))
        rid = value["resident_id"]
        if value.get("deleted_at"):
            for field in DERIVED_FIELDS:
                value.pop(field, None)
            value["workspace_bindings"] = []
        else:
            folder = canonical_path(lineage_folders[value["lineage_id"]])
            own_id = value["owned_workspace_id"]
            value["owned_workspace"] = {"workspace_id": own_id, "path": workspace_paths[own_id]}
            value["workspace_bindings"] = [{**binding, "path": workspace_paths[binding["workspace_id"]]}
                for binding in normalize_bindings(bindings_by_resident.get(rid, []))]
            value["workspace_path"] = folder
            from pathlib import Path
            value["workspace_root"] = str(Path(folder).parent)
            apply_binding_projection(value)
            if value["owned_workspace"]["path"] != canonical_path(Path(folder) / "residents" / rid / "workspace"):
                value["default_cwd"] = ""
        result.append(value)
    return result


def apply_binding_projection(record):
    own_id = record["owned_workspace_id"]
    identity_contract.validate_id(own_id, "workspace")
    own = record["owned_workspace"]
    if own["workspace_id"] != own_id:
        raise ValueError("Resident owned Workspace identity mismatch")
    # Bindings carry no order of their own: a saved record lists them as
    # storage reads them back, so every projection of it is the same.
    bindings = sorted(record["workspace_bindings"], key=lambda binding: binding["workspace_id"])
    values = normalize_bindings(bindings)
    if own_id in {value["workspace_id"] for value in values}:
        raise ValueError("Owned Workspace cannot also be an additional binding")
    record["workspace_bindings"] = bindings
    record["default_cwd"] = canonical_path(own["path"])
    record["workspace_paths"] = [binding["path"] for binding in bindings]
