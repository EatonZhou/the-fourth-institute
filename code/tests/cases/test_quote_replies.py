"""Quote replies: a user message quotes one message of the Conversation it goes to.

The sender names only the quoted message; the platform checks it and fills in
its author and the opening of its text, and the Resident reads them before
the message (decision_c524c775e0be ``quote``).  In a Family facade a quote of
a member's message goes straight back to that member, and the copy the
member receives points at its own message (decision_44cf587c1721).

:module: resident.conversation
"""

from __future__ import annotations
from tests.cases.family_test_support import set_role

import pytest

from management_portal.society_service import (
    family_facade,
    family_definition,
    lineage_message_events,
    message_quotes,
    outward_messages,
    resident_access,
    resident_conversation_stream,
    resident_domain,
    resident_execution_work,
    resident_protocol,
    resident_record_domain,
)
from management_portal.society_service.resident_record_domain import find_resident as _find_resident
from tests.cases.isa_test_support import run
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import (  # noqa: F401
    _new_session_payload,
    future_creation_store,
)

script_module = pytest.importorskip("management_portal.society_service.lineage_effect_script")


def _register(title):
    return resident_domain.register_resident(_new_session_payload(title), "sample_admin")


def _text(text):
    return {"content_parts": [{"type": "text", "text": text}]}


def _say(resident_id, message_id, text, *, role="agent", author_id="", **fields):
    resident_conversation_stream.get_repository().append_message(resident_id, {
        "message_id": message_id, "role": role, "author_id": author_id or resident_id,
        "created_at": "2026-10-06T01:00:00Z", "content_parts": [{"type": "text", "text": text}], **fields,
    }, activated_at="2026-10-06T01:00:00Z")


def _stored(resident_id, message_id):
    return resident_conversation_stream.get_repository().message(resident_id, message_id)


def test_a_user_message_quotes_a_message_of_its_conversation_and_the_platform_fills_it_in(delivery_resident):
    """用户消息引用同一对话中的一条消息，平台填写作者和摘录

    DeliverMessage 的 message.quote 只给被引用消息的 message_id；平台填写作者和正文开头（最多 1000 字），之后不再改变：同一 message_id 重试沿用原引用，改引另一条消息按冲突拒绝。

    :importance: P1
    :design: decision_c524c775e0be
    """
    resident = delivery_resident["resident_id"]
    long_text = "第一行\n" + "长" * 1500
    _say(resident, "msg_00000000000000a1", long_text)

    sent = run("DeliverMessage", {"resident_id": resident, "message": {
        **_text("就这一段"), "quote": {"message_id": "msg_00000000000000a1"}}}, actor="sample_admin", key="quote").raise_for_error()

    quote = {"message_id": "msg_00000000000000a1", "author_id": resident, "text": long_text[:1000]}
    assert _stored(resident, sent["message_id"])["quote"] == quote
    shown = run("GetMessage", {"resident_id": resident, "message_id": sent["message_id"]}, actor="sample_admin")
    assert shown.raise_for_error()["message"]["quote"] == quote
    # A retry is the same message with the same quote; the same ID quoting another message conflicts.
    again = run("DeliverMessage", {"resident_id": resident, "message": {
        **_text("就这一段"), "quote": {"message_id": "msg_00000000000000a1"}}}, actor="sample_admin", key="quote").raise_for_error()
    assert again["message_id"] == sent["message_id"]
    _say(resident, "msg_00000000000000a2", "另一条回复")
    changed = run("DeliverMessage", {"resident_id": resident, "message": {
        "message_id": sent["message_id"], **_text("就这一段"), "quote": {"message_id": "msg_00000000000000a2"}}},
        actor="sample_admin")
    assert changed.error["code"] == "instruction.conflict"
    assert _stored(resident, sent["message_id"])["quote"] == quote


