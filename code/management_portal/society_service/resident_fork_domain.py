"""Resident domain responsibilities: fork."""
from __future__ import annotations

from . import resident_domain_common

from . import (
    identity_contract,
    resident_backend,
    resident_catalog,
    resident_conversation_stream,
    resident_fork_context,
    resident_execution_work,
    resident_lineage,
    resident_store,
)
from .domain_ports import execution_port
from .resident_access import (
    can_manage_resident as _can_manage_resident,
    ensure_resident_visible_to_actor,
    manage_refusal,
)
from management_common.actor_identity import canonical_actor_id
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
import copy

from typing import Any, Mapping


def _fork_target_backend(source: Mapping[str, Any], provider: str = "") -> str:
    backend = str(provider or resident_backend.execution_backend(source)).strip()
    if backend not in {*AGENT_PROVIDERS, "provider_api"}:
        raise ValueError(f"不支持的 Fork Provider：{backend}")
    return backend


def _ensure_derived_resident_folder(
    record: dict[str, Any],
    resident: dict[str, Any],
) -> None:
    from . import resident_record_domain, resident_runtime_domain, resident_workspace_domain
    target_resident_id = str(resident.get("resident_id") or "").strip()
    if not target_resident_id:
        return
    workspace_record = {
        **record,
        **{key: resident[key] for key in ("owned_workspace_id", "owned_workspace") if key in resident},
        "resident_id": target_resident_id,
        "workspace_bindings": resident.get("workspace_bindings") or [],
        "default_cwd": str(resident.get("default_cwd") or ""),
    }
    resident_record_domain.set_resident_owned_workspace_binding(workspace_record, resident_id=target_resident_id)
    resident["workspace_bindings"] = workspace_record["workspace_bindings"]
    resident["owned_workspace_id"] = workspace_record["owned_workspace_id"]
    resident["owned_workspace"] = workspace_record["owned_workspace"]
    resident["default_cwd"] = workspace_record["default_cwd"]
    resident_folder = resident_runtime_domain.resident_folder_for_id(record, target_resident_id)
    runtime = resident_folder / "runtime"
    owned_workspace = resident_record_domain.resident_owned_workspace_path(record, target_resident_id)
    resident_folder.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    owned_workspace.mkdir(parents=True, exist_ok=True)
    parent_identity = resident_record_domain.resident_instructions_path(record)
    identity_text = ""
    if parent_identity.is_file():
        try:
            identity_text = parent_identity.read_text(encoding="utf-8")
        except OSError:
            identity_text = ""
    resident_workspace_domain.ensure_resident_file(
        resident_folder,
        resident_domain_common.RESIDENT_INSTRUCTIONS_FILE,
        initial_content=identity_text,
    )
    resident_workspace_domain.ensure_resident_file(resident_folder, resident_domain_common.RESIDENT_DIARY_FILE)
    resident_workspace_domain.ensure_resident_file(resident_folder, resident_domain_common.RESIDENT_MAILBOX_FILE)
    resident_workspace_domain.ensure_resident_file(
        resident_folder,
        resident_domain_common.RESIDENT_WORKSPACES_FILE,
        initial_content=resident_workspace_domain.resident_workspaces_text(resident),
    )
    resident_payload = resident_record_domain.canonical_derived_resident_record(record, resident)
    resident_payload = copy.deepcopy(dict(resident_payload))
    resident_payload["schema"] = resident_catalog.RESIDENT_RECORD_SCHEMA
    resident_workspace_domain.save_resident_record(resident_payload)
    resident_store.replace_file_text(
        resident_folder / resident_domain_common.RESIDENT_WORKSPACES_FILE,
        resident_workspace_domain.resident_workspaces_text(resident),
    )


