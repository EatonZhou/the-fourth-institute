"""Durable session migrations admitted while the current execution is still running."""
from __future__ import annotations
import copy
import logging
import secrets
from . import resident_catalog, resident_execution_work

def pending(record):
    return (record.get("session_restart") or {}).get("deferred") or {}


def schedule(record, actor, payload, source, intent, *, portal_reason=""):
    from . import resident_domain_common, resident_intake_domain, resident_record_domain
    rid = record["resident_id"]
    with resident_record_domain.state_lock(rid):
        current = copy.deepcopy(resident_record_domain.find_resident(rid))
        previous = pending(current)
        if previous.get("request_id") == payload.get("idempotency_key") and previous:
            return copy.deepcopy(current["session_restart"])
        if previous:
            raise ValueError("已有等待本轮结束的迁移，请先取消原迁移")
        before = "closed" if current.get("intake_open") is False else "open"
        request_id = payload.get("idempotency_key") or "deferred:" + secrets.token_hex(12)
        state = {"status": "accepted", "phase": "waiting_current_execution", "restart_id": "restart_" + secrets.token_hex(12),
                 "request_id": request_id, "resident_id": rid, "submitted_by": actor, "submitted_at": resident_domain_common.now_iso(),
                 "source": {"provider": source["provider"], "account_id": source["account_id"]}, "target": {"provider": intent["target_provider"], "account_id": intent["target_account"]},
                 "requested_strategy": intent["context_strategy"], "intent": intent, "previous_session_retained": True}
        if portal_reason:
            state["reason"] = portal_reason
        def commit():
            if before == "open":
                resident_intake_domain.commit_resident_intake_state(current, "closed")
            state["deferred"] = {"request_id": request_id, "actor": actor,
                                 "payload": {**payload, "idempotency_key": request_id + ":execute"},
                                 "intake_before": before, "transition_id": current.get("intake_transition_id", ""),
                                 "phase": "waiting"}
            current["session_restart"] = state
            resident_record_domain.save_resident_record(current)
        resident_catalog.get_catalog().state_store.transaction(commit)
        return copy.deepcopy(state)


def _release(record, pending):
    from . import resident_intake_domain, resident_record_domain
    def commit():
        from .resident_restart_handoff import restore_preparation
        restore_preparation(record)
        record["session_restart"].pop("deferred", None)
        if pending.get("intake_before") == "open" and pending.get("transition_id") == record.get("intake_transition_id"):
            resident_intake_domain.commit_resident_intake_state(record, "open")
        resident_record_domain.save_resident_record(record)
    resident_catalog.get_catalog().state_store.transaction(commit)


def cancel(record, restart_id):
    deferred = pending(record)
    state = record.get("session_restart") or {}
    if not deferred or deferred.get("phase") != "waiting" or state.get("restart_id") != restart_id:
        return None
    if deferred.get('handoff') and deferred['handoff'].get('phase') != 'withdrawn':
        raise ValueError('排队执行正在交接，请在回执确认后取消迁移')
    state = {**state, "status": "cancelled", "phase": "cancelled"}
    record["session_restart"] = state
    _release(record, deferred)
    return copy.deepcopy(state)


def advance(record):
    """Continue the original request, retaining its explicit or background origin."""
    from . import resident_record_domain, resident_restart_domain
    rid = str(record.get("resident_id") or "")
    if not pending(record):
        return False
    with resident_record_domain.state_lock(rid):
        current = copy.deepcopy(resident_record_domain.find_resident(rid) or {})
        deferred = copy.deepcopy(pending(current))
        if not deferred:
            return False
        state = current.get("session_restart") or {}
        execute_key = deferred["payload"]["idempotency_key"]
        if (state.get("execution_request_id") or state.get("request_id")) == execute_key:
            if state.get("status") in {"completed", "cancelled", "failed"}:
                _release(current, deferred)
                return False
            return True
        if state.get("request_id") != deferred.get("request_id"):
            # A newer human migration takes precedence.
            _release(current, deferred)
            return False
        job = resident_execution_work.read(current)
        needs_handoff = (resident_execution_work.is_active(job)
                         and not resident_execution_work.can_preserve_for_session_restart(job))
    if needs_handoff:
        from .resident_restart_handoff import advance as handoff_unstarted_input
        if not handoff_unstarted_input(current):
            return False  # claimed/running work still finishes on its original route
    try:
        receipt = resident_restart_domain.restart_resident_session(
            rid, deferred["actor"], deferred["payload"], portal_reason=str(state.get("reason") or ""),
        )
    except Exception as exc:
        with resident_record_domain.state_lock(rid):
            current = copy.deepcopy(resident_record_domain.find_resident(rid) or {})
            if pending(current).get("request_id") != deferred["request_id"]:
                return False
            current["session_restart"] = {**(current.get("session_restart") or {}), "status": "failed", "phase": "failed", "error": str(exc)}
            _release(current, deferred)
        return False
    with resident_record_domain.state_lock(rid):
        current = copy.deepcopy(resident_record_domain.find_resident(rid) or {})
        if pending(current).get("request_id") == deferred["request_id"]:
            current["session_restart"]["deferred"]["phase"] = "migrating"
            if receipt.get("status") in {"completed", "cancelled", "failed"}:
                _release(current, deferred)
            else:
                resident_record_domain.save_resident_record(current)
    return receipt.get("status") == "accepted"


def reconcile(records=None):
    if records is None:
        from . import resident_storage
        catalog = resident_catalog.get_catalog()
        with catalog.state_store.database.read() as connection:
            ids = resident_storage.deferred_restart_resident_ids(connection)
        records = catalog.get_many(ids)
    for record in records:
        if pending(record):
            try:
                advance(record)
            except Exception:
                # An unconfirmed handoff keeps its durable intent for retry.
                # It must not prevent other Residents (or the remaining
                # execution recovery stages) from making progress.
                logging.getLogger(__name__).exception(
                    "Deferred session restart reconciliation failed for %s",
                    record.get("resident_id"),
                )
