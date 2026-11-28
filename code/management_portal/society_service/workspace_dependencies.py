"""Workspace identity dependencies of unpublished Society operations."""
from pathlib import Path

from management_common.portal_database import _database_schema
from management_common.resource_lifecycle import current_lifecycle
from management_portal.resources.workspace_store import get_workspace_store

from .identity_contract import validate_id


_RESOURCE_UNKNOWN_REASONS = frozenset({
    "schema_unavailable", "terminal_state_unavailable", "legacy_schema_missing_declaration",
    "missing_declaration", "invalid_declaration",
})


def pending_workspace_dependency(workspace_id, *, connection, diagnostics=None):
    """Classify under the caller's directory lock, with optional safe reason pairs.

    The authorized caller may pass an empty list. Only an unknown result adds
    source and reason fields, with no row data or counts.
    """
    from management_portal.resources.creation import pending_creation_workspace_dependency

    validate_id(workspace_id, "workspace")
    resource_notes = []
    resource_options = {"diagnostics": resource_notes} if diagnostics is not None else {}
    resource = pending_creation_workspace_dependency(workspace_id, connection=connection, **resource_options)
    if resource == "dependent":
        return resource
    resident_reasons, handoff_reasons = set(), set()
    resident = _pending_resident_dependency(workspace_id, connection=connection, unknown_reasons=resident_reasons)
    handoff = _pending_handoff_dependency(workspace_id, connection=connection, unknown_reasons=handoff_reasons)
    if "dependent" in (resident, handoff):
        return "dependent"
    if "unknown" not in (resource, resident, handoff):
        return "independent"
    if diagnostics is not None:
        resource_reasons = {
            reason for note in resource_notes
            if isinstance(note, dict)
            and isinstance(reason := note.get("reason"), str)
            and reason in _RESOURCE_UNKNOWN_REASONS
        }
        for source, status, reasons in (
            ("resource_creation", resource, resource_reasons),
            ("resident_creation_intent", resident, resident_reasons),
            ("workspace_handoff", handoff, handoff_reasons),
        ):
            if status == "unknown":
                diagnostics.extend(
                    {"source": source, "reason": reason}
                    for reason in sorted(reasons or {"evidence_unavailable"})
                )
    return "unknown"


def _matches_creation(row, *, key, resource_id, actor, scope_id, payload):
    return row and (
        row["operation_id"], row["resource_id"], row["actor"], row["scope_id"], row["payload"]
    ) == (key, resource_id, actor, scope_id, payload)


def _pending_resident_dependency(workspace_id, *, connection, unknown_reasons):
    from management_portal.resources.creation import (
        CreationDependencyUnknown, creation_dependency_record, creation_operation_key,
    )
    from .creation_intents import pending_resident_workspace_intents

    for row in pending_resident_workspace_intents(connection):
        record = row["record"]
        if not isinstance(record, dict) or any(
            not isinstance(record.get(field), str) or not record[field] for field in ("resident_id", "created_by")
        ):
            unknown_reasons.add("intent_snapshot_incomplete")
            continue
        actor, resource_id = record["created_by"], record["resident_id"]
        key = creation_operation_key(actor, row["operation_id"])
        try:
            creation = creation_dependency_record(key, connection=connection)
            if creation:
                if not _matches_creation(creation, key=key, resource_id=resource_id, actor=actor,
                                         scope_id=row["scope_id"], payload=row["request"]):
                    unknown_reasons.add("creation_association_mismatch")
                # The Resource reader owns declarations once a creation exists,
                # including the unknown status of any matching legacy NULL row.
                continue
        except CreationDependencyUnknown:
            unknown_reasons.add("creation_association_unavailable")
            continue
        try:
            ids = binding_workspace_ids(record["workspace_bindings"])
            for identity in ids:
                validate_id(identity, "workspace")
            depends = workspace_id in ids or (
                row["operation_id"].startswith("resident:")
                and _possible_default_workspace(workspace_id, record, connection=connection)
            )
        except (KeyError, TypeError, ValueError):
            unknown_reasons.add("workspace_binding_evidence_invalid")
            continue
        if depends:
            return "dependent"
    return "unknown" if unknown_reasons else "independent"


def _possible_default_workspace(workspace_id, record, *, connection):
    from management_common.directory_paths import cleanup_path
    from management_portal.resources.workspace_store import canonical_path, stored_location

    table = f'"{_database_schema("workspace")}".workspaces'
    row = connection.execute(
        f"SELECT owner_id,path FROM {table} WHERE workspace_id=%s AND deleted_at IS NULL",
        (workspace_id,),
    ).fetchone()
    if not row or row["owner_id"] == workspace_id:
        return False
    if (row["owner_id"] == record.get("lineage_id") and record.get("workspace_path")
            and row["path"] == stored_location(canonical_path(Path(record["workspace_path"]) / "workspace"))):
        return True
    # Leadership is chosen in the graph commit. Until then the Project's
    # owned default at a workspace path is a possible dependency.
    return row["owner_id"] == record.get("project_id") and cleanup_path(row["path"]).name == "workspace"


