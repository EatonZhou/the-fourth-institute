"""Resident domain responsibilities: management.

Rename, retitle, move identities, change state, reset context."""
from __future__ import annotations
from contextlib import ExitStack
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
import copy
import logging
import re
import secrets
from management_common.actor_identity import canonical_actor_id
from management_portal.portal_access_control import get_portal_access_store
from . import (
    identity_contract,
    invariant_locks,
    resident_backend,
    resident_catalog,
    resident_conversation_projection_domain,
    resident_conversation_stream,
    resident_domain_common,
    resident_execution_work,
    resident_input_queue_domain,
    resident_intake_domain,
    resident_lineage,
    resident_record_domain,
    resident_state,
)
from .domain_ports import resident_lineage_host_port
from .resident_access import ensure_resident_visible_to_actor


def derived_resident_title_is_generated(resident: Mapping[str, Any]) -> bool:
    origin = str(resident.get("title_origin") or "").strip().lower()
    if origin:
        return origin == "generated"
    return resident_conversation_projection_domain.is_generated_derived_resident_title(resident.get("title"))


def _commit_manual_resident_intake_state(
    record: dict[str, Any], next_state: str, *, allow_quota_override: bool,
) -> dict[str, Any]:
    """Commit a human choice under the caller's Resident lock and authorization."""
    policy = resident_record_domain.resident_quota_override(record)
    commit_configuration = bool(
        allow_quota_override or (next_state == resident_state.INTAKE_CLOSED and policy)
    )
    if commit_configuration:
        record["quota_reserve_override"] = {
            "binding_id": resident_backend.execution_binding_id(record),
            "enabled": next_state == resident_state.INTAKE_OPEN,
            "revision": int(policy.get("revision") or 0) + 1,
        }
    return resident_input_queue_domain.commit_resident_intake_state(
        record, next_state, commit_configuration=commit_configuration,
    )


def _change_resident_intake_state(
    resident_id: str,
    actor: str,
    intake_state: str,
    *,
    allow_quota_override: bool = True,
) -> dict[str, Any]:
    from . import resident_record_domain
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后切换 Agent 接单状态")
    next_state = str(intake_state or "").strip().lower()
    if next_state not in resident_state.INTAKE_VALUES:
        raise ValueError("intake_state 必须是 open 或 closed")
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident_canonical(resident_id)
        if not record:
            raise KeyError("resident not found")
        ensure_resident_visible_to_actor(record, actor_id)
        human_actor = not actor_id.startswith("resident_") and not get_portal_access_store().is_service_actor(actor_id)
        result = _commit_manual_resident_intake_state(
            record, next_state, allow_quota_override=human_actor and allow_quota_override,
        )
    result["quota_override_pending"] = not resident_input_queue_domain.sync_resident_quota_overrides([record])
    if next_state == resident_state.INTAKE_OPEN:
        from .lifecycle import notify_execution_binding
        notify_execution_binding(resident_id)
    return result


def transition_resident_state(
    resident_id: str,
    actor: str,
    *,
    state_domain: str = resident_state.INTAKE_DOMAIN,
    transition: str,
    allow_quota_override: bool = True,
) -> dict[str, Any]:
    from . import resident_record_domain
    domain = str(state_domain or "").strip().lower() or resident_state.INTAKE_DOMAIN
    if domain != resident_state.INTAKE_DOMAIN:
        raise ValueError(f"Resident state module 不拥有状态域：{domain}")
    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )
    result = _change_resident_intake_state(
        target.resident_id,
        actor,
        resident_state.intake_state_for_transition(transition),
        allow_quota_override=allow_quota_override,
    )
    result["resident_id"] = target.resident_id
    return dict(result)


