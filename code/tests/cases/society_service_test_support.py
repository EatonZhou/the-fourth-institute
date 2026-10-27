from __future__ import annotations

import copy
import json
import hashlib
from pathlib import Path
from typing import Any, Mapping

from management_portal import portal_access_control
from management_portal.society_service import (
    effect_failure_notifications,
    lineage_domain,
    resident_catalog,
    resident_hatg_credentials,
    lineage_state_domains,
    resource_client,
    resident_access,
    resident_management_domain,
    resident_protocol,
)


NODE_MESSAGE_SENDER = None
LATEST_COMPLETED_MESSAGE_RESOLVER = None
RESIDENT_MESSAGE_EVIDENCE_RESOLVER = None
RESIDENT_INPUT_STATE_RESOLVER = None
RESIDENT_ACTIVITY_RESOLVER = None
NODE_ACTIVE_JOB_RESOLVER = None
LINEAGE_SNAPSHOT_RESOLVER = None
LINEAGE_NODE_RENAME_RESOLVER = None
LINEAGE_BRANCH_FORK_CREATOR = None
NODE_WORKSPACE_FOLDER_RESOLVER = None
NODE_REFERENCE_WORKSPACE_MOUNTER = None
RESIDENT_TARGET_RESOLVER = None


class _IsolatedPortalAccess:
    """Project authorization boundary for tests using synthetic resources."""

    @staticmethod
    def user(actor: object) -> dict:
        return {"username": str(actor), "configured": True, "status": "active"}

    @staticmethod
    def is_active(_actor: object) -> bool:
        return True

    @staticmethod
    def is_configured(_actor: object) -> bool:
        return True

    # Service principals are fixed names, the same in every installation.
    is_service_actor = staticmethod(portal_access_control.PortalAccessStore.is_service_actor)

    @staticmethod
    def is_portal_admin(_actor: object) -> bool:
        return True

    @staticmethod
    def has_portal_authority(*_args, **_kwargs) -> bool:
        return True


def _install_isolated_portal_access(monkeypatch) -> None:
    from management_portal.society_service import lineage_automation_identity, lineage_instruction_context
    access = _IsolatedPortalAccess()
    canonical_factory = portal_access_control.get_portal_access_store
    canonical_replacement = (
        (lambda: access)
        if getattr(canonical_factory, "__module__", "") == portal_access_control.__name__
        else canonical_factory
    )
    monkeypatch.setattr(
        portal_access_control,
        "get_portal_access_store",
        canonical_replacement,
    )
    for module in (
        lineage_automation_identity,
        lineage_instruction_context,
        effect_failure_notifications,
        lineage_domain,
        resident_access,
        resident_management_domain,
    ):
        current_factory = module.get_portal_access_store
        if getattr(current_factory, "__module__", "") != portal_access_control.__name__:
            continue
        monkeypatch.setattr(module, "get_portal_access_store", canonical_replacement)


def write_lineage_facts(aggregate: Mapping[str, Any]) -> None:
    """Write one aggregate through the strict definition/runtime split."""

    lineage_id = str(aggregate.get("lineage_id") or "")
    definition = lineage_state_domains.definition_projection(
        dict(aggregate)
    )
    definition["schema"] = lineage_domain.LINEAGE_FOLDER_SCHEMA
    lineage_domain._lineage_catalog_store().upsert(definition)
    lineage_domain._lineage_catalog_store().put_runtime(
        lineage_id,
        lineage_state_domains.persisted_runtime_state(aggregate),
    )


def create_lineage_with_resident_members(
    payload: Mapping[str, Any], actor: str,
) -> dict[str, Any]:
    """Create isolated test identities before authoring their graph view."""
    nodes = copy.deepcopy(list(payload.get("nodes") or []))
    created = lineage_domain.create_lineage({
        **copy.deepcopy(dict(payload)),
        "nodes": [],
        "edges": [],
        "program": {"components": [], "wires": []},
    }, actor)
    publish_registered_lineage_members({**created, "nodes": nodes}, actor)
    current = lineage_domain.get_lineage(created["lineage_id"], actor)
    return lineage_domain.update_lineage(created["lineage_id"], {
        "expected_revision": current["revision"],
        "lineage": {**current, **copy.deepcopy(dict(payload)), "nodes": nodes},
    }, actor)


