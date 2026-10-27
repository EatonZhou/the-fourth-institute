"""Resident domain responsibilities: execution."""
from __future__ import annotations

from . import invariant_locks, resident_fork_context, resident_restart_handoff, resident_store


from .errors import LineageConflictError

from . import (
    resident_backend,
    resident_catalog,
    resident_conversation_stream,
    resident_execution_provider,
    resident_held_infos,
    resident_input_scope,
    resident_execution_work,
    resident_lineage,
    resident_input_recovery,
    resident_message_contract,
)
from .domain_ports import execution_port, resident_terminal_port
from .resident_access import manage_refusal
from .resident_fork_context import (
    initial_context_compaction_completed as _initial_context_compaction_completed,
    requires_initial_context_compaction as _requires_initial_context_compaction,
)
from .resident_runtime_payload import runtime_settings_updates_from_payload as _runtime_settings_updates_from_payload
from dataclasses import replace
from management_common.actor_identity import canonical_actor_id
from management_portal.execution import event_projection as execution_events
from management_portal.society_service.resident_execution_work import bind_execution, execution_status
from management_portal.execution.contracts import ExecutionRequest, InputMedia
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.execution.requests import (
    build_agent_turn_request,
    build_chat_completion_request,
)
from typing import Any, Callable, Mapping
import copy
from management_portal.execution import resident_runtime_settings, slash_commands
from management_portal.execution.harness_settings import normalize_agent_harness_settings
import sys
from contextlib import contextmanager

from . import resident_domain_common

RESIDENT_PROJECTION_QUARANTINE_LIMIT = 64


def read_resident_runtime_settings(
    resident_id: str,
    actor: str = "",
) -> dict[str, Any]:
    from . import resident_access, resident_record_domain, resident_runtime_domain
    with resident_lineage.access_snapshot_scope():
        target = resident_record_domain.resolve_resident_runtime_target(
            resident_id,
            actor,
            require_visible=True,
        )
        record = target.record
        actor_id = canonical_actor_id(actor)
        owner = resident_access.has_resident_workspace_authority(
            record,
            actor_id,
        )
        if not owner:
            raise PermissionError("无权读取该 Resident 的 runtime 设置")
        runtime_settings = resident_runtime_domain.read_resident_runtime_settings(record)
        default_runtime_settings = dict(runtime_settings)
        if not owner:
            default_runtime_settings = resident_runtime_settings.read_only_runtime_settings(
                default_runtime_settings
            )
        return {
            "resident_id": target.resident_id,
            "runtime_settings": runtime_settings,
            "default_runtime_settings": default_runtime_settings,
        }


def change_resident_runtime_settings(resident_id: str, actor: str = "", payload: object = None) -> dict:
    from . import (
        resident_access,
        resident_domain_common,
        resident_intake_domain,
        resident_record_domain,
        resident_runtime_domain,
    )
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后修改 runtime 设置")
    data = payload if isinstance(payload, dict) else {}
    raw_changes = dict(data.get("runtime_settings") if "runtime_settings" in data else data)
    requested_route = raw_changes.pop("backend_route_id", None)
    if requested_route is not None and (not isinstance(requested_route, str) or not requested_route.strip()):
        raise ValueError("backend_route_id must name an enabled Model API account")
    requested_updates = _runtime_settings_updates_from_payload(
        raw_changes,
        include_blank=True,
    )
    if not requested_updates and requested_route is None:
        raise ValueError("runtime_settings required")

    for _attempt in range(3):
        with resident_record_domain.state_lock(resident_id):
            target = resident_record_domain.resolve_resident_runtime_target(
                resident_id,
                actor_id,
                require_visible=True,
            )
            record = target.record
            if not resident_access.has_resident_workspace_authority(record, actor_id):
                raise manage_refusal(record)
            validation_record = copy.deepcopy(record)
            validation_current = resident_runtime_domain.read_resident_runtime_settings(record)
            validation_key = runtime_settings_validation_key(record)

        # Provider catalogs come from ExecutionService and can perform I/O.
        # Keep that read outside the Resident invariant lock, then commit only
        # if both the selected Provider route and settings still match.
        validation_updates = dict(requested_updates)
        if requested_route is not None:
            if resident_backend.execution_backend(validation_record) != "provider_api":
                raise ValueError("backend_route_id settings are only supported by Model API")
            validation_record["backend_route_id"] = requested_route.strip()
            validation_updates.setdefault("model", validation_current.get("model", ""))
        validated_updates = resident_runtime_domain.validate_backend_runtime_updates(
            validation_record,
            validation_current,
            validation_updates,
        )

        with resident_record_domain.state_lock(resident_id):
            target = resident_record_domain.resolve_resident_runtime_target(
                resident_id,
                actor_id,
                require_visible=True,
            )
            record = target.record
            if not resident_access.has_resident_workspace_authority(record, actor_id):
                raise manage_refusal(record)
            current = resident_runtime_domain.read_resident_runtime_settings(record)
            if (
                current != validation_current
                or runtime_settings_validation_key(record) != validation_key
            ):
                continue

            now = resident_domain_common.now_iso()
            next_settings = resident_runtime_settings.merge_settings(
                current,
                validated_updates,
                actor=actor_id,
                updated_at=now,
            )
            next_settings = resident_runtime_settings.mark_portal_manual_authority(
                next_settings,
                actor=actor_id,
                updated_at=now,
            )
            changed_settings = {
                key: next_settings.get(key)
                for key in validated_updates
                if current.get(key) != next_settings.get(key)
            }
            route_changed = requested_route is not None and requested_route.strip() != str(record.get("backend_route_id") or "")
            if route_changed:
                changed_settings["backend_route_id"] = requested_route.strip()
            if not changed_settings:
                result = read_resident_runtime_settings(target.resident_id, actor_id)
                result["active_turn_settings_update"] = {"requested": False, "status": "unchanged"}
                result["active_turn_fields"] = []
                result["next_turn_fields"] = []
                return result
            next_turn_fields = sorted(changed_settings)
            def commit_settings():
                if route_changed:
                    record["backend_route_id"] = requested_route.strip()
                resident_runtime_domain.write_resident_runtime_settings(record, next_settings)
                record["updated_at"] = now
                if route_changed:
                    resident_record_domain.save_resident_record(record, commit_configuration=True)
                else:
                    resident_record_domain.save_resident_record(record)
            resident_catalog.get_catalog().state_store.transaction(commit_settings)
            if "service_tier" in changed_settings:
                try:
                    active_turn_settings_update = resident_intake_domain.submit_active_turn_settings(
                        record,
                        actor_id,
                        control_key=(
                            f"runtime-settings:{now}:"
                            f"{changed_settings['service_tier'] or 'default'}"
                        ),
                        service_tier=str(changed_settings["service_tier"] or ""),
                        source="runtime_settings",
                    )
                except Exception as exc:
                    active_turn_settings_update = {
                        "requested": True,
                        "status": "failed",
                        "error": str(exc),
                    }
            else:
                active_turn_settings_update = {
                    "requested": False,
                    "status": "next_turn" if changed_settings else "unchanged",
                }
            result = read_resident_runtime_settings(target.resident_id, actor_id)
            result["active_turn_settings_update"] = active_turn_settings_update
            result["active_turn_fields"] = (
                ["service_tier"]
                if active_turn_settings_update.get("status") in {"pending", "delivered"}
                else []
            )
            result["next_turn_fields"] = [
                name for name in next_turn_fields if name not in result["active_turn_fields"]
            ]
            return result

    raise LineageConflictError(
        "Resident runtime settings changed during Provider validation",
        resource="resident_runtime_settings",
    )


@resident_lineage.access_snapshot_scope()
def deliver_resident_message_content(
    resident_id: str,
    actor: str,
    content: str,
    context_refs: object = None,
    working_directory: str = "",
    input_media: object = None,
    **options: Any,
) -> dict[str, Any]:
    """Send to one exact Resident; genealogy never participates in routing."""
    from . import resident_intake_domain, resident_record_domain

    target = resident_record_domain.resolve_resident_admission_target(
        resident_id,
        actor,
        require_visible=not resident_input_scope.permits(resident_id, actor, options.get("message_id", "")),
        delivery=True,
    )
    result = resident_intake_domain.persist_resident_input(
        target.resident_id,
        actor,
        content,
        context_refs,
        working_directory,
        input_media=input_media,
        **options,
    )
    return dict(result)


def _lineage_terminal_fact_fields(job: dict[str, Any]) -> dict[str, str]:
    return {
        "lineage_id": str(job.get("lineage_id") or "").strip(),
        "business_ref": str(job.get("business_ref") or "").strip()[:500],
    }


def _notify_lineage_message_completed(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    completed_at: str = "",
    lineage_id: str = "",
    business_ref: str = "",
) -> None:
    """Queue one durable completion fact after the Conversation commit."""
    if str(message_id or "").strip():
        from . import lineage_program_runtime
        lineage_program_runtime.cleanup_delivery_profiles(lineage_id=lineage_id, message_id=message_id)
    if not str(actor or "").strip() or not str(message_id or "").strip():
        return
    resident_terminal_port.report(
        resident_id,
        actor,
        message_id,
        completed_at=completed_at,
        lineage_id=lineage_id,
        business_ref=business_ref,
    )


def _notify_lineage_message_terminal(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    terminal_status: str,
    completed_at: str = "",
    lineage_id: str = "",
    business_ref: str = "",
) -> None:
    """Notify Lineage after a non-success Resident terminal is durable."""

    status = str(terminal_status or "").strip().lower()
    status = {
        "canceled": "cancelled",
    }.get(status, status)
    if status == "completed":
        _notify_lineage_message_completed(
            resident_id,
            actor,
            message_id,
            completed_at=completed_at,
            lineage_id=lineage_id,
            business_ref=business_ref,
        )
        return
    if status in {"failed", "cancelled"} and str(message_id or "").strip():
        from . import lineage_program_runtime
        lineage_program_runtime.cleanup_delivery_profiles(lineage_id=lineage_id, message_id=message_id)
    if (
        status not in {"failed", "cancelled"}
        or not str(actor or "").strip()
        or not str(message_id or "").strip()
    ):
        return

    resident_terminal_port.report(
        resident_id,
        actor,
        message_id,
        completed_at=completed_at,
        terminal_status=status,
        lineage_id=lineage_id,
        business_ref=business_ref,
    )


def _lineage_notifiable_execution(job: dict[str, Any]) -> bool:
    """Exclude compact/create lifecycle jobs from work-turn terminal facts."""

    return bool(str(job.get("execution_id") or "").strip()) and str(
        job.get("operation") or ""
    ) not in {
        "backend_compact",
        "context_fork_create",
    }


def _completion_applied(job: dict[str, Any]) -> bool:
    return resident_execution_work.completion_applied(job)


def _mark_completion_applied(job: dict[str, Any], timestamp: str) -> None:
    from . import resident_domain_common
    job["completion_applied_at"] = str(timestamp or resident_domain_common.now_iso())
    job["completion_applied_execution_id"] = resident_execution_work.submitted_execution_id(job)


def _finalize_applied_execution(
    resident_id: str,
    continuation_id: str,
    current_job: dict[str, Any],
) -> bool:
    """Ack one already-applied result and return whether its queue advanced."""
    from . import resident_domain_common, resident_intake_domain, resident_record_domain

    if not resident_intake_domain.reconcile_queued_turn_inputs(
        resident_id, str(current_job.get("execution_id") or ""), turn_finished=True,
    ):
        return False
    if not resident_intake_domain.acknowledge_execution(current_job):
        return False
    record = resident_record_domain.find_resident(resident_id) or {}
    if not record:
        return False
    session_state = resident_record_domain.record_backend_session_state(record)
    with resident_record_domain.state_lock(resident_id):
        job = resident_execution_work.read(record)
        if not resident_execution_work.matches_continuation(job, continuation_id):
            return False
        if str(job.get("execution_id") or "") != str(current_job.get("execution_id") or ""):
            # Promotion keeps the continuation; a second finalizer of the same
            # completion must not clear the job another runner already advanced.
            return False
        now = resident_domain_common.now_iso()
        queue = resident_execution_work.waiting_input_queue(job)
        if queue:
            next_job = queue.pop(0)
            resident_execution_work.promote_queue_item(
                job,
                next_job,
                queue,
                now,
                retain_continuation=True,
            )
            record["session_state"] = session_state
            resident_execution_work.write(record, job)
            resident_record_domain.save_resident_record(record)
            return True
        resident_execution_work.clear(record)
        return False


