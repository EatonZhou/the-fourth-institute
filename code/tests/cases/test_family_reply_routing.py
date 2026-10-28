"""Replies to a Family's message out go back to the member that wrote it (decision_44cf587c1721).

A member writes to a Resident outside its Family as the facade, and the
facade's Conversation keeps a record of the message.  When the Resident
answers - during the turn that works on the message, or later naming it in
``reply_to_message_id`` - its message to the facade quotes that record, and
the facade hands it straight to the member, without the Router's model.

:module: spec.design-governance
"""

from __future__ import annotations
from tests.cases.family_test_support import set_role

import pytest

from management_portal.society_service import (
    family_facade,
    message_quotes,
    outward_messages,
    resident_access,
    resident_execution_work,
    resident_record_domain,
)
from management_portal.society_service.resident_prompt_domain import (
    build_resident_prompt as _build_resident_prompt,
)
from management_portal.society_service.resident_record_domain import find_resident as _find_resident
from tests.cases.isa_test_support import run
from tests.cases.test_quote_replies import _register, _say, _stored, _text
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


@pytest.fixture
def sent_out(delivery_resident, monkeypatch):
    """A member asked an outside Resident a question through its facade."""

    member = delivery_resident["resident_id"]
    facade = _register("Family 门面")["resident_id"]
    set_role(facade, family_id="fbi_reply00001", actor="sample_admin")
    outsider = _register("外部协作者")["resident_id"]
    monkeypatch.setattr(family_facade, "family_member_ids", lambda record: {member})
    monkeypatch.setattr(outward_messages, "speaking_identity",
                        lambda sender, recipient: facade if sender == member else sender)
    turn: list[str] = []
    monkeypatch.setattr(outward_messages, "_turn_messages", lambda resident_id: list(turn) if resident_id == outsider else [])
    asked = run("DeliverMessage", {"resident_id": outsider, "message": _text("接口什么时候能冻结？")},
                actor=member, key="ask-outsider").raise_for_error()["message_id"]
    return {"member": member, "facade": facade, "outsider": outsider, "asked": asked, "turn": turn,
            "record": outward_messages.outward_record_id(facade, asked)}


def _answer(case, text, key, **arguments):
    return run("DeliverMessage", {"resident_id": case["facade"], "message": _text(text), **arguments},
               actor=case["outsider"], key=key)


def _routed(case, message_id):
    return family_facade.quoted_member(_find_resident(case["facade"]), _stored(case["facade"], message_id))


@pytest.fixture
def private_sent_out(sent_out):
    """The facade accepts messages but its Conversation belongs to its owner."""
    record = _find_resident(sent_out["facade"])
    resident_record_domain.save_resident_record({**record, "personal_owner_id": "sample_admin"})
    record = _find_resident(sent_out["facade"])
    assert resident_access.resident_deliverable_to_actor(record, sent_out["outsider"])
    assert not resident_access.resident_visible_to_actor(record, sent_out["outsider"])
    return sent_out


@pytest.mark.parametrize("automatic", [True, False], ids=["current-turn", "explicit-later-turn"])
def test_received_delegation_can_be_answered_without_reading_private_facade(private_sent_out, automatic):
    """合法回信只授权引用已收到的委托，不要求查看私人门面。"""
    case = private_sent_out
    if automatic:
        case["turn"].append(case["asked"])
    arguments = {} if automatic else {"reply_to_message_id": case["asked"]}
    result = _answer(case, "已完成委托。", "private-reply", **arguments).raise_for_error()
    assert _stored(case["facade"], result["message_id"])["quote"] == {
        "message_id": case["record"], "author_id": case["facade"], "text": "接口什么时候能冻结？"}
    assert _routed(case, result["message_id"]) == case["member"]
    assert not {"messages", "queue", "state", "controls"}.intersection(result)
    assert _answer(case, "已完成委托。", "private-reply", **arguments).raise_for_error()["message_id"] == result["message_id"]
    assert not resident_access.resident_visible_to_actor(_find_resident(case["facade"]), case["outsider"])


def test_private_reply_entitlement_does_not_authorize_ordinary_quotes(private_sent_out):
    """回信授权不能用于普通引用，也不能改引另一条私人消息。"""
    case = private_sent_out
    _answer(case, "完成。", "authorized", reply_to_message_id=case["asked"]).raise_for_error()
    private_message = "msg_00000000000003a1"
    _say(case["facade"], private_message, "未对外发送的私人消息")
    for quoted_id in (case["record"], private_message):
        refused = run("DeliverMessage", {"resident_id": case["facade"],
                      "message": {**_text("普通引用"), "quote": {"message_id": quoted_id}}},
                      actor=case["outsider"])
        assert refused.error["code"] == "instruction.forbidden"
    with pytest.raises(PermissionError, match="不能读取"):
        message_quotes.accept(_find_resident(case["facade"]), {"message_id": private_message},
                              reader=case["outsider"], message_id="msg_00000000000003a2",
                              reply_source_message_id=case["asked"])
    forged = run("DeliverMessage", {"resident_id": case["facade"],
                 "message": {**_text("伪造投递证明"), "quote": {"message_id": private_message},
                             "reply_source_message_id": case["asked"]}}, actor=case["outsider"])
    assert forged.error["code"] == "instruction.invalid_arguments"


