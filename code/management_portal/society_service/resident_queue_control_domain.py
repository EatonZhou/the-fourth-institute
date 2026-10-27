"""Resident domain responsibilities: queue control.

What a user does to queued input: reorder, join the running turn, clear,
cancel."""
from __future__ import annotations
from typing import Any
from management_common.actor_identity import canonical_actor_id
from . import (
    family_facade,
    resident_conversation_stream,
    resident_domain_common,
    resident_execution_domain,
    resident_execution_work,
    resident_held_infos,
    resident_input_queue_domain,
    resident_record_domain,
)


def _cancel_resident_queue_item(resident_id: str, actor: str, queue_item_id: str) -> dict:
    from . import resident_domain_common, resident_record_domain
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后操作队列")
    target_item_id = str(queue_item_id or "").strip()
    if not target_item_id:
        raise ValueError("queue_item_id required")
    now = resident_domain_common.now_iso()
    # A waiting info is not in the queue; withdrawing it ends it before any
    # request carries it (decision_b5d349190743).
    info_record = resident_record_domain.find_resident(resident_id)
    if info_record and not family_facade.is_facade(info_record) and resident_held_infos.withdraw(
        info_record, target_item_id, actor_id, manager=True,
    ) is not None:
        conversation_resident_id = str(info_record.get("resident_id") or resident_id)
        resident_conversation_stream.get_repository().set_input_state(
            conversation_resident_id, target_item_id, "cancelled",
            error={"code": "info_withdrawn", "message": "info 已撤回，不再随请求送达"}, updated_at=now,
        )
        return resident_input_queue_domain.resident_operation_result(conversation_resident_id, message_id=target_item_id, cancelled=True)
    if info_record and family_facade.is_facade(info_record):
        # A facade has no queue: a request no member has yet waits as a queued input.
        family_facade.withdraw_request(info_record, target_item_id, actor_id, manager=True)
        return resident_input_queue_domain.resident_operation_result(str(info_record.get("resident_id") or resident_id),
                                          message_id=target_item_id, cancelled=True)
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record:
            raise KeyError("resident not found")
        execution = resident_execution_work.read(record)
        held_head = (resident_execution_work.is_execution_held(execution)
                     and resident_input_queue_domain.queue_item_matches(execution, target_item_id))
        if held_head:
            message_id = str(execution["user_message_id"])
        else:
            selected = next((item for item in resident_execution_work.normalized_queue(execution)
                             if resident_input_queue_domain.queue_item_matches(item, target_item_id)), None)
            if selected is None:
                raise KeyError("queue item not found")
            message_id = str(selected["user_message_id"])
            resident_input_queue_domain.withdraw_waiting_inputs(record, execution, [message_id], now)
    if held_head:
        return dict(resident_execution_domain.cancel_resident_execution_for_target(
            resident_id, actor_id, message_id, continue_queue=True,
        ))
    return resident_input_queue_domain.resident_operation_result(resident_id, message_id=message_id, cancelled=True)


def _reordered_ids(
    ids: list[str],
    *,
    message_id: str,
    before_message_id: str,
    after_message_id: str,
    order: list[str] | None,
) -> list[str]:
    """The waiting order after one relative move or a complete permutation (decision_b5d349190743)."""

    if order is not None:
        wanted = [str(item or "").strip() for item in order]
        if len(set(wanted)) != len(wanted) or sorted(wanted) != sorted(ids):
            raise ValueError("order 必须恰好包含当前全部等待消息 ID，不能重复、遗漏或多出")
        return wanted
    anchor = str(before_message_id or after_message_id or "").strip()
    if bool(before_message_id) == bool(after_message_id):
        raise ValueError("调序需要 before_message_id 与 after_message_id 中的一个")
    if message_id not in ids or anchor not in ids:
        raise ValueError("要移动的消息或参照消息不在等待队列中")
    if anchor == message_id:
        raise ValueError("参照消息不能是要移动的消息本身")
    rest = [item for item in ids if item != message_id]
    position = rest.index(anchor) + (0 if before_message_id else 1)
    return rest[:position] + [message_id] + rest[position:]


