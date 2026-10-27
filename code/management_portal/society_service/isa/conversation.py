"""Conversation and context instructions owned by SocietyService (decision_2fe6fd5367fb)."""
from __future__ import annotations

import time
from management_common.service_shutdown import wait_for_shutdown
from typing import Any, Iterator

from management_portal.hatg.contracts import InstructionError, invalid, not_found
from management_portal.hatg.kernel import Invocation

from .common import domain, lineage_of_resident, protocol
from ..resident_conversation_stream import DEFAULT_HISTORY_LIMIT


STREAM_POLL_SECONDS = 1.0
STREAM_HEARTBEAT_SECONDS = 15.0


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
) -> dict[str, Any]:
    from management_portal.society_service import outward_messages, resident_protocol

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
    )
    # A Family member sending outside its Family speaks as the facade (decision_44cf587c1721).
    result = outward_messages.deliver_to_resident(payload, inv.caller, delivery_context=context, reply_to=reply_to)
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
    if (inv.argument("activation_policy") or "enqueue_and_trigger") != "enqueue_and_trigger":
        raise invalid("发给用户的消息不进入队列，activation_policy 只能省略或为 enqueue_and_trigger")
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


def _credential_requests(inv: Invocation) -> list[dict[str, str]]:
    """The temporary credentials a delivery lends with its message: ``{a, resource_id}`` each."""
    items = inv.argument("temporary_credentials")
    if items is None:
        return []
    if not isinstance(items, list) or len(items) > 20:
        raise invalid("temporary_credentials 是最多 20 项的数组")
    found = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"a", "resource_id"} or not all(
                isinstance(item[key], str) and item[key].strip() for key in ("a", "resource_id")):
            raise invalid("temporary_credentials 的每一项只包含非空的 a 与 resource_id")
        found.append({"a": item["a"].strip(), "resource_id": item["resource_id"].strip()})
    return found