def test_a_quote_names_one_message_with_text_that_the_sender_can_read_in_the_same_conversation(delivery_resident,
                                                                                              monkeypatch):
    """引用只能指向同一对话中发送者能读、有正文的一条消息

    缺少或多给字段、引用另一段对话或没有正文的消息、引用自己时按参数错误拒绝；发送者读不了时按无权限拒绝；info 消息和发给用户的消息不带引用。

    :importance: P1
    :design: decision_c524c775e0be
    """
    resident = delivery_resident["resident_id"]
    other = _register("另一个 Resident")["resident_id"]
    _say(resident, "msg_00000000000000b1", "可以引用的回复")
    _say(other, "msg_00000000000000b2", "别的对话里的回复")
    resident_conversation_stream.get_repository().append_message(resident, {
        "message_id": "msg_00000000000000b3", "role": "agent", "author_id": resident,
        "created_at": "2026-10-06T01:00:00Z", "content_parts": [{"type": "artifact_reference", "artifact_id": "a1"}],
    }, activated_at="2026-10-06T01:00:00Z")

    def send(quote, **arguments):
        return run("DeliverMessage", {"resident_id": resident, "message": {**_text("这条"), "quote": quote},
                                      **arguments}, actor="sample_admin")

    for quote in ({"message_id": "msg_00000000000000ff"}, {"message_id": "msg_00000000000000b2"},
                  {"message_id": "msg_00000000000000b3"}, {"message_id": ""},
                  {"message_id": "msg_00000000000000b1", "text": "自己写的摘录"}, "msg_00000000000000b1"):
        assert send(quote).error["code"] == "instruction.invalid_arguments", quote
    # An info carries only what it says, and a message to a user is the sender's own.
    assert send({"message_id": "msg_00000000000000b1"}, activation_policy="attach_to_next").error["code"] == \
        "instruction.invalid_arguments"
    to_user = run("DeliverMessage", {"user_id": "sample_admin", "message": {
        **_text("这条"), "quote": {"message_id": "msg_00000000000000b1"}}}, actor=resident)
    assert to_user.error["code"] == "instruction.invalid_arguments"
    assert [item["message_id"] for item in resident_conversation_stream.get_repository().read_messages(resident)] == [
        "msg_00000000000000b1", "msg_00000000000000b3"]
    # Only someone who can read the Conversation quotes from it.
    monkeypatch.setattr(resident_access, "resident_visible_to_actor", lambda record, actor: actor != "stranger")
    with pytest.raises(PermissionError):
        message_quotes.accept(_find_resident(resident), {"message_id": "msg_00000000000000b1"},
                              reader="stranger", message_id="msg_00000000000000b9")


def test_the_resident_reads_the_quoted_author_and_excerpt_before_the_message(delivery_resident):
    """收到带引用的消息时，提示先写引用的作者和摘录

    Resident 收到的输入在正文前有 <quoted-message> 块，写明被引用消息的作者（Resident 附标题）、message_id 和摘录。

    :design: decision_c524c775e0be
    """
    target = _register("引用测试")["resident_id"]
    _say(target, "msg_00000000000000c1", "第三节需要重写。")

    sent = run("DeliverMessage", {"resident_id": target, "message": {
        **_text("哪一段？"), "quote": {"message_id": "msg_00000000000000c1"}}}, actor="sample_admin").raise_for_error()

    job = resident_execution_work.read(_find_resident(target))
    assert job["user_message_id"] == sent["message_id"]
    from management_portal.society_service.resident_execution_domain import _prepare_resident_prompt
    job = _prepare_resident_prompt(_find_resident(target), job)
    assert (
        f'<quoted-message author="{target}" author_title="引用测试" message_id="msg_00000000000000c1">\n'
        "第三节需要重写。\n</quoted-message>\n\n哪一段？"
    ) in job["actual_sent_prompt"]
    # The Conversation shows the message as written; the quote is a field of its own.
    assert _stored(target, sent["message_id"])["content_parts"] == [{"type": "text", "text": "哪一段？"}]