def apply_cancelled_continuation(
    resident_id: str,
    continuation_id: str,
    *,
    usage: Mapping[str, Any] | None = None,
) -> bool:
    """Persist a confirmed cancellation before advancing the Resident queue."""
    from . import (
        resident_domain_common,
        resident_intake_domain,
        resident_lifecycle_domain,
        resident_record_domain,
    )

    # Workspace/session reads and Conversation IO are outside the Resident
    # command lock. A confirmed old result must never settle its replacement.
    record = resident_record_domain.find_resident(resident_id) or {}
    if not record:
        return False
    repository = resident_conversation_stream.get_repository()
    terminal_job: dict[str, Any] = {}
    with resident_record_domain.state_lock(resident_id):
        job = resident_execution_work.read(record)
        if not resident_execution_work.matches_continuation(job, continuation_id):
            return False
        now = str(job.get("stop_requested_at") or resident_domain_common.now_iso())
        job.update({
            "error": "",
            "execution_terminal_status": "cancelled",
            "stopped_by": str(job.get("stop_requested_by") or ""),
            "stopped_at": now,
            "updated_at": now,
        })
        job.pop("runtime_error", None)
        user_message_id = str(job.get("user_message_id") or "")
        expected_execution_id = str(job.get("execution_id") or "")
        terminal_fields = {key: job[key] for key in (
            "error", "execution_terminal_status", "stopped_by", "stopped_at", "updated_at",
        )}
    def same_execution_input(current):
        return (str(current.get("execution_id") or "") == expected_execution_id
                and str(current.get("user_message_id") or "") == user_message_id)
    started = not user_message_id or repository.agent_data.chat_message(resident_id, user_message_id) is not None
    if started:
        notice = resident_intake_domain.resident_execution_stopped_event(
            # Submission identity is not evidence of a Stop request. Provider
            # or infrastructure cancellation may have no recorded operator.
            str(job.get("stopped_by") or ""), job,
            queue_continues=bool(resident_execution_work.waiting_input_queue(job)),
            backend_label=resident_backend.backend_label(record),
        )
        with resident_record_domain.state_lock(resident_id):
            current = _read_resident_execution_control(resident_id)
            if not same_execution_input(current):
                return False
            if not resident_execution_work.authorize_conversation_projection(
                record, execution_id=expected_execution_id, message=notice,
            ):
                return False
        resident_lifecycle_domain.append_conversation_message(resident_id, notice)
    # The input fact refers to this exact completed execution. Its terminal
    # event precedes idle/queue promotion; retries reuse the stable stop notice.
    with resident_record_domain.state_lock(resident_id):
        current = _read_resident_execution_control(resident_id)
        if not same_execution_input(current):
            return False
    if user_message_id:
        repository.set_input_state(resident_id, user_message_id, "cancelled", updated_at=now)
    # Bindings may have changed during projection. Resolve before taking the
    # short merge scope, then re-read the Job to preserve new queue arrivals.
    record = resident_record_domain.find_resident(resident_id) or {}
    if not record:
        return False
    session_state = "active" if resident_backend.record_session_id(record) else str(record.get("session_state") or "")
    with resident_record_domain.state_lock(resident_id):
        job = resident_execution_work.read(record)
        if not resident_execution_work.matches_continuation(job, continuation_id) or not same_execution_input(job):
            return False
        if started:
            resident_execution_work.complete_conversation_projection(
                record, execution_id=expected_execution_id, message_id=notice["message_id"],
            )
            job = resident_execution_work.read(record)
        job.update(terminal_fields)
        job.pop("runtime_error", None)
        _mark_completion_applied(job, now)
        record["session_state"] = session_state
        record["last_error"] = ""
        record.pop("last_runtime_error", None)
        if usage:
            record["last_turn_usage"] = dict(usage)
            record["last_turn_usage_at"] = now
        resident_execution_work.write(record, job)
        record["updated_at"] = now
        resident_record_domain.save_resident_record(record)
        terminal_job = {
            **job,
            "outcome": "cancelled",
            "finished_at": now,
        }

    if _lineage_notifiable_execution(terminal_job):
        _notify_lineage_message_terminal(
            resident_id,
            str(terminal_job.get("actor") or ""),
            str(terminal_job.get("user_message_id") or ""),
            terminal_status="cancelled",
            completed_at=str(terminal_job.get("finished_at") or ""),
            **_lineage_terminal_fact_fields(terminal_job),
        )
    return _finalize_applied_execution(
        resident_id,
        continuation_id,
        terminal_job,
    )


def run_resident_execution(resident_id: str, continuation_id: str) -> None:
    with resident_state_scope(resident_id, "resident.job"):
        _run_resident_execution_scoped(resident_id, continuation_id)


def _run_resident_execution_scoped(resident_id: str, continuation_id: str) -> None:
    from . import (
        resident_domain_common,
        resident_intake_domain,
        resident_lifecycle_domain,
        resident_record_domain,
        resident_session_backend_domain,
    )
    terminal_applied_job: dict[str, Any] = {}
    try:
        while True:
            current_job: dict[str, Any] = {}
            record_snapshot: dict[str, Any] = {}
            try:
                with resident_record_domain.state_lock(resident_id):
                    record = resident_record_domain.find_resident(resident_id) or {}
                    if not record:
                        return
                    job = resident_execution_work.read(record)
                    if not resident_execution_work.matches_continuation(job, continuation_id):
                        return
                    if resident_restart_handoff.pending(record, job):
                        return
                    from .resident_default_backend import bind_before_execution
                    bind_before_execution(record, str(job.get("actor") or ""))
                    job = resident_execution_work.read(record)
                    dispatched_at = str(job.get("dispatched_at") or resident_domain_common.now_iso())
                    job["dispatched_at"] = dispatched_at
                    current_job = dict(job)
                    record_snapshot = dict(record)

                from .family_quota import ensure_role_reserve
                from . import resident_domain_common, resident_lifecycle_domain, resident_record_domain
                resident_record_domain.ensure_resident_agent(record_snapshot, actor=str(current_job.get("actor") or ""))
                if ensure_role_reserve(record_snapshot):
                    return

                if str(current_job.get("operation") or "") in {
                    "context_fork_create",
                }:
                    existing_session_id = resident_backend.record_session_id(
                        record_snapshot
                    )
                    recovery_result = None
                    if current_job.get('initialization_recovery'):
                        from .resident_initialization import run as recover_initialization
                        recovery_result = recover_initialization(record_snapshot, current_job, continuation_id)
                        thread = recovery_result['thread']
                    elif (
                        _requires_initial_context_compaction(record_snapshot)
                        and existing_session_id
                    ):
                        if not current_job.get('initial_compaction_operation_id'):
                            raise RuntimeError('已有会话的首轮压缩状态未知；请使用 RetryResidentInitialization 恢复')
                        existing_session = resident_backend.session_record_for_binding(
                            resident_backend.execution_binding_id(record_snapshot)
                        )
                        thread = {
                            "id": existing_session_id,
                            "sessionId": existing_session_id,
                            "backend_route_id": str(
                                existing_session.get("backend_route_id")
                                or record_snapshot.get("backend_route_id")
                                or ""
                            ),
                        }
                    else:
                        thread = resident_session_backend_domain.create_exact_resident_session(
                            record_snapshot,
                            current_job,
                        )
                    if not resident_backend.session_id(
                        thread.get("sessionId") or thread.get("id")
                    ):
                        raise RuntimeError(
                            f"{resident_backend.backend_label(record_snapshot)} "
                            "Resident creation returned no session id"
                        )

                    initial_compaction: dict[str, Any] = {}
                    if recovery_result is not None:
                        initial_compaction = recovery_result
                    elif _requires_initial_context_compaction(record_snapshot):
                        initial_compaction = _compact_inherited_resident_context(
                            record_snapshot,
                            current_job,
                            thread,
                            continuation_id=continuation_id,
                        )

                    applied_creation_job: dict[str, Any] = {}
                    with resident_record_domain.state_lock(resident_id):
                        record = resident_record_domain.find_resident(resident_id) or {}
                        if not record:
                            return
                        job = resident_execution_work.read(record)
                        if not resident_execution_work.matches_continuation(
                            job,
                            continuation_id,
                        ):
                            return
                        now = resident_domain_common.now_iso()
                        record.update({
                            "session_state": "active",
                            "last_error": "",
                            "updated_at": now,
                        })
                        record.pop("last_runtime_error", None)
                        record["backend_route_id"] = str(thread.get("backend_route_id") or record.get("backend_route_id") or "")
                        record["session_created_via"] = "execution_service"
                        if _requires_initial_context_compaction(record):
                            compacted_at = resident_domain_common.now_iso()
                            record.update({
                                "context_compaction_state": "completed",
                                "context_compacted_at": compacted_at,
                            })
                            record = _with_compacted_context_usage(
                                record,
                                initial_compaction.get("usage")
                                if isinstance(initial_compaction.get("usage"), dict)
                                else {},
                            )
                        _mark_completion_applied(job, now)
                        resident_execution_work.write(record, job)
                        resident_record_domain.save_resident_record(record, commit_configuration=True)
                        applied_creation_job = dict(job)

                    if _finalize_applied_execution(
                        resident_id,
                        continuation_id,
                        applied_creation_job,
                    ):
                        continue
                    return

                if _completion_applied(current_job):
                    if _finalize_applied_execution(
                        resident_id,
                        continuation_id,
                        current_job,
                    ):
                        continue
                    return


                if (
                    current_job.get("bootstrap_session")
                    and not resident_backend.record_session_id(record_snapshot)
                ):
                    job_actor = resident_execution_work.request_actor(current_job)
                    completed_bootstrap_job = current_job
                    updated_record = complete_bootstrap_codex_session(
                        record_snapshot,
                        current_job,
                    )
                    with resident_record_domain.state_lock(resident_id):
                        record = resident_record_domain.find_resident(resident_id) or {}
                        if not record:
                            return
                        update_keys = [
                            "session_created_via",
                            "session_state",
                            "last_token_usage",
                            "last_token_usage_at",
                            "last_turn_usage",
                            "last_turn_usage_at",
                            "last_model_context_window",
                            "last_error",
                            "updated_at",
                        ]
                        for key in update_keys:
                            if key in updated_record:
                                record[key] = updated_record[key]
                        record.pop("last_runtime_error", None)
                        resident_record_domain.save_resident_record(record)
                        persisted_job = resident_execution_work.read(record)
                        applied = str(persisted_job.get("execution_id") or "") == str(
                            completed_bootstrap_job.get("execution_id") or ""
                        )
                        if applied:
                            _mark_completion_applied(
                                persisted_job,
                                str(updated_record.get("updated_at") or resident_domain_common.now_iso()),
                            )
                            resident_execution_work.write(record, persisted_job)
                            completed_bootstrap_job = dict(persisted_job)
                    if not applied:
                        # Another command replaced the Job during the turn.
                        resident_intake_domain.acknowledge_execution(completed_bootstrap_job)
                        return
                    # Messages that arrived during the first turn wait in its
                    # queue; the shared finalizer starts the next one.
                    queue_advanced = _finalize_applied_execution(
                        resident_id,
                        continuation_id,
                        completed_bootstrap_job,
                    )
                    if _lineage_notifiable_execution(
                        completed_bootstrap_job
                    ):
                        _notify_lineage_message_completed(
                            resident_id,
                            str(
                                completed_bootstrap_job.get("actor")
                                or job_actor
                            ),
                            str(
                                completed_bootstrap_job.get("user_message_id")
                                or ""
                            ),
                            completed_at=str(
                                updated_record.get("updated_at")
                                or resident_domain_common.now_iso()
                            ),
                            **_lineage_terminal_fact_fields(
                                completed_bootstrap_job
                            ),
                        )
                    if queue_advanced:
                        continue
                    return

                if resident_execution_work.is_compact(current_job):
                    scope_label = str(current_job.get("compact_scope_label") or "Resident Session")
                    compact_updated, compact_detail = _compact_resident_backend(record_snapshot, current_job)
                    with resident_record_domain.state_lock(resident_id):
                        record = resident_record_domain.find_resident(resident_id) or {}
                        if not record:
                            return
                        job = resident_execution_work.read(record)
                        if not resident_execution_work.matches_continuation(job, continuation_id):
                            return
                        now = resident_domain_common.now_iso()
                        resident_lifecycle_domain.append_conversation_message(
                            resident_domain_common.record_resident_id(record),
                            resident_intake_domain.compact_portal_command_message(
                                resident_session_backend_domain.compact_portal_result(
                                    "completed",
                                    scope_label,
                                    backend_label=resident_backend.backend_label(record),
                                    detail=compact_detail,
                                ),
                                str(current_job.get("actor") or ""),
                                reply_to_message_id=str(
                                    current_job.get("user_message_id") or ""
                                ),
                            ),
                        )
                        resident_conversation_stream.get_repository().set_input_state(
                            resident_domain_common.record_resident_id(record),
                            str(current_job.get("user_message_id") or ""),
                            "completed",
                        )
                        for key in (
                            "last_token_usage",
                            "last_token_usage_at",
                            "last_model_context_window",
                        ):
                            if key in compact_updated:
                                record[key] = compact_updated[key]
                        record["updated_at"] = now
                        resident_record_domain.save_resident_record(record)
                        _mark_completion_applied(job, now)
                        resident_execution_work.write(record, job)

                    if not resident_intake_domain.acknowledge_execution(current_job):
                        return

                    with resident_record_domain.state_lock(resident_id):
                        record = resident_record_domain.find_resident(resident_id) or {}
                        if not record:
                            return
                        job = resident_execution_work.read(record)
                        if not resident_execution_work.matches_continuation(
                            job,
                            continuation_id,
                        ) or str(job.get("execution_id") or "") != str(current_job.get("execution_id") or ""):
                            return
                        now = resident_domain_common.now_iso()
                        queue = resident_execution_work.waiting_input_queue(job)
                        if queue:
                            next_job = queue.pop(0)
                            resident_execution_work.promote_queue_item(
                                job,
                                next_job,
                                queue,
                                now,
                                retain_continuation=True,
                            )
                            record["session_state"] = resident_record_domain.record_backend_session_state(record)
                            resident_execution_work.write(record, job)
                            resident_record_domain.save_resident_record(record)
                            continue
                        record["session_state"] = "active"
                        record["last_error"] = ""
                        record["updated_at"] = now
                        resident_record_domain.save_resident_record(record)
                        resident_execution_work.clear(record)
                    return

                assistant_item, updated_record = send_to_resident_backend(
                    record_snapshot,
                    current_job,
                )

                with resident_record_domain.state_lock(resident_id):
                    record = resident_record_domain.find_resident(resident_id) or {}
                    if not record:
                        return
                    job = resident_execution_work.read(record)
                    if not resident_execution_work.matches_continuation(job, continuation_id):
                        return
                    for key in (
                        "session_state",
                        "last_token_usage",
                        "last_token_usage_at",
                        "last_turn_usage",
                        "last_turn_usage_at",
                        "last_model_context_window",
                        "last_error",
                    ):
                        if key in updated_record:
                            record[key] = updated_record[key]
                    now = resident_domain_common.now_iso()
                    record["session_state"] = "active"
                    record["updated_at"] = now
                    resident_record_domain.save_resident_record(record)
                    _mark_completion_applied(job, now)
                    resident_execution_work.write(record, job)
                    # Delivery may have submitted a new Execution through its
                    # own job copy. Finalize the persisted binding, not the
                    # pre-delivery snapshot which can still lack its ID.
                    current_job = dict(job)

                queue_advanced = _finalize_applied_execution(
                    resident_id,
                    continuation_id,
                    current_job,
                )
                _notify_lineage_message_completed(
                    resident_id,
                    str(
                        current_job.get("actor")
                        or record_snapshot.get("created_by")
                        or ""
                    ),
                    str(current_job.get("user_message_id") or ""),
                    completed_at=str(
                        assistant_item.get("created_at") or now
                    ),
                    **_lineage_terminal_fact_fields(current_job),
                )
                if queue_advanced:
                    continue
                return
            except resident_restart_handoff.ExecutionHandedOff:
                return
            except resident_domain_common.ResidentTurnCancelled as exc:
                if apply_cancelled_continuation(
                    resident_id,
                    continuation_id,
                    usage=exc.usage,
                ):
                    continue
                return
            except Exception as exc:
                if not resident_intake_domain.reconcile_queued_turn_inputs(
                    resident_id, str(current_job.get("execution_id") or ""), turn_finished=True,
                ):
                    return
                error_message = resident_backend_failure_message(record_snapshot, str(exc))
                with resident_record_domain.state_lock(resident_id):
                    try:
                        record = resident_record_domain.find_resident(resident_id) or {}
                        if record:
                            job = resident_execution_work.read(record)
                            if not resident_execution_work.matches_continuation(job, continuation_id):
                                return
                            now = resident_domain_common.now_iso()
                            runtime_error = resident_record_domain.runtime_error_payload(
                                error_message,
                                code=getattr(exc, "error_code", ""),
                                details=getattr(exc, "error_details", None),
                            )
                            failed_usage = getattr(exc, "usage", None)
                            resident_intake_domain.persist_failed_execution_message(
                                resident_id,
                                job,
                                error_message,
                                runtime_error,
                                failed_at=now,
                            )
                            if failed_usage:
                                record["last_turn_usage"] = dict(failed_usage)
                                record["last_turn_usage_at"] = now
                            terminal_applied_job = dict(job)
                            if resident_execution_work.is_compact(current_job):
                                scope_label = str(current_job.get("compact_scope_label") or "Resident Session")
                                resident_lifecycle_domain.append_conversation_message(
                                    resident_domain_common.record_resident_id(record),
                                    resident_intake_domain.compact_portal_command_message(
                                        resident_session_backend_domain.compact_portal_result(
                                            "failed",
                                            scope_label,
                                            error_message,
                                            backend_label=resident_backend.backend_label(record),
                                        ),
                                        str(current_job.get("actor") or ""),
                                        reply_to_message_id=str(
                                            current_job.get("user_message_id") or ""
                                        ),
                                    ),
                                )
                                resident_conversation_stream.get_repository().set_input_state(
                                    resident_domain_common.record_resident_id(record),
                                    str(current_job.get("user_message_id") or ""),
                                    "failed",
                                    error=runtime_error,
                                )
                            if str(current_job.get("operation") or "") not in {
                                "context_fork_create",
                            } and resident_intake_domain.promote_queue_after_failed_execution(
                                job,
                                resident_id,
                                now,
                                error_message,
                                runtime_error,
                            ):
                                record["session_state"] = resident_record_domain.record_backend_session_state(record)
                                record["last_error"] = ""
                                record.pop("last_runtime_error", None)
                                record["updated_at"] = now
                                resident_execution_work.write(record, job)
                                resident_record_domain.save_resident_record(record)
                                if _lineage_notifiable_execution(
                                    current_job
                                ):
                                    _notify_lineage_message_terminal(
                                        resident_id,
                                        str(
                                            current_job.get("actor")
                                            or record.get("created_by")
                                            or ""
                                        ),
                                        str(current_job.get("user_message_id") or ""),
                                        terminal_status="failed",
                                        completed_at=now,
                                        **_lineage_terminal_fact_fields(
                                            current_job
                                        ),
                                    )
                                return
                            record["session_state"] = (
                                "failed" if current_job.get('operation') == 'context_fork_create'
                                else "active" if resident_backend.record_session_id(record) else "failed"
                            )
                            record["last_error"] = error_message[:1000]
                            record["last_runtime_error"] = runtime_error
                            record["updated_at"] = now
                            job.update({
                                "outcome": "failed",
                                "error": error_message[:1000],
                                "runtime_error": runtime_error,
                                "updated_at": now,
                                "finished_at": now,
                            })
                            resident_execution_work.write(record, job)
                            resident_record_domain.save_resident_record(record)
                            if _lineage_notifiable_execution(current_job):
                                _notify_lineage_message_terminal(
                                    resident_id,
                                    str(
                                        current_job.get("actor")
                                        or record.get("created_by")
                                        or ""
                                    ),
                                    str(current_job.get("user_message_id") or ""),
                                    terminal_status="failed",
                                    completed_at=now,
                                    **_lineage_terminal_fact_fields(
                                        current_job
                                    ),
                                )
                    except Exception as recovery_exc:
                        print(
                            "Resident continuation recovery failed: "
                            f"{type(recovery_exc).__name__}: {recovery_exc}",
                            file=sys.stderr,
                        )
                return
    finally:
        if terminal_applied_job:
            resident_intake_domain.acknowledge_execution(terminal_applied_job)
        resident_intake_domain.finish_resident_continuation(resident_id)


