"""Lineage lifecycle: creating a Lineage, its metadata, layout and Project folder, and deleting it."""

from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path
from typing import Any, Mapping

from .errors import LineageConflictError
from .lineage_program_queries import _compiled_program
from . import lineage_graph_authoring
from .lineage_program_contracts import _program_invoke_wire_execution_configs
from .lineage_values import _clone_json, _safe_actor, _text
from .lineage_program import _position
from . import identity_contract, lineage_state_domains, lineage_storage, resident_catalog, resident_protocol
from . import lineage_aggregate
from .domain_ports import resident_domain_port as resident_domain
from . import project_folders
from . import lineage_state, lineage_resident_nodes


LINEAGE_DELETION_MARKER = ".portal-lineage-delete.json"


def _deleted_root() -> Path:
    return lineage_aggregate.DATA_ROOT / "deleted"


def _read_lineage_for_deletion(lineage_id: str, actor: str) -> dict[str, Any]:
    # An interrupted deletion may already have tombstoned its descriptor.
    # The still-existing domain aggregate and revision remain the recovery truth.
    from .business_fact_views import canonical_view
    return canonical_view(lineage_aggregate.read_lineage_state(lineage_id), resource_id=lineage_id, actor=_text(actor, 120))


def _stage_empty_lineage_deletion(
    lineage: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    lineage_id = str(lineage["lineage_id"])
    store = lineage_aggregate.lineage_catalog_store().state_store
    from . import lineage_effect_script_journal
    lineage_effect_script_journal.ensure_deletion_can_settle(lineage_id)
    existing = lineage_aggregate.lineage_deletion_intent(lineage_id)
    if existing:
        if int(existing.get("definition_revision") or -1) != int(
            lineage.get("revision") or 0
        ):
            raise LineageConflictError("Lineage changed after deletion was prepared")
        return existing

    canonical = lineage_storage.canonical_lineage_folder(
        lineage_id,
        folder_root=lineage_state._lineages_root(),
    )
    source = canonical.resolve(strict=True)
    operation_id = f"lineage-delete_{secrets.token_hex(12)}"
    deleted_at = lineage_state._now_iso()
    intent = {
        "operation_id": operation_id,
        "lineage_id": lineage_id,
        "definition_revision": int(lineage.get("revision") or 0),
        "source_path": str(source),
        "canonical_path": str(canonical),
        "deleted_at": deleted_at,
        "deleted_by": actor,
        "phase": "prepared",
        "last_error": "",
    }
    with store.lineage_deletion_operations.transaction(lineage_id) as state:
        if state:
            raise LineageConflictError("Another Lineage deletion is in progress")
        state.update(intent)
    return intent


def _purge_deleted_lineage_folder(intent: Mapping[str, Any]) -> None:
    """Clean in place so surviving Workspace paths never change.

    Previously staged, already moved journals are drained at their recorded
    target; new deletion intents never create a target or move the folder.
    """
    from .resident_workspace_retirement import remove_retired_folder
    source = Path(str(intent["source_path"]))
    if not source.is_absolute():
        raise ValueError("Invalid Lineage deletion path")
    target_path = intent.get("target_path")
    if target_path and Path(target_path).exists():
        target = Path(target_path)
        proof = json.dumps({key: str(intent.get(key) or "") for key in (
            "operation_id", "lineage_id", "source_path", "target_path",
        )}, sort_keys=True, separators=(",", ":"))
        marker = target / LINEAGE_DELETION_MARKER
        if (target.parent.resolve() != _deleted_root().resolve()
                or not target.name.startswith(f"{intent['lineage_id']}_lineage-delete_")
                or not marker.is_file() or marker.is_symlink()
                or marker.read_text(encoding="utf-8") != proof):
            raise ValueError("Invalid staged Lineage deletion")
        source = target
    lineage_aggregate.lineage_catalog_store().state_store.retire_workspaces_under(
        [source], deleted_at=intent["deleted_at"], deleted_by=intent["deleted_by"],
    )
    remove_retired_folder(source)


def _record_lineage_deletion_error(
    lineage_id: str,
    operation_id: str,
    error: BaseException,
) -> None:
    with lineage_aggregate.lineage_scope(lineage_id):
        current = lineage_aggregate.lineage_deletion_intent(lineage_id)
        if str(current.get("operation_id") or "") != operation_id:
            return
        with lineage_aggregate.lineage_catalog_store().state_store.lineage_deletion_operations.transaction(lineage_id) as state:
            if str(state.get("operation_id") or "") == operation_id:
                state["last_error"] = f"{type(error).__name__}: {error}"[:2000]
                state["phase"] = "retryable"


def _lineage_project_folder(
    lineage_id: str,
    project_id: str,
    actor: str,
) -> Path:
    if not project_id:
        return lineage_storage.canonical_lineage_folder(
            lineage_id,
            folder_root=lineage_state._lineages_root(),
        )
    project_root = project_folders.project_root_for_id(project_id)
    return project_root / "knowledge_base" / "agents" / lineage_id


def _lineage_relocation_blockers(lineage_id: str) -> list[str]:
    records = resident_catalog.get_catalog().for_lineage(
        lineage_id
    )
    resident_ids = [
        str(record.get("resident_id") or "")
        for record in records
        if str(record.get("resident_id") or "")
    ]
    states = resident_protocol.read_resident_states(resident_ids)
    return [
        resident_id
        for resident_id in resident_ids
        if str((states.get(resident_id) or {}).get("work_state") or "idle")
        in {"queued", "running", "stopping"}
    ]


def _relocate_lineage_for_project(
    lineage_id: str,
    project_id: str,
    actor: str,
) -> Path:
    blockers = _lineage_relocation_blockers(lineage_id)
    if blockers:
        raise ValueError(
            "Lineage 仍有任务正在排队或处理，停止或完成后再修改所属项目: "
            + ", ".join(blockers)
        )
    target = _lineage_project_folder(lineage_id, project_id, actor)
    lineage_storage.relocate_lineage_folder(
        lineage_id,
        target,
        folder_root=lineage_state._lineages_root(),
    )
    return target


def _sync_deleted_lineage_resource_identity(
    lineage: dict[str, Any],
    actor: str,
    deleted_at: str,
) -> None:
    tombstone = {
        **lineage,
        "deleted_at": deleted_at,
        "deleted_by": actor,
        "updated_at": deleted_at,
        "updated_by": actor,
    }
    lineage_state._sync_lineage_resource_identity(tombstone)


def _new_lineage_base(
    lineage_id: str,
    title: str,
    description: str,
    actor: str,
) -> dict[str, Any]:
    return lineage_graph_authoring.new_lineage_base(
        lineage_id, title, description, actor,
        schema=lineage_state.LINEAGE_FOLDER_SCHEMA,
        schema_version=lineage_state.SCHEMA_VERSION,
        now=lineage_state._now_iso(),
    )


def _create_lineage_in_scope(
    payload: dict[str, Any],
    actor_id: str,
    lineage_id: str,
) -> dict[str, Any]:
    if not identity_contract.is_valid_id(lineage_id, "lineage"):
        raise ValueError("invalid Lineage id")
    title = _text(payload.get("title"), 140) or "未命名 Lineage"
    lineage_dir = lineage_state._lineage_dir(lineage_id)
    base = _new_lineage_base(
        lineage_id,
        title,
        _text(payload.get("description"), 3000),
        actor_id,
    )
    lineage = lineage_state._normalize_lineage_payload(payload, base, actor_id)
    project_id = _text(lineage.get("project_id"), 120)
    if lineage_state._lineage_membership(lineage) == "formal" and not project_id:
        raise ValueError("Lineage 必须属于 Project")
    pass
    physical_dir = _lineage_project_folder(
        lineage_id,
        project_id,
        actor_id,
    )
    physical_dir.mkdir(parents=True, exist_ok=False)
    try:
        if physical_dir != lineage_dir:
            lineage_storage.bind_lineage_folder(
                lineage_id,
                physical_dir,
                folder_root=lineage_state._lineages_root(),
            )
        lineage_state._persist_lineage_state(None, lineage, "创建 Lineage")
    except Exception:
        if physical_dir != lineage_dir:
            lineage_storage.unbind_lineage_folder(
                lineage_id,
                folder_root=lineage_state._lineages_root(),
                expected_target=physical_dir,
            )
        shutil.rmtree(physical_dir, ignore_errors=True)
        raise
    return lineage


def create_lineage(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    for _ in range(20):
        lineage_id = identity_contract.new_id("lineage")
        with lineage_aggregate.execution_authority(lineage_id):
            with lineage_aggregate.lineage_scope(lineage_id):
                if lineage_state._lineage_dir(lineage_id).exists():
                    continue
                lineage = _create_lineage_in_scope(
                    payload,
                    actor_id,
                    lineage_id,
                )
                plan = _compiled_program(lineage)
                completion_baselines = (
                    lineage_state._resident_completion_baseline_requests(
                        lineage,
                        {
                            "event_source_ids": set(
                                (plan.get("event_sources") or {}).keys()
                            ),
                            "invoke_wire_ids": set(
                                _program_invoke_wire_execution_configs(
                                    plan
                                )
                            ),
                        },
                    )
                )
            lineage_state._establish_resident_completion_baselines(
                lineage_id,
                actor_id,
                completion_baselines,
            )
            return lineage
    raise RuntimeError("无法生成 Lineage id")


def initialize_resident_lineage(
    record: dict[str, Any], actor: str,
) -> dict[str, Any]:
    """Initialize one newly registered Resident as a complete HATG Lineage.

    The caller owns Resident registration serialization.  This helper is only
    valid for a newly allocated canonical Lineage identity that does not yet
    exist, so it does not acquire the Lineage mutation lock.
    """
    actor_id = _safe_actor(actor)
    lineage_id = _text(record.get("lineage_id"), 80).lower()
    if not identity_contract.is_valid_id(lineage_id, "lineage"):
        raise ValueError("Resident 缺少规范的 lineage_id")
    try:
        lineage_aggregate.lineage_catalog_store().get(lineage_id)
    except KeyError:
        pass
    else:
        raise FileExistsError("Lineage 已存在，不能作为新 Resident Lineage 重新初始化")

    node = lineage_resident_nodes._node_from_resident(
        record,
        initial_work_state="idle",
    )
    base = _new_lineage_base(
        lineage_id,
        _text(record.get("lineage_title") or record.get("title"), 140),
        f"由 Lineage Workbench 创建的 Resident Lineage：{node['title']}",
        actor_id,
    )
    lineage_membership = str(
        record.get("lineage_membership") or "formal"
    ).strip().lower()
    project_id = _text(
        record.get("lineage_project_id")
        or record.get("project_id"),
        120,
    )
    if (
        lineage_membership == "formal"
        and not project_id
    ):
        raise ValueError("Resident Lineage 必须属于 Project")
    payload = {
        **base,
        "lineage_membership": lineage_membership,
        "project_id": project_id,
        "leader_resident_id": node["id"],
        "nodes": [node],
    }
    lineage = lineage_state._normalize_lineage_payload(
        payload,
        base,
        actor_id,
        resident_references={
            node["id"]: {
                "resident_id": node["id"],
            },
        },
    )
    hierarchy_level = (record.get("principal_hierarchy") or {}).get("level")
    if hierarchy_level == "user" and lineage_membership == "personal":
        lineage["principal_hierarchy"] = {"level": "user", "scope_id": lineage_id}
    if hierarchy_level == "project":
        lineage["principal_hierarchy"] = {"level": "project", "scope_id": project_id}
        from .principal_hierarchy import project_leaders
        from .lineage_member_projection import project_resident_nodes
        references = project_leaders(lineage, lineage_aggregate.lineage_catalog_store().listing_records())
        project_resident_nodes(lineage, [record, *resident_catalog.get_catalog().get_many(list(references))])
    lineage_state._persist_lineage_state(None, lineage, "创建 Resident Lineage")
    return lineage


def update_lineage_layout(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Merge canvas positions for UpdateLineageLayout under their own layout revision.

    Layout is last-write-wins per object id, so moving one object never
    invalidates another object's move or an open editing form.
    """

    actor_id = _safe_actor(actor)
    current = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
    lineage_aggregate.ensure_manager(current, actor_id)
    candidate = _clone_json(current)
    normalized = lineage_state_domains.apply_layout_patch(
        candidate,
        payload.get("positions"),
        normalize_position=_position,
    )
    result = lineage_aggregate.lineage_catalog_store().update_layout(
        lineage_id,
        normalized["applied"],
        updated_at=lineage_state._now_iso(),
    )
    result["missing_object_ids"] = list(dict.fromkeys([
        *normalized["missing_object_ids"],
        *result["missing_object_ids"],
    ]))
    if result["applied"]:
        from management_portal.society_service.changes import lineage_changed

        lineage_changed(lineage_id, runtime=False)
    return result


def update_lineage_metadata(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Update Lineage-owned metadata and fragments without accepting a whole graph body."""

    actor_id = _safe_actor(actor)
    if "fragments" in payload and not isinstance(payload["fragments"], list):
        raise ValueError("fragments 必须是数组")
    with lineage_aggregate.lineage_scope(lineage_id):
        current = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(current, actor_id)
        lineage, previous_project_id, project_id = _lineage_with_metadata(current, payload, actor_id)
        original_folder: Path | None = None
        folder_relocated = False
        previous_resident_records: list[dict[str, Any]] = []
        next_resident_records: list[dict[str, Any]] = []
        try:
            if (
                project_id != previous_project_id
                or "project_id" in payload
            ):
                original_folder = lineage_state._lineage_dir(lineage_id).resolve(strict=True)

                canonical_folder = lineage_storage.canonical_lineage_folder(
                    lineage_id,
                    folder_root=lineage_state._lineages_root(),
                )
                previous_resident_records, next_resident_records = (
                    resident_domain.prepare_lineage_project_member_updates(
                        lineage_id,
                        project_id,
                        canonical_folder=canonical_folder,
                        source_folder=original_folder,
                    )
                )
                if project_id != previous_project_id:
                    _relocate_lineage_for_project(
                        lineage_id,
                        project_id,
                        actor_id,
                    )
                    folder_relocated = True
            lineage_state._persist_lineage_state(
                current,
                lineage,
                "更新 Lineage 元数据",
                    )
            if next_resident_records:
                resident_domain.persist_lineage_project_member_updates(
                    next_resident_records
                )
        except BaseException:
            if folder_relocated and original_folder is not None:
                lineage_storage.relocate_lineage_folder(
                    lineage_id,
                    original_folder,
                    folder_root=lineage_state._lineages_root(),
                )
                # Revert this update's own change; concurrent writes stay.
                lineage_state._persist_lineage_state(lineage, current, "撤回 Lineage 元数据更新")
                if previous_resident_records:
                    resident_domain.persist_lineage_project_member_updates(
                        previous_resident_records
                    )
            raise
        return _clone_json(lineage)


def _lineage_with_metadata(
    current: dict[str, Any],
    payload: dict[str, Any],
    actor_id: str,
) -> tuple[dict[str, Any], str, str]:
    """``current`` with ``payload``'s metadata applied, and its previous and requested Project IDs.

    Validates the payload and writes nothing.
    """
    lineage = _clone_json(current)
    if "fragments" in payload:
        lineage["fragments"] = lineage_state._normalize_lineage_payload(
            {**_clone_json(current), "fragments": payload["fragments"]}, current, actor_id,
        )["fragments"]
    previous_project_id = _text(current.get("project_id"), 120)
    if "run_as_principal" in payload:
        raise ValueError("run_as_principal 已移除；自动运行使用 Lineage Leader 的身份")
    project_id = lineage_state._requested_lineage_project_id(
        payload,
        current,
        actor_id,
    )
    if lineage_state._lineage_membership(current) == "formal" and not project_id:
        raise ValueError("Lineage 必须属于 Project")
    lineage["project_id"] = project_id
    if "principal_hierarchy" in current:
        from .principal_hierarchy import scope_after_project_change
        lineage["principal_hierarchy"] = scope_after_project_change(current["principal_hierarchy"], project_id)

    if "title" in payload:
        lineage["title"] = (
            _text(payload.get("title"), 140)
            or "未命名 Lineage"
        )
    if "description" in payload:
        lineage["description"] = _text(
            payload.get("description"),
            3000,
        )
    previous_leader_id = _text(
        current.get("leader_resident_id"),
        80,
    ).lower()
    leader_id = _text(
        (
            payload.get("leader_resident_id")
            if "leader_resident_id" in payload
            else previous_leader_id
        ),
        80,
    ).lower()
    nodes = [
        node
        for node in current.get("nodes") or []
        if isinstance(node, dict)
    ]
    resident_ids = {
        str(node.get("id") or "")
        for node in nodes
    }
    if leader_id and leader_id not in resident_ids:
        raise ValueError("Leader 必须是当前 HATG 中的节点")
    if "leader_resident_id" in payload:
        lineage["leader_resident_id"] = leader_id
    return lineage, previous_project_id, project_id


def delete_lineage(lineage_id: str, actor: str) -> dict[str, Any]:
    """Delete a Lineage and its Residents; their folders are removed, not archived."""
    # Resident deletion confirms Resource tombstones after its own commit. Do not
    # hold the Lineage lock while invoking that cross-domain operation.
    with lineage_aggregate.lineage_scope(lineage_id):
        lineage = _read_lineage_for_deletion(lineage_id, actor)
        lineage_aggregate.ensure_manager(lineage, actor)
        if (lineage.get("principal_hierarchy") or {}).get("level") == "project":
            from .principal_hierarchy import project_leaders
            if project_leaders(lineage, lineage_aggregate.lineage_catalog_store().listing_records()):
                raise ValueError("Project principal still manages child Lineages")
        preferred_resident_id = _text(lineage.get("leader_resident_id"), 80).lower()

    result = resident_domain.delete_registered_lineage(
        lineage_id, preferred_resident_id, actor,
    )
    if result is not None:
        from . import system_workflow_switches
        system_workflow_switches.defer_definition_projection(lineage_id, lineage)
        return {
            "lineage_id": lineage_id,
            "deleted_at": str(result.get("deleted_at") or lineage_state._now_iso()),
            "deleted_resident_ids": result.get("deleted_resident_ids") or [],
        }

    with lineage_aggregate.lineage_scope(lineage_id):
        lineage = _read_lineage_for_deletion(lineage_id, actor)
        lineage_aggregate.ensure_manager(lineage, actor)
        if (lineage.get("principal_hierarchy") or {}).get("level") == "project":
            from .principal_hierarchy import project_leaders
            if project_leaders(lineage, lineage_aggregate.lineage_catalog_store().listing_records()):
                raise ValueError("Project principal still manages child Lineages")
        if resident_catalog.get_catalog().state_store.residents_for_lineage(lineage_id):
            raise LineageConflictError("Lineage membership changed during deletion")
        intent = _stage_empty_lineage_deletion(lineage, actor)

    operation_id = str(intent["operation_id"])
    try:
        _sync_deleted_lineage_resource_identity(
            lineage,
            str(intent["deleted_by"]),
            str(intent["deleted_at"]),
        )
        _purge_deleted_lineage_folder(intent)
        lineage_storage.unbind_lineage_folder(
            lineage_id,
            folder_root=lineage_state._lineages_root(),
            expected_target=str(intent["source_path"]),
        )
    except BaseException as exc:
        _record_lineage_deletion_error(lineage_id, operation_id, exc)
        raise

    removed = False
    with lineage_aggregate.lineage_scope(lineage_id):
        current = lineage_aggregate.lineage_deletion_intent(lineage_id)
        if current:
            if str(current.get("operation_id") or "") != operation_id:
                raise LineageConflictError("Lineage deletion operation changed")
            latest = lineage_aggregate.lineage_catalog_store().get(lineage_id)
            if int(latest.get("revision") or 0) != int(
                intent["definition_revision"]
            ):
                raise LineageConflictError("Lineage changed after deletion was prepared")
            lineage_aggregate.lineage_catalog_store().remove(lineage_id, deleted_by=str(intent["deleted_by"]))
            removed = True

    if removed:
        from . import system_workflow_switches
        system_workflow_switches.defer_definition_projection(lineage_id, lineage)
        from management_portal.society_service.changes import lineage_changed

        lineage_changed(lineage_id)
    return {
        "lineage_id": lineage_id,
        "operation_id": operation_id,
        "deleted_at": str(intent["deleted_at"]),
    }