def publish_registered_lineage_members(
    lineage: Mapping[str, Any], actor: str = "sample_admin",
) -> None:
    """Publish missing graph identities without rewriting nodes or existing Residents."""
    from management_portal.society_service import identity_contract
    from management_portal.society_service.lineage_storage import canonical_lineage_folder
    from management_portal.society_service.principal_hierarchy import classify_existing
    from management_portal.society_service.resident_catalog import ensure_resident_agent

    store = resident_catalog.get_catalog().state_store
    nodes = [node for node in lineage.get("nodes") or []
             if str(node.get("id") or "").startswith("resident_")]
    if not nodes:
        return
    # Commit one event time: empty timestamps make descriptor normalization read
    # the clock independently of its canonical guard and race across seconds.
    created_at = lineage.get("created_at") or lineage_domain._now_iso()
    updated_at = lineage.get("updated_at") or created_at
    with store.database.read() as connection:
        existing = {row["resident_id"] for row in connection.execute(
            "SELECT resident_id FROM residents WHERE resident_id=ANY(%s)",
            ([node["id"] for node in nodes],),
        ).fetchall()}
    lineage_id = str(lineage["lineage_id"])
    for node in nodes:
        resident_id = str(node["id"])
        if resident_id in existing:
            continue
        owned_path = canonical_lineage_folder(lineage_id) / "residents" / resident_id / "workspace"
        owned_id = identity_contract.new_id("workspace")
        record = {
            "schema": "tfi.resident.v6",
            "folder_schema": "lineage-folder-v3",
            "resident_id": resident_id,
            "lineage_id": lineage_id,
            "workspace_path": str(canonical_lineage_folder(lineage_id)),
            "backend": "codex",
            "title": str(node.get("title") or resident_id),
            "created_by": actor,
            "created_at": created_at,
            "updated_at": updated_at,
            "registration_state": "ready",
            "owned_workspace_id": owned_id,
            "owned_workspace": {"workspace_id": owned_id, "path": str(owned_path)},
            "workspace_bindings": [],
        }
        record["principal_hierarchy"] = classify_existing(
            record, lineage, evidence={"scope_ids": [lineage_id]},
        )
        ensure_resident_agent(record, actor=actor)
        store.put_resident_record(resident_id, record)
        resource_client.sync_resident(record, updated_by=actor)
        existing.add(resident_id)


def install_registered_lineage_members(monkeypatch) -> None:
    original_create = lineage_domain.create_lineage

    def create(payload, actor):
        lineage = original_create(payload, actor)
        publish_registered_lineage_members(lineage, actor)
        return lineage

    monkeypatch.setattr(lineage_domain, "create_lineage", create)


