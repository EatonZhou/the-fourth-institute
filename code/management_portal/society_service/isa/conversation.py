"""Conversation and context instructions owned by SocietyService (decision_2fe6fd5367fb)."""
from __future__ import annotations

import time
from management_common.service_shutdown import wait_for_shutdown
from typing import Any, Iterator

from management_portal.hatg.contracts import InstructionError, invalid, not_found
from management_portal.hatg.kernel import Invocation

from .common import domain, lineage_branches, lineage_of_resident, protocol
from .. import resident_conversation_records


STREAM_POLL_SECONDS = 1.0
STREAM_HEARTBEAT_SECONDS = 15.0


def get_deleted_resident_history(inv: Invocation) -> dict[str, Any]:
    from ..resident_deleted_history import read_history

    return read_history(
        inv.argument("resident_id"), inv.caller,
        section=inv.argument("section") or "identity",
        cursor=inv.argument("cursor") or "", limit=inv.argument("limit") or 50,
        ordinal=inv.argument("ordinal"), tool_index=inv.argument("tool_index"),
        expected_hash=inv.argument("expected_hash") or "", tool_item_id=inv.argument("tool_item_id") or "",
    )


def _message_id(inv: Invocation, message: dict[str, Any], resident_id: str) -> str:
    from management_portal.society_service import resident_message_contract

    supplied = str(message.get("message_id") or "").strip()
    if supplied:
        return supplied
    if inv.idempotency_key:
        return resident_message_contract.derived_message_identity(
            "isa-deliver", resident_id, inv.caller, inv.idempotency_key,
        )
    return resident_message_contract.message_identity()


def deliver(
    inv: Invocation, resident_id: str, message: dict[str, Any], *, business_ref: str = "", reply_to: str = "",
    prepared: bool = False,
) -> dict[str, Any]:
    from management_portal.society_service import outward_messages, resident_protocol

    from management_portal import workflow_delivery_policy
    workflow_delivery_policy.require_delivery(resident_id, inv.caller, inv.context)

    body = dict(message)
    role = str(body.get("role") or "user")
    if role != "user":
        raise invalid("message.role 只能是 user")
    body["role"] = "user"
    body["message_id"] = _message_id(inv, body, resident_id)
    payload: dict[str, Any] = {
        "resident_id": resident_id,
        "message": body,
        "activation_policy": inv.argument("activation_policy") or "enqueue_and_trigger",
    }
    context = resident_protocol.ResidentDeliveryContext(
        lineage_id=lineage_of_resident(resident_id),

        business_ref=business_ref,
        channel=inv.context.channel,
        workflow_context=inv.context,
    )
    constraints = inv.argument('execution_constraints')
    if constraints is not None:
        if constraints != {'version': 1, 'text_only': True, 'context': 'fresh'} or inv.argument('context_policy') != 'fresh':
            raise invalid('execution_constraints requires the v1 text_only fresh contract')
        if not body.get('content_parts') or any(not isinstance(part, dict) or part.get('type') != 'text'
                                                for part in body['content_parts']):
            raise invalid('text_only delivery only accepts explicit text parts')
    if inv.argument("context_policy") == "fresh":
        from .. import lineage_program_resident_inputs, resident_conversation_stream

        if payload["activation_policy"] != "enqueue_and_trigger":
            raise invalid("fresh 上下文只用于独立排队的新任务")
        existing = resident_conversation_stream.get_repository().message(resident_id, body["message_id"])
        if existing and not lineage_program_resident_inputs.delivery_profile(
            context.lineage_id, body["message_id"],
        ).get("isolated"):
            raise invalid("不能将已接收的会话输入改成新上下文任务")
        lineage_program_resident_inputs.record_delivery_profile(
            context.lineage_id, body["message_id"], {"isolated": True, **({'execution_constraints': constraints} if constraints is not None else {})},
        )
    # A Family member sending outside its Family speaks as the facade (decision_44cf587c1721).
    reply_options = {"allow_implicit_reply": False} if prepared else {}
    result = outward_messages.deliver_to_resident(
        payload, inv.caller, delivery_context=context, reply_to=reply_to, **reply_options,
    )
    return {
        **result,
        "resident_id": resident_id,
        "message_id": str(result.get("message_id") or body["message_id"]),
        "input_state": result.get("input_state") or {},
    }


