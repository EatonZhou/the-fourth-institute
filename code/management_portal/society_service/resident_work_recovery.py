"""Durable recovery of failed inputs, independent of the steward's model.

Background session replacement and the coordinator share a bounded retry budget.
Explicit recovery, including a requested session replacement, does not spend it.
Family stewards choose member migrations; the coordinator can migrate a
quota-exhausted steward so supervision does not depend on its own model.
"""
from __future__ import annotations

import time
from typing import Any, Mapping

from management_portal.execution.provider_failures import (
    failure_code, is_egress_block_failure, is_network_failure, is_usage_limit_failure,
)
from . import resident_backend, resident_conversation_stream, resident_execution_work, resident_input_recovery
from .domain_ports import execution_port
from .resident_account_policy import account_available, fallback_account

MAX_RECOVERY_ATTEMPTS = 2
RECHECK_SECONDS = 600


def latest_failed_input(record: Mapping[str, Any]) -> dict[str, Any]:
    """Only the latest round is eligible for automatic restart recovery."""
    entries = resident_input_recovery.checkpoints(record)
    if not entries:
        return {}
    entry = entries[-1]
    try:
        state = resident_conversation_stream.get_repository().input_state(
            str(record["resident_id"]), str(entry["message_id"]),
        )
    except KeyError:
        return {}
    return {**entry, "input_state": state} if state.get("state") == "failed" else {}


def recovery_entry(record: Mapping[str, Any], message_id: str) -> dict[str, Any]:
    return dict((record.get("work_recovery") or {}).get(message_id) or {})


def set_recovery_entry(record: dict[str, Any], message_id: str, **values: Any) -> None:
    # Retain counters for every round that can still be requeued.
    retained = {str(e["message_id"]) for e in resident_input_recovery.recent_executions(str(record["resident_id"]))} | {message_id}
    job = resident_execution_work.read(record)
    retained.add(str(job.get("user_message_id") or ""))
    retained.update(str(item.get("user_message_id") or "") for item in resident_execution_work.normalized_queue(job))
    ledger = {k: v for k, v in (record.get("work_recovery") or {}).items() if k in retained}
    ledger[message_id] = {**recovery_entry(record, message_id), **values}
    record["work_recovery"] = ledger


def resume_after_restart(record: dict[str, Any], restart: dict[str, Any]) -> None:
    message_id = str(restart.get("resume_message_id") or "")
    if not message_id:
        return
    from .resident_mailbox import MailboxConflict
    try:
        state = resident_conversation_stream.get_repository().input_state(str(record["resident_id"]), message_id)
        if state.get("state") != "failed":
            restart["resume_error"] = "原输入已停止、已恢复或已完成，未自动放回"
            return
        resident_input_recovery.requeue(
            str(record["resident_id"]), str(record["resident_id"]), message_id,
            portal_reason="session_restart_recovery",
            automatic=bool(restart.get("reason")),
        )
        restart["resumed_message_id"] = message_id
    except (MailboxConflict, KeyError) as error:
        # A concurrent/manual recovery or the retry limit must not undo a
        # successful Provider migration.
        restart["resume_error"] = str(error)


def _intake_allows_recovery(record: Mapping[str, Any]) -> bool:
    from . import resident_state
    from .resident_lifecycle_domain import _resident_intake_state
    if _resident_intake_state(dict(record)) == resident_state.INTAKE_OPEN:
        return True
    return False


