"""Resident execution and queue instructions (decision_2a3ff353165c)."""
from __future__ import annotations

import hashlib
from typing import Any

from management_portal.hatg.contracts import InstructionError
from management_portal.hatg.kernel import Invocation

from .common import domain, lineage_of_resident, protocol


def list_resident_states(inv: Invocation) -> dict[str, Any]:
    result = protocol().get_resident_states(
        [str(item).strip().lower() for item in inv.argument("resident_ids") or []], inv.caller,
    )
    states = list(result.get("items") or [])
    if inv.argument("include_viewer_state") is not False:
        from management_portal.resident_view_projection import attach_viewer_state
        states = attach_viewer_state(states, inv.caller)
    return {"states": states}


def get_input_state(inv: Invocation) -> dict[str, Any]:
    return {
        "input_state": protocol().get_resident_input_state(
            inv.argument("resident_id"), inv.caller, inv.argument("message_id"),
            include_progress=inv.argument("include_progress") is True,
            include_context=inv.argument("include_context") is True,
        )
    }


def _queue_revision(items: list[Any]) -> str:
    """Names one waiting order; any arrival, removal or reorder changes it."""

    ids = "\n".join(str(item.get("message_id") or "") for item in items if isinstance(item, dict))
    return "queue-" + hashlib.sha256(ids.encode("utf-8")).hexdigest()[:16]


def _intake_state(resident_id: str, caller: str) -> str:
    state = protocol().get_resident_state(resident_id, caller)
    inner = state.get("state") if isinstance(state.get("state"), dict) else state
    return str(inner.get("intake_state") or "")


def _change_intake(inv: Invocation, transition: str) -> dict[str, Any]:
    target_state = "open" if transition == "open" else "closed"
    # Human or Resident callers change intake by hand; manual opening also
    # records a manual release of the account's quota guard.
    manual = inv.entry != "lineage"
    single = inv.argument("resident_id")
    targets = [single] if single else [str(item).strip().lower() for item in inv.argument("resident_ids") or []]
    changed: list[str] = []
    unchanged: list[str] = []
    failed: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    quota_override_pending = False
    for resident_id in targets:
        try:
            previous = _intake_state(resident_id, inv.caller)
            # A manual Close still runs on a closed Resident: it turns a closure made
            # by the quota guard into a manual one, so quota recovery keeps it closed.
            if previous == target_state and not (manual and transition == "close"):
                unchanged.append(resident_id)
                last = {"previous": previous, "current": previous, "changed": False}
                continue
            lineage_id = lineage_of_resident(resident_id)
            result = domain().change_lineage_resident_state(
                lineage_id,
                {"resident_id": resident_id, "state_domain": "intake", "transition": transition},
                inv.caller,
                allow_quota_override=manual,
            )
            # The transition returns the committed intake state; no second Resident
            # projection is needed merely to classify the result.
            current = str(result.get("intake_state") or result.get("state") or "")
            (changed if previous != current else unchanged).append(resident_id)
            quota_override_pending = quota_override_pending or bool(result.get("quota_override_pending"))
            last = {"previous": previous, "current": current, "changed": previous != current, "state": result}
        except Exception as exc:  # noqa: BLE001 - one Resident failing does not stop the batch
            if single:
                raise
            failed.append({"resident_id": resident_id, "error": str(exc) or type(exc).__name__})
    if single:
        return last
    return {
        "changed_resident_ids": changed,
        "unchanged_resident_ids": unchanged,
        "failed": failed,
        "changed_count": len(changed),
        "unchanged_count": len(unchanged),
        "failed_count": len(failed),
        "quota_override_pending": quota_override_pending,
    }


def open_intake(inv: Invocation) -> dict[str, Any]:
    return _change_intake(inv, "open" if inv.argument("open") else "close")


def _queue_call(function_name: str, inv: Invocation) -> dict[str, Any]:
    resident_id = inv.argument("resident_id")
    lineage_id = lineage_of_resident(resident_id)
    payload = {"resident_id": resident_id}
    if inv.argument("message_id"):
        payload["message_id"] = inv.argument("message_id")
    try:
        return getattr(domain(), function_name)(lineage_id, payload, inv.caller)
    except ValueError as exc:
        text = str(exc)
        if any(word in text for word in ("已经", "不在队列", "已结束", "已开始", "not queued", "no longer")):
            raise InstructionError("instruction.conflict", text) from exc
        raise