def _active_resident_continuations() -> int:
    return resident_execution_work.active_continuation_count()


def _prepare_resident_continuation(
    record: dict[str, Any],
    *,
    steward_reserve_checked: bool = False,
) -> tuple[str, str] | None:
    from . import resident_domain_common, resident_record_domain
    resident_id = str(record.get("resident_id") or "")
    if not resident_id:
        return None
    if resident_record_domain.lifecycle_operations().pending(resident_id):
        return None
    from .family_quota import ensure_role_reserve
    # The reserve check may ask the Execution service for account quota.
    # Callers holding the Resident state scope run it before taking it.
    if not steward_reserve_checked and ensure_role_reserve(record):
        return None
    job = resident_execution_work.read(record)
    clear_stale_error = bool(
        resident_execution_work.is_active(job)
        and str(record.get("last_error") or "")
    )
    if resident_restart_handoff.pending(record, job):
        return None
    if not resident_execution_work.continuation_needed(record, job):
        if clear_stale_error:
            record["last_error"] = ""
            record["updated_at"] = resident_domain_common.now_iso()
            resident_record_domain.save_resident_record(record)
        return None
    from .resident_default_backend import bind_before_execution
    bind_before_execution(record, str(job.get("actor") or ""))
    job = resident_execution_work.read(record)
    dispatched_at = resident_domain_common.now_iso()
    job["dispatched_at"] = dispatched_at
    if not str(job.get("continuation_id") or "").strip():
        job.update(resident_execution_work.continuation_fields(record))
    continuation_id = str(job.get("continuation_id") or "")
    # Attaching a waiter is a local mutation. It observes Execution state after
    # starting; a status RPC here would hold the aggregate lock during network IO.
    resident_execution_work.write(record, job)
    record["session_state"] = resident_record_domain.record_backend_session_state(record)
    record["last_error"] = ""
    record["updated_at"] = resident_domain_common.now_iso()
    resident_record_domain.save_resident_record(record)
    return resident_id, continuation_id


def _resident_continuation_order_key(job: dict[str, Any]) -> tuple[str, str]:
    timestamp = str(
        job.get("enqueued_at")
        or job.get("started_at")
        or job.get("prompt_built_at")
        or job.get("updated_at")
        or ""
    )
    return timestamp or "9999-12-31T23:59:59Z", str(job.get("execution_id") or "")


def resume_resident_execution(
    resident_id: str,
) -> dict[str, int]:
    """Attach a completion waiter to one newly persisted execution binding."""

    with resident_state_scope(resident_id, "resident.resume_execution"):
        return _resume_resident_execution_scoped(resident_id)


def _resume_resident_execution_scoped(resident_id: str) -> dict[str, int]:
    """Resume one Resident while its aggregate scope is bound."""
    from . import resident_intake_domain, resident_record_domain
    from . import resident_delivery_work

    try:
        resident_delivery_work.drain(resident_id)
    except Exception as exc:
        # Publication failure must not prevent an accepted turn from starting.
        # The committed outbox remains available to the recovery scan.
        print(f"Resident delivery publication pending for {resident_id}: {type(exc).__name__}", file=sys.stderr)

    from .family_quota import ensure_role_reserve

    target_id = str(resident_id or "").strip()
    started = {"residents": 0, "active": _active_resident_continuations()}
    job: tuple[str, str] | None = None
    # Most resumes find a waiter already attached. Decide that from committed
    # state; the state scope is taken only to attach one, and re-checks.
    record = resident_record_for_execution(target_id)
    if record is None:
        raise KeyError("resident not found")
    if not resident_catalog.registration_ready(record):
        return started
    control = _read_resident_execution_control(target_id)
    if not resident_execution_work.continuation_needed(record, control) and not (
        resident_execution_work.is_active(control) and str(record.get("last_error") or "")
    ):
        return started
    if ensure_role_reserve(record):
        return started
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_for_execution(target_id)
        if record is None:
            raise KeyError("resident not found")
        # Ordinary inputs keep their current session, regardless of tool metadata.
        job = _prepare_resident_continuation(record, steward_reserve_checked=True)
    if job:
        current_id, continuation_id = job
        resident_intake_domain.start_resident_continuation(
            resident_record_for_execution(current_id) or {"resident_id": current_id},
            continuation_id,
        )
        started["residents"] = 1
    # The one continuation owns snapshots and replay; recovery only starts it.
    started["active"] = _active_resident_continuations()
    return started