def _pending_handoff_dependency(workspace_id, *, connection, unknown_reasons):
    schema = _database_schema("lineage")
    work = f'"{schema}".workspace_handoff_work'
    items = f'"{schema}".lineage_handoff_items'
    edges = f'"{schema}".lineage_edges'
    tables = connection.execute(
        "SELECT to_regclass(%s), to_regclass(%s), to_regclass(%s)", (work, edges, items),
    ).fetchone()
    if tables[0] is None:
        return "independent"
    if tables[1] is None or tables[2] is None:
        unknown_reasons.add("handoff_store_incomplete")
        return "unknown"
    rows = connection.execute(
        f"SELECT i.source_workspace_id,i.target_workspace_id FROM {work} w "
        f"JOIN {edges} e ON e.edge_id=w.edge_id AND e.lineage_id=w.lineage_id "
        f"LEFT JOIN {items} i ON i.edge_id=w.edge_id "
        "WHERE w.progress_json::jsonb->>'status'='preparing' "
        "AND e.edge_type='handoff' AND e.deleted_at IS NULL",
    )
    for row in rows:
        sources = (row["source_workspace_id"], row["target_workspace_id"])
        if workspace_id in sources:
            return "dependent"
        try:
            validate_id(row["source_workspace_id"], "workspace")
        except (ValueError, TypeError):
            unknown_reasons.add("handoff_source_invalid")
    return "unknown" if unknown_reasons else "independent"


def recoverable_workspace_dependencies(resource_id, *, actor, operation_id, payload, workspace_ids):
    """A uniquely matched unfinished old allocation may finish under its original NULL."""
    from management_portal.resources.creation import (
        CreationDependencyUnknown, creation_dependency_record, creation_operation_key,
    )

    ids = tuple(sorted(set(workspace_ids)))
    key = creation_operation_key(actor, operation_id)
    workspaces = get_workspace_store()
    with workspaces.database.transaction() as connection:
        workspaces.cleanup.lock(connection)
        require_active_workspaces(ids, connection=connection)
        try:
            row = creation_dependency_record(key, connection=connection)
        except CreationDependencyUnknown:
            # The typed prepare path owns schema creation and rejects any old
            # NULL or conflicting row. Never choose legacy from missing proof.
            return ids
        if (row and not row["completed"] and not row["aborted"]
                and row["workspace_dependencies"] is None
                and _matches_creation(row, key=key, resource_id=resource_id, actor=actor,
                                      scope_id=current_lifecycle(), payload=payload)):
            return None
    return ids


def resident_creation(resource_id, *, actor, intent, bindings):
    from management_portal.resources.creation import Creation

    ids = binding_workspace_ids(bindings)
    creation = Creation(resource_id, actor=actor, operation_id=intent.key, payload=intent.request,
                        workspace_dependencies=ids)
    # Construction enlists the Resource schema upgrade before reading old rows.
    # The Resource writer still compares the original declaration under its lease.
    if recoverable_workspace_dependencies(
        resource_id, actor=actor, operation_id=intent.key, payload=intent.request, workspace_ids=ids,
    ) is None:
        creation.workspace_dependencies = None
    return creation


def binding_workspace_ids(bindings):
    return {binding["workspace_id"] for binding in bindings}


def require_active_workspaces(workspace_ids, *, connection):
    """Take the retirement lock before checking the rows a binding can publish."""
    identities = sorted(set(workspace_ids))
    if not identities:
        return
    if not connection.in_transaction:
        raise RuntimeError("Workspace dependency validation requires the owner transaction")
    workspaces = get_workspace_store()
    workspaces.cleanup.lock(connection)
    rows = connection.execute(
        f"SELECT workspace_id FROM {workspaces.table} "
        "WHERE workspace_id=ANY(%s) AND deleted_at IS NULL "
        "ORDER BY workspace_id FOR KEY SHARE",
        (identities,),
    ).fetchall()
    if len(rows) != len(identities):
        raise ValueError("A required Workspace is no longer active")


def validate_creation_bindings(creation, bindings, *, connection, legacy=False):
    actual = binding_workspace_ids(bindings)
    if legacy:
        from management_portal.resources.creation import CreationDependencyUnknown, creation_dependency_record

        get_workspace_store().cleanup.lock(connection)
        row = creation_dependency_record(creation.key, connection=connection)
        if (not _matches_creation(row, key=creation.key, resource_id=creation.resource_id,
                                  actor=creation.actor, scope_id=current_lifecycle(), payload=creation.payload)
                or row["completed"] or row["aborted"] or row["workspace_dependencies"] is not None):
            raise CreationDependencyUnknown("Legacy Workspace dependency proof changed before commit")
    else:
        creation.assert_workspace_dependencies(actual, connection=connection)
    require_active_workspaces(actual, connection=connection)