def test_someone_elses_delegation_cannot_be_claimed_by_copying_its_message(private_sent_out):
    """只有真实收件人及正式转交链可以回复委托，消息 ID 或正文副本不是授权。"""
    case = private_sent_out
    stranger = _register("未受托者")["resident_id"]
    arguments = {"resident_id": case["facade"], "message": _text("冒领回复"),
                 "reply_to_message_id": case["asked"]}
    refused = run("DeliverMessage", arguments, actor=stranger)
    assert refused.error["code"] == "instruction.invalid_arguments"
    _say(stranger, case["asked"], "接口什么时候能冻结？", role="user", author_id=case["facade"])
    refused = run("DeliverMessage", arguments, actor=stranger)
    assert refused.error["code"] == "instruction.invalid_arguments"


def test_received_reply_keeps_entitlement_when_its_sender_speaks_as_a_family(private_sent_out, monkeypatch):
    """受托者经自己的 Family 回信，引用仍按实际受托者核验。"""
    case = private_sent_out
    replying_facade = _register("协作者门面")["resident_id"]
    set_role(replying_facade, family_id="fbi_reply00002", actor="sample_admin")
    monkeypatch.setattr(outward_messages, "speaking_identity", lambda *_: replying_facade)
    result = _answer(case, "由团队交付。", "family-reply", reply_to_message_id=case["asked"]).raise_for_error()
    stored = _stored(case["facade"], result["message_id"])
    assert result["sender_id"] == replying_facade
    assert stored["author_id"] == replying_facade
    assert stored["quote"]["message_id"] == case["record"]
    assert _routed(case, result["message_id"]) == case["member"]


def test_private_delegation_received_through_a_family_relay_can_be_answered(private_sent_out, monkeypatch):
    """正式转交链上的实际承接成员可回信，仍不开放原门面对话。"""
    case = private_sent_out
    helper = _register("承接成员")["resident_id"]
    copy_id = "msg_00000000000003b1"
    _say(helper, copy_id, "接口什么时候能冻结？", role="user", author_id=case["facade"])
    monkeypatch.setattr(family_facade, "relay_sources_of", lambda member_id, message_id: (
        [(case["outsider"], case["asked"])] if (member_id, message_id) == (helper, copy_id) else []))
    result = run("DeliverMessage", {"resident_id": case["facade"], "message": _text("转交后完成。"),
                 "reply_to_message_id": copy_id}, actor=helper).raise_for_error()
    assert _stored(case["facade"], result["message_id"])["quote"]["message_id"] == case["record"]
    assert _routed(case, result["message_id"]) == case["member"]


def test_an_answer_sent_during_the_turn_on_the_question_goes_back_to_its_member(sent_out):
    """问题所在轮次内的回复自动返回原成员"""
    sent_out["turn"].append(sent_out["asked"])

    answered = _answer(sent_out, "周五冻结。", "same-turn").raise_for_error()["message_id"]

    assert _stored(sent_out["facade"], answered)["quote"] == {
        "message_id": sent_out["record"], "author_id": sent_out["facade"], "text": "接口什么时候能冻结？"}
    assert _routed(sent_out, answered) == sent_out["member"]


def test_a_message_outside_that_turn_needs_reply_to_message_id(sent_out):
    """轮次外回复需指定 reply_to_message_id 才路由回原成员"""
    later = _answer(sent_out, "另外一件事。", "no-turn").raise_for_error()["message_id"]
    assert "quote" not in _stored(sent_out["facade"], later)
    assert _routed(sent_out, later) == ""

    answered = _answer(sent_out, "改到下周一冻结。", "later-turn",
                       reply_to_message_id=sent_out["asked"]).raise_for_error()["message_id"]
    assert _stored(sent_out["facade"], answered)["quote"]["message_id"] == sent_out["record"]
    assert _routed(sent_out, answered) == sent_out["member"]


def test_a_turn_with_two_questions_from_the_facade_leaves_the_choice_to_the_router(sent_out):
    """同轮收到门面两个问题时由 Router 选择回复去向"""
    second = run("DeliverMessage", {"resident_id": sent_out["outsider"], "message": _text("文档放哪？")},
                 actor=sent_out["member"], key="ask-again").raise_for_error()["message_id"]
    sent_out["turn"].extend([sent_out["asked"], second])

    answered = _answer(sent_out, "都在 wiki。", "two-questions").raise_for_error()["message_id"]

    assert "quote" not in _stored(sent_out["facade"], answered)