def deliver_to_user(inv: Invocation, user_id: str, message: dict[str, Any]) -> dict[str, Any]:
    """A message to a user stays in the Conversation it is sent from; it starts no work (decision_c524c775e0be)."""

    from management_portal.society_service import outward_messages, resident_message_contract

    if str(message.get("role") or "user") != "user":
        raise invalid("message.role 只能是 user")
    supplied = str(message.get("message_id") or "").strip()
    if supplied:
        message_id = resident_message_contract.message_identity(supplied)
    elif inv.idempotency_key:
        message_id = resident_message_contract.derived_message_identity(
            "isa-deliver", f"user:{user_id}", inv.caller, inv.idempotency_key,
        )
    else:
        message_id = resident_message_contract.message_identity()
    result = outward_messages.deliver_to_user(
        inv.caller, user_id, message.get("content_parts"),
        message_id=message_id, business_ref=inv.argument("business_ref") or "",
    )
    return {**result, "input_state": {}}


def _delivery_message(inv: Invocation, *, user: bool = False) -> dict[str, Any]:
    message = inv.argument("message") or {}
    allowed = {"message_id", "role", "content_parts", "metadata"}
    if not user:
        allowed.add("quote")
    unknown = sorted(set(message) - allowed)
    if unknown:
        raise invalid("message 包含不支持的字段", unknown_fields=unknown)
    return message


def deliver_user_message(inv: Invocation) -> dict[str, Any]:
    return deliver_to_user(inv, inv.argument("user_id"), _delivery_message(inv, user=True))


def prepare_resident_input(inv: Invocation) -> dict[str, Any]:
    from management_portal import permission_system, workflow_delivery_policy
    from .. import resident_prepared_inputs

    resident_id = inv.argument("resident_id")
    message = _delivery_message(inv)
    delivery_arguments = {**inv.call.arguments, "activation_policy": "enqueue_and_trigger"}
    if not permission_system.allows(inv.caller, "DeliverMessage", delivery_arguments):
        raise InstructionError("instruction.forbidden", "缺少向该 Resident 投递的权限")
    workflow_delivery_policy.require_delivery(resident_id, inv.caller, inv.context)
    return resident_prepared_inputs.prepare(
        resident_id, inv.caller, message, context_policy=inv.argument("context_policy"),
        execution_constraints=inv.argument("execution_constraints"), business_ref=inv.argument("business_ref") or "",
    )


def deliver_message(inv: Invocation) -> dict[str, Any]:
    from .. import resident_prepared_inputs

    message = _delivery_message(inv)
    resident_id = inv.argument("resident_id")
    message = {**message, "message_id": _message_id(inv, message, resident_id)}
    business_ref = inv.argument("business_ref") or ""
    with resident_prepared_inputs.release(
        resident_id, inv.caller, message, activation_policy=inv.argument("activation_policy") or "enqueue_and_trigger",
        context_policy=inv.argument("context_policy"), execution_constraints=inv.argument("execution_constraints"),
        business_ref=business_ref,
    ) as prepared:
        result = deliver(inv, resident_id, message, business_ref=business_ref, prepared=prepared)
    if any(isinstance(part, dict) and part.get("type") == "async_question_answer" for part in message.get("content_parts") or []):
        from management_portal.society_service import family_quota
        # A facade's own question about its Router/Steward account is answered here, not by any Resident.
        family_quota.answer_role_quota_question(result["resident_id"], result["message_id"], inv.caller)
    return result


def relay_message(inv: Invocation) -> dict[str, Any]:
    from ..relay_domain import relay_message as transfer

    resident_id = inv.argument("resident_id")
    reply_to = inv.argument("reply_to_message_id")
    if reply_to is not None:
        if inv.argument("message") is None or any(inv.argument(key) is not None for key in (
                "message_id", "member_id", "notice", "continue_previous", "reassign", "reason",
        )):
            raise invalid("回复必须提供 message，不能同时指定转交参数")
        if not reply_to:
            raise invalid("reply_to_message_id 不能为空")
        result = deliver(inv, resident_id, _delivery_message(inv),
                         business_ref=inv.argument("business_ref") or "", reply_to=reply_to)
        return {**result, "status": "replied"}
    if inv.argument("message") is not None or inv.argument("business_ref") is not None:
        raise invalid("message 和 business_ref 只用于回复门面外发消息")
    return transfer(
        resident_id, inv.argument("message_id"), inv.argument("member_id"), inv.caller,
        notice=inv.argument("notice") is True, continue_previous=inv.argument("continue_previous") is True,
        reassign=inv.argument("reassign") is True, reason=inv.argument("reason") or "",
    )


