from __future__ import annotations

import pytest

from management_portal.hatg.contracts import InstructionError


RESIDENT = "resident_111111111111"
CHATROOM = "chatroom_222222222222"
MESSAGE = "msg_1234567890abcdef"


def test_forward_reads_sources_using_caller_and_persisted_content(monkeypatch):
    from management_portal import chatroom_backend
    from management_portal.society_service import message_forwarding, resident_protocol

    def resident_message(resident_id, actor, message_id):
        assert (resident_id, actor, message_id) == (RESIDENT, "alice", MESSAGE)
        return {"role": "user", "author_id": "bob", "content_parts": [
            {"type": "text", "text": "Stored Resident text"},
            {"type": "file_reference", "text": "Not part of the forwarded text"},
        ]}

    def chatroom_message(actor, chatroom_id, message_id):
        assert (actor, chatroom_id, message_id) == ("alice", CHATROOM, MESSAGE)
        return {"role": "user", "author": "carol", "content": "Stored Chatroom text"}

    monkeypatch.setattr(resident_protocol, "get_resident_message", resident_message)
    monkeypatch.setattr(chatroom_backend, "forward_source", chatroom_message)

    result = message_forwarding.read_sources("alice", [
        {"resident_id": RESIDENT, "message_id": MESSAGE, "content": "supplied by caller"},
        {"chatroom_id": CHATROOM, "message_id": MESSAGE},
    ])
    assert [item["author"] for item in result] == ["bob", "carol"]
    assert [item["content"] for item in result] == ["Stored Resident text", "Stored Chatroom text"]


def test_forward_leaves_source_permission_denial_and_not_found_intact(monkeypatch):
    from management_portal.society_service import message_forwarding, resident_protocol

    ref = {"resident_id": RESIDENT, "message_id": MESSAGE}

    def forbidden(*_):
        raise PermissionError("not visible")

    monkeypatch.setattr(resident_protocol, "get_resident_message", forbidden)
    with pytest.raises(PermissionError, match="not visible"):
        message_forwarding.read_sources("alice", [ref])

    monkeypatch.setattr(resident_protocol, "get_resident_message", lambda *_: None)
    with pytest.raises(InstructionError) as error:
        message_forwarding.read_sources("alice", [ref])
    assert error.value.code == "instruction.not_found"


def test_native_request_answer_and_cancel_remain_distinct(monkeypatch):
    from management_portal.society_service import resident_protocol, resident_requests

    requests = []

    def respond(resident_id, actor, payload):
        assert (resident_id, actor) == (RESIDENT, "alice")
        requests.append(payload)
        return {"status": "pending"}

    monkeypatch.setattr(resident_protocol, "respond_resident_user_input", respond)

    answers = {"choice": {"answers": ["yes"]}}
    content = {"destination": "inbox"}
    assert resident_requests.answer(RESIDENT, "alice", MESSAGE, answers=answers) == {"status": "pending"}
    resident_requests.answer(RESIDENT, "alice", MESSAGE, decision="accept", content=content)
    resident_requests.answer(RESIDENT, "alice", MESSAGE, decision="decline")
    resident_requests.cancel(RESIDENT, "alice", MESSAGE)
    assert requests == [
        {"message_id": MESSAGE, "action": "answer", "answers": answers},
        {"message_id": MESSAGE, "action": "accept", "content": content},
        {"message_id": MESSAGE, "action": "decline"},
        {"message_id": MESSAGE, "action": "cancel"},
    ]

    for kwargs in ({"decision": "cancel"}, {"answers": answers, "decision": "accept"},
                   {"answers": answers, "content": {}}, {"decision": "decline", "content": {}}):
        with pytest.raises(InstructionError) as error:
            resident_requests.answer(RESIDENT, "alice", MESSAGE, **kwargs)
        assert error.value.code == "instruction.invalid_arguments"
    assert len(requests) == 4


def test_user_delivery_keeps_message_identity_and_source(monkeypatch):
    from management_portal.society_service import outward_messages

    def deliver(sender, recipient, parts, **options):
        assert (sender, recipient) == (RESIDENT, "alice")
        assert parts == [{"type": "text", "text": "result"}]
        assert options["business_ref"] == "related-work"
        return {"message_id": options["message_id"], "resident_id": sender}

    monkeypatch.setattr(outward_messages, "deliver_to_user", deliver)
    message = {"content_parts": [{"type": "text", "text": "result"}]}
    first = outward_messages.deliver_user_message(RESIDENT, "alice", message,
        business_ref="related-work", idempotency_key="send-once")
    assert first == outward_messages.deliver_user_message(RESIDENT, "alice", message,
        business_ref="related-work", idempotency_key="send-once")
    assert first["input_state"] == {}

    with pytest.raises(InstructionError):
        outward_messages.deliver_user_message(RESIDENT, "alice", {**message, "quote": {"message_id": MESSAGE}})