def reset_resident_context(resident_id: str, actor: str) -> dict:
    """Commit a context reset and durably schedule its external cleanup."""
    from . import resident_domain_common, resident_lifecycle_domain, resident_record_domain

    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后清空上下文")
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record:
            raise KeyError("resident not found")
        if resident_execution_work.is_active(resident_execution_work.read(record)):
            raise ValueError("当前仍有任务正在排队或处理，停止或完成后再清空上下文")
        operations = resident_record_domain.lifecycle_operations()
        if operations.pending(resident_id):
            raise ValueError("Resident 生命周期操作正在完成，请稍后重试")
        now = resident_domain_common.now_iso()
        next_record = copy.deepcopy(record)
        next_record.update({"session_state": "ready", "last_error": "", "updated_at": now})
        # Provider sessions that ran before this time hold the cleared context:
        # a restart or a Fork must not hand any of it to a new session.
        next_record["context_reset_at"] = datetime.now(timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z")
        for key in (
            "last_runtime_error", "last_turn_usage", "last_turn_usage_at",
            "last_model_context_window", "session_created_via",
        ):
            next_record.pop(key, None)
        resident_record_domain.validated_resident_record_payload(next_record)
        archive_stamp = re.sub(r"[^0-9A-Za-z]+", "", now) + "_" + secrets.token_hex(6)
        repository = resident_conversation_stream.get_repository()
        steps = [{
            "kind": "context_archive", "resident_id": resident_id,
            "archived_at": now, "message_ids": repository.context_reset_boundary(resident_id),
            # The clear is also marked in the Resident's trace, after what it
            # held now (nothing runs during a clear), whatever arrives later.
            "reset_at": next_record["context_reset_at"], "actor": actor_id,
            "trace_position": repository.agent_data.trace_length(resident_id),
        }, resident_lifecycle_domain.resident_release_step(record, actor_id)]
        def commit() -> str:
            resident_record_domain.save_resident_record(next_record)
            resident_execution_work.clear(record)
            return operations.enqueue(resident_id, steps)
        operation_id = resident_catalog.get_catalog().state_store.transaction(commit)
    return resident_input_queue_domain.resident_operation_result(
        resident_id, reset=True, cleanup_pending=True, operation_id=operation_id,
    )


def rename_resident_title(
    resident_id: str,
    actor: str,
    title: str,
) -> dict[str, Any]:
    from . import resident_record_domain
    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )

    def commit() -> dict[str, Any]:
        result = _rename_resident_title_record(target.resident_id, actor, title)
        resident_lineage_host_port.commit_resident_title_projection(
            target.resident_id,
            str(result.get("title") or ""),
            actor,
        )
        return dict(result)

    with invariant_locks.lineage_scope(
        resident_lineage.lineage_id(target.record), operation="resident.rename",
    ), resident_record_domain.state_lock(resident_id):
        return resident_catalog.get_catalog().state_store.transaction(commit)


def _set_resident_title_locked(
    record: dict[str, Any],
    title: str,
    *,
    origin: str,
) -> dict[str, Any]:
    from . import resident_domain_common, resident_record_domain, resident_workspace_domain
    resident_intake_domain.require_no_pending_lifecycle(str(record.get("resident_id") or ""))
    previous_workspace_path = str(record.get("workspace_path") or "")
    record["title"] = title
    record["title_origin"] = origin
    record["updated_at"] = resident_domain_common.now_iso()
    resident_record_domain.apply_resident_resource_identity(record)
    resident_record_domain.require_resident_owned_workspace_binding(record)
    resident_workspace_domain.ensure_resident_workspace(record)
    resident_workspace_domain.refresh_resident_workspace_document(record)
    resident_record_domain.save_resident_record(record)
    return record


def adopt_generated_resident_title(
    resident_id: str,
    actor: str,
    title: str,
) -> bool:
    """Adopt a Lineage title while the exact Resident still has its generated name."""
    from . import resident_record_domain

    actor_id = canonical_actor_id(actor)
    next_title = str(title or "").strip()
    if not actor_id:
        raise ValueError("需要登录后同步 Resident 名称")
    if not next_title:
        raise ValueError("title required")
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record:
            raise KeyError("resident not found")
        if str(record.get("deleted_at") or "").strip():
            raise KeyError("resident not found")
        if not derived_resident_title_is_generated(record):
            return False
        _set_resident_title_locked(
            record,
            next_title,
            origin="lineage",
        )
    return True


def _rename_resident_title_record(resident_id: str, actor: str, title: str) -> dict:
    """Rename a Resident without changing its owners, workspace, or lineage."""
    from . import resident_record_domain
    actor_id = canonical_actor_id(actor)
    next_title = str(title or "").strip()
    if not actor_id:
        raise ValueError("需要登录后重命名 Resident")
    if not next_title:
        raise ValueError("title required")
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record:
            raise KeyError("resident not found")
        if (
            str(record.get("title") or "") == next_title
            and str(record.get("title_origin") or "") == "custom"
        ):
            return record
        if (record.get("family") or {}).get("role") == "facade":
            from . import family_storage
            retitled = resident_catalog.get_catalog().state_store.database.write(
                lambda connection: family_storage.rename_facade(connection, resident_id, next_title)
            )
            _publish_family_retitle(retitled)
        previous_title = str(record.get("title") or "")
        renamed = _set_resident_title_locked(
            record,
            next_title,
            origin="custom",
        )
        _announce_family_retitle(resident_id, previous_title, str(renamed.get("title") or ""), actor_id)
        return renamed