def _forwarded_item(caller: str, item: Any) -> dict[str, Any]:
    """One selected message, read with the caller's own permission."""

    if not isinstance(item, dict):
        raise invalid("messages 的每一项必须包含 resident_id 与 message_id")
    from management_portal import portal_features

    for _feature, source in portal_features.contributions("conversation.forwarding"):
        if item.get(source.FORWARD_FIELD):
            # A Chatroom message (decision_a6bebf4ac283), read as a member of that Chatroom.
            return source.forward_source(caller, item.get(source.FORWARD_FIELD), item.get("message_id"))
    resident_id = str(item.get("resident_id") or "").strip().lower()
    message_id = str(item.get("message_id") or "").strip()
    if not resident_id or not message_id:
        raise invalid("messages 的每一项必须包含 resident_id 与 message_id")
    message = protocol().get_resident_message(resident_id, caller, message_id)
    if not message:
        raise not_found(f"消息不存在：{message_id}")
    text = "\n\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
    )
    return {
        "role": str(message.get("role") or ""),
        "author": str(message.get("author_id") or ""),
        "title": str(message.get("title") or ""),
        "content": text,
    }


def _forwarded_items(inv: Invocation) -> list[dict[str, Any]]:
    return [_forwarded_item(inv.caller, item) for item in inv.argument("messages")]


def forward_messages_to_chatroom(inv: Invocation) -> dict[str, Any]:
    chatroom = inv.argument("target_chatroom_id")
    items = _forwarded_items(inv)
    from management_portal.hatg.routing import execute_or_raise

    result = execute_or_raise({
        "instruction": "CreateChatroomMessage",
        "arguments": {
            "chatroom_id": chatroom,
            "content_parts": [{"type": "text", "text": protocol().forward_content(
                items, inv.argument("note") or "", quoted=True,
            )}],
        },
        "idempotency_key": inv.idempotency_key,
    }, inv.context)
    return {**result, "chatroom_id": chatroom, "message_id": result["message"]["message_id"]}


def forward_messages_to_resident(inv: Invocation) -> dict[str, Any]:
    items = _forwarded_items(inv)
    prepared = protocol().prepare_resident_forward(
        {
            "target_resident_id": inv.argument("target_resident_id"),
            "messages": items,
            "message": inv.argument("note") or "",
        },
        inv.caller,
    )
    return deliver(inv, prepared["resident_id"], prepared["message"])


def get_conversation(inv: Invocation) -> dict[str, Any]:
    result = protocol().get_resident_conversation_snapshot(
        inv.argument("resident_id"),
        inv.caller,
        history_limit=int(inv.argument("history_limit") or resident_conversation_records.DEFAULT_HISTORY_LIMIT),
        input_message_id=inv.argument("input_message_id") or "",
        include_inheritance=True,
        include_inherited_context=inv.argument("include_inherited_context") is True,
    )
    if inv.argument("include_context_evidence"):
        from ..resident_context_evidence import read_context_evidence
        result["context_evidence"] = read_context_evidence(inv.argument("resident_id"), inv.caller)
    return _paged(result)


def _paged(page: dict[str, Any]) -> dict[str, Any]:
    """A page in the ISA's paging names: the conversation's ``next_page_token`` is the ``next_cursor``."""
    page = dict(page)
    token = page.pop("next_page_token", "")
    if token:
        page["next_cursor"] = token
    return page


def list_message_attempts(inv: Invocation) -> dict[str, Any]:
    from ..resident_business_history import page

    return _paged(page(inv.argument("resident_id"), inv.caller,
                       cursor=inv.argument("cursor") or "", limit=int(inv.argument("limit") or 50),
                       input_message_id=inv.argument("input_message_id") or ""))


def list_messages(inv: Invocation) -> dict[str, Any]:
    if not inv.argument('cursor'):
        raise invalid('cursor required for raw messages')
    return _paged(protocol().get_conversation_messages(
        inv.argument("resident_id"), inv.caller, inv.argument("cursor"),
        limit=int(inv.argument("limit") or 200),
    ))