def _relayed_message(resident_id: str, message_id: str) -> tuple[bool, tuple[str, str] | None]:
    """Whether the Resident is a facade, and the Resident message its message was relayed to.

    The message is ``None`` until the Router relays it.  A nested Family's
    relays are followed to the member that does the work after ISA has checked
    cancellation authority on the facade named by the caller.
    """

    from management_portal.society_service import family_facade
    from management_portal.society_service.resident_workspace_domain import _find_resident

    if not family_facade.is_facade(_find_resident(resident_id)):
        return False, None
    via, holder = family_facade.relay_holder(str(resident_id or ""), str(message_id or ""))
    return True, (holder if via else None)


FINISHED_INPUT_STATES = frozenset({"completed", "failed", "cancelled"})


def _stored_input_state(resident_id: str, message_id: str) -> dict[str, Any]:
    try:
        return dict(protocol()._stored_resident_input_state(resident_id, message_id) or {})
    except (KeyError, ValueError):
        return {}


def cancel_input(inv: Invocation) -> dict[str, Any]:
    """Stop one message (decision_2a3ff353165c).

    A running turn is cancelled; a waiting request, group or info leaves the
    Mailbox (one that starts meanwhile is stopped instead); a finished message
    keeps its result and the receipt carries its state.  A facade request goes to
    the member message the Router relayed it to, and one not relayed yet is
    withdrawn from the facade (decision_44cf587c1721).
    """
    _, relayed = _relayed_message(inv.argument("resident_id"), inv.argument("message_id"))
    resident_id, message_id = relayed or (inv.argument("resident_id"), inv.argument("message_id"))
    lineage_id = lineage_of_resident(resident_id)
    payload = {"resident_id": resident_id, "message_id": message_id}
    # A submitted Execution can still be queued (quota/capacity/session wait).
    # The public input command resolves its durable owner, preserves terminal
    # results and handles queue-to-execution handoff under the same authority.
    try:
        result = dict(domain().cancel_lineage_resident_execution(lineage_id, payload, inv.caller) or {})
    except (KeyError, ValueError) as exc:
        current = _stored_input_state(resident_id, message_id)
        if str(current.get("state") or "") in FINISHED_INPUT_STATES:
            result = {"already_terminal": True, "input_state": current}
        elif any(word in str(exc) for word in ("已经", "不在队列", "已结束", "已开始", "not queued", "no longer")):
            raise InstructionError("instruction.conflict", str(exc)) from exc
        else:
            raise
    receipt = {**result, "resident_id": resident_id, "message_id": message_id,
               "input_state": result.get("input_state") or _stored_input_state(resident_id, message_id)}
    if relayed is not None:
        receipt.update(facade_message_id=inv.argument("message_id"), member_resident_id=resident_id,
                       member_message_id=message_id)
    return receipt


def clear_mailbox(inv: Invocation) -> dict[str, Any]:
    """ClearMailbox with resident_id; the user_id form runs at Web (web_isa.clear_user_mailbox)."""
    if inv.argument("mode") or inv.argument("section"):
        raise InstructionError("instruction.invalid_arguments", "mode 与 section 只用于清理 User Mailbox；带 resident_id 时取消全部等待消息")
    result = _queue_call("clear_lineage_resident_queue", inv)
    cancelled = int(result.pop("cleared_count", 0))
    return {**result, "resident_id": inv.argument("resident_id"), "cancelled": cancelled}