def install_resource_boundary(monkeypatch, *, synthetic_residents=True) -> dict[str, dict[str, Any]]:
    """Install a domain projection port for isolated Lineage tests."""

    _install_isolated_portal_access(monkeypatch)
    # This suite supplies synthetic Resident references through its resolver.
    # Persist their identities in the same transaction as their first graph.
    from management_portal.society_service.lineage_catalog import LineageCatalog
    from tests.cases.test_lineage_catalogs import _record
    upsert = LineageCatalog.upsert
    def upsert_with_synthetic_residents(catalog, definition):
        with catalog.state_store.database.transaction():
            result = upsert(catalog, definition)
            from management_portal.resources.workspace_store import get_workspace_store
            with catalog.state_store.database.transaction() as connection:
                for node in definition.get("nodes") or []:
                    rid = node["id"]
                    if connection.execute("SELECT 1 FROM residents WHERE resident_id=%s", (rid,)).fetchone() is None:
                        owned = _record(rid, lineage_id=definition["lineage_id"])["owned_workspace"]
                        workspaces = get_workspace_store()
                        # Real registration already prepared its own Workspace;
                        # it will insert the Resident before the outer commit.
                        if connection.execute(f"SELECT 1 FROM {workspaces.table} WHERE path=%s AND deleted_at IS NULL", (owned['path'],)).fetchone():
                            continue
                        wid = workspaces.ensure(owned["workspace_id"], owned["path"], connection=connection)
                        connection.execute("INSERT INTO residents(resident_id,title,backend,owned_workspace_id) VALUES(%s,%s,%s,%s)",
                                           (rid, node.get("title") or rid, node.get("backend") or "codex", wid))
            return result
    if synthetic_residents:
        monkeypatch.setattr(LineageCatalog, "upsert", upsert_with_synthetic_residents)
    records: dict[str, dict[str, Any]] = {}

    original_latest_completed = (
        resident_protocol.get_latest_completed_message_reference
    )
    original_message_evidence = resident_protocol.get_completed_message_evidence
    original_input_state = resident_protocol.get_input_reconciliation_state
    original_activity = resident_protocol.get_resident_activity_snapshot
    original_has_active_work = lineage_domain._node_has_active_execution

    def latest_completed(resident_id, actor):
        resolver = LATEST_COMPLETED_MESSAGE_RESOLVER
        if resolver is not None:
            return resolver(resident_id, actor)
        return original_latest_completed(resident_id, actor)

    def message_evidence(resident_id, actor, message_id):
        resolver = RESIDENT_MESSAGE_EVIDENCE_RESOLVER
        if resolver is not None:
            return resolver(resident_id, message_id, actor)
        return original_message_evidence(resident_id, actor, message_id)

    def has_active_work(resident_id, actor):
        resolver = NODE_ACTIVE_JOB_RESOLVER
        if resolver is not None:
            return bool(resolver({"id": resident_id}, actor))
        return original_has_active_work(
            {"id": resident_id},
            actor,
        )

    def input_state(resident_id, actor, message_id):
        resolver = RESIDENT_INPUT_STATE_RESOLVER
        if resolver is not None:
            return resolver(
                {"id": resident_id},
                message_id,
                actor,
            )
        if (
            RESIDENT_MESSAGE_EVIDENCE_RESOLVER is not None
            or NODE_ACTIVE_JOB_RESOLVER is not None
        ):
            first = message_evidence(resident_id, actor, message_id)
            if isinstance(first, dict) and first:
                return {
                    "status": "completed",
                    "message_id": message_id,
                    "evidence": first,
                }
            if has_active_work(resident_id, actor):
                return {
                    "status": "running",
                    "message_id": message_id,
                    "evidence": {},
                }
            second = message_evidence(resident_id, actor, message_id)
            if isinstance(second, dict) and second:
                return {
                    "status": "completed",
                    "message_id": message_id,
                    "evidence": second,
                }
            return {
                "status": "unknown",
                "message_id": message_id,
                "evidence": {},
            }
        return original_input_state(resident_id, actor, message_id)

    def activity(resident_ids=None):
        resolver = RESIDENT_ACTIVITY_RESOLVER
        if resolver is not None:
            return resolver(resident_ids)
        return original_activity(resident_ids)

    monkeypatch.setattr(
        resident_protocol,
        "get_latest_completed_message_reference",
        latest_completed,
    )
    monkeypatch.setattr(
        resident_protocol,
        "get_completed_message_evidence",
        message_evidence,
    )
    monkeypatch.setattr(
        resident_protocol,
        "get_input_reconciliation_state",
        input_state,
    )
    monkeypatch.setattr(
        resident_protocol,
        "get_resident_activity_snapshot",
        activity,
    )
    monkeypatch.setattr(
        lineage_domain,
        "_node_has_active_execution",
        lambda node, actor: has_active_work(
            str(node.get("id") or ""),
            actor,
        ),
    )

    original_rename = lineage_domain._rename_resident_lineage_node
    original_branch_fork = lineage_domain._create_derived_resident
    original_workspace_roots = lineage_domain._resident_workspace_roots
    original_workspace_mount = lineage_domain._mount_resident_reference_workspace

    monkeypatch.setattr(
        lineage_domain,
        "_rename_resident_lineage_node",
        lambda node, title, actor: (
            LINEAGE_NODE_RENAME_RESOLVER(copy.deepcopy(node), title, actor)
            if LINEAGE_NODE_RENAME_RESOLVER is not None
            else original_rename(node, title, actor)
        ),
    )

    def branch_fork(source, payload, actor, lineage_source):
        creator = LINEAGE_BRANCH_FORK_CREATOR
        if creator is not None:
            return creator(
                copy.deepcopy(source),
                copy.deepcopy(payload),
                actor,
                lineage_source,
            )
        return original_branch_fork(source, payload, actor, lineage_source)

    def workspace_roots(resident_id, actor):
        resolver = NODE_WORKSPACE_FOLDER_RESOLVER
        if resolver is not None:
            return resolver(resident_id, actor)
        return original_workspace_roots(resident_id, actor)

    def workspace_mount(resident_id, actor, workspace_path, **kwargs):
        resolver = NODE_REFERENCE_WORKSPACE_MOUNTER
        if resolver is not None:
            return resolver(resident_id, workspace_path, actor)
        return original_workspace_mount(resident_id, actor, workspace_path, **kwargs)

    monkeypatch.setattr(lineage_domain, "_create_derived_resident", branch_fork)
    monkeypatch.setattr(lineage_domain, "_resident_workspace_roots", workspace_roots)
    monkeypatch.setattr(
        lineage_domain,
        "_mount_resident_reference_workspace",
        workspace_mount,
    )

    def deliver_test_resident_input(payload, actor, *, delivery_context=None):
        del delivery_context
        sender = NODE_MESSAGE_SENDER
        if sender is None:
            raise RuntimeError("No test Resident input sink installed")
        resident_id = str(payload.get("resident_id") or "")
        message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
        parts = message.get("content_parts") if isinstance(message.get("content_parts"), list) else []
        text = "\n".join(
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, Mapping) and str(part.get("type") or "") == "text"
        ).strip()
        runtime_context = "\n\n".join(
            str((part.get("context") or {}).get("context_text") or "").strip()
            for part in parts
            if isinstance(part, Mapping)
            and str(part.get("type") or "") == "context_reference"
            and isinstance(part.get("context"), Mapping)
            and str((part.get("context") or {}).get("context_text") or "").strip()
        )
        if runtime_context:
            text = (
                f"<runtime-context>\n{runtime_context}\n</runtime-context>\n\n"
                f"{text}"
            )
        response = sender(
            {"id": resident_id},
            text,
            actor,
        )
        if not isinstance(response, dict):
            return response
        if isinstance(response.get("message"), Mapping):
            return response
        message_id = str(response.get("message_id") or "")
        return {
            **response,
            "message": {"message_id": message_id},
        }

    monkeypatch.setattr(
        resident_protocol,
        "deliver_resident_input",
        deliver_test_resident_input,
    )
    original_requested_project_id = lineage_domain._requested_lineage_project_id
    original_project_folder = lineage_domain._lineage_project_folder
    test_project_id = "project_test"

    def requested_project_id(payload, previous, actor):
        if str(payload.get("project_id") or "").strip() == test_project_id:
            return test_project_id
        project_id = original_requested_project_id(payload, previous, actor)
        if project_id or "project_id" in payload:
            return project_id
        return test_project_id

    def project_folder(lineage_id, project_id, actor):
        if project_id == test_project_id:
            return lineage_domain._lineages_root() / lineage_id
        return original_project_folder(lineage_id, project_id, actor)

    monkeypatch.setattr(
        lineage_domain,
        "_requested_lineage_project_id",
        requested_project_id,
    )
    monkeypatch.setattr(
        lineage_domain,
        "_lineage_project_folder",
        project_folder,
    )

    def sync_lineage(lineage: Mapping[str, Any], *, updated_by: object = ""):
        del updated_by
        lineage_id = str(lineage.get("lineage_id") or "")
        records[lineage_id] = {
            "status": "deleted" if lineage.get("deleted_at") else "active",
        }
        return {"resource_id": lineage_id}


    def sync_resident(
        record: Mapping[str, Any],
        *,
        updated_by: object = "",
    ):
        del updated_by
        resident_id = str(record.get("resident_id") or "")
        records[resident_id] = {
            "status": "deleted" if record.get("deleted_at") else "active",
        }
        return {"resource_id": resident_id}

    def sync_resident_resource_bundles(
        resident_records,
        *,
        updated_by: object = "society-service",
    ):
        count = 0
        for record in resident_records:
            sync_resident(record, updated_by=updated_by)
            count += 1
        return count

    def workspace_id_for_path(path, *, preferred_id: str = ""):
        from management_portal.resources.workspace_store import get_workspace_store
        suffix = hashlib.sha256(str(Path(path).resolve(strict=False)).encode()).hexdigest()[:12]
        return get_workspace_store().for_path(str(path), preferred_id=preferred_id,
                                              new_id=lambda: f"workspace_{suffix}")

    monkeypatch.setattr(resource_client, "sync_lineage", sync_lineage)
    monkeypatch.setattr(resource_client, "sync_resident", sync_resident)
    monkeypatch.setattr(
        resource_client,
        "sync_resident_resource_bundles",
        sync_resident_resource_bundles,
    )
    monkeypatch.setattr(resource_client, "workspace_id_for_path", workspace_id_for_path)
    for method_name in (
        "sync_lineage",
        "sync_resident",
        "sync_resident_resource_bundles",
    ):
        monkeypatch.delitem(lineage_domain.resource_port.__dict__, method_name, raising=False)
    monkeypatch.setattr(lineage_domain.resource_port, "_implementation", resource_client)
    monkeypatch.setattr(resource_client, "project_ids_for_path", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(resource_client, "project_roots", lambda **_kwargs: {})
    monkeypatch.setattr(
        resource_client,
        "project_root",
        lambda project_id, **kwargs: resource_client.project_roots(**kwargs).get(project_id),
    )

    def resolve_resident_targets(resident_ids):
        resolver = RESIDENT_TARGET_RESOLVER
        if resolver is not None:
            return resolver(resident_ids)
        try:
            records = {
                str(item.get("resident_id") or ""): item
                for item in resident_catalog.get_catalog().get_many(resident_ids)
            }
        except (OSError, ValueError):
            records = {}
        return {
            resident_id: {
                "resident_id": resident_id,
                "source_resident_id": str(
                    records.get(resident_id, {}).get("source_resident_id") or ""
                ),
                "lineage_id": str(
                    records.get(resident_id, {}).get("lineage_id") or ""
                ),
                "title": str(records.get(resident_id, {}).get("title") or ""),
                "backend": str(records.get(resident_id, {}).get("backend") or ""),
            }
            for resident_id in resident_ids
            if not records.get(resident_id, {}).get("deleted_at")
        }

    monkeypatch.setattr(
        lineage_domain,
        "_resident_targets",
        resolve_resident_targets,
    )
    monkeypatch.setattr(
        resident_hatg_credentials,
        "ensure_resident_credential",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", sync_lineage)
    return records



def record_terminal_execution(resident_id, message_id, actor="sample_admin", *, status="completed", at="2026-10-14T00:00:00Z"):
    """Simulate a provider outcome for graph tests whose sender is a stub."""
    from uuid import uuid4
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution import execution_inputs
    store = get_agent_data_store()
    existing = execution_inputs.latest(store, resident_id, message_id)
    if existing:
        assert existing["status"] == status
        return existing["execution_id"]
    identity = "execution-" + uuid4().hex
    store.ensure_agent(resident_id, actor_ref=actor)
    store.begin_execution(execution_id=identity, agent_id=resident_id, actor_ref=actor, provider="codex", started_at=at)
    execution_inputs.attach(store, identity, [{"message_id": message_id, "role": "primary", "accepted_at": at}])
    store.finish_execution(identity, status=status, finished_at=at)
    return identity



def complete_resident_message(resident_id, actor, message_id, **options):
    record_terminal_execution(resident_id, message_id, actor, at=options.get("completed_at") or "2026-10-14T00:00:00Z")
    return lineage_domain.handle_resident_message_completion(resident_id, actor, message_id, **options)


def emit_resident_completion(lineage_id, resident_id, message_id, actor, **options):
    record_terminal_execution(resident_id, message_id, actor, at=options.get("completed_at") or "2026-10-14T00:00:00Z")
    return lineage_domain.lineage_program_terminal_reconciliation.dispatch_program_resident_work_finished(
        lineage_id, resident_id, message_id, actor, **options)


def completed_execution(resident_id: str, message_id: str, *, status: str = "completed") -> str:
    """Admit a real input to Agent's execution record and commit its outcome."""
    import secrets
    from management_portal.execution import execution_inputs
    from management_portal.execution.agent_data_store import get_agent_data_store
    store = get_agent_data_store()
    identity = "execution-" + secrets.token_hex(12)
    at = "2026-10-12T00:00:00Z"
    store.begin_execution(execution_id=identity, agent_id=resident_id,
                          actor_ref="sample_admin", provider="codex", started_at=at)
    execution_inputs.attach(store, identity, [{"message_id": message_id, "role": "primary", "accepted_at": at}])
    store.finish_execution(identity, status=status, finished_at=at)
    return identity
