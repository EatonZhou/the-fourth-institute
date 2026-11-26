"""Facade input routing and relay admission, independent of Family policy."""
from __future__ import annotations

from contextlib import contextmanager

from management_common.async_questions import answered_set
from management_common.operation_identity import operation_identity_scope
from management_common.principal import OperationIdentity
from management_common.resident_handoff_cards import selected_card
from management_portal.execution.transport import ExecutionResponseUnknown, ExecutionServiceUnavailable
from management_portal.hatg.contracts import InstructionError

from . import facade_members, family_facade, invariant_locks, resident_access
from . import resident_conversation_stream, resident_record_domain
from .domain_ports import execution_port, resident_domain_port
from .state_store import get_lineage_data_store


def update_facade_members(resident_id, member_ids, actor):
    from management_portal import permission_system
    from . import changes

    facade = resident_record_domain.resolve_resident_runtime_target(
        resident_id, actor, require_visible=True,
    ).record
    if not resident_access.can_manage(facade, actor):
        raise PermissionError("只有门面管理者可以设置转交成员")
    targets = facade_members.normalize(resident_id, member_ids)

    def write(connection):
        previous = set(facade_members.read(connection, resident_id))
        for member_id in targets:
            if member_id in previous:
                continue
            target = resident_record_domain.find_resident_for_admission(member_id)
            if not target or target.get("deleted_at"):
                raise KeyError("转交成员不存在")
            # An independent registration delegates message intake. It cannot
            # grant an authority the registering caller does not hold.
            if not (resident_access.resident_deliverable_to_actor(target, actor)
                    and permission_system.allows(actor, "DeliverMessage", {"resident_id": member_id})):
                raise PermissionError("需要对新增转交成员具有投递权限")
        return facade_members.replace(connection, resident_id, targets)

    with resident_record_domain.state_lock(resident_id):
        members = get_lineage_data_store().database.write(write)
    changes.resident_changed(resident_id)
    return {"resident_id": resident_id, "member_ids": members}


def visible_facade(resident_id, actor):
    records = resident_access.visible_resident_records([resident_id], actor, "portal")
    if not records:
        raise PermissionError("无权读取此门面")
    facade = records[0]
    if not family_facade.is_facade(facade):
        raise ValueError("resident_id 必须为门面")
    return facade


def check_target(resident_id, member_id, actor):
    """Read the target's intake and limiting account window without changing either."""
    return target_status(visible_facade(resident_id, actor), member_id)


def target_status(facade, member_id):
    """Check a target for a facade whose caller or admission is already verified."""
    from . import resident_deferred_restart
    from .resident_account_policy import (
        MIGRATION_RESERVE_PERCENT, account_available, account_credits_active, resident_account, reserve_remaining,
    )

    if not family_facade.is_relay_target(facade, member_id):
        raise ValueError("所选 Resident 不在门面转交成员中")
    member = resident_record_domain.find_resident_for_admission(member_id)
    if not member or member.get("deleted_at"):
        return _readiness("unavailable", "member_unavailable")
    if (resident_deferred_restart.pending(member)
            or resident_record_domain.lifecycle_operations().pending(member_id)):
        return _readiness("unavailable", "lifecycle_pending")
    if resident_record_domain.resident_intake_state(member) != "open":
        return _readiness("unavailable", "intake_closed")
    if family_facade.is_facade(member):
        return _readiness("ready", "facade")
    try:
        accounts = list(execution_port.service().accounts().get("accounts") or [])
        account = resident_account(member, accounts)
    except (ExecutionServiceUnavailable, ExecutionResponseUnknown, TimeoutError):
        return _readiness("unknown", "execution_unavailable")
    if not account:
        return _readiness("unknown", "account_unavailable")
    if account.get("enabled") is False:
        return _readiness("unavailable", "account_disabled")
    if account.get("kind") == "subscription_login" and account.get("login_state") in {"logged_out", "login_expired"}:
        return _readiness("unavailable", "account_login_required")
    remaining = reserve_remaining(account)
    if account_credits_active(account):
        return _readiness("ready", "credits_active", remaining)
    if remaining is None:
        return _readiness("unknown", "quota_unknown")
    if remaining < MIGRATION_RESERVE_PERCENT:
        return _readiness("quota_low", "quota_reserve", remaining)
    if not account_available(account):
        return _readiness("unavailable", "account_protected", remaining)
    return _readiness("ready", "", remaining)


def _readiness(status, reason, remaining=None):
    return {"status": status, "reason": reason, "remaining_percent": remaining}


def relay_message(resident_id, message_id, member_id, actor, *, notice=False,
                  continue_previous=False, reassign=False, reason=""):
    """Transfer an already stored request or keep it as a notice."""
    from . import family_reassign

    if notice and (member_id or reassign or continue_previous):
        raise ValueError("notice 不和 member_id、reassign 或 continue_previous 同用")
    if reassign and continue_previous:
        raise ValueError("reassign 不和 continue_previous 同用")
    if not message_id:
        raise ValueError("转交要求门面的原 message_id")
    if reassign:
        if not member_id:
            raise ValueError("改派要求 member_id")
        return family_reassign.reassign_request(resident_id, message_id, member_id, actor, reason=reason)
    with invariant_locks.family_reassignment_lock(resident_id, message_id) as owned:
        if not owned:
            raise InstructionError("instruction.in_progress", "门面请求正在转交", retryable=True)
        if notice:
            return family_facade.keep_notice(resident_id, message_id, actor, reason)
        if not member_id:
            raise ValueError("转交要求 member_id")
        return _relay_owned(
            resident_id, message_id, member_id, actor, continue_previous=continue_previous, reason=reason,
        )