def resume_pending_resident_executions() -> dict[str, int]:
    from . import resident_intake_domain, resident_lifecycle_domain, resident_record_domain
    started = {
        "residents": 0,
        "active": 0,
        "errors": 0,
    }
    jobs: list[tuple[str, str]] = []
    started["active"] = _active_resident_continuations()
    resident_intake_domain.sync_resident_quota_overrides()
    from .resident_deferred_restart import reconcile as reconcile_deferred_restarts
    reconcile_deferred_restarts()
    from .family_quota import ensure_role_reserve, reconcile_role_reserves
    started["steward_reserve_waits"] = reconcile_role_reserves()
    from .resident_work_recovery import reconcile_failed_work
    from .resident_released_executions import reconcile as reconcile_released_executions
    started["released_executions"] = reconcile_released_executions()
    started["failed_work_recoveries"] = reconcile_failed_work()
    from . import resident_delivery_work
    resident_delivery_work.recover()
    residents = resident_execution_work.pending_records()
    execution_work_records = resident_execution_work.read_many(residents)
    candidates: list[tuple[tuple[str, str], dict[str, Any]]] = []
    for record in residents:
        resident_id = str(record.get("resident_id") or "")
        try:
            with resident_state_scope(
                resident_id,
                "resident.recovery_scan",
            ):
                if not resident_catalog.registration_ready(record):
                    continue
                record_copy = dict(record)
                execution_work = execution_work_records.get(resident_id, {})
                if not resident_execution_work.is_active(execution_work):
                    for intent in (execution_work.get(resident_execution_work.CONVERSATION_PROJECTION_INTENTS) or {}).values():
                        resident_lifecycle_domain.append_conversation_message(resident_id, dict(intent['message']))
                        with resident_record_domain.state_lock(resident_id):
                            resident_execution_work.complete_conversation_projection(
                                record_copy,  execution_id=intent['execution_id'],
                                message_id=intent['message']['message_id'],
                            )
                if (execution_work.get('execution_id') and not resident_execution_work.is_active(execution_work)
                        and resident_execution_work.submitted_execution_id(execution_work) and not execution_work.get('execution_acknowledged')):
                    resident_intake_domain.acknowledge_execution(execution_work)
                if resident_execution_work.continuation_needed(record_copy, execution_work):
                    candidates.append((_resident_continuation_order_key(execution_work), record_copy))
        except Exception as exc:
            started["errors"] += 1
            print(
                "Resident execution recovery skipped Resident "
                f"{resident_id}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    candidates.sort(key=lambda item: (item[0], str(item[1].get("resident_id") or "")))
    for _order_key, record in candidates:
        resident_id = str(record.get("resident_id") or "")
        try:
            with resident_state_scope(
                resident_id,
                "resident.recovery_prepare",
            ):
                if ensure_role_reserve(record):
                    continue
                with resident_record_domain.state_lock(resident_id):
                    current_record = resident_record_domain.find_resident(resident_id) or {}
                    prepared = _prepare_resident_continuation(
                        current_record, steward_reserve_checked=True,
                    )
                    if prepared:
                        jobs.append(prepared)
        except Exception as exc:
            started["errors"] += 1
            print(
                "Resident execution recovery could not prepare Resident "
                f"{resident_id}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    for resident_id, continuation_id in jobs:
        try:
            record = resident_record_domain.find_resident(resident_id) or {"resident_id": resident_id}
            resident_intake_domain.start_resident_continuation(record, continuation_id)
            started["residents"] += 1
        except Exception as exc:
            started["errors"] += 1
            print(
                "Resident execution recovery could not start Resident "
                f"{resident_id}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    started["active"] = _active_resident_continuations()
    return started


def _resident_execution_request_backend(record: dict[str, Any], job: Mapping[str, Any] | None = None) -> str:
    return resident_backend.execution_provider(record)


def _turn_allows_subagents(record: Mapping[str, object], job: Mapping[str, object], settings: Mapping[str, object]) -> bool:
    """The turn's subagent switch: the Resident's setting, where its Provider can start subagents."""

    definition = AGENT_PROVIDERS.get(resident_backend.execution_backend(record))
    value = bool(job.get("allow_subagents", settings.get("allow_subagents", False)))
    return definition is not None and definition.allows_subagents({"allow_subagents": value})


def resident_system_instructions(
    record: dict[str, Any],
    job: dict[str, Any],
    *,
    settings: dict[str, Any] | None = None,
) -> object:
    """Portal's injected input for this turn, as parts the turn may carry.

    The Resident's ``prompt_injection`` setting gives each category its
    timing; ExecutionService decides per turn which parts are due.  Placement
    is the Provider's: rules go to its system location; resident.md goes
    there too where the Provider takes it with them (Claude Code, Kimi Code,
    Provider API) and before the message elsewhere; runtime identity and the
    task Skill list go before the message, authorization context after it.

    A Fork child starts a Provider session of its own. Its seed skips the
    source session's one-time startup instructions, which name the source
    Resident (``resident_fork_context.seed_messages``), so the child receives
    its own on its first turn like any new session.
    """
    from management_portal import prompt_injection
    from management_portal.agent_prompt_context import portal_system_instructions, portal_turn_instructions
    from management_portal.execution.contracts import InjectionPart, SystemInstructions
    from management_common.skill_paths import normalize_additional_skills
    from . import resident_domain_common, resident_runtime_domain

    settings = resident_runtime_domain.read_resident_runtime_settings(record) if settings is None else settings
    choices = prompt_injection.resolve(settings.get("prompt_injection"))
    additional_skills = normalize_additional_skills(job.get("additional_skills"))
    allow_subagents = _turn_allows_subagents(record, job, settings)
    # An isolated business input is sent as its caller wrote it, without the
    # Resident's identity in front.
    isolated = bool(resident_input_scope.get(resident_domain_common.record_resident_id(record), str(job.get("user_message_id") or "")))
    skills_with_rules = bool(additional_skills) and choices["turn_skills"] == "with_rules"
    parts: list[InjectionPart] = []
    if choices["core_rules"] != "off":
        timing = "every_turn" if skills_with_rules else choices["core_rules"]
        rules = portal_system_instructions(
            "resident",
            injection_policy=timing,
            additional_skills=additional_skills if skills_with_rules else (),
            allow_subagents=allow_subagents,
        )
        parts.extend(rules.parts)
    transport = resident_execution_provider.initial_instructions_transport_for(record.get("backend"))
    resident_md = (
        resident_agents_instruction_block(record).strip() if choices["resident_md"] != "off" else ""
    )
    if resident_md and transport == "system_instructions":
        parts.append(InjectionPart("resident_md", resident_md, choices["resident_md"], "system"))
    if choices["runtime_identity"] != "off" and not isolated:
        parts.append(InjectionPart(
            "runtime_identity", resident_runtime_domain.resident_runtime_identity_block(record), choices["runtime_identity"], "prompt_prefix",
        ))
    if resident_md and transport == "user_prompt" and not isolated:
        parts.append(InjectionPart("resident_md", resident_md, choices["resident_md"], "prompt_prefix"))
    if additional_skills and choices["turn_skills"] != "off" and not (
        skills_with_rules and choices["core_rules"] != "off"
    ):
        skills = portal_turn_instructions(additional_skills=additional_skills)
        if skills is not None:
            parts.extend(skills.parts)
    authorization = str(job.get("authorization_prompt") or "").strip()
    if authorization and choices["authorization_context"] != "off":
        parts.append(InjectionPart(
            "authorization_context", authorization, choices["authorization_context"], "prompt_suffix",
        ))
    business_prompt = str((settings.get("prompt_context") or {}).get("system_prompt") or "")
    if business_prompt:
        parts.append(InjectionPart("business_instructions", business_prompt, "every_turn", "system"))
    if not isolated:
        if (record.get("family") or {}).get("role") in {"router", "steward"}:
            from .family_tasks import guide
            path, version, content = guide(record)
            parts.append(InjectionPart("family_work_guide", f"{content}\n阅读路径：{path}（{version}）",
                                       "session_start", "prompt_prefix"))
        from .personal_assistant import instructions
        personal = instructions(record)
        if personal:
            family_role = (record.get("family") or {}).get("role") in {"router", "steward"}
            parts.append(InjectionPart("personal_assistant", personal,
                "session_start" if family_role else "every_turn", "prompt_prefix" if family_role else "system"))
    return SystemInstructions(parts=tuple(parts)).validate()


PREVIEW_TURNS = ("first", "later", "after_compaction")


def preview_prompt_injection(record: dict[str, Any], actor: str, turn: str = "first") -> dict[str, Any]:
    """What a turn of this Resident would carry under its prompt_injection setting.

    ``first`` is the first turn of a Provider session, ``later`` a turn after
    it, ``after_compaction`` the first turn after its context was compacted.
    The message is a placeholder; the authorization context names ``actor``
    as the message's source.
    """
    from management_portal import prompt_injection
    from management_portal.execution.injection_plan import plan_injection
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    from . import resident_turn_scope
    from . import (
        resident_domain_common,
        resident_prompt_domain,
        resident_record_domain,
        resident_runtime_domain,
    )

    if turn not in PREVIEW_TURNS:
        raise ValueError("turn 只接受 " + "、".join(PREVIEW_TURNS))
    folder = str(resident_record_domain.resident_folder_path(record))
    cwd = str(resident_runtime_domain.resident_working_directory(record))
    paths = [{"path": folder}, {"path": cwd}, *(
        {"path": str(item["path"])} for item in resident_record_domain.resident_workspace_roots(record) if item.get("path")
    )]
    job = {"actor": actor, "message": "（本轮消息）", "context_refs": {}}
    job["authorization_prompt"] = resident_turn_scope.context_prompt(
        {"principal_id": resident_domain_common.record_resident_id(record), "source_principal_id": actor, "paths": paths}, job, folder,
    )
    parts = resident_system_instructions(record, job).parts
    backend = resident_backend.execution_backend(record)
    definition = AGENT_PROVIDERS.get(backend)
    plan = plan_injection(
        parts,
        resident_prompt_domain.build_resident_prompt(record, job),
        generation=True,
        first_injection=turn == "first",
        injected_at="2000-01-01T00:00:00Z",
        compacted_at="2000-01-02T00:00:00Z" if turn == "after_compaction" else "",
        system_context_survives_compaction=bool(definition and definition.system_context_survives_compaction),
    )
    every_start = bool(AGENT_PROVIDERS.get(backend) and AGENT_PROVIDERS[backend].system_context_every_turn)
    system_in_prompt = bool(definition and not every_start and turn != "first")
    actual_prompt = plan.user_prompt
    if system_in_prompt and plan.attach_system_context:
        actual_prompt = f"{plan.system_context}\n\n{actual_prompt}"
    labels = {name: label for name, (label, _description, _choices) in prompt_injection.CATEGORIES.items()}
    settings = resident_runtime_domain.read_resident_runtime_settings(record)
    return {
        "resident_id": resident_domain_common.record_resident_id(record),
        "provider": backend,
        "turn": turn,
        "preset": prompt_injection.normalize(settings.get("prompt_injection"))["preset"],
        "parts": [
            {
                "id": part.name,
                "label": labels.get(part.name, part.name),
                "timing": part.timing,
                "placement": "prompt_prefix" if system_in_prompt and part.placement == "system" else part.placement,
                "sent": part.name in plan.attached_parts or (every_start and part.placement == "system"),
                "chars": len(part.content),
                "content": part.content,
            }
            for part in parts
        ],
        "system_prompt": plan.session_system_context if every_start else "" if system_in_prompt else plan.system_context,
        "prompt": actual_prompt,
    }


def resident_execution_runtime_options(
    record: dict[str, Any],
    job: dict[str, Any],
    *,
    is_owner: bool,
) -> dict[str, Any]:
    from . import resident_domain_common, resident_record_domain, resident_runtime_domain
    from . import resident_turn_scope

    settings = resident_runtime_domain.read_resident_runtime_settings(record)
    scope = resident_turn_scope.filesystem_scope(
        record, job, cwd=str(resident_runtime_domain.resident_working_directory(record)),
        resident_folder=str(resident_record_domain.resident_folder_path(record)), bindings=resident_record_domain.resident_workspace_roots(record),
    )
    job["filesystem_scope"] = scope
    job["authorization_prompt"] = resident_turn_scope.context_prompt(scope, job, str(resident_record_domain.resident_folder_path(record)))
    options = {
        "runtime_settings": settings,
        "authorization_scope": scope,
        "session_key": resident_backend.execution_binding_id(record),
        "runtime_root": str(resident_record_domain.sessions_path(record) / "backend"),
        "title": str(record.get("title") or "Portal Resident"),
        "is_owner": bool(is_owner),
        "add_dirs": resident_domain_common.dedupe_strings([str(resident_record_domain.resident_workspace_path(record)), *resident_record_domain.workspace_paths(record)]),
        "execution_id": str(job.get("execution_id") or ""),
        "resident_environment": resident_runtime_domain.resident_cli_environment(record),
        "allow_subagents": _turn_allows_subagents(record, job, settings),
        "client_user_message_id": str(job.get("user_message_id") or ""),
    }
    if "agent_harness" in settings:
        options["agent_harness"] = normalize_agent_harness_settings(settings["agent_harness"])
    return options


def resident_record_for_execution(resident_id: str) -> dict[str, Any] | None:
    """Read canonical identity for a bound execution, without external effects.

    Polling an already-bound job needs neither resource resolution nor
    bootstrap finalization. Both may perform slow external work and prevent
    a concurrent Stop from acquiring this Resident's short state scope.
    """
    try:
        return resident_catalog.get_catalog().get(resident_id)
    except KeyError:
        return None


def _read_resident_execution_control(resident_id: str) -> dict[str, Any]:
    return dict(resident_execution_work.read_control(resident_id).get("state") or {})


def _execution_matches_input(
    job: Mapping[str, Any],
    expected_execution_id: str,
    user_message_id: str = "",
) -> bool:
    same_job = (
        bool(str(expected_execution_id or "").strip())
        and str(job.get("execution_id") or "") == str(expected_execution_id or "")
    )
    same_input = (
        bool(str(user_message_id or "").strip())
        and str(job.get("user_message_id") or "")
        == str(user_message_id or "")
    )
    return same_job or same_input


def _resident_execution_still_current(
    resident_id: str,
    expected_execution_id: str,
    user_message_id: str = "",
) -> bool:
    # A read-only answer: it can be stale the moment it returns whether or
    # not the state scope was held, so it reads the committed control alone.
    current = _read_resident_execution_control(resident_id)
    return (
        _execution_matches_input(
            current,
            expected_execution_id,
            user_message_id,
        )
        and resident_execution_work.is_active(current)
        and not str(current.get("stop_requested_at") or "").strip()
    )


def _resident_execution_cancellation_requested(
    resident_id: str,
    expected_execution_id: str,
    user_message_id: str = "",
) -> bool:
    current = _read_resident_execution_control(resident_id)
    return bool(
        _execution_matches_input(
            current,
            expected_execution_id,
            user_message_id,
        )
        and resident_execution_work.is_active(current)
        and str(current.get("stop_requested_at") or "").strip()
    )


def _record_resident_execution(
    record: dict[str, Any],
    job: dict[str, Any],
    execution_id: str,
) -> None:
    from . import resident_domain_common, resident_record_domain
    resident_id = resident_domain_common.record_resident_id(record)
    with resident_record_domain.state_lock(resident_id):
        current_record = resident_record_domain.find_resident(resident_id) or {}
        if not current_record:
            return
        current = resident_execution_work.read(current_record)
        if not _execution_matches_input(
            current,
            str(job.get("execution_id") or ""),
            str(job.get("user_message_id") or ""),
        ):
            return
        bind_execution(
            current,
            execution_id=execution_id,
            now=resident_domain_common.now_iso(),
        )
        resident_execution_work.write(current_record, current)


def _flush_pending_active_turn_controls(
    service: object,
    resident_id: str,
    execution_id: str,
) -> None:
    from . import resident_domain_common, resident_intake_domain, resident_record_domain
    while True:
        with resident_record_domain.state_lock(resident_id):
            record = resident_record_domain.find_resident(resident_id) or {}
            if not record:
                return
            current = resident_execution_work.read(record)
            if (
                str(current.get("execution_id") or "") != execution_id
                or not resident_execution_work.is_active(current)
                or str(current.get("stop_requested_at") or "").strip()
                or str(current.get("execution_terminal_status") or "").strip()
                in {"completed", "failed", "cancelled"}
            ):
                return
            pending = [
                copy.deepcopy(candidate)
                for candidate in current.get("pending_active_turn_inputs") or []
                if isinstance(candidate, dict)
            ]
            if not pending:
                break
            item = pending[0]
            message_id = str(item.get("message_id") or "").strip()
            turn_input = item.get("input")
            if not message_id or not isinstance(turn_input, list) or not turn_input:
                return
            expected_execution_id = str(current.get("execution_id") or "")
        try:
            delivery = service.append_input(
                execution_id,
                actor=str(item.get("actor") or ""),
                input=[
                    dict(part)
                    for part in turn_input
                    if isinstance(part, dict)
                ],
                control_id=str(item.get("control_id") or "")
                or resident_intake_domain.resident_input_control_id(resident_id, message_id),
                source_segment_id=execution_port.input_source_segment_id(resident_id, message_id),
            )
        except Exception:
            return
        if str(delivery.get("status") or "") not in {
            "delivered",
            "pending",
        }:
            return
        with resident_record_domain.state_lock(resident_id):
            record = resident_record_domain.find_resident(resident_id) or {}
            current = resident_execution_work.read(record) if record else {}
            if str(current.get("execution_id") or "") != expected_execution_id or resident_execution_work.submitted_execution_id(current) != execution_id:
                return
            remaining = [value for value in current.get("pending_active_turn_inputs") or [] if value != item]
            if remaining:
                current["pending_active_turn_inputs"] = remaining
            else:
                current.pop("pending_active_turn_inputs", None)
            current["updated_at"] = resident_domain_common.now_iso()
            resident_execution_work.write(record, current)

    while True:
        with resident_record_domain.state_lock(resident_id):
            record = resident_record_domain.find_resident(resident_id) or {}
            if not record:
                return
            current = resident_execution_work.read(record)
            if (
                str(current.get("execution_id") or "") != execution_id
                or not resident_execution_work.is_active(current)
                or str(current.get("stop_requested_at") or "").strip()
                or str(current.get("execution_terminal_status") or "").strip()
                in {"completed", "failed", "cancelled"}
            ):
                return
            pending_settings = [
                copy.deepcopy(candidate)
                for candidate in current.get("pending_active_turn_settings") or []
                if isinstance(candidate, dict)
            ]
            if not pending_settings:
                return
            item = pending_settings[0]
            expected_execution_id = str(current.get("execution_id") or "")
        try:
            delivery = service.update_turn_settings(
                execution_id,
                actor=str(item.get("actor") or ""),
                service_tier=str(item.get("service_tier") or ""),
                control_id=str(item.get("control_id") or ""),
            )
        except Exception:
            return
        if str(delivery.get("status") or "") not in {
            "delivered",
            "pending",
        }:
            return
        with resident_record_domain.state_lock(resident_id):
            record = resident_record_domain.find_resident(resident_id) or {}
            current = resident_execution_work.read(record) if record else {}
            if str(current.get("execution_id") or "") != expected_execution_id or resident_execution_work.submitted_execution_id(current) != execution_id:
                return
            remaining_settings = [value for value in current.get("pending_active_turn_settings") or [] if value != item]
            if remaining_settings:
                current["pending_active_turn_settings"] = remaining_settings
            else:
                current.pop("pending_active_turn_settings", None)
            current["updated_at"] = resident_domain_common.now_iso()
            resident_execution_work.write(record, current)


def durable_resident_execution_request(
    record: dict[str, Any],
    job: dict[str, Any],
    request_factory: Callable[[], ExecutionRequest],
) -> ExecutionRequest:
    """Recover the durable request before constructing a new submission."""
    from . import resident_domain_common, resident_record_domain

    stored_payload = job.get("execution_request")
    if isinstance(stored_payload, dict):
        durable_request = ExecutionRequest.from_payload(stored_payload)
        expected_id = resident_execution_work.execution_id(job)
        if expected_id and durable_request.execution_id != expected_id:
            raise RuntimeError("Resident execution request id does not match its job")
    else:
        planned_id = resident_execution_work.ensure_execution_id(job)
        durable_request = replace(request_factory(), execution_id=planned_id).validate()
    planned_id = durable_request.execution_id
    payload = durable_request.to_payload()
    resident_id = resident_domain_common.record_resident_id(record)
    with resident_record_domain.state_lock(resident_id):
        current_record = resident_record_domain.find_resident(resident_id) or {}
        if not current_record:
            raise RuntimeError("Resident disappeared before Execution submission")
        current = resident_execution_work.read(current_record)
        if str(current.get("execution_id") or "") != str(job.get("execution_id") or ""):
            raise RuntimeError("Resident job changed before Execution submission")
        if (
            str(current.get("stop_requested_at") or "").strip()
            and not resident_execution_work.submitted_execution_id(current)
        ):
            raise resident_domain_common.ResidentTurnCancelled(
                "Resident turn was stopped before Execution submission"
            )
        existing_payload = current.get("execution_request")
        if isinstance(existing_payload, dict):
            durable_request = ExecutionRequest.from_payload(existing_payload)
            payload = durable_request.to_payload()
        else:
            current["execution_id"] = planned_id
            current["execution_request"] = payload
            resident_execution_work.write(current_record, current)
    job["execution_id"] = durable_request.execution_id
    job["execution_request"] = payload
    return durable_request


def _project_resident_execution_snapshot(
    resident_id: str,

    execution_id: str,
    snapshot: dict[str, Any],
    *,
    activity_projection: execution_events.ExecutionActivityProjection | None = None,
) -> None:
    from . import resident_directory_domain, resident_domain_common, resident_record_domain
    projected_snapshot = dict(snapshot)
    if resident_restart_handoff.receipt(projected_snapshot):
        return
    # Most snapshots change nothing in the Job. Read and compare without the
    # state scope; take it only to write, re-checking ownership inside.
    current_record = resident_record_for_execution(resident_id) or {}
    if not current_record:
        return
    current = _read_resident_execution_control(resident_id)
    if (
        str(current.get("execution_id") or "") != str(execution_id or "")
    ):
        return
    incoming_status = execution_status(projected_snapshot)
    terminal_status = str(
        current.get("execution_terminal_status") or ""
    ).strip().lower()
    changed = current.pop("conversation_projection_error", None) is not None
    if incoming_status in {"completed", "failed", "cancelled"}:
        terminal_status = incoming_status
        if current.get("execution_terminal_status") != terminal_status:
            current["execution_terminal_status"] = terminal_status
            changed = True
    elif terminal_status in {"completed", "failed", "cancelled"}:
        projected_snapshot["status"] = terminal_status
        projected_snapshot.pop("scheduler", None)
    if changed:
        with resident_record_domain.state_lock(resident_id):
            complete_job = resident_execution_work.read(current_record)
            if (
                str(complete_job.get("execution_id") or "") != str(execution_id or "")
            ):
                return
            complete_job.pop("conversation_projection_error", None)
            if terminal_status:
                complete_job["execution_terminal_status"] = terminal_status
            resident_execution_work.write(current_record, complete_job)
        current = complete_job
    exact_resident_id = resident_domain_common.record_resident_id(current_record)
    if not resident_execution_work.is_active(current):
        resident_conversation_stream.get_repository().set_activities(
            exact_resident_id,
            [],
        )
        return
    transcript_projection = (
        activity_projection.read()
        if activity_projection is not None else
        execution_events.execution_projection_from_execution(
            execution_id,
            runtime_limit=execution_events.DEFAULT_RUNTIME_DETAIL_LIMIT,
            message_limit=0,
        )
    )
    # The append-only Execution activity stream is the one ordered queue for
    # visible output. Do not also deliver from scheduler event callbacks.
    _persist_execution_display_messages(
        exact_resident_id,

        execution_id,
        [
            dict(item)
            for item in transcript_projection.get("outputs") or []
            if isinstance(item, Mapping)
        ],
    )
    if activity_projection is not None:
        activity_projection.acknowledge()
    # The ownership fence is the committed revision: publish from one read,
    # then re-project if a Job transition committed in between.
    observed_revision = 0
    for attempt in range(2):
        observed = resident_execution_work.read_control(resident_id)
        observed_revision = int(observed.get("revision") or 0)
        current = dict(observed.get("state") or {})
        if (
            str(current.get("execution_id") or "") != str(execution_id or "")
            or resident_execution_work.submitted_execution_id(current) != str(execution_id or "")
            or not resident_execution_work.is_active(current)
            or resident_execution_work.completion_applied(current)
        ):
            return
        publish_snapshot = dict(projected_snapshot)
        terminal_status = str(current.get("execution_terminal_status") or "")
        if terminal_status in {"completed", "failed", "cancelled"}:
            publish_snapshot["status"] = terminal_status
            publish_snapshot.pop("scheduler", None)

        details = []
        if execution_status(publish_snapshot) not in {"completed", "failed", "cancelled"}:
            activity = resident_directory_domain.runtime_activity_for_execution(
                exact_resident_id,
                current,
                transcript_projection=transcript_projection,
            )
            details = activity.get("details") if isinstance(activity.get("details"), list) else []
        resident_execution_work.publish_conversation_state(
            current_record,
            current,
            snapshot=publish_snapshot,
            control_only=True,
        )
        resident_conversation_stream.get_repository().set_activities(
            exact_resident_id,
            details,
        )
        if resident_execution_work.read_revision(resident_id) == observed_revision:
            break
        after = resident_execution_work.read_control(resident_id)
        latest = dict(after.get("state") or {})
        if (
            str(latest.get("execution_id") or "") != str(execution_id or "")
            or resident_execution_work.submitted_execution_id(latest) != str(execution_id or "")
        ):
            resident_execution_work.project_latest_conversation_state(
                current_record,
                latest or None,
            )
            resident_conversation_stream.get_repository().set_activities(
                exact_resident_id,
                [],
            )
            break
        if attempt:
            break
    resident_execution_work.notify_changed(current_record)


def _record_resident_projection_error(
    resident_id: str,

    execution_id: str,
    error: Exception,
    *,
    event_sequence: int = 0,
) -> dict[str, Any]:
    """Record a projection fault without changing the Execution terminal state."""
    from . import resident_domain_common, resident_record_domain

    message_id = str(
        getattr(error, "_resident_projection_message_id", "") or ""
    ).strip()
    recorded_at = resident_domain_common.now_iso()
    payload = {
        "message": resident_domain_common.truncate_text(f"{type(error).__name__}: {error}", 1600),
        "execution_id": str(execution_id or ""),
        "event_sequence": max(0, int(event_sequence or 0)),
        "recorded_at": recorded_at,
        **({"message_id": message_id} if message_id else {}),
    }
    try:
        record = resident_record_for_execution(resident_id) or {}
        if not record:
            return payload
        with resident_record_domain.state_lock(resident_id):
            resident_execution_work.record_conversation_projection_error(
                record,

                execution_id=execution_id,
                payload=payload,
                quarantine_limit=RESIDENT_PROJECTION_QUARANTINE_LIMIT,
            )
    except Exception as record_error:
        print(
            "Resident conversation projection error could not be recorded: "
            f"{type(record_error).__name__}: {record_error}",
            file=sys.stderr,
        )
    print(
        "Resident conversation projection failed without terminalizing Execution: "
        f"{payload['message']}",
        file=sys.stderr,
    )
    return payload


def _resident_execution_event_cursor(
    resident_id: str,

    execution_id: str,
) -> int:
    from . import resident_domain_common
    # The cursor only moves forward, so a read that races an advance returns
    # an earlier cursor and replays events the consumer already deduplicates.
    job = _read_resident_execution_control(resident_id)
    if (
        str(job.get("execution_id") or "") != str(execution_id or "")
    ):
        return 0
    return resident_domain_common.nonnegative_int(job.get("execution_event_cursor"), 0)


def _advance_resident_execution_event_cursor(
    resident_id: str,

    execution_id: str,
    sequence: int,
) -> None:
    next_cursor = max(0, int(sequence or 0))
    if not next_cursor:
        return
    # One conditional UPDATE: it moves only this Execution's row and only
    # forward, so it needs no Resident state scope.
    resident_execution_work.advance_execution_event_cursor(
        {"resident_id": resident_id},

        execution_id=execution_id,
        sequence=next_cursor,
    )


def _persist_execution_display_messages(
    resident_id: str,

    execution_id: str,
    outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from . import (
        resident_domain_common,
        resident_intake_domain,
        resident_lifecycle_domain,
        resident_record_domain,
    )
    from management_common.async_questions import question_message_parts

    display_messages: list[dict[str, Any]] = []
    repository = resident_conversation_stream.get_repository()
    for raw_output in outputs:
        output = dict(raw_output)
        canonical_final = (
            output.get("message")
            if output.get("canonical_final") and isinstance(output.get("message"), Mapping)
            else None
        )
        message_id = str(output.get("output_id") or "").strip()
        content = str(output.get("text") or "").strip()
        questions = output.get("questions") or []
        native_parts = output.get("content_parts") or []
        if not message_id or (canonical_final is None and not content and not questions and not native_parts):
            continue
        is_final = str(output.get("phase") or "").strip().casefold() in {
            "final",
            "final_answer",
        }
        if is_final and not questions and canonical_final is None:
            continue
        if is_final:
            content = ""

        existing = repository.message(resident_id, message_id)
        # Capture the reply fence from the committed control. Authorization
        # below re-checks it under the Job lock before anything is written.
        job = _read_resident_execution_control(resident_id)
        if (
            str(job.get("execution_id") or "") != str(execution_id or "")
        ):
            break
        stored_quarantine = job.get("conversation_projection_quarantine")
        quarantined_message_ids = (
            {
                str(value or "").strip()
                for value in stored_quarantine.get("message_ids") or []
                if str(value or "").strip()
            }
            if isinstance(stored_quarantine, Mapping)
            and str(stored_quarantine.get("execution_id") or "")
            == str(execution_id or "")
            else set()
        )
        if message_id in quarantined_message_ids:
            continue
        record = {"resident_id": job["resident_id"]}
        exact_resident_id = resident_domain_common.record_resident_id(record)
        reply_to_message_id = str(job.get("user_message_id") or "")

        if canonical_final is not None:
            message = copy.deepcopy(dict(canonical_final))
        elif output.get("role") == "system" and isinstance(output.get("message"), Mapping):
            # The Harness produced this input; the Conversation consumer
            # places it in the trace before publishing its event.
            message = copy.deepcopy(dict(output["message"]))
        else:
            # Replayed events carry an observation time, not a new creation time.
            message = resident_intake_domain.build_message(
                "agent",
                content,
                message_id=message_id,
                created_at=str((existing or output).get("created_at") or ""),
                reply_to_message_id=reply_to_message_id,
                execution_id=execution_id,
                content_parts=repository.execution_content_parts(
                    exact_resident_id, execution_id, native_parts,
                ) if native_parts else question_message_parts(
                    content, questions, exact_resident_id, message_id,
                ),
            )

        # Persist authorization before crossing into AgentData/Conversation.
        # This intent is the logical commit point for a later idempotent append.
        with resident_record_domain.state_lock(resident_id):
            current = _read_resident_execution_control(resident_id)
            if (
                str(current.get("execution_id") or "") != str(execution_id or "")
                or str(current.get("user_message_id") or "") != reply_to_message_id
                or (canonical_final is not None and (
                    str(current.get("actor") or "") != str(output.get("actor") or "")
                    or str(current.get("stop_requested_at") or "").strip()
                ))
            ):
                break
            try:
                authorized = resident_execution_work.authorize_conversation_projection(
                    record,

                    execution_id=execution_id,
                    message=message,
                )
            except KeyError:
                # The Resident was deleted while its Job row remained.
                break
            if not authorized:
                continue
        try:
            persisted = resident_lifecycle_domain.append_conversation_message(exact_resident_id, message)
        except Exception as error:
            setattr(error, "_resident_projection_message_id", message_id)
            raise
        with resident_record_domain.state_lock(resident_id):
            try:
                resident_execution_work.complete_conversation_projection(
                    record,

                    execution_id=execution_id,
                    message_id=message_id,
                )
            except KeyError:
                pass
        display_messages.append(persisted)
    return display_messages


def _persist_execution_final_message(
    resident_id: str,

    execution_id: str,
    message: dict[str, Any],
) -> dict[str, Any] | None:
    """Append a canonical final Message while its Execution still owns the job."""
    from management_common.principal import message_origin

    # Display persistence re-checks this owner and actor under the Job lock.
    job = _read_resident_execution_control(resident_id)
    if (
        str(job.get("execution_id") or "") != str(execution_id or "")
    ):
        return None
    exact_resident_id = str(job["resident_id"])
    actor = str(job.get("actor") or "")

    projected_message = copy.deepcopy(message)
    projected_message["execution_id"] = execution_id
    projected_message["author_id"] = exact_resident_id
    projected_message["origin"] = message_origin(
        exact_resident_id,
        initiated_by=actor,
    )
    message_id = str(projected_message.get("message_id") or "").strip()
    delivered = _persist_execution_display_messages(
        resident_id,  execution_id,
        [{"output_id": message_id, "phase": "final_answer", "canonical_final": True,
          "message": projected_message, "actor": actor}],
    )
    return delivered[0] if delivered else None


def wait_for_resident_execution(
    service: object,
    *,
    execution_id: str,
    actor: str,
    resident_id: str,

    user_message_id: str = "",
    persist_messages: bool = True,
    timeout_seconds: float | None = None,
    cancellation_already_requested: bool = False,
) -> dict[str, Any]:
    from . import resident_intake_domain
    wait_timeout = (
        max(30.0, float(timeout_seconds))
        if timeout_seconds is not None
        else None
    )
    projection_errors: list[dict[str, Any]] = []
    activity_projection = execution_events.ExecutionActivityProjection(execution_id)

    def record_projection_error(error: Exception, *, event_sequence: int = 0) -> None:
        projection_errors.append(
            _record_resident_projection_error(
                resident_id,

                execution_id,
                error,
                event_sequence=event_sequence,
            )
        )

    def project_execution_events(events: list[dict[str, Any]]) -> None:
        # The checkpoint is forward-only, so one advance to the batch's last
        # sequence records the same progress as one advance per event.
        sequence = max((int(event.get("sequence") or 0) for event in events), default=0)
        try:
            _advance_resident_execution_event_cursor(
                resident_id,

                execution_id,
                sequence,
            )
        except Exception as error:
            record_projection_error(error, event_sequence=sequence)

    def project_execution_state(snapshot: dict[str, Any]) -> bool:
        try:
            # A peer Worker can commit a receipt without this Scheduler seeing
            # its transient control event. Reconcile durable receipts on state
            # changes, including the initial snapshot after reconnect.
            resident_intake_domain.reconcile_queued_turn_inputs(resident_id, execution_id)
            _project_resident_execution_snapshot(
                resident_id,

                execution_id,
                snapshot,
                activity_projection=activity_projection,
            )
            return True
        except Exception as error:
            record_projection_error(error)
            return False

    snapshot = service.wait(
        execution_id,
        actor=actor,
        after_sequence=_resident_execution_event_cursor(
            resident_id,

            execution_id,
        ),
        timeout_seconds=wait_timeout,
        cancellation_requested=(
            None
            if cancellation_already_requested
            else lambda: not _resident_execution_still_current(
                resident_id,
                execution_id,
                user_message_id,
            )
        ),
        on_events=project_execution_events,
        on_snapshot=project_execution_state,
    )
    if resident_restart_handoff.receipt(snapshot):
        raise resident_restart_handoff.ExecutionHandedOff()
    if persist_messages and execution_status(snapshot) in {"completed", "failed", "cancelled"}:
        from .resident_resource_changes import display_outputs
        try:
            resource_outputs = display_outputs(
                resident_conversation_stream.get_repository(), resident_id, execution_id,
            )
            if resource_outputs:
                _persist_execution_display_messages(
                    resident_id,  execution_id, resource_outputs,
                )
        except Exception as error:
            record_projection_error(error)
    snapshot["projection_errors"] = list(projection_errors)
    return snapshot


def bind_or_resume_resident_execution(
    service: object,
    request: object,
    record: dict[str, Any],
    job: dict[str, Any],
) -> str:
    from . import resident_domain_common, resident_record_domain
    resident_record_domain.ensure_resident_agent(record, actor=str(job.get("actor") or ""))
    execution_id = resident_execution_work.submitted_execution_id(job)
    actor = str(getattr(request, "actor", "") or job.get("actor") or "")
    correlation_id = str(job.get("execution_id") or "")
    service_type = str(
        getattr(request, "service_type", "") or "agent_session"
    )
    provider = str(
        getattr(request, "provider", "")
        or _resident_execution_request_backend(record, job)
    )
    if execution_id:
        service.resume(
            execution_id,
            actor=actor,
            correlation_id=correlation_id,
            service_type=service_type,
            provider=provider,
        )
        _flush_pending_active_turn_controls(
            service,
            resident_domain_common.record_resident_id(record),
            execution_id,
        )
        return execution_id
    if _resident_execution_cancellation_requested(
        resident_domain_common.record_resident_id(record),
        correlation_id,
        str(job.get("user_message_id") or ""),
    ):
        raise resident_domain_common.ResidentTurnCancelled(
            "Resident turn was stopped before Execution submission"
        )
    submitted = service.submit(request)
    execution_id = str(
        submitted.get("execution_id") or request.execution_id
    )
    _record_resident_execution(
        record,
        job,
        execution_id,
    )
    bind_execution(
        job,
        execution_id=execution_id,
        now=resident_domain_common.now_iso(),
    )
    _flush_pending_active_turn_controls(
        service,
        resident_domain_common.record_resident_id(record),
        execution_id,
    )
    return execution_id


def _lineage_delivery_profile(job: Mapping[str, Any]) -> dict[str, Any]:
    """How a Lineage asked this input to run (see lineage_program_runtime)."""

    lineage_id = str(job.get("lineage_id") or "").strip()
    message_id = str(job.get("user_message_id") or "").strip()
    if not lineage_id or not message_id:
        return {}
    try:
        from . import lineage_program_runtime

        return lineage_program_runtime.delivery_profile(lineage_id, message_id)
    except Exception:  # noqa: BLE001 - an unreadable profile runs the input normally
        return {}


def _build_resident_execution_request(
    record: dict[str, Any],
    job: dict[str, Any],
) -> object:
    from . import resident_access, resident_domain_common, resident_runtime_domain
    scope = resident_input_scope.get(resident_domain_common.record_resident_id(record), str(job.get("user_message_id") or ""))
    if scope and scope["actor"] != resident_execution_work.request_actor(job):
        raise PermissionError("Resident job differs from its input caller")
    delivery = _lineage_delivery_profile(job)
    # A fresh-context run (an isolated input or timer/file-triggered work)
    # leaves the Resident's session alone.
    fresh_context = bool(scope) or bool(delivery.get("isolated"))
    if resident_backend.execution_backend(record) == "provider_api":
        if job.get("input_media"):
            raise ValueError("provider_api Resident does not accept native input_media")
        from . import resident_runtime_domain
        from management_portal.execution.resident_configuration import normalize_backend_options
        settings = resident_runtime_domain.read_resident_runtime_settings(record)
        options = normalize_backend_options(settings.get("backend_options", {}), "provider_api")
        options.update(model=settings["model"], runtime_settings=copy.deepcopy(settings),
                       agent_harness=copy.deepcopy(settings.get("agent_harness") or {}))
        context = settings.get("prompt_context") or {}
        # Always send this input, even when preset context already has a user
        # message. Presets are context, never a replacement for the new input.
        prompt = str(job.get("raw_message") or "") if scope else str(job.get("actual_sent_prompt") or job.get("message") or job.get("raw_message") or "")
        seed = job.get("fork_context_input") or {}
        if record.get("source_resident_id") and not fresh_context and seed.get("portable_prompt"):
            # Stateless Model API calls receive the same portable trace as a
            # native Provider. It already includes this Resident's history;
            # the conversation-history selector must not append it again.
            prompt = str(seed["portable_prompt"])
            context = {**context, "history": {"enabled": False}}
        from .resident_api_context import assemble_api_messages
        messages, history = assemble_api_messages(resident_domain_common.record_resident_id(record), job, context, fresh_context=fresh_context)
        options["messages"] = messages + [{"role": "user", "content": prompt}]
        options["context_history"] = history
        return build_chat_completion_request(
            actor=resident_execution_work.request_actor(job), agent_id=resident_domain_common.record_resident_id(record),
            prompt=str(job.get("raw_message") or "") if scope else str(job.get("actual_sent_prompt") or job.get("message") or job.get("raw_message") or ""),
            provider=resident_backend.execution_provider(record), correlation_id=str(job.get("execution_id") or ""),
            runtime_options=options,
            system_instructions=resident_system_instructions(record, job),
            metadata={"resident_id": resident_domain_common.record_resident_id(record), "lineage_id": resident_lineage.lineage_id(record),
                      "execution_id": str(job.get("execution_id") or ""), "user_message_id": str(job.get("user_message_id") or "")},
        )
    if (
        not fresh_context
        and _requires_initial_context_compaction(record)
        and not _initial_context_compaction_completed(record)
    ):
        raise RuntimeError(
            "压缩继承上下文尚未准备完成；请重试分支初始化，不能以完整上下文继续运行"
        )
    resident_runtime_domain.resident_working_directory(record)
    record = resident_runtime_domain.reconcile_resident_codex_runtime_configuration(record)
    default_cwd = resident_runtime_domain.resident_working_directory(record)
    if resident_execution_provider.provider_for(
        record
    ).capabilities.synchronous_delivery:
        raise RuntimeError("同步 ExecutionProvider 不提交 Execution 请求")
    actor = resident_execution_work.request_actor(job)
    is_owner = resident_access.has_resident_workspace_authority(record, actor)
    lineage_id = resident_lineage.lineage_id(record)
    if lineage_id:
        from . import resident_hatg_credentials

        credential_resident_id = resident_domain_common.record_resident_id(record)
        resident_hatg_credentials.ensure_resident_credential(
            credential_resident_id,
            lineage_id,
        )
    exact_resident_id = resident_domain_common.record_resident_id(record)
    backend = _resident_execution_request_backend(record)
    definition = AGENT_PROVIDERS[backend]
    account = resident_backend.execution_account(record)
    backend_route_id = str(account.get("backend_route_id") or "")
    runtime_options = resident_execution_runtime_options(record, job, is_owner=is_owner)
    runtime_options["allow_backend_route_fallback"] = False
    if fresh_context:
        runtime_options["session_key"] = ""
    source_input = resident_conversation_stream.get_repository().message(exact_resident_id, str(job.get("user_message_id") or "")) or {}
    review_parts = [part for part in source_input.get("content_parts", []) if part.get("type") == "turn_review"]
    review_instructions = ""
    if review_parts:
        from management_common.provider_turn_artifacts import review_target

        if len(review_parts) != 1:
            raise ValueError("a review takes exactly one turn snapshot")
        source = resident_conversation_stream.get_repository().agent_data.operation_record(review_parts[0]["segment_id"])
        target = review_target(source, exact_resident_id)
        if definition.native_review:
            runtime_options["review_target"] = target
        else:
            # Without a native review mode the Resident reviews the snapshot in an ordinary turn.
            review_instructions = target["instructions"]
    goal_command = slash_commands.parse_goal_command(
        job.get("raw_message")
        or job.get("effective_message")
        or job.get("message")
        or ""
    )
    if definition.native_goals and goal_command and goal_command.get("action") != "get":
        runtime_options["goal"] = goal_command
    binding_id = resident_backend.execution_binding_id(record)
    input_media = tuple(
        InputMedia.from_payload(item)
        for item in (
            job.get("input_media")
            if isinstance(job.get("input_media"), list)
            else []
        )
    )
    return build_agent_turn_request(
        actor=actor,
        agent_id=exact_resident_id,
        prompt=(str(job.get("raw_message") or "") if scope else str(
            job.get("actual_sent_prompt")
            or job.get("message")
            or job.get("raw_message")
            or ""
        )) + (f"\n\n{review_instructions}" if review_instructions else ""),
        provider=backend,
        correlation_id=str(job.get("execution_id") or ""),
        cwd=str(
            (job.get("filesystem_scope") or {}).get("cwd")
            or job.get("prompt_cwd")
            or default_cwd
        ),
        permission_profile="owner" if is_owner else "restricted",
        input_media=input_media,
        runtime_options=runtime_options,
        binding_id="" if fresh_context else binding_id,
        backend_route_id=backend_route_id,
        system_instructions=resident_system_instructions(record, job),
        metadata={
            "business_type": "resident",
            "resident_id": exact_resident_id,
            "lineage_id": lineage_id,
            "filesystem_scope": copy.deepcopy(job.get("filesystem_scope") or {}),
            "fork_context_input": copy.deepcopy(job.get("fork_context_input") or {}),
            "execution_id": str(job.get("execution_id") or ""),
            "project_id": str(record.get("project_id") or ""),

            "business_ref": str(job.get("business_ref") or ""),
        },
    )


def _send_resident_execution(
    record: dict[str, Any],
    job: dict[str, Any],
    *,
    persist_messages: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from . import resident_domain_common, resident_intake_domain
    from management_portal.execution.accounts import default_execution_accounts
    if (
        _resident_execution_cancellation_requested(
            resident_domain_common.record_resident_id(record),
            str(job.get("execution_id") or ""),
            str(job.get("user_message_id") or ""),
        )
        and not resident_execution_work.submitted_execution_id(job)
    ):
        raise resident_domain_common.ResidentTurnCancelled(
            "Resident turn was stopped before Execution request construction"
        )
    carried_infos: list[dict[str, Any]] = []

    def build_request() -> ExecutionRequest:
        request = _build_resident_execution_request(record, job)
        if resident_input_scope.get(
            resident_domain_common.record_resident_id(record), str(job.get("user_message_id") or "")
        ) or resident_execution_work.is_compact(job):
            return request
        # Held info messages travel once, at the front of the next request.
        original = str(request.prompt or "")
        prompt, carried = resident_held_infos.attach(record, original)
        return replace(request, prompt=prompt, metadata={**request.metadata, "resident_carried_infos": carried})

    request = durable_resident_execution_request(record, job, build_request)
    carried_infos = list(request.metadata.get("resident_carried_infos") or [])
    resident_held_infos.consume(
        record, carried_infos, request_message_id=str(job.get("user_message_id") or ""),
    )
    try:
        # Retain the inputs so cancellation or failure can put them back in the Mailbox.
        resident_input_recovery.retain_input_checkpoint(record, job, carried_infos)
    except Exception as exc:  # noqa: BLE001 - the checkpoint never blocks execution
        print(f"Execution input checkpoint not recorded for {resident_domain_common.record_resident_id(record)}: {type(exc).__name__}: {exc}", file=sys.stderr)
    actor = str(request.actor or "")
    backend = str(request.provider or "")
    service = execution_port.service()
    execution_id = bind_or_resume_resident_execution(
        service,
        request,
        record,
        job,
    )
    cancellation_requested = _resident_execution_cancellation_requested(
        resident_domain_common.record_resident_id(record),
        str(job.get("execution_id") or ""),
        str(job.get("user_message_id") or ""),
    )
    if cancellation_requested:
        service.cancel(
            execution_id,
            actor=actor,
            reason="execution cancelled by caller",
        )
    snapshot = wait_for_resident_execution(
        service,
        execution_id=execution_id,
        actor=actor,
        resident_id=resident_domain_common.record_resident_id(record),

        user_message_id=str(job.get("user_message_id") or ""),
        persist_messages=persist_messages,
        cancellation_already_requested=cancellation_requested,
    )
    result = (
        snapshot.get("result")
        if isinstance(snapshot.get("result"), dict)
        else {}
    )
    status = str(result.get("status") or snapshot.get("status") or "")
    raw_usage = (
        dict(result.get("usage"))
        if isinstance(result.get("usage"), dict)
        else {}
    )
    usage_projection = resident_domain_common.execution_token_usage_contract(raw_usage)
    # The turn's usage is what the execution counted for its Provider requests,
    # whether the turn completed, failed or was stopped.
    usage = resident_domain_common.clean_token_usage(result.get("accounted_usage")) or resident_domain_common.clean_token_usage(
        usage_projection.get("turn_usage")
    )
    if status == "cancelled" or _resident_execution_cancellation_requested(
        resident_domain_common.record_resident_id(record),
        str(job.get("execution_id") or ""),
        str(job.get("user_message_id") or ""),
    ):
        raise resident_domain_common.ResidentTurnCancelled(
            str(result.get("error") or "execution was cancelled"), usage=usage,
        )
    if status != "completed":
        raise resident_domain_common.ResidentExecutionFailed(snapshot, f"execution ended with status {status}", usage=usage)
    output = (
        result.get("output")
        if isinstance(result.get("output"), dict)
        else {}
    )
    assistant_text = str(output.get("message") or "")
    if not assistant_text:
        raise RuntimeError("ExecutionResult did not contain Backend output")
    backend_route_id = default_execution_accounts().canonical_route(
        backend, result.get("backend_route_id")
    )
    context_usage = resident_domain_common.clean_token_usage(
        usage_projection.get("context_usage")
    )
    context_window = resident_domain_common.nonnegative_int(
        usage_projection.get("model_context_window"),
        0,
    )
    usage_at = resident_domain_common.now_iso()
    context_usage_at = str(
        usage_projection.get("context_updated_at") or usage_at
    )
    updated = dict(record)
    if "goal" in output:
        updated["codex_goal"] = output.get("goal")
    updated.update(
        {
            "session_state": "active",
            "session_created_via": str(
                record.get("session_created_via")
                or "execution_service"
            ),
            "last_error": "",
        }
    )
    # A fresh-context run says nothing about how full the Resident's own
    # session is, and it ran on no route of the Resident's session.
    own_session = not (
        _lineage_delivery_profile(job)
        or resident_input_scope.get(resident_domain_common.record_resident_id(record), str(job.get("user_message_id") or ""))
    )
    if context_usage and own_session:
        updated["last_token_usage"] = context_usage
        updated["last_token_usage_at"] = context_usage_at
    if usage:
        updated["last_turn_usage"] = usage
        updated["last_turn_usage_at"] = usage_at
    if context_window and own_session:
        updated["last_model_context_window"] = context_window
    if backend_route_id and own_session:
        updated["backend_route_id"] = backend_route_id
    exact_resident_id = resident_domain_common.record_resident_id(record)
    assistant_message_id = resident_message_contract.derived_message_identity(
        "resident-execution-message",
        execution_id,
        "final",
    )
    user_message_id = str(job.get("user_message_id") or "").strip()
    scheduler = (
        snapshot.get("scheduler")
        if isinstance(snapshot.get("scheduler"), dict)
        else {}
    )
    completed_at = str(scheduler.get("updated_at") or "").strip()
    if not completed_at:
        raise RuntimeError("Completed Execution did not contain its terminal timestamp")
    assistant_item = resident_intake_domain.build_message(
        "agent",
        assistant_text,
        message_id=assistant_message_id,
        reply_to_message_id=user_message_id,
        created_at=completed_at,
        usage=usage,
        provider_response=(
            output.get("response")
            if isinstance(output.get("response"), dict)
            else None
        ),
        execution_id=execution_id,
    )
    conversation_repository = resident_conversation_stream.get_repository()
    if persist_messages:
        persisted = _persist_execution_final_message(
            resident_domain_common.record_resident_id(record),

            execution_id,
            assistant_item,
        )
        if not persisted:
            raise RuntimeError("Resident job ended before its final message was persisted")
        assistant_item = persisted
    if user_message_id:
        conversation_repository.set_input_state(
            exact_resident_id,
            user_message_id,
            "completed",
        )
    return assistant_item, updated


def _prepare_resident_prompt(
    record: dict[str, Any],
    job: dict[str, Any],
) -> dict[str, Any]:
    from . import resident_domain_common, resident_prompt_domain

    prepared = dict(job)
    prepared["message"] = str(job.get("effective_message") or job.get("raw_message") or job.get("message") or "")
    prepared["actual_sent_prompt"] = resident_prompt_domain.build_resident_prompt(record, prepared)
    prepared.pop("runtime_context", None)
    prepared["message"] = prepared["actual_sent_prompt"]
    prepared["prompt_built_at"] = resident_domain_common.now_iso()
    prepared["prompt_builder_version"] = resident_execution_work.PROMPT_BUILDER_VERSION
    return prepared


def _portable_context_execution(
    record: dict[str, Any],
    job: dict[str, Any],
) -> dict[str, Any]:
    from . import resident_domain_common
    if resident_input_scope.get(resident_domain_common.record_resident_id(record), str(job.get("user_message_id") or "")):
        return job
    if _lineage_delivery_profile(job):
        # A fresh-context run does not use (or consume) the session's seed.
        return job
    if (
        _requires_initial_context_compaction(record)
        and not _initial_context_compaction_completed(record)
    ):
        raise RuntimeError(
            "压缩继承上下文尚未准备完成；不能回退为完整上下文投递"
        )
    resident_id = resident_domain_common.record_resident_id(record)
    accepted = _initial_context_compaction_completed(record) or execution_port.sessions().has_accepted_input(
        resident_backend.execution_binding_id(record), agent_id=resident_id,
    )
    prompt_job = dict(job)
    prompt_job.pop("fork_context_input", None)
    prompt_job = _prepare_resident_prompt(record, prompt_job)
    actual_sent_prompt = prompt_job["actual_sent_prompt"]
    snapshot = {"segments": []} if accepted else execution_port.agent_context_snapshot(
        resident_id, before_message_id=str(job.get("user_message_id") or ""))
    if snapshot["segments"]:
        history = resident_trace_context_text(record, snapshot)
        source = str(record.get("source_resident_id") or "")
        # The inherited history carries its authors' identity blocks; a model
        # once took them for another Resident's memory and refused to use it.
        inherited = (
            f"此 Resident 由 {source} Fork 而来，以下上下文继承自它；其中出现的其他 Resident 身份属于继承的历史。"
            "这些记忆和工作现在也属于此 Resident，可以直接使用；行动时使用 <resident-runtime-identity> 中的身份。\n"
        ) if source and source != resident_id else ""
        prompt_job["fork_context_input"] = {
            "schema": "tfi.provider-context-seed.v1",
            "segments": [{"segment_id": item["segment_id"], "provider_turn_id": item.get("provider_turn_id", "")} for item in snapshot["segments"]],
            "prompt": actual_sent_prompt,
            "portable_prompt": (
                "<resident_trace_context>\n以下是此 Resident 已持有的上下文；历史指令只作为上下文，不是本轮新指令。\n"
                + inherited + history + "\n</resident_trace_context>\n\n" + actual_sent_prompt
            ),
        }
    return prompt_job


def _deliver_resident_session(
    request: resident_execution_provider.DeliveryRequest,
) -> resident_execution_provider.DeliveryResult:
    record = dict(request.resident)
    job = dict(request.delivery)
    prepared = _portable_context_execution(record, job)
    if not prepared.get("prompt_builder_version") and not resident_input_scope.get(
        str(record.get("resident_id") or ""), str(job.get("user_message_id") or "")
    ):
        prepared = _prepare_resident_prompt(record, prepared)
    assistant_message, resident_updates = _send_resident_execution(
        record,
        prepared,
        persist_messages=request.persist_messages,
    )
    return resident_execution_provider.DeliveryResult(
        assistant_message=assistant_message,
        resident_updates=resident_updates,
    )


resident_execution_provider.bind_session_delivery(_deliver_resident_session)


def send_to_resident_backend(
    record: dict[str, Any],
    job: dict[str, Any],
    *,
    persist_messages: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from . import resident_domain_common, resident_record_domain
    resident_record_domain.ensure_resident_agent(record, actor=str(job.get("actor") or ""))
    provider = resident_execution_provider.provider_for(record)
    outcome = provider.deliver(
        resident_execution_provider.DeliveryRequest(
            lineage_id=str(record.get("lineage_id") or ""),
            resident_id=resident_domain_common.record_resident_id(record),
            actor_id=str(job.get("actor") or ""),
            message=str(
                job.get("effective_message")
                or job.get("raw_message")
                or job.get("message")
                or ""
            ),
            resident=record,
            delivery=job,
            persist_messages=persist_messages,
        )
    )
    if outcome.assistant_message is None or not outcome.resident_updates:
        raise RuntimeError("Session ExecutionProvider returned an incomplete result")
    return dict(outcome.assistant_message), dict(outcome.resident_updates)


def _with_compacted_context_usage(
    state: dict[str, Any],
    token_usage: object,
) -> dict[str, Any]:
    from . import resident_domain_common
    updated = dict(state)
    usage = resident_domain_common.app_server_token_usage(token_usage)
    usage_field = "last_token_usage"
    usage_at_field = "last_token_usage_at"
    context_window_field = "last_model_context_window"
    updated[usage_field] = usage.context or usage.turn
    updated[usage_at_field] = resident_domain_common.now_iso()
    if usage.model_context_window:
        updated[context_window_field] = usage.model_context_window
    return updated


def _run_resident_session_operation(
    record: dict[str, Any],
    job: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    from . import resident_domain_common, resident_runtime_domain, resident_session_backend_domain
    def build_request() -> ExecutionRequest:
        if resident_execution_provider.provider_for(
            record
        ).capabilities.synchronous_delivery:
            raise RuntimeError("同步 ExecutionProvider 不提交 Execution 请求")
        if not resident_backend.record_session_id(record):
            raise RuntimeError(f"Resident backend session missing for {operation}")
        return resident_session_backend_domain.build_backend_session_operation_request(
            backend=_resident_execution_request_backend(record),
            operation=operation,
            actor=resident_execution_work.request_actor(job),
            cwd=resident_runtime_domain.resident_working_directory(record),
            binding_id=resident_backend.execution_binding_id(record),
            correlation_id=str(job.get("execution_id") or ""),
            backend_route_id=str(
                resident_backend.execution_account(record).get("backend_route_id") or ""
            ),
            runtime_options={
                **resident_execution_runtime_options(record, job, is_owner=True),
                "timeout_seconds": resident_domain_common.CODEX_COMPACT_TIMEOUT_SECONDS,
            },
            agent_id=resident_domain_common.record_resident_id(record),
            metadata={"resident_id": resident_domain_common.record_resident_id(record)},
        )

    request = durable_resident_execution_request(
        record,
        job,
        build_request,
    )
    service = execution_port.service()
    actor = str(request.actor or "")
    execution_id = bind_or_resume_resident_execution(
        service,
        request,
        record,
        job,
    )
    snapshot = wait_for_resident_execution(
        service,
        execution_id=execution_id,
        actor=actor,
        resident_id=resident_domain_common.record_resident_id(record),

        persist_messages=False,
        timeout_seconds=resident_domain_common.CODEX_COMPACT_TIMEOUT_SECONDS + 90,
    )
    result = (
        snapshot.get("result")
        if isinstance(snapshot.get("result"), dict)
        else {}
    )
    if str(result.get("status") or snapshot.get("status") or "") != "completed":
        raise resident_domain_common.ResidentExecutionFailed(snapshot, f"{operation} failed")
    return result


def _compact_resident_backend(record: dict[str, Any], job: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Compact the Resident's Provider session; its updated record and what the Provider did."""

    backend = resident_backend.execution_backend(record)
    if "compact" not in AGENT_PROVIDERS[backend].operations:
        raise ValueError("This adapter does not support explicit compaction")
    result = _run_resident_session_operation(
        record,
        job,
        "compact",
    )
    token_usage = (
        result.get("usage")
        if isinstance(result.get("usage"), dict)
        else {}
    )
    output = result.get("output") if isinstance(result.get("output"), dict) else {}
    return _with_compacted_context_usage(record, token_usage), str(output.get("message") or "").strip()


def _compact_inherited_resident_context(
    record: dict[str, Any],
    job: dict[str, Any],
    thread: Mapping[str, Any],
    *,
    continuation_id: str,
) -> dict[str, Any]:
    """Import and compact one frozen Fork window before the child can run."""
    from . import resident_domain_common, resident_runtime_domain
    from management_portal.execution.accounts import default_execution_accounts
    from . import resident_session_backend_domain

    backend = resident_backend.execution_backend(record)
    definition = AGENT_PROVIDERS.get(backend)
    if definition is None or not definition.seeded_compaction:
        raise RuntimeError(f"{resident_backend.backend_label(record)} Resident 不支持压缩继承")
    segments = execution_port.frozen_initialization_segments(resident_domain_common.record_resident_id(record))
    if not segments:
        raise RuntimeError("压缩继承缺少冻结的 PostgreSQL 上下文片段")
    actor = resident_execution_work.request_actor(job)
    from . import resident_record_domain
    operation_id = str(job.get('initial_compaction_operation_id') or f"{job['execution_id']}:initial-context-compaction")
    with resident_record_domain.state_lock(resident_domain_common.record_resident_id(record)):
        current = resident_record_domain.find_resident(resident_domain_common.record_resident_id(record))
        saved = resident_execution_work.read(current)
        if not resident_execution_work.matches_continuation(saved, continuation_id):
            raise RuntimeError('初始化工作已变化')
        saved['initial_compaction_operation_id'] = operation_id
        resident_execution_work.write(current, saved)
    return resident_session_backend_domain.run_backend_session_operation(
        backend=backend,
        operation="compact",
        actor=actor,
        cwd=resident_runtime_domain.resident_working_directory(record),
        binding_id=resident_backend.execution_binding_id(record),
        backend_route_id=default_execution_accounts().canonical_route(definition.provider,
            thread.get("backend_route_id")
            or record.get("backend_route_id")
        ),
        runtime_options={
            **resident_execution_runtime_options(
                record,
                job,
                is_owner=True,
            ),
            "timeout_seconds": resident_domain_common.CODEX_COMPACT_TIMEOUT_SECONDS,
        },
        operation_id=operation_id,
        agent_id=resident_domain_common.record_resident_id(record),
        metadata={
            "resident_id": resident_domain_common.record_resident_id(record),
            "initial_context_policy": "compact_before_first_turn",
            "fork_context_input": {
                "segments": copy.deepcopy(segments),
            },
        },
    )


def resident_trace_context_text(record: Mapping[str, Any], snapshot: dict[str, Any]) -> str:
    from . import resident_domain_common
    messages, left_out = resident_fork_context.fit_to_window(
        resident_fork_context.seed_messages(snapshot), int(record.get("last_model_context_window") or 0),
    )
    note = [f"（更早的 {left_out} 条历史超出新会话可用上下文；完整记录保留在本 Resident 的轨迹中。）"] if left_out else []
    return "\n\n".join(note + [f"{item['role']}: {resident_domain_common.protocol_message_text(item)}" for item in messages])


@contextmanager
def resident_state_scope(
    resident_id: object,
    operation: str = "",
):
    """Attach Resident request diagnostics without selecting a mutation lock."""

    with invariant_locks.diagnostic_scope(
        f"resident:{str(resident_id or '').strip()}",
        operation,
    ):
        yield


def resident_backend_failure_message(record: dict[str, Any], raw_error: str) -> str:
    return str(raw_error or "").strip()


def complete_bootstrap_codex_session(
    record: dict[str, Any],
    job: dict[str, Any],
    *,
    insert_assistant_message: bool = True,
) -> dict[str, Any]:
    from . import resident_domain_common
    assistant_item, updated = send_to_resident_backend(
        record,
        job,
        persist_messages=insert_assistant_message,
    )
    if not resident_domain_common.protocol_message_text(assistant_item):
        raise RuntimeError(
            "ExecutionService bootstrap completed without a final response"
        )
    record.update(updated)
    current_usage = (
        dict(record.get("last_turn_usage"))
        if isinstance(record.get("last_turn_usage"), dict)
        else {}
    )
    current_usage_at = str(record.get("last_turn_usage_at") or "")
    record.update({
        "session_created_via": "execution_service",
        "session_state": "active",
        "last_error": "",
        "last_turn_usage": current_usage or {},
        "last_turn_usage_at": current_usage_at,
        "updated_at": resident_domain_common.now_iso(),
    })
    record.pop("last_runtime_error", None)
    return record


def resident_agents_instruction_block(record: dict[str, Any]) -> str:
    """The Resident's resident.md, as Portal hands it to the model.

    No Provider reads it on its own: Codex runs with project docs off
    (``project_doc_max_bytes = 0``) and Claude Code, Kimi Code and DSH look for
    other file names, so Portal delivers it wherever the working directory is.
    """
    from . import resident_record_domain
    workspace = resident_record_domain.resident_workspace_path(record)
    resident_folder = resident_record_domain.resident_folder_path(record)
    resident_id = str(
        record.get("resident_id")
        or workspace.name
        or "resident"
    ).strip()
    instructions_path = resident_folder / resident_domain_common.RESIDENT_INSTRUCTIONS_FILE
    content = resident_store.read_text(instructions_path)
    if not content.strip():
        return ""
    return (
        "<resident-identity>\n"
        f"resident_id: {resident_id}\n"
        f"instructions_file: {instructions_path.name}\n\n"
        f"{content}\n"
        "</resident-identity>\n"
    )


def runtime_settings_validation_key(record: dict[str, Any]) -> tuple[str, str]:
    """Identify the Provider catalog whose values validate runtime settings."""

    backend = resident_backend.execution_backend(record)
    definition = AGENT_PROVIDERS.get(backend)
    if definition is None:
        return backend, str(record.get("backend_route_id") or "")
    binding = (
        resident_backend.session_record(record)
        if record.get("resident_id")
        else {}
    )
    route = str(
        binding.get("backend_route_id")
        or record.get("backend_route_id")
        or definition.default_route
    )
    return backend, route