def reorder_mailbox(inv: Invocation) -> dict[str, Any]:
    order = inv.argument("order")
    if order is None and not inv.argument("message_id"):
        raise InstructionError("instruction.invalid_arguments", "调序需要 order，或 message_id 加一个参照位置")
    try:
        result = protocol().reorder_resident_queue(
            inv.argument("resident_id"), inv.caller,
            message_id=inv.argument("message_id") or "",
            before_message_id=inv.argument("before_message_id") or "",
            after_message_id=inv.argument("after_message_id") or "",
            order=[str(item) for item in order] if order is not None else None,
        )
    except ValueError as exc:
        text = str(exc)
        code = "instruction.conflict" if ("不在等待队列" in text or "为空" in text or "注入" in text) else "instruction.invalid_arguments"
        raise InstructionError(code, text) from exc
    queue = result.get("queue")
    items = (queue.get("items") if isinstance(queue, dict) else queue) or []
    return {"items": items, "revision": _queue_revision(items), "reordered": bool(result.get("reordered"))}


def _mailbox_call(call):
    from management_portal.society_service.resident_mailbox import MailboxConflict

    try:
        result = call()
    except MailboxConflict as exc:
        raise InstructionError("instruction.conflict", str(exc)) from exc
    queue = result.get("queue")
    items = (queue.get("items") if isinstance(queue, dict) else queue) or []
    if "message_ids" in result:
        # RequeueMessage: the messages put back, the request first.
        return {"message_ids": list(result["message_ids"]), "items": items, "revision": _queue_revision(items)}
    return {
        "group_message_id": str(result.get("group_message_id") or ""),
        "member_message_ids": list(result.get("member_message_ids") or []),
        "items": items,
        "revision": _queue_revision(items),
    }


def merge_mailbox_items(inv: Invocation) -> dict[str, Any]:
    return _mailbox_call(lambda: protocol().merge_resident_queue_items(
        inv.argument("resident_id"), inv.caller, [str(item) for item in inv.argument("message_ids") or []],
    ))


def split_mailbox_item(inv: Invocation) -> dict[str, Any]:
    return _mailbox_call(lambda: protocol().split_resident_queue_item(
        inv.argument("resident_id"), inv.caller, inv.argument("message_id"),
    ))


def requeue_message(inv: Invocation) -> dict[str, Any]:
    return _mailbox_call(lambda: protocol().requeue_resident_message(
        inv.argument("resident_id"), inv.caller, inv.argument("message_id"),
    ))


def get_mailbox(inv: Invocation) -> dict[str, Any]:
    """GetMailbox with resident_id; the user_id form runs at Web (web_isa.get_user_mailbox)."""
    if inv.argument("related_resident_id"):
        raise InstructionError("instruction.invalid_arguments", "related_resident_id 只用于读取 User Mailbox")
    result = protocol().get_resident_mailbox(inv.argument("resident_id"), inv.caller)
    queued = [item for item in result.get("pending") or [] if item.get("kind") != "info"]
    return {**result, "revision": _queue_revision(queued)}


def inject_mailbox_item(inv: Invocation) -> dict[str, Any]:
    result = _queue_call("inject_lineage_resident_queue_item_into_active_turn", inv)
    status = "delivered" if result.get("injected") else (
        "next_turn" if result.get("scheduled_next_turn") else "pending"
    )
    return {**result, "status": str(result.get("status") or result.get("delivery_status") or status)}


def cancel_subagent(inv: Invocation) -> dict[str, Any]:
    try:
        return protocol().stop_subagent(
            inv.argument("resident_id"), inv.caller, inv.argument("execution_id"), inv.argument("tool_use_id"),
        )
    except KeyError:
        raise InstructionError("instruction.not_found", "这一轮已经结束或不属于这个 Resident") from None
    except ValueError as exc:
        # The Execution took no more input: its turn ended meanwhile.
        raise InstructionError("instruction.conflict", str(exc)) from None




HANDLERS = {
    "ListResidentStates": list_resident_states,
    "GetInputState": get_input_state,
    "OpenIntake": open_intake,
    "CancelInput": cancel_input,
    "CancelSubagent": cancel_subagent,
    "ClearMailbox": clear_mailbox,
    "ReorderMailbox": reorder_mailbox,
    "MergeMailboxItems": merge_mailbox_items,
    "SplitMailboxItem": split_mailbox_item,
    "RequeueMessage": requeue_message,
    "GetMailbox": get_mailbox,
    "InjectMailboxItem": inject_mailbox_item,
}