def list_input_history(inv: Invocation) -> dict[str, Any]:
    return _paged(protocol().get_input_history(
        inv.argument("resident_id"), inv.caller, inv.argument("cursor") or "",
        limit=int(inv.argument("limit") or 50),
    ))


def get_message(inv: Invocation) -> dict[str, Any]:
    message = protocol().get_resident_message(inv.argument("resident_id"), inv.caller, inv.argument("message_id"))
    if not message:
        raise not_found("消息不存在")
    return {"message": message}


def list_recent_rounds(inv: Invocation) -> dict[str, Any]:
    return protocol().recent_rounds(
        inv.argument("resident_id"), inv.caller,
        sender_id=str(inv.argument("sender_id") or "").strip(),
        before_message_id=str(inv.argument("before_message_id") or "").strip(),
        limit=int(inv.argument("limit") or 3),
    )


def search_messages(inv: Invocation) -> dict[str, Any]:
    return _paged(protocol().search_conversation_messages(
        [str(item).strip().lower() for item in inv.argument("resident_ids") or []],
        inv.caller,
        inv.argument("query"),
        page_token=inv.argument("cursor") or "",
        limit=int(inv.argument("limit") or 50),
    ))


def get_message_artifact(inv: Invocation) -> dict[str, Any]:
    return {"artifact": protocol().get_resident_artifact(inv.argument("resident_id"), inv.caller, inv.argument("artifact_id"))}


def watch_conversation_events(inv: Invocation) -> Iterator[list[dict[str, Any]]]:
    resident_id = inv.argument("resident_id")
    cursor = int(inv.argument("after_cursor") or 0)
    limit = int(inv.argument("limit") or 500)
    input_message_id = inv.argument("input_message_id") or ""
    last_yield = time.monotonic()
    initial = True
    while True:
        result = protocol().get_resident_events(
            resident_id, inv.caller, cursor, limit=limit, input_message_id=input_message_id,
        )
        events = result.get("events") or []
        watermark = int(result["cursor"])
        snapshot_required = cursor > watermark
        if snapshot_required:
            # The cursor is ahead of the log (a stale or foreign cursor): waiting would
            # never deliver. Tell the reader to take a snapshot and continue from here.
            cursor = watermark
            events = []
        if snapshot_required or initial or events or time.monotonic() - last_yield >= STREAM_HEARTBEAT_SECONDS:
            if events:
                cursor = int(events[-1]["cursor"])
            last_yield = time.monotonic()
            initial = False
            # The checkpoint reports the server's current watermark even when
            # the log has no readable events. Its cursor remains the last event
            # actually delivered, so reconnecting cannot skip a limited batch.
            yield [*({"cursor": int(event["cursor"]), "event": event} for event in events),
                   {"cursor": cursor, "high_watermark": watermark,
                    **({"snapshot_required": True} if snapshot_required else {})}]
            if events:
                continue
        if wait_for_shutdown(STREAM_POLL_SECONDS):
            return


def get_sent_prompt(inv: Invocation) -> dict[str, Any]:
    return {"prompt": protocol().get_actual_sent_prompt(inv.argument("resident_id"), inv.caller, inv.argument("message_id"))}


def get_subagent_run(inv: Invocation) -> dict[str, Any]:
    try:
        run = protocol().get_subagent_run(
            inv.argument("resident_id"), inv.caller, inv.argument("trace_ref"),
            offset=int(inv.argument("offset") or 0), limit=int(inv.argument("limit") or 200),
        )
    except KeyError:
        raise not_found("这个子 agent 没有记录的步骤") from None
    return {"run": run}


def clear_context(inv: Invocation) -> dict[str, Any]:
    return _reset_context(inv, "reject")


def create_context_reset(inv: Invocation) -> dict[str, Any]:
    return _reset_context(inv, "wait")


def _reset_context(inv: Invocation, active_execution_policy: str) -> dict[str, Any]:
    from management_portal.society_service import resident_management_domain

    result = resident_management_domain.reset_resident_context(
        inv.argument("resident_id"), inv.caller,
        active_execution_policy=active_execution_policy,
        idempotency_key=inv.idempotency_key or "",
    )
    return {
        **result,
        "cleanup_pending": bool(result.get("cleanup_pending")),
        "operation_id": str(result.get("operation_id") or ""),
    }