def _publish_family_retitle(resource_ids: list[str]) -> None:
    """Once the rename commits, the Families' descriptors carry the new title."""

    if not resource_ids:
        return

    def publish() -> None:
        from .resource_client import sync_families

        try:
            sync_families(resource_ids)
        except Exception:  # The reconciliation worker republishes from the domain.
            logging.getLogger(__name__).exception("Family descriptor publication for %s failed", resource_ids)

    invariant_locks.defer_until_state_unlock(publish)


def _announce_family_retitle(resident_id: str, previous: str, title: str, actor: str) -> None:
    """Once the rename commits, a Family member's steward hears its new title."""

    if not title or title == previous:
        return

    def announce() -> None:
        from .family_familize import announce_member_retitle

        try:
            announce_member_retitle(resident_id, previous, title, actor)
        except Exception:  # The rename stands; a lost note must not report it as failed.
            logging.getLogger(__name__).exception("Family retitle note for %s failed", resident_id)

    invariant_locks.defer_until_state_unlock(announce)


def rename_resident_identity(resident_id: str, actor: str, title: str) -> dict:
    """Rename Resident-owned state while the caller owns the graph transition."""

    return _rename_resident_title_record(resident_id, actor, title)


def move_resident_identities(
    source_id: str, target_id: str, resident_ids: list[str], actor: str,
    commit_graphs: Callable[[dict[str, dict[str, Any]]], None],
) -> list[dict[str, Any]]:
    """Publish a batch membership transition and workspace intents in one transaction.

    The Lineage owner holds both graph scopes; this owner takes sorted Resident
    scopes and supplies exact new identity projections to the graph callback.
    """
    from .resident_projection import _resident_lineage_node_projection
    from . import resident_record_domain, resident_workspace_domain
    with ExitStack() as stack:
        for resident_id in sorted(resident_ids):
            stack.enter_context(resident_record_domain.state_lock(resident_id))
        prepared = []
        for resident_id in sorted(resident_ids):
            record = copy.deepcopy(resident_record_domain.find_resident(resident_id))
            if not record or resident_lineage.lineage_id(record) != source_id:
                raise ValueError("Resident membership changed; refresh and retry")
            if resident_record_domain.lifecycle_operations().pending(resident_id):
                raise ValueError("Resident 生命周期操作正在完成，请稍后重试")
            previous_path = str(record.get("workspace_path") or "")
            transfer = resident_workspace_domain.prepare_resident_lineage_move(record, target_id, actor)
            resident_record_domain.set_resident_owned_workspace_binding(record)
            prepared.append((record, transfer, previous_path))

        def commit():
            from .lineage_definition_scope import check_before_membership_write
            check_before_membership_write(resident_catalog.get_catalog().state_store, source_id)
            projections = {}
            operations = []
            for record, transfer, previous_path in prepared:
                resident_id = record["resident_id"]
                resident_record_domain.apply_resident_resource_identity(record)
                resident_record_domain.save_resident_record(record)
                projections[resident_id] = _resident_lineage_node_projection(record)
                operation_id = resident_workspace_domain.enqueue_resident_lineage_move(record, transfer, source_id, previous_path)
                operations.append({"resident_id": resident_id, "operation_id": operation_id})
            commit_graphs(projections)
            return operations

        return resident_catalog.get_catalog().state_store.membership_transaction(commit)


def update_resident(
    resident_id: str,
    actor: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from . import resident_record_domain, resident_workspace_domain
    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )

    def commit() -> dict[str, Any]:
        previous_title = str(target.record.get("title") or "")
        result = resident_workspace_domain.update_resident_record(target.resident_id, payload, actor)
        next_title = str(result.get("title") or "")
        if next_title != previous_title:
            resident_lineage_host_port.commit_resident_title_projection(
                target.resident_id,
                next_title,
                actor,
            )
            _announce_family_retitle(target.resident_id, previous_title, next_title, actor)
        canonical = dict(result)
        canonical["resident_id"] = target.resident_id
        return canonical

    graph_ids = [resident_lineage.lineage_id(target.record)]
    destination_lineage = str(payload.get("target_lineage_id") or "").strip()
    if destination_lineage:
        identity_contract.validate_id(destination_lineage, "lineage")
        graph_ids.append(destination_lineage)
    with invariant_locks.lineage_scopes(
        graph_ids, operation="resident.update",
    ), resident_record_domain.state_lock(resident_id):
        current = resident_record_domain.find_resident(target.resident_id)
        if not current or resident_lineage.lineage_id(current) != graph_ids[0]:
            raise ValueError("Resident membership changed; retry the update")
        return resident_catalog.get_catalog().state_store.transaction(commit)