def _sync_resident_files(record: dict, resident: dict[str, Any]) -> None:
    from . import resident_runtime_domain
    from .resident_settings_contract import stage_runtime_settings
    resident_backend.validate_resident_session_identity(resident)
    target_resident_id = str(resident.get("resident_id") or "")
    if not target_resident_id:
        return
    resident_runtime_dir = resident_runtime_domain.resident_runtime_dir_for_id(record, target_resident_id)
    if "execution_settings" not in resident or "harness_config" not in resident:
        stage_runtime_settings(resident, resident_runtime_domain.read_resident_runtime_settings({**record, **resident}))
    _ensure_derived_resident_folder(record, resident)
    resident_runtime_dir.mkdir(parents=True, exist_ok=True)


def _persist_derived_resident(
    record: dict[str, Any],
    resident: dict[str, Any],
) -> None:
    """Commit a newly generated Resident without a global lock.

    Generated Residents have unique canonical IDs and canonical records.
    Their Resource identity is delivered from the transactional outbox after
    the canonical Resident fact commits.
    """
    from . import resident_record_domain

    target_resident_id = resident_lineage.resident_id(resident)
    if not identity_contract.is_valid_id(target_resident_id, "resident"):
        raise ValueError("Resident creation requires a canonical resident_id")
    try:
        existing = resident_catalog.get_catalog().get(target_resident_id)
    except KeyError:
        existing = None
    if existing is not None and str(existing.get("lineage_id") or "").strip() != str(
        resident.get("lineage_id") or record.get("lineage_id") or ""
    ).strip():
        raise RuntimeError(
            f"Resident already exists during derivation: {target_resident_id}"
        )

    try:
        persisted_source = resident_catalog.get_catalog().get(resident_record_domain.record_resident_id(record))
    except KeyError:
        raise KeyError("resident not found") from None
    if str(persisted_source.get("deleted_at") or "").strip():
        raise KeyError("resident not found")
    if str(persisted_source.get("lineage_id") or "").strip() != str(
        record.get("lineage_id") or ""
    ).strip():
        raise RuntimeError("Resident lineage changed during derivation")

    resident_backend.validate_resident_session_identity(resident)
    workspace_record = {
        **record,
        **{key: resident[key] for key in ("owned_workspace_id", "owned_workspace") if key in resident},
        "resident_id": target_resident_id,
        "workspace_bindings": resident.get("workspace_bindings") or [],
        "default_cwd": str(resident.get("default_cwd") or ""),
    }
    resident_record_domain.set_resident_owned_workspace_binding(
        workspace_record,
        resident_id=target_resident_id,
    )
    resident["workspace_bindings"] = workspace_record["workspace_bindings"]
    resident["owned_workspace_id"] = workspace_record["owned_workspace_id"]
    resident["owned_workspace"] = workspace_record["owned_workspace"]
    resident["default_cwd"] = workspace_record["default_cwd"]

    _sync_resident_files(record, resident)

    from management_portal.society_service.changes import resident_changed

    resident_changed(target_resident_id, scope="identity")


