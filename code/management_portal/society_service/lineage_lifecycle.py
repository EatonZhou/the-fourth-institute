"""Lineage lifecycle: creating a Lineage, its metadata, layout and Project folder, and deleting it."""

from __future__ import annotations

import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from .errors import LineageConflictError
from .lineage_program_queries import _compiled_program
from . import lineage_graph_authoring
from .lineage_program_contracts import _program_invoke_wire_execution_configs
from .lineage_values import _clone_json, _safe_actor, _text
from .lineage_program_fields import _position
from . import identity_contract, lineage_state_domains, lineage_storage, resident_catalog, resident_protocol
from . import lineage_aggregate
from .domain_ports import resident_domain_port as resident_domain
from . import lineage_state, lineage_resident_nodes


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

    source = lineage_state._lineage_dir(lineage_id)
    from management_common.directory_paths import validate_removal
    validate_removal(source)
    operation_id = f"lineage-delete_{secrets.token_hex(12)}"
    deleted_at = lineage_state._now_iso()
    intent = {
        "operation_id": operation_id,
        "lineage_id": lineage_id,
        "definition_revision": int(lineage.get("revision") or 0),
        "source_path": str(source),
        "deleted_at": deleted_at,
        "deleted_by": actor,
        "phase": "prepared",
        "last_error": "",
        "definition": _clone_json(lineage),
    }
    with store.lineage_deletion_operations.transaction(lineage_id) as state:
        if state:
            raise LineageConflictError("Another Lineage deletion is in progress")
        state.update(intent)
    return intent


def _lineage_project_folder(lineage_id: str, project_id: str) -> Path:
    return lineage_storage.lineage_folder_for_project(lineage_id, project_id)

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
    *, intent=None,
) -> dict[str, Any]:
    if not identity_contract.is_valid_id(lineage_id, "lineage"):
        raise ValueError("invalid Lineage id")
    title = _text(payload.get("title"), 140) or "未命名 Lineage"
    base = _new_lineage_base(
        lineage_id,
        title,
        _text(payload.get("description"), 3000),
        actor_id,
    )
    if intent and intent.snapshot.get('lineage'):
        base = _clone_json(intent.snapshot['lineage'])
        lineage = lineage_state._normalize_lineage_payload(base, base, actor_id)
    else:
        lineage = lineage_state._normalize_lineage_payload(payload, base, actor_id)
    project_id = _text(lineage.get("project_id"), 120)
    if lineage_state._lineage_membership(lineage) == "formal" and not project_id:
        raise ValueError("Lineage 必须属于 Project")
    if intent and not intent.snapshot.get('lineage'):
        intent.snapshot['lineage'] = _clone_json(lineage)
        intent.save()
    lineage_dir = _lineage_project_folder(lineage_id, project_id)
    from management_portal.resources.creation import Creation
    store = lineage_aggregate.lineage_catalog_store().state_store
    creation = Creation(lineage_id, actor=actor_id, payload=payload,
                        operation_id=intent.key if intent else lineage_id)
    with creation.prepare([(lineage_dir, 'directory')]):
        if creation.completed:
            return lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
        def initialize(_index, folder):
            (folder / 'residents').mkdir()
            (folder / 'runtime').mkdir()
            (folder / 'LINEAGE.md').touch()
            (folder / 'README.md').write_text(lineage_state._lineage_readme_text(lineage), encoding='utf-8')
        creation.publish(initialize)
        with store.database.transaction() as connection:
            with creation.registration(connection):
                lineage_state._persist_lineage_state(None, lineage, "创建 Lineage")
                if intent:
                    intent.complete(connection)
    return lineage