@pytest.fixture
def family(delivery_resident, monkeypatch):
    """A facade with one member; sample_admin's first request was relayed to the member, who answered it."""

    member = delivery_resident["resident_id"]
    facade = _register("Family 门面")["resident_id"]
    set_role(facade, family_id="fbi_quote00001", actor="sample_admin")
    members = {member}
    monkeypatch.setattr(family_facade, "family_member_ids", lambda record: set(members))
    monkeypatch.setattr(family_facade, "family_members", lambda record: [
        found for rid in sorted(members) if (found := _find_resident(rid)) is not None
    ])
    marks = {}
    monkeypatch.setattr(family_facade, "validate_relay", lambda business_ref, actor: marks.get(business_ref))
    monkeypatch.setattr(family_facade, "relay_delegates", lambda source, member_id: source is not None)

    def ask(text, quote="", key=""):
        message = {**_text(text), **({"quote": {"message_id": quote}} if quote else {})}
        return run("DeliverMessage", {"resident_id": facade, "message": message}, actor="sample_admin",
                   key=key).raise_for_error()["message_id"]

    def relay(request_id, copy_id):
        mark = family_facade.relay_mark(facade, request_id)
        marks[mark] = (facade, request_id)
        stored = _stored(facade, request_id)
        resident_protocol.deliver_resident_input({"resident_id": member, "message": {
            "message_id": copy_id, "role": "user", "content_parts": stored["content_parts"]}},
            # The Router delivers as the sender, so the member sees the sender as the author.
            "sample_admin", delivery_context=resident_protocol.ResidentDeliveryContext(business_ref=mark))
        return _stored(member, copy_id)

    first = ask("登录页 500 了")
    relay(first, "msg_00000000000001c1")
    _say(member, "msg_00000000000001d1", "已修好，原因是会话过期。", reply_to_message_id="msg_00000000000001c1")
    return {"member": member, "facade": facade, "members": members, "ask": ask, "relay": relay, "first": first}


def test_a_facade_request_quotes_a_member_reply_shown_there_as_the_familys(family):
    """门面对话中可以引用显示在那里的成员回复，作者记为门面

    门面对话里显示的成员回复可以被引用；引用的作者与显示一致，是门面。

    :module: lineage.team
    :design: decision_44cf587c1721, decision_c524c775e0be
    """
    facade = family["facade"]

    asked = family["ask"]("这次又出现了", quote="msg_00000000000001d1")

    assert _stored(facade, asked)["quote"] == {
        "message_id": "msg_00000000000001d1", "author_id": facade, "text": "已修好，原因是会话过期。"}


def test_a_quote_of_a_members_message_goes_straight_back_to_that_member(family, monkeypatch):
    """引用成员的回复或成员发出的消息时，请求直接交回该成员

    门面收到的消息引用成员的回复或成员以门面发出的消息时，到达事件带 quoted_member_id；引用门面中的请求（包括发送者自己的）、成员已离开 Family、在回收站或读不了附件时不带，按常规路由。

    :module: lineage.team
    :importance: P1
    :design: decision_44cf587c1721
    """
    from management_portal.society_service import resident_execution_provider, resident_workspace_domain

    facade, member, ask = family["facade"], family["member"], family["ask"]
    record = _find_resident(facade)
    _say(facade, "msg_00000000000001e1", "需要你决定发布时间。", recipients=[{"kind": "user", "id": "sample_admin"}],
         origin=outward_messages.message_origin(facade, initiated_by=member))
    _say(facade, "msg_00000000000001e2", "别人的请求", role="user", author_id="CASE-A")

    def routed(quote):
        return family_facade.quoted_member(record, _stored(facade, ask("接着说", quote=quote)))

    # A member's reply and a message a member sent out go back to the member.
    assert routed("msg_00000000000001d1") == member
    assert routed("msg_00000000000001e1") == member
    # A quoted request, the sender's own included, is routed as usual.
    assert routed(family["first"]) == ""
    assert routed("msg_00000000000001e2") == ""
    assert family_facade.quoted_member(record, _stored(facade, ask("没有引用"))) == ""
    # The Router's arrival Event carries the member.
    asked = ask("第三次", quote="msg_00000000000001d1")
    repository = resident_conversation_stream.get_repository()
    arrival = lineage_message_events.arrival_details(repository, facade, asked, "sample_admin", record)
    assert arrival["quoted_member_id"] == member
    event = lineage_message_events.arrival_event("source_1", facade, {**arrival, "occurred_at": "2026-10-06T02:00:00Z"})
    assert event.payload["quoted_member_id"] == member

    # A deleted member, gone from the Family, or not reading the request's images is passed over.
    with monkeypatch.context() as patched:
        find = resident_record_domain.find_resident
        patched.setattr(resident_record_domain, "find_resident", lambda rid: None if rid == member else find(rid))
        assert routed("msg_00000000000001d1") == ""
    family["members"].clear()
    assert routed("msg_00000000000001d1") == ""
    family["members"].add(member)

    image = {"type": "image", "media_type": "image", "upload_id": "upl_x", "name": "shot.png"}
    message = {**_stored(facade, ask("看图", quote="msg_00000000000001d1")), "content_parts": [image]}

    class Provider:
        def __init__(self, reads_images):
            self.capabilities = type("Capabilities", (), {"supports_media": reads_images, "local_tools": True})()

    monkeypatch.setattr(resident_execution_provider, "provider_for", lambda item: Provider(True))
    assert family_facade.quoted_member(record, message) == member
    monkeypatch.setattr(resident_execution_provider, "provider_for", lambda item: Provider(False))
    assert family_facade.quoted_member(record, message) == ""


