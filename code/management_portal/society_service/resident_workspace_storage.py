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


def create_workspace_deletion_binding(connection):
    """Society owns the same-transaction response to Workspace logical deletion.

    Like its foreign key, this binding applies to every Workspace owner without
    making ResourceService import Society. Generated files use the existing
    Resident lifecycle outbox and are retried after the registration commits.
    """
    from management_common.portal_database import _database_schema
    lineage = '"' + _database_schema('lineage') + '"'
    workspace = '"' + _database_schema('workspace') + '".workspaces'
    connection.execute(f'''CREATE OR REPLACE FUNCTION {lineage}.workspace_deleted_bindings()
        RETURNS trigger LANGUAGE plpgsql AS $body$
        DECLARE affected TEXT; step JSONB;
        BEGIN
            FOR affected IN
                WITH removed AS (
                    DELETE FROM {lineage}.resident_workspace_bindings
                    WHERE workspace_id=NEW.workspace_id RETURNING resident_id
                )
                SELECT r.resident_id FROM {lineage}.residents r
                WHERE r.deleted_at IS NULL AND
                    (r.resident_id IN (SELECT resident_id FROM removed) OR r.owned_workspace_id=NEW.workspace_id)
            LOOP
                step := jsonb_build_object('kind','workspace_document','resident_id',affected);
                INSERT INTO {lineage}.resident_lifecycle_operations(operation_id,resident_id,payload_json)
                VALUES ('workspace-document:' || affected, affected,
                        jsonb_build_object('steps',jsonb_build_array(step),'next_step',0))
                ON CONFLICT(resident_id) DO UPDATE SET payload_json=jsonb_set(
                    resident_lifecycle_operations.payload_json::jsonb, '{{steps}}',
                    (resident_lifecycle_operations.payload_json::jsonb->'steps') || jsonb_build_array(step));
            END LOOP;
            RETURN NEW;
        END $body$''')
    connection.execute(f'DROP TRIGGER IF EXISTS workspace_deleted_bindings ON {workspace}')
    connection.execute(f'''CREATE TRIGGER workspace_deleted_bindings AFTER UPDATE OF deleted_at ON {workspace}
        FOR EACH ROW WHEN (OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL)
        EXECUTE FUNCTION {lineage}.workspace_deleted_bindings()''')


def normalize_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    return _normalize_binding(binding, canonical_path)


def _normalize_binding(binding, canonicalize):
    unknown = set(binding) - set(BINDING_FIELDS) - {"path"}
    if unknown:
        raise ValueError("Unknown Workspace binding fields: " + ", ".join(sorted(unknown)))
    value = {field: binding.get(field, "") for field in BINDING_FIELDS}
    identity_contract.validate_id(value["workspace_id"], "workspace")
    if not isinstance(value["purpose"], str):
        raise ValueError("Workspace purpose must be text")
    if "path" in binding and binding["path"] != canonicalize(binding["path"]):
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
    # A projection observes paths once per batch. No filesystem observation is
    # retained across calls or reused by a later write/registration decision.
    canonical_paths = {}

    def canonicalize(path):
        if path not in canonical_paths:
            canonical_paths[path] = canonical_path(path)
        return canonical_paths[path]

    result = []
    for record in records:
        value = copy.deepcopy(dict(record))
        rid = value["resident_id"]
        if value.get("deleted_at"):
            for field in DERIVED_FIELDS:
                value.pop(field, None)
            value["workspace_bindings"] = []
        else:
            folder = lineage_folders.get(value["lineage_id"])
            own_id = value["owned_workspace_id"]
            value["owned_workspace"] = {"workspace_id": own_id, "path": workspace_paths[own_id]}
            if not workspace_paths[own_id]:
                value["owned_workspace"]["status"] = "deleted"
            value["workspace_bindings"] = [
                {**_normalize_binding(binding, canonicalize),
                 "path": workspace_paths[binding["workspace_id"]]}
                for binding in bindings_by_resident.get(rid, [])]
            if folder is not None:
                value["workspace_path"] = canonicalize(folder)
                from pathlib import Path
                value["workspace_root"] = str(Path(folder).parent)
            _apply_binding_projection(value, canonicalize)
        result.append(value)
    return result


def apply_binding_projection(record):
    _apply_binding_projection(record, canonical_path)


def _apply_binding_projection(record, canonicalize):
    own_id = record["owned_workspace_id"]
    identity_contract.validate_id(own_id, "workspace")
    own = record["owned_workspace"]
    if own["workspace_id"] != own_id:
        raise ValueError("Resident owned Workspace identity mismatch")
    # Bindings carry no order of their own: a saved record lists them as
    # storage reads them back, so every projection of it is the same.
    bindings = sorted(record["workspace_bindings"], key=lambda binding: binding["workspace_id"])
    values = [_normalize_binding(binding, canonicalize) for binding in bindings]
    if len({value["workspace_id"] for value in values}) != len(values):
        raise ValueError("Duplicate Workspace binding")
    if own_id in {value["workspace_id"] for value in values}:
        raise ValueError("Owned Workspace cannot also be an additional binding")
    record["workspace_bindings"] = bindings
    record["default_cwd"] = '' if own.get('status') == 'deleted' else canonicalize(own["path"])
    record["workspace_paths"] = [binding["path"] for binding in bindings]