def create_lineage(payload: dict[str, Any], actor: str, *, operation_id: str = '') -> dict[str, Any]:
    from .creation_intents import CreationIntent
    actor_id = _safe_actor(actor)
    intent = CreationIntent('lineage', actor_id, payload, operation_id)
    with intent.open(lambda: {'lineage_id': identity_contract.creation_candidate_id('lineage')}):
        lineage_id = intent.snapshot['lineage_id']
        if intent.snapshot.get('completed'):
            return lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
        with lineage_aggregate.execution_authority(lineage_id):
            with lineage_aggregate.lineage_scope(lineage_id):
                lineage = _create_lineage_in_scope(
                    payload,
                    actor_id,
                    lineage_id,
                    intent=intent,
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
    current = lineage_aggregate.read_lineage(lineage_id, actor=actor_id, include_runtime=False)
    lineage_aggregate.ensure_manager(current, actor_id)
    # This read owns its definition. Coordinates need neither runtime state
    # nor another copy of component configurations and graph content.
    normalized = lineage_state_domains.apply_layout_patch(
        current,
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
        if project_id == previous_project_id:
            lineage_state._persist_lineage_state(current, lineage, "更新 Lineage 元数据")
            return _clone_json(lineage)
        from management_common.directory_move_store import DirectoryMoveStore
        from .directory_relocation import relocate_nested, reconcile_dependencies
        from .directory_owner import directory_metadata
        store = lineage_aggregate.lineage_catalog_store().state_store
        members = resident_catalog.get_catalog().for_lineage(lineage_id)
        directory_metadata(current.get('directory'), lineage_id)
        source = _lineage_project_folder(lineage_id, previous_project_id)
        target = _lineage_project_folder(lineage_id, project_id)

        def update(connection, source, target):
            relocate_nested(connection, source, target)
            lineage_state._persist_lineage_state(current, lineage, "更新 Lineage 元数据")
            for member in members:
                rid = member['resident_id']
                store.enqueue_resident_resource_projection(rid, store.resident_record(rid))

        with resident_domain.directory_move_execution_scope(members, lineage_ids=[lineage_id], actor=actor_id) as terminate:
            DirectoryMoveStore().move(source, target, owner_id=lineage_id, actor=actor_id,
                terminate=terminate, database=store.database, update=update, dependencies=reconcile_dependencies)
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
    with prepare_lineage_deletion(lineage_id, actor) as commit:
        return commit()


@contextmanager
def prepare_lineage_deletion(lineage_id: str, actor: str):
    """Own exclusion from termination through the enclosing identity commit."""
    with lineage_aggregate.execution_authority(lineage_id), lineage_aggregate.lineage_scope(lineage_id):
        pending = lineage_aggregate.lineage_deletion_intent(lineage_id)
        if pending.get('phase') == 'committed':
            lineage_aggregate.ensure_manager(pending['definition'], actor)
            yield lambda: _finish_empty_lineage_deletion(pending)
            return
        lineage = _read_lineage_for_deletion(lineage_id, actor)
        lineage_aggregate.ensure_manager(lineage, actor)
        preferred_resident_id = _text(lineage.get('leader_resident_id'), 80).lower()
        resident_domain.terminate_lineage_scripts(lineage_id, actor)
        preparation = resident_domain.prepare_registered_lineage_deletion(lineage_id, preferred_resident_id, actor)
        if preparation is None:
            intent = _stage_empty_lineage_deletion(lineage, actor)
            yield lambda: _commit_empty_lineage_deletion(lineage_id, actor, lineage, intent)
            return
        with preparation as commit_residents:
            def commit():
                _check_project_principal_children(lineage)
                result = commit_residents()
                from . import system_workflow_switches
                lineage_aggregate.lineage_catalog_store().state_store.database.after_commit(
                    lambda: system_workflow_switches.defer_definition_projection(lineage_id, lineage))
                return {'lineage_id': lineage_id, 'deleted_at': result['deleted_at'],
                        'deleted_resident_ids': result.get('deleted_resident_ids') or []}
            yield commit


def _check_project_principal_children(lineage):
    if (lineage.get('principal_hierarchy') or {}).get('level') == 'project':
        from .principal_hierarchy import project_leaders
        if project_leaders(lineage, lineage_aggregate.lineage_catalog_store().listing_records()):
            raise ValueError('Project principal still manages child Lineages')


def _commit_empty_lineage_deletion(lineage_id, actor, lineage, intent):
    _check_project_principal_children(lineage)
    operation_id = str(intent["operation_id"])
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
            catalog = lineage_aggregate.lineage_catalog_store()
            def commit():
                catalog.remove(lineage_id, deleted_by=str(intent['deleted_by']))
                with catalog.state_store.lineage_deletion_operations.transaction(lineage_id) as state:
                    state.update({**intent, 'phase': 'committed'})
            catalog.state_store.transaction(commit)
            removed = True

    if removed:
        from . import system_workflow_switches
        lineage_aggregate.lineage_catalog_store().state_store.database.after_commit(
            lambda: system_workflow_switches.defer_definition_projection(lineage_id, lineage))
        from management_portal.society_service.changes import lineage_changed

        lineage_changed(lineage_id)
    return _finish_empty_lineage_deletion(intent)


def _finish_empty_lineage_deletion(intent):
    lineage_id = intent['lineage_id']
    store = lineage_aggregate.lineage_catalog_store().state_store
    def publish():
        _sync_deleted_lineage_resource_identity(intent['definition'], intent['deleted_by'], intent['deleted_at'])
        store.lineage_deletion_operations.delete(lineage_id)
    store.database.after_commit(publish)
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    return {'lineage_id': lineage_id, 'operation_id': intent['operation_id'],
            'deleted_at': str(intent['deleted_at']),
            'directory_cleanup': get_directory_cleanup_store().status(owner_id=lineage_id)}