def test_until_it_is_handed_over_a_quote_shows_its_member_not_the_router_choosing(family, monkeypatch):
    """引用成员消息的门面请求在交到成员前显示“正在交给 X”，不显示 Router 正在选择

    多成员 Family 中，排队的门面请求引用了某个成员的回复时，请求状态为 delivering 并指向该成员，门面状态为 family_waiting；同时有需要 Router 选择的请求时，门面状态仍为 family_routing。

    CASE-A 10-12: a quote of a member's reply waiting to be handed over showed "Family Router 正在选择成员".

    :design: decision_44cf587c1721
    """

    facade, member, ask = family["facade"], family["member"], family["ask"]
    family["members"].add(_register("另一个成员")["resident_id"])
    monkeypatch.setattr(family_facade, "family_members", lambda record: sorted(family["members"]))
    # The member answered the first request, so no request waits at a member.
    resident_conversation_stream.get_repository().set_input_state(member, "msg_00000000000001c1", "completed")
    record = _find_resident(facade)
    quoted, plain = ask("又出现了", quote="msg_00000000000001d1"), ask("新问题")

    def shown(*message_ids):
        return family_facade.project_snapshot(record, {
            "messages": [_stored(facade, message_id) for message_id in message_ids],
            "input_states": [{"message_id": message_id, "state": "queued"} for message_id in message_ids],
        })

    snapshot = shown(quoted)
    assert snapshot["messages"][0]["relay"]["state"] == "delivering"
    assert snapshot["messages"][0]["relay"]["resident_id"] == member
    assert snapshot["state"]["work_phase"] == family_facade.WAITING_PHASE
    assert snapshot["state"]["active_message_id"] == quoted
    assert snapshot["state"]["family_member"]["resident_id"] == member
    # Beside a request the Router still has to choose for, the facade shows the Router choosing.
    snapshot = shown(quoted, plain)
    assert snapshot["messages"][1]["relay"] == {"state": family_facade.ROUTING_STATE}
    assert snapshot["state"]["work_phase"] == family_facade.ROUTING_PHASE