def test_public_delivery_policy_rejects_raw_active_turn_injection():
    from management_portal.society_service import resident_message_contract

    assert resident_message_contract.delivery_activation_policy() == "enqueue_and_trigger"
    assert resident_message_contract.delivery_activation_policy("attach_to_next") == "attach_to_next"
    with pytest.raises(ValueError, match="InjectMailboxItem"):
        resident_message_contract.delivery_activation_policy("inject_active_turn")
    assert resident_message_contract.activation_policy("inject_active_turn") == "inject_active_turn"


def test_public_message_contracts_have_one_target_or_request_kind():
    from management_portal.hatg.catalog import instruction_catalog

    catalog = instruction_catalog()
    target = {"resident_id": RESIDENT, "message_id": MESSAGE}
    answer = catalog["AnswerResidentRequest"]
    answer.validate_arguments({**target, "answers": {"question": {"answers": ["response"]}}})
    answer.validate_arguments({**target, "decision": "decline"})
    catalog["CancelResidentRequest"].validate_arguments(target)
    with pytest.raises(InstructionError):
        answer.validate_arguments({**target, "decision": "cancel"})
    with pytest.raises(InstructionError):
        answer.validate_arguments({**target, "decision": "accept", "answers": {"question": {"answers": ["response"]}}})

    for key, value in (
        ("user_id", "alice"), ("activation_policy", "inject_active_turn"),
        ("reply_to_message_id", MESSAGE), ("temporary_credentials", []),
    ):
        with pytest.raises(InstructionError):
            catalog["DeliverMessage"].validate_arguments({"resident_id": RESIDENT, "message": {}, key: value})

    raw = catalog["ListMessages"]
    raw.validate_arguments({"resident_id": RESIDENT, "cursor": "older"})
    with pytest.raises(InstructionError):
        raw.validate_arguments({"resident_id": RESIDENT, "cursor": "older", "view": "attempts"})
    catalog["ListMessageAttempts"].validate_arguments({"resident_id": RESIDENT, "input_message_id": MESSAGE})


def test_message_attempt_detail_reauthorizes_business_subject(monkeypatch):
    from types import SimpleNamespace

    from management_portal.society_service import resident_business_history, resident_conversation_stream, resident_protocol
    from management_portal.society_service.isa import conversation

    context = {"application": "paper.review", "subject_ref": "paper_1234567890ab",
               "group_ref": "review-group", "attempt_ref": "review-attempt"}
    repository = SimpleNamespace(messages=SimpleNamespace(stored_message=lambda *_: {
        "metadata": {"business_context": context},
    }))
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repository)
    resident_reads, subject_reads, snapshots = [], [], []
    monkeypatch.setattr(resident_protocol, "_target", lambda *values: resident_reads.append(values))
    monkeypatch.setattr(resident_protocol, "get_resident_conversation_snapshot", lambda *values, **options:
                        snapshots.append((values, options)) or {"messages": [{"message_id": MESSAGE}]})

    visible = True

    def resolve(actor, instruction, arguments, **kwargs):
        subject_reads.append((actor, instruction, arguments["target_resident_id"], arguments["reference"]["business_context"]))
        return {"snapshot": {"type": "business-history", "visible": visible, "label": "Review"}}

    monkeypatch.setattr(resident_business_history.isa_client, "call", resolve)
    inv = SimpleNamespace(argument={"resident_id": RESIDENT, "input_message_id": MESSAGE}.get, caller="alice")
    assert conversation.list_message_attempts(inv)["conversation"]["messages"] == [{"message_id": MESSAGE}]
    visible = False
    with pytest.raises(PermissionError, match="unavailable"):
        conversation.list_message_attempts(inv)
    assert resident_reads == [(RESIDENT, "alice")] * 2
    assert subject_reads == [("alice", "ResolveMessageContextReferences", RESIDENT, context)] * 2
    assert snapshots == [((RESIDENT, "alice"), {"input_message_id": MESSAGE, "history_limit": 1000})]


def test_reply_hint_advertises_explicit_relay_reply(monkeypatch):
    from management_portal.society_service import outward_messages

    facade = "resident_333333333333"
    monkeypatch.setattr(outward_messages, "speaking_for", lambda _: "")
    monkeypatch.setattr(outward_messages, "reply_target", lambda *_: (facade, MESSAGE))
    hint = outward_messages.reply_hint(RESIDENT, "alice", MESSAGE)
    assert "本轮用 DeliverMessage" in hint
    assert f"RelayMessage，传 resident_id={facade}、reply_to_message_id={MESSAGE}" in hint