def absorb_context(inv: Invocation) -> dict[str, Any]:
    source = inv.argument("source_resident_id")
    target = inv.argument("target_resident_id")
    lineage_id = lineage_of_resident(target)
    result = lineage_branches().absorb_lineage_node_context(
        lineage_id, {"source_resident_id": source, "target_resident_id": target}, inv.caller,
    )
    edge = result.get("edge") if isinstance(result.get("edge"), dict) else {}
    delivery = result.get("delivery") if isinstance(result.get("delivery"), dict) else {}
    if delivery.get("status") == "failed":
        # The absorption relation stays recorded; the delivery itself failed.
        raise InstructionError(
            "instruction.failed", str(delivery.get("error") or "投递给目标 Resident 失败"),
            details={"relation_id": str(edge.get("id") or "")}, commit_state="committed",
        )
    return {
        "source_resident_id": source,
        "target_resident_id": target,
        "relation_id": str(edge.get("id") or ""),
        "message_id": str(delivery.get("target_message_id") or ""),
    }


def list_resident_requests(inv: Invocation) -> dict[str, Any]:
    return {"requests": protocol().list_resident_requests(inv.argument("resident_ids"), inv.caller)}


def get_resident_request(inv: Invocation) -> dict[str, Any]:
    request = protocol().get_resident_request(inv.argument("resident_id"), inv.caller, inv.argument("message_id"))
    return {"request": request, "status": request["status"]}


def answer_resident_request(inv: Invocation) -> dict[str, Any]:
    from .. import resident_requests

    return resident_requests.answer(
        inv.argument("resident_id"), inv.caller, inv.argument("message_id"),
        answers=inv.argument("answers"), decision=inv.argument("decision"), content=inv.argument("content"),
    )


def cancel_resident_request(inv: Invocation) -> dict[str, Any]:
    from .. import resident_requests

    return resident_requests.cancel(inv.argument("resident_id"), inv.caller, inv.argument("message_id"))


def create_notification(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id") or ""
    payload = {
        "title": inv.argument("title"),
        "message": inv.argument("message"),
        "severity": inv.argument("severity") or "info",
    }
    for name in ("recipients", "notification_id"):
        if inv.argument(name):
            payload[name] = inv.argument(name)
    result = domain().create_lineage_human_notification(lineage_id, payload, inv.caller)
    notification = result.get("notification") if isinstance(result.get("notification"), dict) else {}
    return {
        "notification_id": str(notification.get("id") or result.get("notification_id") or ""),
        "recipients": list(notification.get("recipients") or result.get("recipients") or []),
        "created": bool(result.get("created")),
    }


def accept_user_authorization(inv: Invocation) -> dict[str, Any]:
    from .. import resident_user_authorization

    return resident_user_authorization.accept(
        caller=inv.caller, execution_id=inv.context.execution_id,
        message_id=inv.argument("message_id"), credentials=inv.argument("credentials"),
        evidence=inv.argument("evidence"),
        expires_in_seconds=inv.argument("expires_in_seconds"),
    )


HANDLERS = {
    "AcceptUserAuthorization": accept_user_authorization,
    "RelayMessage": relay_message,
    "DeliverMessage": deliver_message,
    "PrepareResidentInput": prepare_resident_input,
    "DeliverUserMessage": deliver_user_message,
    "ForwardMessagesToResident": forward_messages_to_resident,
    "ForwardMessagesToChatroom": forward_messages_to_chatroom,
    "GetConversation": get_conversation,
    "ListMessages": list_messages,
    "ListMessageAttempts": list_message_attempts,
    "ListResidentInputHistory": list_input_history,
    "GetMessage": get_message,
    "ListRecentRounds": list_recent_rounds,
    "SearchResidentMessages": search_messages,
    "GetMessageArtifact": get_message_artifact,
    "GetDeletedResidentHistory": get_deleted_resident_history,
    "GetSentPrompt": get_sent_prompt,
    "GetSubagentRun": get_subagent_run,
    "ClearContext": clear_context,
    "CreateContextReset": create_context_reset,
    "AbsorbContext": absorb_context,
    "ListResidentRequests": list_resident_requests,
    "GetResidentRequest": get_resident_request,
    "AnswerResidentRequest": answer_resident_request,
    "CancelResidentRequest": cancel_resident_request,
    "CreateNotification": create_notification,
}
STREAMS = {"WatchConversationEvents": watch_conversation_events}