def reorder_resident_queue(
    resident_id: str,
    actor: str,
    *,
    message_id: str = "",
    before_message_id: str = "",
    after_message_id: str = "",
    order: list[str] | None = None,
) -> dict[str, Any]:
    """Change only the waiting order; times, authors and contents stay as they were."""

    from . import resident_domain_common, resident_record_domain

    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后操作队列")
    target = resident_record_domain.resolve_resident_runtime_target(resident_id, actor, require_visible=True)
    now = resident_domain_common.now_iso()
    with resident_record_domain.state_lock(target.resident_id):
        record = resident_record_domain.find_resident(target.resident_id)
        if not record:
            raise KeyError("resident not found")
        job = resident_execution_work.read(record)
        queue = resident_execution_work.normalized_queue(job)
        if not queue:
            raise ValueError("等待队列为空，没有可以调序的消息")
        ids = [str(item.get("user_message_id") or "") for item in queue]
        wanted = _reordered_ids(
            ids,
            message_id=str(message_id or "").strip(),
            before_message_id=str(before_message_id or "").strip(),
            after_message_id=str(after_message_id or "").strip(),
            order=order,
        )
        if wanted != ids:
            by_id = {str(item.get("user_message_id") or ""): item for item in queue}
            job["queue"] = [by_id[item] for item in wanted]
            job["updated_at"] = now
            resident_execution_work.write(record, job)
            record["session_state"] = resident_record_domain.record_backend_session_state(record)
            record["updated_at"] = now
            resident_record_domain.save_resident_record(record)
    return resident_input_queue_domain.resident_operation_result(target.resident_id, reordered=wanted != ids, order=wanted)


def inject_resident_queue_input_into_active_turn(
    resident_id: str,
    actor: str,
    queue_item_id: str,
) -> dict[str, Any]:
    from . import resident_record_domain
    target = resident_record_domain.resolve_resident_admission_target(
        resident_id,
        actor,
        require_visible=True,
    )
    return resident_input_queue_domain.inject_queued_input_into_active_turn(
        target.resident_id,
        actor,
        queue_item_id,
    )


def clear_resident_queued_inputs(resident_id: str, actor: str) -> dict[str, Any]:
    from . import resident_domain_common, resident_record_domain

    target = resident_record_domain.resolve_resident_runtime_target(resident_id, actor, require_visible=True)
    resident_id = target.resident_id
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record:
            raise KeyError("resident not found")
        current = resident_execution_work.read(record)
        message_ids = [item["user_message_id"] for item in resident_execution_work.normalized_queue(current)]
        held_head = (str(current.get("user_message_id") or "")
                     if resident_execution_work.is_execution_held(current) else "")
        if message_ids:
            resident_input_queue_domain.withdraw_waiting_inputs(record, current, message_ids, resident_domain_common.now_iso())
    # Already admitted Executions belong to Stop, not ClearMailbox. A held head
    # has not been admitted; cancellation revalidates its input identity.
    errors = []
    if held_head:
        try:
            resident_execution_domain.cancel_resident_execution_for_target(resident_id, canonical_actor_id(actor), held_head, continue_queue=False)
            message_ids.append(held_head)
        except (KeyError, ValueError, PermissionError, RuntimeError) as error:
            errors.append({"message_id": held_head, "error": str(error)})
    return resident_input_queue_domain.resident_operation_result(
        resident_id, cleared_count=len(message_ids), cleared_message_ids=message_ids,
        skipped_message_ids=[], errors=errors,
    )


def cancel_resident_queued_input(
    resident_id: str,
    actor: str,
    queue_item_id: str,
) -> dict[str, Any]:
    from . import resident_record_domain
    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )
    result = _cancel_resident_queue_item(
        target.resident_id,
        actor,
        queue_item_id,
    )
    return dict(result)