def _queue_derived_resident_session_creation(
    record: dict[str, Any],
    resident: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    from . import resident_lifecycle_domain, resident_session_backend_domain, resident_workspace_domain
    if resident_backend.record_session_id(resident):
        return resident
    target_resident_id = str(resident.get("resident_id") or "")
    agent_data_fork = resident.pop("_pending_agent_data_fork", None)
    operation = "context_fork_create"
    resident["session_state"] = "creating"
    resident["last_error"] = ""
    resident.pop("last_runtime_error", None)
    resident["updated_at"] = resident_lifecycle_domain.now_iso()
    if isinstance(agent_data_fork, dict):
        # The database history is part of creating the identity, not Provider
        # startup. This also makes a just-created branch a valid Fork source.
        resident_session_backend_domain.materialize_agent_data_fork(target_resident_id, agent_data_fork, actor)
    _persist_derived_resident(record, resident)
    exact_record = resident_workspace_domain.find_resident(target_resident_id)
    if not exact_record:
        raise RuntimeError("new Resident was not persisted")
    existing = resident_execution_work.read(exact_record)
    if resident_execution_work.is_active(existing) and str(existing.get("operation") or "") == operation:
        if isinstance(agent_data_fork, dict) and not isinstance(
            existing.get("agent_data_fork"),
            dict,
        ):
            existing["agent_data_fork"] = agent_data_fork
            resident_execution_work.write(exact_record, existing)
        return resident
    now = resident_lifecycle_domain.now_iso()
    if str(existing.get("operation") or "") == operation:
        job = dict(existing)
        job.update({
            "error": "",
            "updated_at": now,
        })
        job.pop("outcome", None)
        job.pop("finished_at", None)
        job.pop("runtime_error", None)
    else:
        job = {
            "operation": operation,
            "resident_id": target_resident_id,
            "actor": actor,
            "started_at": now,
            "updated_at": now,
            "error": "",
            "queue": [],
        }
        resident_execution_work.ensure_execution_id(job)
    if isinstance(agent_data_fork, dict):
        job["agent_data_fork"] = agent_data_fork
    resident_execution_work.write(exact_record, job)
    return resident


def _ensure_derived_resident_session(
    record: dict[str, Any],
    registry: dict[str, Any],
    resident: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    from . import resident_lifecycle_domain, resident_session_backend_domain, resident_workspace_domain
    resident_workspace_domain.ensure_resident_supports_forks(record)
    resident.pop("backend_start_deferred", None)
    resident.pop("backend_start_gate", None)
    execution_backend = resident_backend.execution_backend(resident)
    definition = AGENT_PROVIDERS.get(execution_backend)
    resident.update({
        "backend": execution_backend,
        "session_state": "active",
        "last_error": "",
        "updated_at": resident_lifecycle_domain.now_iso(),
    })
    if definition and not resident_backend.record_session_id(resident) and "create" in definition.operations:
        return _queue_derived_resident_session_creation(
            record,
            resident,
            actor,
        )
    if definition and not resident_backend.record_session_id(resident) and not definition.resume_without_session:
        raise ValueError(
            f"{definition.label} cannot create a fresh context-seeded session"
        )
    resident_session_backend_domain.materialize_pending_agent_data_fork(resident, actor)
    _persist_derived_resident(record, resident)
    return resident


def _create_resident_by_full_context_fork(
    record: dict,
    registry: dict[str, Any],
    actor: str,
    *,
    lineage_source: str = "",
    backend_route_id: str = "",
    provider: str = "",
    defer_backend_start: bool = False,
    fork_title: str = "",
    compact_context: bool = False,
) -> dict[str, Any]:
    """Create a full Fork from a frozen PostgreSQL effective-context snapshot."""
    from . import (
        resident_conversation_projection_domain,
        resident_lifecycle_domain,
        resident_record_domain,
        resident_runtime_domain,
        resident_session_backend_domain,
        resident_watchboard_domain,
        resident_workspace_domain,
    )

    resident_workspace_domain.ensure_resident_supports_forks(record)
    target_backend = _fork_target_backend(record, provider)
    actor_id = canonical_actor_id(actor)
    owner = _can_manage_resident(record, actor_id)
    derived_residents = resident_runtime_domain.managed_derived_residents(
        record,
        registry,
        actor_id,
    )
    now = resident_lifecycle_domain.now_iso()
    # Freeze the effective model window before publishing the child. Chat and
    # the audit trace are copied independently and are not an input fallback.
    context_snapshot = _fork_provider_context_snapshot(record)
    if not context_snapshot or (not context_snapshot.get("segments") and not context_snapshot.get("empty_context")):
        raise ValueError("Fork 来源缺少 PostgreSQL 中的有效模型上下文；不能创建空白分支")
    if compact_context:
        definition = AGENT_PROVIDERS.get(target_backend)
        if definition is None or not definition.seeded_compaction:
            raise ValueError(f"目标 Provider {target_backend} 不支持压缩继承")
        if not context_snapshot.get("segments"):
            raise ValueError("压缩继承需要非空的有效模型上下文")
    target_resident_id = resident_domain_common.new_resident_id()
    sequence = len(derived_residents) + 1
    child = {
        "resident_id": target_resident_id,
        "title": str(fork_title or "").strip()
        or f"派生 Resident {sequence} · {resident_conversation_projection_domain.date_part(now)}",
        "title_origin": "custom" if str(fork_title or "").strip() else "generated",
        "session_state": "active",
        "created_by": actor_id,
        "created_at": now,
        "updated_at": now,
        "last_interaction_at": now,
        "forked_from_at": now,
        "source_resident_id": resident_record_domain.record_resident_id(record),
        "context_source_resident_id": resident_record_domain.record_resident_id(record),
        "backend": target_backend,
        "last_error": "",
    }
    resident_runtime_domain.inherit_derived_resident_runtime_from_source(
        record,
        child,
        record,
        None,
        actor_id,
        now,
        target_workspace_authority=owner,
    )
    resident_runtime_domain.assign_derived_resident_account_route(
        record,
        child,
        record,
        requested_account_id=backend_route_id,
    )
    resident_runtime_domain.complete_derived_resident_identity(
        record,
        child,
        actor_id,
        lineage_source or "fork:full_context",
        now,
    )
    watchboard_child = {**record, **child}
    resident_watchboard_domain.inherit_watchboard(record, watchboard_child)
    if "watchboard" in watchboard_child:
        child["watchboard"] = watchboard_child["watchboard"]
    registry["residents"].append(child)
    child["fork_mode"] = "full"
    if compact_context:
        child.update({
            "fork_mode": "compacted",
            "context_compaction_policy": "before_first_turn",
            "context_compaction_state": "pending",
        })
    resident_session_backend_domain.stage_agent_data_fork(
        child,
        str(child["source_resident_id"]),
        segment_ids=[item["segment_id"] for item in resident_fork_context.select_segments(context_snapshot)],
    )
    if defer_backend_start:
        resident_session_backend_domain.materialize_pending_agent_data_fork(child, actor_id)
        child["updated_at"] = resident_lifecycle_domain.now_iso()
        _persist_derived_resident(record, child)
        return child
    return _ensure_derived_resident_session(
        record,
        registry,
        child,
        actor_id,
    )


def _derived_resident_creation_result(
    record: dict[str, Any],
    resident: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Return the stable result of a Resident derivation operation."""
    from . import resident_lifecycle_domain, resident_runtime_domain

    created_resident_id = resident_lineage.resident_id(resident)
    selected_resident = {
        "resident_id": created_resident_id,
        "title": str(resident.get("title") or created_resident_id),
        "created_at": str(resident.get("created_at") or ""),
        "updated_at": str(resident.get("updated_at") or ""),
        "user_message_count": resident_lifecycle_domain.resident_user_message_count(created_resident_id),
        "backend_binding": resident_runtime_domain.derived_resident_backend_binding(record, resident),
    }
    return {
        "schema": "tfi.resident-creation-result.v1",
        "resident_id": created_resident_id,
        **selected_resident,
    }


def create_resident_full_context_fork(
    source_resident_id: str,
    actor: str,
    lineage_source: str = "",
    *,
    backend_route_id: str = "",
    provider: str = "",
    defer_backend_start: bool = False,
    fork_title: str = "",
) -> dict[str, Any]:
    from . import resident_workspace_domain
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后执行 Fork")
    with resident_lineage.access_snapshot_scope():
        source = resident_workspace_domain.resolve_resident_runtime_target(
            source_resident_id,
            actor_id,
            require_visible=True,
        )
        record = source.record
        resident_workspace_domain.ensure_resident_supports_forks(record)
        registry = resident_lineage.snapshot(record)
        child = _create_resident_by_full_context_fork(
            record,
            registry,
            actor_id,
            lineage_source=lineage_source or "fork:full_context",
            backend_route_id=backend_route_id,
            provider=provider,
            defer_backend_start=defer_backend_start,
            fork_title=fork_title,
        )
    return _derived_resident_creation_result(record, child, actor_id)


def create_resident_compacted_context_fork(
    source_resident_id: str,
    actor: str,
    lineage_source: str = "",
    *,
    backend_route_id: str = "",
    provider: str = "",
    fork_title: str = "",
) -> dict[str, Any]:
    """Create a child whose frozen effective context is compacted before use."""
    from . import resident_workspace_domain

    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后执行 Fork")
    with resident_lineage.access_snapshot_scope():
        source = resident_workspace_domain.resolve_resident_runtime_target(
            source_resident_id,
            actor_id,
            require_visible=True,
        )
        record = source.record
        resident_workspace_domain.ensure_resident_supports_forks(record)
        registry = resident_lineage.snapshot(record)
        child = _create_resident_by_full_context_fork(
            record,
            registry,
            actor_id,
            lineage_source=lineage_source or "fork:compacted-context",
            backend_route_id=backend_route_id,
            provider=provider,
            fork_title=fork_title,
            compact_context=True,
        )
    return _derived_resident_creation_result(record, child, actor_id)


def _message_matches_selector(
    item: Mapping[str, Any],
    selector: Mapping[str, Any],
) -> bool:
    message_id = str(selector.get("message_id") or "").strip()
    if not message_id:
        raise ValueError("message_id is required")
    return str(item.get("message_id") or "") == message_id


def _fork_provider_context_snapshot(
    record: Mapping[str, Any], *, through_message_id: str = "", through_turn_id: str = "",
) -> dict[str, Any]:
    from . import resident_record_domain
    return execution_port.agent_context_snapshot(resident_record_domain.record_resident_id(record),
        through_message_id=through_message_id, through_turn_id=through_turn_id)


def create_resident_fork_from_message(
    source_resident_id: str,
    actor: str,
    selector: dict[str, Any],
    target_lineage_resident_id: str = "",
    lineage_source: str = "",
    *,
    backend_route_id: str = "",
    provider: str = "",
    fork_title: str = "",
) -> dict[str, Any]:
    from . import (
        resident_conversation_projection_domain,
        resident_directory_domain,
        resident_lifecycle_domain,
        resident_runtime_domain,
        resident_session_backend_domain,
        resident_watchboard_domain,
        resident_workspace_domain,
    )
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后执行 Fork")
    with resident_lineage.access_snapshot_scope():
        source = resident_workspace_domain.resolve_resident_runtime_target(
            source_resident_id,
            actor_id,
            require_visible=True,
        )
        record = source.record
        resident_workspace_domain.ensure_resident_supports_forks(record)
        registry = resident_lineage.snapshot(record)
        ensure_resident_visible_to_actor(record, actor_id)
        messages = resident_conversation_stream.get_repository().messages_through_turn(
            source.resident_id,
            [selector.get("message_id")],
            limit=resident_domain_common.MAX_TIMELINE_ITEMS,
        )
        target_index = next(
            (
                idx
                for idx, item in enumerate(messages)
                if _message_matches_selector(item, selector)
            ),
            -1,
        )
        if target_index < 0:
            raise ValueError("未找到所选消息")
        target_item = messages[target_index]
        if str(target_item.get("role") or "") != "user":
            raise ValueError("只能从用户消息执行 Fork")
        target_message_id = str(target_item.get("message_id") or "")
        target_id = str(target_lineage_resident_id or "").strip()
        target_record = record
        target_registry = registry
        if target_id and target_id != str(record.get("resident_id") or ""):
            target_record = resident_workspace_domain.find_resident(target_id)
            if not target_record:
                raise KeyError("target resident not found")
            ensure_resident_visible_to_actor(target_record, actor_id)
            if not resident_directory_domain.can_manage(target_record, actor_id):
                raise manage_refusal(target_record)
            target_registry = resident_lineage.snapshot(target_record)
        target_owner = resident_directory_domain.can_manage(target_record, actor_id)
        derived_residents = resident_runtime_domain.managed_derived_residents(
            target_record,
            target_registry,
            actor_id,
        )
        now = resident_lifecycle_domain.now_iso()
        target_resident_id = resident_domain_common.new_resident_id()
        sequence = len(derived_residents) + 1
        snapshot = _fork_provider_context_snapshot(
            record,
            through_message_id=target_message_id,
        )
        if not snapshot:
            raise ValueError("Fork 截止位置缺少有效模型上下文")
        # Conversation owns its display boundary. Its canonical Messages do
        # not carry Provider IDs; retain the selected turn's existing replies,
        # not just the input, without using Chat text as model context.
        segments = resident_fork_context.select_segments(snapshot)
        child = {
            "resident_id": target_resident_id,
            "title": str(fork_title or "").strip()
            or f"Fork from here {sequence} · {resident_conversation_projection_domain.date_part(now)}",
            "title_origin": "custom" if str(fork_title or "").strip() else "generated",
            "session_state": "active",
            "created_by": actor_id,
            "created_at": now,
            "updated_at": now,
            "last_interaction_at": now,
            "forked_from_at": now,
            "backend": _fork_target_backend(record, provider),
            "last_error": "",
            "fork_mode": "message",
            "source_resident_id": str(target_record["resident_id"]),
            "context_source_resident_id": source.resident_id,
            "source_message_id": target_message_id,
        }
        resident_runtime_domain.inherit_derived_resident_runtime_from_source(
            target_record,
            child,
            record,
            None,
            actor_id,
            now,
            target_workspace_authority=target_owner,
        )
        resident_runtime_domain.assign_derived_resident_account_route(
            target_record,
            child,
            record,
            requested_account_id=backend_route_id,
        )
        resident_runtime_domain.complete_derived_resident_identity(
            target_record,
            child,
            actor_id,
            lineage_source or ("branch:message" if target_record is not record else "fork:message"),
            now,
        )
        watchboard_child = {**target_record, **child}
        resident_watchboard_domain.inherit_watchboard(record, watchboard_child)
        if "watchboard" in watchboard_child:
            child["watchboard"] = watchboard_child["watchboard"]
        resident_session_backend_domain.stage_agent_data_fork(
            child,
            source.resident_id,
            segment_ids=[item["segment_id"] for item in segments],
        )
        target_registry["residents"].append(child)
        child = _ensure_derived_resident_session(target_record, target_registry, child, actor_id)
    return _derived_resident_creation_result(target_record, child, actor_id)


def create_resident_context_fork(
    source_resident_id: str,
    actor: str,
    turns: int,
    lineage_source: str = "",
    *,
    backend_route_id: str = "",
    provider: str = "",
    fork_title: str = "",
) -> dict[str, Any]:
    from . import (
        resident_conversation_projection_domain,
        resident_lifecycle_domain,
        resident_runtime_domain,
        resident_session_backend_domain,
        resident_watchboard_domain,
        resident_workspace_domain,
    )
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后执行 Fork")
    turn_count = int(turns or 0)
    if turn_count not in {5, 10}:
        raise ValueError("turns must be 5 or 10")
    with resident_lineage.access_snapshot_scope():
        source = resident_workspace_domain.resolve_resident_runtime_target(
            source_resident_id,
            actor_id,
            require_visible=True,
        )
        record = source.record
        resident_workspace_domain.ensure_resident_supports_forks(record)
        registry = resident_lineage.snapshot(record)
        ensure_resident_visible_to_actor(record, actor_id)
        owner = _can_manage_resident(record, actor_id)
        context_snapshot = _fork_provider_context_snapshot(record)
        if not context_snapshot:
            raise ValueError("Fork 来源缺少有效模型上下文")
        segments = resident_fork_context.select_segments(context_snapshot, recent_turns=turn_count)
        derived_residents = resident_runtime_domain.managed_derived_residents(
            record,
            registry,
            actor_id,
        )
        now = resident_lifecycle_domain.now_iso()
        target_resident_id = resident_domain_common.new_resident_id()
        sequence = len(derived_residents) + 1
        child = {
            "resident_id": target_resident_id,
            "title": str(fork_title or "").strip()
            or f"Fork-{turn_count} {sequence} · {resident_conversation_projection_domain.date_part(now)}",
            "title_origin": "custom" if str(fork_title or "").strip() else "generated",
            "session_state": "active",
            "created_by": actor_id,
            "created_at": now,
            "updated_at": now,
            "last_interaction_at": now,
            "forked_from_at": now,
            "backend": _fork_target_backend(record, provider),
            "last_error": "",
            "fork_mode": f"recent-{turn_count}",
            "source_resident_id": source.resident_id,
            "context_source_resident_id": source.resident_id,
        }
        resident_runtime_domain.inherit_derived_resident_runtime_from_source(
            record,
            child,
            record,
            None,
            actor_id,
            now,
            target_workspace_authority=owner,
        )
        resident_runtime_domain.assign_derived_resident_account_route(
            record,
            child,
            record,
            requested_account_id=backend_route_id,
        )
        resident_runtime_domain.complete_derived_resident_identity(
            record,
            child,
            actor_id,
            lineage_source or "fork:recent_context",
            now,
        )
        watchboard_child = {**record, **child}
        resident_watchboard_domain.inherit_watchboard(record, watchboard_child)
        if "watchboard" in watchboard_child:
            child["watchboard"] = watchboard_child["watchboard"]
        resident_session_backend_domain.stage_agent_data_fork(child, source.resident_id,
            segment_ids=[item["segment_id"] for item in segments])
        registry["residents"].append(child)
        child = _ensure_derived_resident_session(record, registry, child, actor_id)
    return _derived_resident_creation_result(record, child, actor_id)


def initialize_resident_context_from_source(
    record: dict[str, Any],
    source_resident_id: str,
    context_mode: str,
    actor: str,
    preparation_id: str = "",
) -> None:
    from . import (
        resident_directory_domain,
        resident_execution_domain,
        resident_lifecycle_domain,
        resident_record_domain,
        resident_runtime_domain,
        resident_session_backend_domain,
        resident_workspace_domain,
    )
    source_target = resident_workspace_domain.resolve_resident_runtime_target(
        source_resident_id,
        actor,
        require_visible=True,
    )
    source = source_target.record
    target_backend = resident_backend.execution_backend(record)
    mode = str(context_mode or "full").strip().lower()
    if mode not in {"full", "5", "10"}:
        raise ValueError("context mode 必须是 full、5 或 10")
    snapshot = _fork_provider_context_snapshot(source)
    if not snapshot:
        raise ValueError("Fork 来源缺少有效模型上下文；不能用 Chat 历史替代")
    turns = int(mode) if mode in {"5", "10"} else 0
    segments = resident_fork_context.select_segments(
        snapshot,
        recent_turns=turns,
    )
    resident_session_backend_domain.stage_agent_data_fork(record, resident_record_domain.record_resident_id(source),
        segment_ids=[item["segment_id"] for item in segments])
    definition = AGENT_PROVIDERS[target_backend]
    record.update({
        "source_resident_id": resident_record_domain.record_resident_id(source),
        "context_source_resident_id": resident_record_domain.record_resident_id(source),
        "forked_from_at": resident_lifecycle_domain.now_iso(),
    })
    record["fork_mode"] = "full" if mode == "full" else f"recent-{mode}"
    if not resident_backend.record_session_id(record) and "create" in definition.operations:
        account = resident_backend.execution_account(record)
        resident_session_backend_domain.run_backend_session_operation(
            backend=definition.provider,
            operation="create",
            actor=actor,
            cwd=resident_runtime_domain.resident_working_directory(record),
            binding_id=resident_backend.execution_binding_id(record),
            backend_route_id=str(account.get("backend_route_id") or ""),
            runtime_options=resident_execution_domain.resident_execution_runtime_options(
                record,
                {},
                is_owner=resident_directory_domain.has_resident_workspace_authority(record, actor),
            ),
            preparation_id=preparation_id,
        )
    if not resident_backend.record_session_id(record) and not definition.resume_without_session:
        raise ValueError(
            f"{definition.label} cannot create a fresh context-seeded session"
        )
    record["session_state"] = "active"
    record["session_created_via"] = "source_resident_derivation"
    record["updated_at"] = resident_lifecycle_domain.now_iso()