def test_the_copy_a_member_receives_quotes_its_own_message_or_keeps_the_excerpt(family):
    """成员收到的转交副本引用它自己对话中的消息，不在时只保留作者和摘录

    Router 转交时副本的 quote 指向成员对话中的被引用消息（回复、请求的转交副本）；成员对话中没有这条消息时只保留作者和摘录。

    :module: lineage.team
    :design: decision_44cf587c1721, decision_c524c775e0be
    """
    facade, member, ask, relay = family["facade"], family["member"], family["ask"], family["relay"]
    _say(facade, "msg_00000000000001f1", "需要你决定发布时间。", recipients=[{"kind": "user", "id": "sample_admin"}],
         origin=outward_messages.message_origin(facade, initiated_by=member))

    reply_copy = relay(ask("又出现了", quote="msg_00000000000001d1"), "msg_00000000000001c2")
    request_copy = relay(ask("第一个问题还在吗", quote=family["first"]), "msg_00000000000001c3")
    outward_copy = relay(ask("周五发布", quote="msg_00000000000001f1"), "msg_00000000000001c4")

    # Its own reply, by the member; its own copy of the request, by the sender.
    assert reply_copy["quote"] == {"message_id": "msg_00000000000001d1", "author_id": member,
                                   "text": "已修好，原因是会话过期。"}
    assert request_copy["quote"] == {"message_id": "msg_00000000000001c1", "author_id": "sample_admin",
                                     "text": "登录页 500 了"}
    # The facade's record is not in the member's Conversation: only the author and excerpt remain.
    assert outward_copy["quote"] == {"author_id": facade, "text": "需要你决定发布时间。"}


def _route(payload: dict, quote: dict | None = None):
    message = {"content_parts": [{"type": "text", "text": "接着说"}], "author_id": "CASE-A", **({"quote": quote} if quote else {})}
    results = {
        "GetMessage": {"message": message},
        "ListRecentRounds": {"rounds": []},
        "GetResidentSummaries": {"summaries": []},
        "ListResidentStates": {"states": []},
        "GetSharedState": {"state": {}, "value": None},
        "DeliverMessage": {"message_id": "msg_" + "2" * 16},
        "DispatchFamilyTask": {"message_id": "msg_" + "2" * 16},
        "UpdateSharedState": {"changed": True},
        "RelayFamilyMessage": {"message_id": "msg_" + "2" * 16},
    }
    calls = []

    def execute(request):
        calls.append((request.instruction, request.arguments))
        return script_module.CallResult(True, results[request.instruction])

    inputs = {
        "arguments": {"members": ["resident_aaaaaaaaaaaa", "resident_bbbbbbbbbbbb"], "router": "resident_eeeeeeeeeeee",
                      "lineage_id": "lineage_000000000001", "guide_key": "family_guide_x"},
        "event": {"payload": {"resident_id": "resident_dddddddddddd", "message_id": "msg_" + "1" * 16,
                              "relay_depth": 0, **payload}},
        "proposal": {}, "endpoint": {},
    }
    outcome = script_module.Interpreter(script_module.parse(family_definition.FAMILY_ROUTER_SCRIPT),
                                        inputs, execute).run()
    return outcome, calls


def test_the_router_hands_a_quoted_members_message_back_without_its_model():
    """Family Router 不经 Router Resident 直接把引用请求交回成员，否则照常询问并参考引用

    事件带 quoted_member_id 时 Router Effect 直接 RelayFamilyMessage 给该成员并结束；不带时以 DispatchFamilyTask 绑定原请求询问 Router Resident。原引用进入真实路由任务的合同由 test_family_tasks 验证。

    :module: lineage.hatg
    :importance: P1
    :design: decision_44cf587c1721
    """
    quote = {"message_id": "msg_" + "2" * 16, "author_id": "resident_dddddddddddd", "text": "已修好"}

    outcome, calls = _route({"quoted_member_id": "resident_bbbbbbbbbbbb"}, quote)

    assert outcome.status == "succeeded"
    assert [name for name, _ in calls] == ["RelayFamilyMessage"]
    delivered = calls[-1][1]
    assert delivered["member_id"] == "resident_bbbbbbbbbbbb"
    assert delivered["message_id"] == "msg_" + "1" * 16
    assert outcome.outputs["target"] == "resident_bbbbbbbbbbbb" and outcome.outputs["confidence"] == 100

    # Task dispatch owns reading the original message and preserving its quote.
    outcome, calls = _route({"quoted_member_id": ""}, quote)
    assert outcome.status == "succeeded"
    assert calls == [("DispatchFamilyTask", {"resident_id": "resident_dddddddddddd",
                     "trigger": "route", "source_message_id": "msg_" + "1" * 16})]
    assert outcome.outputs == {"router": "resident_eeeeeeeeeeee", "request_message_id": "msg_" + "2" * 16}