def _recoverable_input(record: Mapping[str, Any], job: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Recover a failed round only after its active execution has settled."""
    job = resident_execution_work.read(record) if job is None else job
    if resident_execution_work.is_active(job):
        return {}
    if not record.get("last_error") and job.get("outcome") != "failed":
        return {}
    return latest_failed_input(record)


def _notify_steward_blocked(record: Mapping[str, Any], message_id: str, reason: str) -> None:
    from . import family_facade, lineage_human_notifications
    if not family_facade.is_operating_role(record):
        return
    if recovery_entry(record, message_id).get("owner_notified"):
        return
    from .family_owners import recipients
    message = resident_conversation_stream.get_repository().message(str(record["resident_id"]), message_id)
    owners = recipients(record, message=message or {}, creator_only=True)
    if owners:
        lineage_human_notifications.create_notification(
            str(record["lineage_id"]), title=f"请恢复 Family 角色：{record.get('title') or record['resident_id']}",
            message=f"{reason}。原输入 {message_id} 已保留，请处理账号或手动恢复。",
            recipients=owners, severity="error", created_by="portal",
            notification_id=f"work-recovery:{record['resident_id']}:{message_id}",
        )
        from .family_owners import message_creator
        message_creator(record, identity=f"work-recovery:{record['resident_id']}:{message_id}",
                        text=f"Family 内部角色 {record.get('title') or record['resident_id']} 无法继续恢复。{reason}。原输入 {message_id} 已保留。")
        from .resident_lifecycle_domain import _state_lock
        from .resident_workspace_domain import _find_resident, _save_resident_record
        with _state_lock(record["resident_id"]):
            current = _find_resident(record["resident_id"])
            if current:
                set_recovery_entry(current, message_id, owner_notified=True)
                _save_resident_record(current)


def reconcile_failed_work(residents: list[dict[str, Any]] | None = None, *, jobs: Mapping[str, Any] | None = None) -> int:
    import logging
    if residents is None:
        from . import resident_catalog, resident_storage
        catalog = resident_catalog.get_catalog()
        with catalog.state_store.database.read() as connection:
            ids = resident_storage.failed_work_resident_ids(connection, checked_before=time.time() - RECHECK_SECONDS)
        residents = catalog.get_many(ids)
    if not residents:
        return 0
    changed = 0
    context: dict[str, Any] = {"network": {}, "jobs": resident_execution_work.read_many(residents) if jobs is None else jobs}
    for record in residents:
        try:
            changed += _reconcile_failed_work([record], context)
        except Exception:
            # A missing Provider service, malformed input or competing manual
            # recovery must not stop unrelated Residents from being dispatched.
            logging.getLogger(__name__).exception("Failed work recovery deferred for %s", record.get("resident_id"))
    return changed


def _reconcile_failed_work(residents: list[dict[str, Any]], context: dict[str, Any]) -> int:
    """Recheck only eligible failed rounds; no model or sleeping worker required."""
    from . import family_facade, resident_catalog
    from .resident_lifecycle_domain import _lifecycle_operations, _state_lock
    from .resident_workspace_domain import _find_resident, _save_resident_record
    from .resident_restart_domain import restart_resident_session
    from management_portal.execution.provider_egress import provider_network_state

    accounts = context.get("accounts")
    network = context["network"]
    changed = 0
    now = time.time()
    for snapshot in residents:
        resident_id = str(snapshot.get("resident_id") or "")
        if not resident_id or snapshot.get("deleted_at") or family_facade.is_facade(snapshot):
            continue
        if _lifecycle_operations().pending(resident_id):
            continue
        failed = _recoverable_input(snapshot, context.get("jobs", {}).get(resident_id))
        if not failed:
            continue
        message_id = str(failed["message_id"])
        error = failed["input_state"].get("error") or {}
        usage = is_usage_limit_failure(error)
        net = (is_network_failure(error) or is_egress_block_failure(error)
               or failure_code(error) in {"network", "timeout"})
        if not usage and not net:
            continue
        recovery = recovery_entry(snapshot, message_id)
        if recovery.get("disabled"):
            continue
        checked = float(recovery.get("checked_at") or 0)
        if now - checked < RECHECK_SECONDS:
            continue
        if int(recovery.get("attempts") or 0) >= MAX_RECOVERY_ATTEMPTS:
            _notify_steward_blocked(snapshot, message_id, "同一输入已自动恢复两次，仍然失败")
            continue
        # Recovery respects the independently persisted manual intake gate.
        if not _intake_allows_recovery(snapshot):
            continue
        with _state_lock(resident_id):
            record = _find_resident(resident_id)
            if (not record or not _intake_allows_recovery(record)
                    or _recoverable_input(record) != failed):
                continue
            set_recovery_entry(record, message_id, checked_at=now)
            _save_resident_record(record)
        provider = resident_backend.execution_provider(record)
        binding = resident_backend.session_record(record)
        route = str(binding.get("backend_route_id")
                    or record.get("backend_route_id") or "")
        if usage and not route:
            route = str(resident_backend.execution_account(record, binding).get("backend_route_id") or "")
        pending = recovery.get("pending_restart")
        if pending:
            restart_resident_session(resident_id, resident_id, pending, portal_reason="steward_quota_recovery")
            with _state_lock(resident_id):
                current = _find_resident(resident_id)
                set_recovery_entry(current, message_id, pending_restart=None)
                _save_resident_record(current)
            changed += 1
            continue
        if net:
            key = (provider, route)
            if key not in network:
                network[key] = provider_network_state(provider, route) == "allowed"
            ready = network[key]
        else:
            if accounts is None:
                accounts = list(execution_port.service().accounts().get("accounts") or [])
                context["accounts"] = accounts
            ready = any(account_available(a) and a["provider"] == provider
                        and str(a.get("backend_route_id") or "") == route for a in accounts)
        if ready:
            with _state_lock(resident_id):
                current = _find_resident(resident_id)
                if (not current or latest_failed_input(current).get("message_id") != message_id
                        or not _intake_allows_recovery(current) or resident_execution_work.is_active(resident_execution_work.read(current))):
                    continue
                resident_catalog.get_catalog().state_store.transaction(lambda: resident_input_recovery.requeue(
                    resident_id, resident_id, message_id, portal_reason="environment_recovery",
                    automatic=True,
                ))
            changed += 1
        elif usage and family_facade.is_operating_role(record):
            switches = int(recovery.get("switch_attempts") or 0)
            if switches >= MAX_RECOVERY_ATTEMPTS:
                _notify_steward_blocked(record, message_id, "steward 备用后端切换已失败两次")
                continue
            tried = list(recovery.get("tried_accounts") or [])
            target = fallback_account(accounts or [], provider, route, tried, weekly_reserve=True)
            if not target:
                _notify_steward_blocked(record, message_id, "没有已确认有可用额度的备用账号或后端")
                continue
            # Reserve the migration intent before starting it. A crash retries
            # the same idempotent RestartSession, not a new initialization.
            payload = {"target_provider": target["provider"],
                       "target_account": str(target.get("backend_route_id") or ""),
                       "context_strategy": "compact", "active_execution_policy": "wait",
                       "resume_failed_input": True,
                       "idempotency_key": f"work-recovery:{message_id}:{switches + 1}"}
            with _state_lock(resident_id):
                current = _find_resident(resident_id)
                if not current or _recoverable_input(current) != failed:
                    continue
                set_recovery_entry(current, message_id, pending_restart=payload,
                                   switch_attempts=switches + 1,
                                   tried_accounts=[*tried, str(target["account_id"])])
                _save_resident_record(current)
            restart_resident_session(resident_id, resident_id, payload, portal_reason="steward_quota_recovery")
            with _state_lock(resident_id):
                current = _find_resident(resident_id)
                set_recovery_entry(current, message_id, pending_restart=None)
                _save_resident_record(current)
            changed += 1
    return changed