def test_reply_to_message_id_names_a_message_the_facade_sent_and_nothing_else(sent_out):
    """reply_to_message_id 仅接受目标门面发来的原消息"""
    outsider, facade = sent_out["outsider"], sent_out["facade"]
    _say(outsider, "msg_00000000000002a1", "别人发的", role="user", author_id="sample_admin")
    other = _register("另一个 Resident")["resident_id"]
    cases = [
        ({"reply_to_message_id": "msg_00000000000002a1"}, facade),
        ({"reply_to_message_id": sent_out["asked"]}, other),
        ({"reply_to_message_id": sent_out["asked"], "activation_policy": "attach_to_next"}, facade),
    ]
    for arguments, recipient in cases:
        refused = run("DeliverMessage", {"resident_id": recipient, "message": _text("回复"), **arguments},
                      actor=outsider)
        assert refused.error["code"] == "instruction.invalid_arguments", arguments
    quoted = run("DeliverMessage", {"resident_id": facade, "reply_to_message_id": sent_out["asked"],
                                    "message": {**_text("回复"), "quote": {"message_id": sent_out["record"]}}},
                 actor=outsider)
    assert quoted.error["code"] == "instruction.invalid_arguments"
    to_user = run("DeliverMessage", {"user_id": "sample_admin", "reply_to_message_id": sent_out["asked"],
                                     "message": _text("回复")}, actor=outsider)
    assert to_user.error["code"] == "instruction.invalid_arguments"


def test_a_question_relayed_by_another_familys_router_is_answered_to_the_facade_that_sent_it(sent_out, monkeypatch):
    """跨 Family 转交的问题回复返回原发信门面"""
    # The outsider fronts its own Family; its Router relayed the question to a member.
    outsider, facade = sent_out["outsider"], sent_out["facade"]
    helper = _register("外部成员")["resident_id"]
    _say(helper, "msg_00000000000002b1", "接口什么时候能冻结？", role="user", author_id=facade)
    monkeypatch.setattr(family_facade, "relay_sources_of", lambda member_id, message_id: (
        [(outsider, sent_out["asked"])] if (member_id, message_id) == (helper, "msg_00000000000002b1") else []))

    assert outward_messages.reply_target(helper, "msg_00000000000002b1") == (facade, sent_out["record"])
    assert outward_messages.reply_target(helper, "msg_00000000000002a9") is None


def test_the_resident_reads_how_to_answer_a_message_the_facade_sent(sent_out):
    """Resident 提示词包含门面消息的回复指引"""
    outsider, facade, asked = sent_out["outsider"], sent_out["facade"], sent_out["asked"]
    # Prompt composition happens when execution starts.
    queued = [*resident_execution_work.normalized_queue(resident_execution_work.read(_find_resident(outsider))),
              resident_execution_work.read(_find_resident(outsider))]
    arrived = next(item for item in queued if item.get("user_message_id") == asked)
    from management_portal.society_service.resident_execution_domain import _prepare_resident_prompt
    arrived = _prepare_resident_prompt(_find_resident(outsider), arrived)
    assert f"reply_to_message_id: {asked}" in arrived["actual_sent_prompt"]
    assert f"Family 门面 {facade}" in arrived["actual_sent_prompt"]
    # Rebuilt later, the facade's record still shows it.
    rebuilt = _build_resident_prompt(_find_resident(outsider), {"actor": facade, "user_message_id": asked,
                                                                 "message": "接口什么时候能冻结？"})
    assert "<reply-hint>" in rebuilt and f"reply_to_message_id: {asked}" in rebuilt
    # Any other message carries no hint.
    other = _build_resident_prompt(_find_resident(outsider), {"actor": "sample_admin", "user_message_id": "msg_00000000000002c1",
                                                               "message": "你好"})
    assert "<reply-hint>" not in other


def test_the_turns_messages_are_its_own_the_merged_ones_and_those_sent_into_it(delivery_resident, monkeypatch):
    """当前轮消息包括主输入、合并输入和即时插入输入"""
    resident = delivery_resident["resident_id"]
    job = {'execution_id': "job_0000000000000001", "user_message_id": "msg_group",
           "mailbox_members": [{"user_message_id": "msg_a"}, {"user_message_id": "msg_b"}],
           "send_now_inputs": [{"user_message_id": "msg_c"}]}
    monkeypatch.setattr(resident_execution_work, "read", lambda record: dict(job))
    assert outward_messages._turn_messages(resident) == ["msg_group", "msg_a", "msg_b", "msg_c"]
    job["finished_at"] = "2026-10-10T00:00:00Z"
    assert outward_messages._turn_messages(resident) == []