def _lend(inv: Invocation, resident_id: str, message_id: str, credentials: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Issue the credentials for the delivered message; each one is checked against what the caller holds now."""
    from management_portal import permission_system

    issued = []
    for item in credentials:
        issued.append(permission_system.manage("IssueTemporaryCredential", {
            "principal_id": resident_id, "a": item["a"], "resource_id": item["resource_id"], "message_id": message_id,
        }, actor=inv.caller))
    return issued


def deliver_message(inv: Invocation) -> dict[str, Any]:
    message = inv.argument("message") or {}
    unknown = sorted(set(message) - {"message_id", "role", "content_parts", "quote"})
    if unknown:
        raise invalid("message 只包含 message_id、role、content_parts 和 quote", unknown_fields=unknown)
    credentials = _credential_requests(inv)
    if inv.argument("user_id"):
        if "quote" in message or inv.argument("reply_to_message_id"):
            # Only a user message quotes; a message to a user is the sender's own (decision_c524c775e0be).
            raise invalid("发给用户的消息不带引用")
        if credentials:
            raise invalid("临时凭证只随发给 Resident 的消息签发")
        return deliver_to_user(inv, inv.argument("user_id"), message)
    resident_id = inv.argument("resident_id")
    _check_answer_credentials(resident_id, message, credentials)
    if credentials:
        _check_lendable(inv, credentials)
    result = deliver(inv, resident_id, message, business_ref=inv.argument("business_ref") or "",
                     reply_to=inv.argument("reply_to_message_id") or "")
    if credentials:
        # The message exists now and its input is open: the credentials end with that input (decision_b820faf42218 §5.6).
        result["temporary_credentials"] = _lend(inv, result["resident_id"], result["message_id"], credentials)
    return result


def _check_answer_credentials(resident_id: object, message: dict[str, Any], credentials: list[dict[str, str]]) -> None:
    """An answer lends exactly the permissions its approved questions asked for (decision_1e2eb869bc10)."""
    from management_common.async_questions import ANSWER_PART, approved_credentials, question_set
    from management_portal.society_service import resident_conversation_stream

    answer = next((part for part in message.get("content_parts") or []
                   if isinstance(part, dict) and part.get("type") == ANSWER_PART), None)
    if answer is None:
        return
    try:
        source = resident_conversation_stream.get_repository().message(
            resident_id, str(answer.get("source_message_id") or "").strip())
    except ValueError:
        source = None  # Delivery reports the invalid Resident.
    questions = question_set(source)
    requested = {(item["a"], item["resource_id"])
                 for item in approved_credentials(questions["questions"], answer.get("answers"))} if questions else set()
    lent = [(item["a"], item["resource_id"]) for item in credentials]
    if set(lent) != requested or len(set(lent)) != len(lent):
        raise invalid("回答随消息交出的权限必须正好是所同意的问题请求的权限")


def _check_lendable(inv: Invocation, credentials: list[dict[str, str]]) -> None:
    """Before delivering: the caller holds every A node it lends, under the active scheme."""
    from management_portal import permission_system

    for item in credentials:
        check = permission_system.manage(
            "CheckPermission", {"principal_id": inv.caller, "a": item["a"], "resource_id": item["resource_id"]},
            actor=inv.caller)
        if not check.get("allowed"):
            raise InstructionError("instruction.forbidden", f"调用者对 {item['resource_id']} 没有 {item['a']}，不能随消息签发",
                                   details={"a": item["a"], "resource_id": item["resource_id"]})


def relay_family_message(inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service.family_quota import relay_message

    if inv.argument("reassign") is True:
        from management_portal.society_service.family_reassign import reassign_request

        return reassign_request(inv.argument("resident_id"), inv.argument("message_id"),
                                inv.argument("member_id"), inv.caller, reason=inv.argument("reason") or "")
    return relay_message(inv.argument("resident_id"), inv.argument("message_id"),
                         inv.argument("member_id"), inv.caller,
                         quota_reviewed=inv.argument("quota_reviewed") is True,
                         reason=inv.argument("reason") or "",
                         scope_gap=inv.argument("scope_gap") or "",
                         scope_reviewed=inv.argument("scope_reviewed") is True)


def _forwarded_item(caller: str, item: Any) -> dict[str, Any]:
    """One selected message, read with the caller's own permission."""

    if not isinstance(item, dict):
        raise invalid("messages 的每一项必须包含 resident_id 与 message_id")
    if item.get("chatroom_id"):
        # A Chatroom message (decision_a6bebf4ac283), read as a member of that Chatroom.
        from management_portal import chatroom_backend

        return chatroom_backend.forward_source(caller, item.get("chatroom_id"), item.get("message_id"))
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


def forward_messages(inv: Invocation) -> dict[str, Any]:
    target = inv.argument("target_resident_id")
    chatroom = inv.argument("target_chatroom_id")
    if bool(target) == bool(chatroom):
        raise invalid("target_resident_id 与 target_chatroom_id 需要且只能提供一个")
    if chatroom and inv.argument("activation_policy"):
        raise invalid("转发到聊天室不接受 activation_policy")
    items = [_forwarded_item(inv.caller, item) for item in inv.argument("messages") or []]
    if chatroom:
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
    prepared = protocol().prepare_resident_forward(
        {
            "target_resident_id": target,
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
        history_limit=int(inv.argument("history_limit") or DEFAULT_HISTORY_LIMIT),
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


def list_messages(inv: Invocation) -> dict[str, Any]:
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
        if initial or events or time.monotonic() - last_yield >= STREAM_HEARTBEAT_SECONDS:
            if events:
                cursor = int(events[-1]["cursor"])
            last_yield = time.monotonic()
            initial = False
            # The checkpoint reports the server's current watermark even when
            # the log has no readable events. Its cursor remains the last event
            # actually delivered, so reconnecting cannot skip a limited batch.
            yield [*({"cursor": int(event["cursor"]), "event": event} for event in events),
                   {"cursor": cursor, "high_watermark": int(result["cursor"])}]
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
    from management_portal.society_service import resident_management_domain

    result = resident_management_domain.reset_resident_context(inv.argument("resident_id"), inv.caller)
    return {
        **result,
        "cleanup_pending": bool(result.get("cleanup_pending")),
        "operation_id": str(result.get("operation_id") or ""),
    }


def absorb_context(inv: Invocation) -> dict[str, Any]:
    source = inv.argument("source_resident_id")
    target = inv.argument("target_resident_id")
    lineage_id = lineage_of_resident(target)
    result = domain().absorb_lineage_node_context(
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
    resident_id = inv.argument("resident_id")
    answers = inv.argument("answers") or {}
    payload: dict[str, Any] = {"message_id": inv.argument("message_id")}
    if set(answers) <= {"action", "content"} and answers.get("action") in {"accept", "decline", "cancel"}:
        payload.update(answers)
    else:
        payload.update({"action": "answer", "answers": answers})
    try:
        receipt = protocol().respond_resident_user_input(resident_id, inv.caller, payload)
    except ValueError as exc:
        if "已回答" in str(exc) or "失效" in str(exc):
            raise InstructionError("instruction.conflict", str(exc)) from exc
        raise
    return {**receipt, "status": str(receipt.get("status") or "")}


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


HANDLERS = {
    "RelayFamilyMessage": relay_family_message,
    "DeliverMessage": deliver_message,
    "ForwardMessages": forward_messages,
    "GetConversation": get_conversation,
    "ListMessages": list_messages,
    "ListInputHistory": list_input_history,
    "GetMessage": get_message,
    "ListRecentRounds": list_recent_rounds,
    "SearchMessages": search_messages,
    "GetMessageArtifact": get_message_artifact,
    "GetSentPrompt": get_sent_prompt,
    "GetSubagentRun": get_subagent_run,
    "ClearContext": clear_context,
    "AbsorbContext": absorb_context,
    "ListResidentRequests": list_resident_requests,
    "GetResidentRequest": get_resident_request,
    "AnswerResidentRequest": answer_resident_request,
    "CreateNotification": create_notification,
}
STREAMS = {"WatchConversationEvents": watch_conversation_events}