def _relay_owned(resident_id, message_id, member_id, actor, *, continue_previous, reason):
    from . import family_chat_preferences, family_continuation, outward_messages
    from . import resident_intake_admission, resident_message_contract, resident_protocol

    facade = resident_record_domain.find_resident_for_admission(resident_id)
    if not family_facade.is_facade(facade):
        raise ValueError("目标必须是门面")
    source = (resident_id, message_id)
    mark = family_facade.relay_mark(*source)
    if family_facade.validate_relay(mark, actor) != source:
        raise ValueError("门面已取消，不能转交")
    repository = resident_conversation_stream.get_repository()
    message = repository.message(*source)
    if message is None or message.get("role") != "user":
        raise KeyError("门面原请求不存在")
    current = repository.input_state(*source)["state"]
    if current == "cancelled":
        return {"status": "withdrawn", "member_id": member_id}
    link = family_facade.relay_links(facade).get(message_id)
    if link:
        if link["resident_id"] != member_id:
            raise ValueError("该门面请求已有其他承接成员")
        if current == "queued" and not link.get("archive"):
            family_facade.complete_relay(source, member_id, link["message_id"])
        return {"status": "relayed", "member_id": member_id, "message_id": link["message_id"]}
    if current != "queued" or family_facade.kept_notice(facade, message_id):
        raise ValueError("只能转交尚未承接的排队请求")
    if not family_facade.is_relay_target(facade, member_id):
        raise ValueError("所选 Resident 不在门面转交成员中")
    delivered_id = message_id if answered_set(message) or selected_card(message) else (
        resident_message_contract.derived_message_identity("family-relay", resident_id, message_id, member_id)
    )
    author = str(message["author_id"])
    envelope = {"role": "user", "message_id": delivered_id, "content_parts": message["content_parts"]}
    ledger = get_lineage_data_store().family_relays
    lineage_id = facade["lineage_id"]
    pending = (ledger.get(lineage_id, resident_id).get("pending_deliveries") or {}).get(message_id)
    if pending and pending["member_id"] != member_id:
        raise ValueError("原请求已有待确认的转交目标，请按原目标重试")
    initiator = pending["initiated_by"] if pending else actor
    with operation_identity_scope(OperationIdentity.for_actor(author, initiated_by=initiator)):
        candidate = resident_domain_port.preflight_resident_input(member_id, author, envelope, business_ref=mark)
        if not resident_domain_port.existing_resident_delivery(member_id, candidate):
            family_chat_preferences.validate_target(facade, message, member_id)
            family_continuation.decide(facade, message, continue_previous)
            with ledger.transaction(lineage_id, resident_id) as state:
                state.setdefault("pending_deliveries", {}).setdefault(message_id, {
                    "member_id": member_id, "message_id": delivered_id,
                    "initiated_by": actor, "reason": str(reason or "").strip(),
                })

            @contextmanager
            def guard():
                if not family_facade.is_relay_target(facade, member_id):
                    raise ValueError("目标已不在门面转交成员中")
                yield

            try:
                with resident_intake_admission.bind(member_id, delivered_id, guard, lambda _: None):
                    result = outward_messages.deliver_to_resident(
                        {"resident_id": member_id, "message": envelope,
                         "activation_policy": "enqueue_and_trigger"}, author,
                        delivery_context=resident_protocol.ResidentDeliveryContext(
                            lineage_id=lineage_id, business_ref=mark,
                        ),
                    )
            except Exception as exc:
                if getattr(exc, "commit_state", None) == "not_committed":
                    with ledger.transaction(lineage_id, resident_id) as state:
                        state["pending_deliveries"].pop(message_id, None)
                raise
            delivered_id = str(result.get("message_id") or delivered_id)
    family_facade.complete_relay(source, member_id, delivered_id)
    if repository.input_state(*source)["state"] == "cancelled":
        return {"status": "withdrawn", "member_id": member_id}
    return {"status": "relayed", "member_id": member_id, "message_id": delivered_id}


def requeue_failed(resident_id, message_id, actor):
    """Reopen the existing routing input; the Conversation owns its envelope."""
    from . import lineage_message_events
    from .resident_mailbox import MailboxConflict

    repository = resident_conversation_stream.get_repository()
    with resident_record_domain.state_lock(resident_id), invariant_locks.resident_conversation_lock(resident_id):
        facade = resident_record_domain.find_resident_for_admission(resident_id)
        if not family_facade.is_facade(facade):
            raise ValueError("目标必须是门面")
        if not resident_access.can_manage(facade, actor):
            raise PermissionError("只有门面管理者可以重开失败的路由")
        message = repository.message(resident_id, message_id)
        if not message or message.get("role") != "user":
            raise KeyError("门面原请求不存在")
        state = repository.input_state(resident_id, message_id)
        if state["state"] == "cancelled":
            raise MailboxConflict("原请求已经取消，不能放回路由队列")
        link = family_facade.relay_links(facade).get(message_id)
        if link or state["state"] != "failed":
            return {
                "resident_id": resident_id, "message_id": message_id,
                "status": "relayed" if link else state["state"], "input_state": state,
            }
        state = repository.reopen_input(resident_id, message_id, routing_retry=True)
    lineage_message_events.wake_watchers(facade)
    return {
        "resident_id": resident_id, "message_id": message_id,
        "status": state["state"], "input_state": state, "requeued": True,
    }


def resolve_request(resident_id, message_id, review_message_id, actor, *, reason):
    """Keep the failed receipt and attach a verified completed review."""
    from .relay_review_resolution import resolve

    return resolve(resident_id, message_id, review_message_id, actor, reason=reason)