ASKS = '先确认一件事。\n```portal-question\n{"title":"先修哪个页面？","options":["登录页","设置页"]}\n```'


def _answer_on_facade(family, source_id, answer="登录页"):
    from management_common.async_questions import question_answer_id

    return run("DeliverMessage", {"resident_id": family["facade"], "message": {
        "message_id": question_answer_id(family["member"], source_id), "role": "user", "content_parts": [
            {"type": "text", "text": answer},
            {"type": "async_question_answer", "source_message_id": source_id,
             "answers": [{"question_index": 0, "answer": answer}]},
        ]}}, actor="sample_admin")


def test_questions_a_member_asks_on_the_facade_are_answered_back_to_that_member(family, monkeypatch):
    """成员在 Family 门面上提出的问题集，回答引用原回复并以同一答复 ID 转交给该成员

    A member reply shown on the facade may ask a question set (decision_1e2eb869bc10): the
    set and its state are the member's, and the answer quotes the reply so it goes back to the
    member with the set's own answer ID.

    :module: platform.core
    """

    facade, member = family["facade"], family["member"]
    record = _find_resident(facade)
    repository = resident_conversation_stream.get_repository()
    # The member's turn on the request has ended with its questions; nothing else waits.
    run("CancelInput", {"resident_id": member, "message_id": "msg_00000000000001c1"}, actor="sample_admin").raise_for_error()
    _say(member, "msg_00000000000001d2", ASKS, reply_to_message_id="msg_00000000000001c1")
    shown = family_facade.projected_reply(record, "msg_00000000000001d2")
    asked = next(part for part in shown["content_parts"] if part["type"] == "async_questions")
    assert "state" not in asked

    answered = _answer_on_facade(family, "msg_00000000000001d2").raise_for_error()["message_id"]

    assert answered == asked["answer_message_id"]
    stored = _stored(facade, answered)
    assert stored["content_parts"][0]["text"] == "关于你提出的问题「先修哪个页面？」，我的回答是：\n登录页"
    assert stored["quote"]["message_id"] == "msg_00000000000001d2"
    assert family_facade.quoted_member(record, stored) == member
    # The Router relays it: the member takes the copy under the set's answer ID, quoting its own questions.
    from management_portal.society_service import family_quota

    monkeypatch.setattr(family_facade, "validate_relay", lambda ref, actor: family_facade.parse_relay_mark(ref))
    monkeypatch.setattr(family_quota.execution_port, "service", lambda: type("S", (), {"accounts": lambda self: {}})())
    relayed = family_quota.relay_message(facade, answered, member, "sample_admin")
    assert relayed == {"status": "relayed", "member_id": member, "message_id": answered}
    copy = _stored(member, answered)
    assert copy["quote"]["message_id"] == "msg_00000000000001d2"
    assert repository.question_set_state(member, "msg_00000000000001d2") == "answered"
    shown = family_facade.projected_reply(record, "msg_00000000000001d2")
    assert next(part for part in shown["content_parts"] if part["type"] == "async_questions")["state"] == "answered"
    # A retry is the same answer.
    assert _answer_on_facade(family, "msg_00000000000001d2").raise_for_error()["message_id"] == answered


def test_a_members_questions_stay_open_on_the_facade_when_its_own_conversation_goes_on(family):
    """成员自己的对话继续后，它在 Family 门面上提出的问题集仍可作答

    :module: platform.core"""
    facade, member = family["facade"], family["member"]
    run("CancelInput", {"resident_id": member, "message_id": "msg_00000000000001c1"}, actor="sample_admin").raise_for_error()
    _say(member, "msg_00000000000001d3", ASKS, reply_to_message_id="msg_00000000000001c1")
    _say(member, "msg_00000000000001c9", "先别管了", role="user", author_id="sample_admin")

    shown = family_facade.projected_reply(_find_resident(facade), "msg_00000000000001d3")
    asked = next(part for part in shown["content_parts"] if part["type"] == "async_questions")
    assert "state" not in asked
    assert _answer_on_facade(family, "msg_00000000000001d3").raise_for_error()["message_id"] == asked["answer_message_id"]
