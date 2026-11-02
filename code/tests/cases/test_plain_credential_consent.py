"""Plain consent uses the real User and the existing atomic question-answer loan.

:module: resident.identity
:importance: P1
:design: decision_1e2eb869bc10, decision_b820faf42218
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal import permission_system
from management_portal.society_service import resident_credential_answers
from management_portal.society_service.isa import conversation
from tests.cases.test_credential_delivery_atomicity import delivery, loans  # noqa: F401
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401
from tests.cases.permission_test_support import permissions  # noqa: F401


def plain(delivery, text="我给你临时凭证"):
    delivery.arguments.pop("temporary_credentials", None)
    delivery.arguments["message"] = {"role": "user", "content_parts": [{"type": "text", "text": text}]}


@pytest.mark.parametrize("page_context", [False, True])
def test_plain_approval_signs_as_authenticated_user_and_replays_without_resigning(delivery, page_context):
    plain(delivery)
    original_id = "msg_" + "9" * 32
    delivery.arguments["message"]["message_id"] = original_id
    if page_context:
        delivery.arguments["message"]["content_parts"].append({"type": "context_reference", "context": {
            "resources": [{"resource_id": "resident_bbbbbbbbbbbb"}], "issuer": "someone_else"}})
    first = conversation.deliver_message(delivery.inv)
    assert first["message_id"] == delivery.message_id
    assert {c["issued_by"] for c in loans(delivery)} == {"lender"}
    assert {c["principal_id"] for c in loans(delivery)} == {delivery.resident}
    assert {c["message_id"] for c in loans(delivery)} == {delivery.message_id}
    assert delivery.repository.message(delivery.resident, delivery.message_id)["author_id"] == "lender"
    assert delivery.repository.message(delivery.resident, delivery.message_id)["content_parts"][1]["plain_consent"]["message_id"] == original_id
    assert conversation.deliver_message(delivery.inv)["temporary_credentials"] == first["temporary_credentials"]
    permission_system.manage("RevokeTemporaryCredential", {
        "credential_id": first["temporary_credentials"][0]["credential_id"]}, actor="lender")
    with pytest.raises(Exception, match="原回答的凭证"):
        conversation.deliver_message(delivery.inv)
    assert len(loans(delivery)) == 2


def test_plain_approval_failure_is_atomic_and_terminal_answer_cannot_regrant(delivery, monkeypatch):
    from management_portal.permission_system import temporary
    plain(delivery)
    original = temporary.issue
    count = 0
    def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise temporary.LoanRefused("second signature failed")
        return original(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(temporary, "issue", fail_second)
        with pytest.raises(Exception, match="second signature failed"):
            conversation.deliver_message(delivery.inv)
    assert not loans(delivery)
    assert delivery.repository.message(delivery.resident, delivery.message_id) is None
    conversation.deliver_message(delivery.inv)
    delivery.repository.set_input_state(delivery.resident, delivery.message_id, "failed")
    with pytest.raises(Exception):
        conversation.deliver_message(delivery.inv)
    assert not loans(delivery)


def test_user_must_still_hold_every_requested_permission(delivery):
    plain(delivery)
    delivery.permissions.deny("lender", delivery.requests[0]["resource_id"], "@contributor")
    with pytest.raises(Exception):
        conversation.deliver_message(delivery.inv)
    assert not loans(delivery)
    assert delivery.repository.message(delivery.resident, delivery.message_id) is None


def test_real_isa_call_keeps_original_consent_and_authenticated_caller_in_audit(delivery):
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.society_service.isa import build_kernel
    plain(delivery)
    kernel = build_kernel()
    call = InstructionCall("DeliverMessage", deepcopy(delivery.arguments), idempotency_key="plain-consent-audit")
    result = kernel.execute(call, InstructionContext(caller="lender", entry="web"))
    assert result.status == "committed", result.to_dict()
    assert result.result["message_id"] == delivery.message_id
    audit = kernel.store.get(result.instruction_id)
    assert audit["caller"] == "lender"
    stored = delivery.repository.message(delivery.resident, delivery.message_id)
    assert stored["author_id"] == "lender"
    assert stored["content_parts"][0]["text"].startswith("我给你临时凭证\n\n")
    assert stored["content_parts"][1]["plain_consent"] == {"text": "我给你临时凭证", "message_id": ""}
    replay = kernel.execute(call, InstructionContext(caller="lender", entry="web"))
    assert replay.replayed and replay.result["message_id"] == result.result["message_id"]
    assert len(loans(delivery)) == 3


@pytest.mark.parametrize("change", ["quote_text", "negative", "hypothetical", "resident", "mixed_questions",
                                   "different_recipient", "attachment", "no_source", "ordinary_latest"])
def test_ambiguous_or_untrusted_text_never_infers_a_grant(delivery, change):
    plain(delivery)
    body = delivery.arguments["message"]
    source = deepcopy(delivery.repository.latest_message(delivery.resident, role="agent"))
    actor = "lender"
    if change == "quote_text":
        body["content_parts"][0]["text"] = "他说‘我给你临时凭证’"
    elif change == "negative":
        body["content_parts"][0]["text"] = "我不给你临时凭证"
    elif change == "hypothetical":
        body["content_parts"][0]["text"] = "我说‘我给你临时凭证’就应该签发"
    elif change == "resident":
        actor = delivery.resident
    elif change == "mixed_questions":
        question = next(p for p in source["content_parts"] if p["type"] == "async_questions")
        question["questions"].append({"question_index": 1, "title": "还需要什么？"})
    elif change == "different_recipient":
        source["recipients"] = [{"kind": "user", "id": "someone_else"}]
    elif change == "attachment":
        body["content_parts"].append({"type": "image", "url": "fake"})
    elif change == "no_source":
        source = None
    else:
        source["content_parts"] = [{"type": "text", "text": "Already done"}]
    repository = SimpleNamespace(latest_message=lambda *a, **k: source)
    unchanged, credentials = resident_credential_answers.plain_answer(
        delivery.resident, body, actor=actor, repository=repository)
    assert unchanged == body and not credentials


def test_exact_quote_can_select_older_scope(delivery):
    plain(delivery)
    source_id = "msg_permission_question"
    delivery.repository.append_message(delivery.resident, {
        "message_id": "msg_new_reply", "role": "agent", "author_id": delivery.resident,
        "content_parts": [{"type": "text", "text": "Another reply"}]})
    delivery.arguments["message"]["quote"] = {"message_id": source_id}
    assert conversation.deliver_message(delivery.inv)["message_id"] == delivery.message_id


def test_closed_question_never_lends(delivery):
    plain(delivery)
    delivery.repository.close_question_set(delivery.resident, "msg_permission_question", actor="lender")
    with pytest.raises(ValueError, match="已关闭"):
        conversation.deliver_message(delivery.inv)
    assert not loans(delivery)
