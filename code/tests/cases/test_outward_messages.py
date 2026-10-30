"""Messages Residents send to users, and a Family's messages to the outside.

A message to a user stays in the Conversation it is sent from and names its
recipient; only the recipient finds it unread (decision_c524c775e0be,
decision_b4e5ea4a47ec).  A Family member or steward speaks to anyone outside
its Family as the outermost facade that holds it but not the recipient
(decision_44cf587c1721).

:module: resident.identity
:importance: P1
"""

from __future__ import annotations
from tests.cases.family_test_support import set_role, mock_family_rosters
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from management_portal.execution import chat_position_index
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.agent_data_tables import agent_data_schema_matches
from management_portal.society_service import (
    outward_messages,
    resident_access,
    resident_conversation_projection_domain as projection,
    resident_conversation_stream,
    resident_domain,
)
from management_portal.society_service import family_facade
from tests.cases.isa_test_support import run
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from tests.cases.test_agent_data_store import _chat
from tests.cases.test_family_facade import _upload
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.resident_execution_test_support import VALID_PNG_BYTES as PNG
from tests.cases.test_resident_folder_creation_contract import (  # noqa: F401
    _new_session_payload,
    future_creation_store,
)


def _to(message: dict, *recipients: tuple[str, str]) -> dict:
    return {**message, "recipients": [{"kind": kind, "id": identifier} for kind, identifier in recipients]}


def test_a_message_to_a_user_is_activity_and_news_only_for_that_user():
    """发给用户的消息只算收件人的会话活动

    Resident 对话中注明收件人的消息让收件用户在会话活动和会话摘要中看到这个对话与最新一条发给他的消息；其他用户看不到；消息内部（工具调用参数）出现 recipients 键不算发给谁；对话被清空后不再计入。

    :module: platform.core
    :design: decision_c524c775e0be, decision_b4e5ea4a47ec
    """
    store = AgentDataStore()
    resident, other = "resident_sender", "resident_quiet"
    store.display_chat_message(resident, _to(
        _chat("msg_0000000000000a01", "agent", "周报已写好", author_id="resident_aaaaaaaaaaaa",
              created_at="2026-10-06T01:00:00Z"),
        ("user", "CASE-A"),
    ))
    # A key named "recipients" deeper in the message does not address anyone.
    tool = _chat("msg_0000000000000a02", "agent", "通知已发", author_id="resident_aaaaaaaaaaaa")
    tool["content_parts"].append({"type": "tool_call", "tool_name": "CreateNotification",
                                  "arguments": {"recipients": [{"kind": "user", "id": "CASE-A"}]}})
    store.display_chat_message(other, tool)

    assert store.chat_actor_activity("case-a") == {resident: "2026-10-06T01:00:00Z"}
    assert store.chat_actor_activity("CASE-A", [resident, other]) == {resident: "2026-10-06T01:00:00Z"}
    assert store.chat_actor_activity("sample_admin") == {}
    summary = store.chat_conversation_summaries([resident, other], "CASE-A")
    assert summary[resident]["actor_addressed_message_id"] == "msg_0000000000000a01"
    assert summary[resident]["actor_addressed_at"] == "2026-10-06T01:00:00Z"
    assert summary[resident]["user_message_count"] == 0  # it is not a request
    assert summary[other]["actor_addressed_message_id"] == ""
    assert store.chat_conversation_summaries([resident], "sample_admin")[resident]["actor_addressed_message_id"] == ""

    # A cleared Conversation no longer brings the message.
    store.clear_chat(resident, actor_ref="sample_admin")
    assert store.chat_actor_activity("CASE-A") == {}


def test_the_kept_activity_and_its_index_are_part_of_the_schema():
    """Mailbox 最近活动表及其索引属于 AgentData schema，缺失时拒绝打开

    :module: platform.core
    :importance: P2
    """
    store = AgentDataStore()
    store.database.write(lambda connection: connection.execute("DROP INDEX chat_actor_activity_actor_idx"))
    try:
        with pytest.raises(RuntimeError, match="does not match its schema"):
            AgentDataStore()
    finally:
        store.database.write(lambda connection: connection.execute(
            "CREATE INDEX chat_actor_activity_actor_idx "
            + chat_position_index.INDEX_SQL["chat_actor_activity_actor_idx"]))
    with AgentDataStore().database.read() as connection:
        assert agent_data_schema_matches(connection)


LINEAGE = "lineage_00000000000a"
MEMBER, PEER, STEWARD, INNER_FACADE = (
    "resident_000000000001", "resident_000000000002", "resident_000000000003", "resident_000000000004",
)
COUSIN, OUTER_FACADE, OUTER_STEWARD, STRANGER = (
    "resident_000000000005", "resident_000000000006", "resident_000000000007", "resident_000000000008",
)


@pytest.fixture
def nested_families(monkeypatch):
    """An inner Family (MEMBER, PEER) whose facade is a member of an outer Family with COUSIN."""

    def family(family_id, members, facade, steward):
        return {"id": family_id, "family": {"members": members, "facade": facade, "steward": steward}}

    definition = {"function_blocks": [
        family("fbi_000000inner", [MEMBER, PEER], INNER_FACADE, STEWARD),
        family("fbi_000000outer", [INNER_FACADE, COUSIN], OUTER_FACADE, OUTER_STEWARD),
    ]}
    router = "resident_abcdefabcdef"
    definition["function_blocks"][0]["family"]["router"] = router

    class Store:
        database = SimpleNamespace(read=lambda: nullcontext(None))

    mock_family_rosters(monkeypatch, lambda: definition['function_blocks'])

    # When each party last wrote to each Resident itself, and when Residents were created.
    talked: dict[tuple[str, str], str] = {}
    created: dict[str, str] = {}
    monkeypatch.setattr(outward_messages, "get_lineage_data_store", lambda: Store())
    monkeypatch.setattr(outward_messages, "_find", lambda resident_id: {
        "resident_id": resident_id, "lineage_id": LINEAGE, "created_at": created.get(resident_id, ""),
    })
    monkeypatch.setattr(outward_messages, "_last_direct_messages", lambda party, resident_ids: {
        resident_id: talked.get((party, resident_id), "") for resident_id in resident_ids
    })
    return {"talked": talked, "created": created, "router": router}


def _user(user_id):
    return {"kind": "user", "id": user_id}


def _resident(resident_id):
    return {"kind": "resident", "id": resident_id}


def test_a_family_speaks_outward_as_the_outermost_facade_that_holds_the_sender(nested_families):
    """Family 对外以包含发送者、不包含收件方的最外层门面发出

    嵌套 Family 中，成员和 steward 发给用户或 Family 外的 Resident 时以最外层门面发出；发给只同属外层的 Resident 时以内层门面发出；Family 内部互发（包括发给自己的门面）保留本人身份；不在 Family 中的 Resident 以本人身份发出。

    :design: decision_44cf587c1721, decision_c524c775e0be
    """
    speak = outward_messages.speaking_identity
    # Users are outside every Family: the outermost facade speaks.
    assert speak(MEMBER, _user("CASE-A")) == OUTER_FACADE
    assert speak(STEWARD, _user("CASE-A")) == OUTER_FACADE
    assert speak(OUTER_STEWARD, _user("CASE-A")) == OUTER_FACADE
    assert speak(STRANGER, _user("CASE-A")) == STRANGER
    # A Resident outside both Families hears from the outer facade.
    assert speak(MEMBER, _resident(STRANGER)) == OUTER_FACADE
    # A cousin shares the outer Family only: the inner Family speaks to it.
    assert speak(MEMBER, _resident(COUSIN)) == INNER_FACADE
    assert speak(COUSIN, _resident(MEMBER)) == COUSIN
    # Inside one Family, everyone keeps their own name, including towards its facade.
    assert speak(MEMBER, _resident(PEER)) == MEMBER
    assert speak(STEWARD, _resident(MEMBER)) == STEWARD
    assert speak(MEMBER, _resident(INNER_FACADE)) == MEMBER
    assert speak(INNER_FACADE, _resident(STRANGER)) == OUTER_FACADE
    # Routing inside the inner Family must never become outer-facade correspondence.
    router = nested_families["router"]
    assert speak(INNER_FACADE, _resident(router)) == INNER_FACADE
    assert speak(router, _resident(MEMBER)) == router
    assert speak(router, _resident(INNER_FACADE)) == router
    assert speak(MEMBER, _resident(router)) == MEMBER


def test_a_recipient_hears_back_from_where_it_last_wrote_itself(nested_families):
    """成员发出的消息回到对方最近亲自写信的地方

    成员或 steward 发给 Family 外的用户或 Resident 时，对方最近一次亲自（不经门面转交）写信的对象若是这个成员或某个内层门面，就以它发出；最近写给的是外层门面，或从没亲自写过，就以最外层门面发出。对方之后又直接找成员，消息回到成员本人（CASE-A 10-06）。

    :aspects: functional, behavior
    :design: decision_44cf587c1721, decision_c524c775e0be
    """
    speak = outward_messages.speaking_identity
    talked = nested_families["talked"]
    talked[("CASE-A", MEMBER)] = "2026-10-06T01:00:00Z"
    assert speak(MEMBER, _user("CASE-A")) == MEMBER
    assert speak(PEER, _user("CASE-A")) == OUTER_FACADE
    # CASE-A has since been writing to the facade: the member answers there (CASE-A 10-06).
    talked[("CASE-A", OUTER_FACADE)] = "2026-10-06T02:00:00Z"
    assert speak(MEMBER, _user("CASE-A")) == OUTER_FACADE
    talked[("CASE-A", MEMBER)] = "2026-10-06T03:00:00Z"
    assert speak(MEMBER, _user("CASE-A")) == MEMBER
    talked[("sample_admin", INNER_FACADE)] = "2026-10-06T01:00:00Z"
    assert speak(PEER, _user("sample_admin")) == INNER_FACADE
    talked[(STRANGER, MEMBER)] = "2026-10-06T01:00:00Z"
    assert speak(MEMBER, _resident(STRANGER)) == MEMBER


def test_history_a_fork_copied_was_not_written_to_the_fork(nested_families):
    """Fork 复制来的历史不算对方写给新成员

    成员由 Fork 产生时复制的对话早于成员创建，不算对方亲自写给这个成员；成员创建之后对方直接写来的消息才算。

    :aspects: functional, behavior
    :design: decision_44cf587c1721
    """
    talked, created = nested_families["talked"], nested_families["created"]
    # CASE-A wrote to the Resident the member was forked from; the Fork copied those messages.
    talked[("CASE-A", MEMBER)] = "2026-10-05T06:32:06Z"
    created[MEMBER] = "2026-10-05T16:59:42Z"
    assert outward_messages.speaking_identity(MEMBER, _user("CASE-A")) == OUTER_FACADE
    talked[("CASE-A", MEMBER)] = "2026-10-05T18:00:00Z"
    assert outward_messages.speaking_identity(MEMBER, _user("CASE-A")) == MEMBER


def test_a_relayed_copy_is_not_a_message_written_to_the_member(delivery_resident):
    """门面转交的副本不算对方写给成员

    成员对话中带门面转交标注的消息不算发送者亲自写给成员；发送者直接发给成员的消息才记为最近一次写信。

    :importance: P2
    :design: decision_44cf587c1721
    """
    from management_portal.society_service import family_facade

    member = delivery_resident["resident_id"]
    repository = resident_conversation_stream.get_repository()
    repository.append_message(member, {
        "message_id": "msg_0000000000000b01", "role": "user", "author_id": "CASE-A",
        "created_at": "2026-10-06T01:00:00Z", "content_parts": [{"type": "text", "text": "经门面转交"}],
        "business_ref": family_facade.relay_mark("resident_0000000000ff", "msg_0000000000000c01"),
    })
    assert outward_messages._last_direct_messages("CASE-A", [member]) == {member: ""}
    repository.append_message(member, {
        "message_id": "msg_0000000000000b02", "role": "user", "author_id": "CASE-A",
        "created_at": "2026-10-06T01:01:00Z", "content_parts": [{"type": "text", "text": "直接问你"}],
    })
    assert outward_messages._last_direct_messages("CASE-A", [member])[member]


@pytest.fixture
def readers(monkeypatch):
    """Users who may open each Resident's Conversation."""

    allowed: dict[str, set[str]] = {}
    monkeypatch.setattr(
        resident_access, "resident_visible_to_actor",
        lambda record, actor: actor in allowed.get(str(record.get("resident_id") or ""), set()),
    )
    return allowed


def _text(text):
    return {"content_parts": [{"type": "text", "text": text}]}


def test_a_resident_sends_a_user_a_message_in_its_own_conversation(delivery_resident, readers):
    """Resident 用 DeliverMessage 给用户发消息，消息写在自己的对话中

    DeliverMessage 给出 user_id 时，消息以 Resident 的 agent 消息写入它自己的对话并立即显示，注明收件人，不进入队列、不开始工作；同一幂等键重试得到同一条消息，同一消息 ID 换正文报冲突；只有收件人看到未读和会话条目，能看这段对话的其他用户没有。

    :module: resident.conversation
    :design: decision_c524c775e0be, decision_b4e5ea4a47ec
    """
    sender = delivery_resident["resident_id"]
    readers[sender] = {"CASE-A", "sample_admin"}
    repository = resident_conversation_stream.get_repository()

    sent = run("DeliverMessage", {"user_id": "CASE-A", "message": _text("周报已经写好，请看第三节。")},
               actor=sender, key="report-ready").raise_for_error()

    assert (sent["resident_id"], sent["sender_id"], sent["input_state"]) == (sender, sender, {})
    assert sent["recipients"] == [{"kind": "user", "id": "CASE-A"}]
    message = repository.message(sender, sent["message_id"])
    assert (message["role"], message["author_id"]) == ("agent", sender)
    assert message["origin"]["sender"] == {"kind": "resident", "id": sender}
    assert message["recipients"] == [{"kind": "user", "id": "CASE-A"}]
    assert message["activated_at"]  # shown at once
    assert "reply_to_message_id" not in message
    with pytest.raises(KeyError):  # it is no one's input: nothing waits or runs
        repository.input_state(sender, sent["message_id"])
    assert repository.snapshot(sender)["queue"]["items"] == []

    # A retry is the same message.
    again = run("DeliverMessage", {"user_id": "CASE-A", "message": _text("周报已经写好，请看第三节。")},
                actor=sender, key="report-ready").raise_for_error()
    assert again["message_id"] == sent["message_id"]
    assert [item["message_id"] for item in repository.read_messages(sender)].count(sent["message_id"]) == 1
    changed = run("DeliverMessage", {"user_id": "CASE-A", "message": {
        "message_id": sent["message_id"], **_text("改过的正文")}}, actor=sender)
    assert changed.error["code"] == "instruction.conflict"

    # Only the recipient finds it unread; another reader of the Conversation does not.
    unread = projection.conversation_family_summaries([sender], "CASE-A")[sender]["unread_signature"]
    assert (unread["completion_id"], unread["request_actor"]) == (sent["message_id"], "CASE-A")
    assert projection.conversation_family_summaries([sender], "sample_admin")[sender]["unread_signature"]["completion_id"] == ""
    listed = resident_domain.list_resident_conversation_activity("CASE-A", [sender])["residents"]
    assert [item["resident_id"] for item in listed] == [sender]
    assert resident_domain.list_resident_conversation_activity("sample_admin", [sender])["residents"] == []


def test_portal_writes_a_notice_in_a_residents_name_without_starting_its_work(delivery_resident, readers):
    """Portal 以 Resident 名义写通知，只送达有读权限的用户且不让它开始工作，同一消息只写一次

    :module: platform.core
    :importance: P2
    """
    leader = delivery_resident["resident_id"]
    readers[leader] = {"CASE-A", "sample_admin"}
    repository = resident_conversation_stream.get_repository()

    written = outward_messages.post_platform_notice(
        leader, ["CASE-A", "sample_admin", "outsider"], "自动触发的脚本失败：sync.py\n\n请查看运行记录。",
        message_id="msg_0000000000000f01", business_ref="lineage-failure:lineage_aaaaaaaaaaaa",
    )

    assert written["resident_id"] == leader
    assert written["recipients"] == [{"kind": "user", "id": "CASE-A"}, {"kind": "user", "id": "sample_admin"}]
    assert written["unreached"] == ["outsider"]
    message = repository.message(leader, "msg_0000000000000f01")
    assert (message["role"], message["author_id"]) == ("agent", leader)
    assert message["origin"]["initiated_by"] == {"kind": "service", "id": "portal"}
    assert message["recipients"] == written["recipients"]
    with pytest.raises(KeyError):  # the Leader is not asked to work
        repository.input_state(leader, "msg_0000000000000f01")
    assert repository.snapshot(leader)["queue"]["items"] == []
    unread = projection.conversation_family_summaries([leader], "CASE-A")[leader]["unread_signature"]
    assert unread["completion_id"] == "msg_0000000000000f01"

    # The same subject on the same day keeps its first message.
    outward_messages.post_platform_notice(leader, ["CASE-A"], "另一次失败", message_id="msg_0000000000000f01")
    assert [item["message_id"] for item in repository.read_messages(leader)].count("msg_0000000000000f01") == 1
    assert repository.message(leader, "msg_0000000000000f01")["content_parts"] == message["content_parts"]


def test_a_message_to_a_user_needs_a_resident_sender_a_reader_and_plain_content(delivery_resident, readers):
    """给用户发消息的权限与内容限制

    用户不能用 user_id 发消息；收件用户不能查看发出消息的对话时拒绝；带图片等非文字、非资源引用分段、使用 attach_to_next、同时给出 user_id 与 resident_id 都被拒绝，且不写入任何消息。

    :module: security.identity-access
    :design: decision_c524c775e0be
    """
    sender = delivery_resident["resident_id"]
    readers[sender] = {"CASE-A"}
    by_user = run("DeliverMessage", {"user_id": "CASE-A", "message": _text("你好")}, actor="sample_admin")
    assert by_user.error["code"] == "instruction.forbidden"
    unreadable = run("DeliverMessage", {"user_id": "nobody", "message": _text("你好")}, actor=sender)
    assert unreadable.error["code"] == "instruction.forbidden"
    image = run("DeliverMessage", {"user_id": "CASE-A", "message": {"content_parts": [
        {"type": "text", "text": "截图"}, {"type": "image", "path": "/tmp/a.png"}]}}, actor=sender)
    assert image.error["code"] == "instruction.invalid_arguments"
    info = run("DeliverMessage", {"user_id": "CASE-A", "message": _text("背景"), "activation_policy": "attach_to_next"},
               actor=sender)
    assert info.error["code"] == "instruction.invalid_arguments"
    both = run("DeliverMessage", {"user_id": "CASE-A", "resident_id": sender, "message": _text("你好")}, actor=sender)
    assert both.error["code"] == "instruction.invalid_arguments"
    assert resident_conversation_stream.get_repository().read_messages(sender) == []


def _register(title):
    return resident_domain.register_resident(_new_session_payload(title), "sample_admin")


def test_a_family_member_writes_to_a_user_as_the_facade(delivery_resident, readers, monkeypatch):
    """Family 成员给用户的消息写在门面对话中

    成员发给用户的消息以门面身份写入门面对话，实际发出的成员记为 initiated_by，成员自己的对话中没有这条消息；收件人在门面条目上看到未读。

    :design: decision_44cf587c1721, decision_c524c775e0be
    """
    member = delivery_resident["resident_id"]
    facade = _register("Family 门面")["resident_id"]
    readers[facade] = {"CASE-A"}
    monkeypatch.setattr(outward_messages, "speaking_identity",
                        lambda sender, recipient: facade if sender == member else sender)

    sent = run("DeliverMessage", {"user_id": "CASE-A", "message": _text("需要你决定发布时间。")},
               actor=member).raise_for_error()

    assert (sent["resident_id"], sent["sender_id"]) == (facade, facade)
    repository = resident_conversation_stream.get_repository()
    message = repository.message(facade, sent["message_id"])
    assert message["author_id"] == facade
    assert message["origin"]["initiated_by"] == {"kind": "resident", "id": member}
    assert repository.read_messages(member) == []
    assert projection.conversation_family_summaries([facade], "CASE-A")[facade]["unread_signature"]["completion_id"] == \
        sent["message_id"]


def test_a_family_member_reaches_an_outside_resident_as_the_facade(delivery_resident, monkeypatch):
    """Family 成员发给 Family 外的 Resident 时以门面发出并在门面对话留记录

    收件 Resident 收到门面署名、initiated_by 为成员的消息；门面对话中记下一条注明收件人的门面消息，正文相同；重试不重复记录；成员自己无权发给收件方时拒绝，门面不代为发送。

    :design: decision_44cf587c1721, decision_c524c775e0be
    """
    member = delivery_resident["resident_id"]
    facade = _register("Family 门面")["resident_id"]
    outsider = _register("外部协作者")["resident_id"]
    monkeypatch.setattr(outward_messages, "speaking_identity",
                        lambda sender, recipient: facade if sender == member else sender)
    repository = resident_conversation_stream.get_repository()

    sent = run("DeliverMessage", {"resident_id": outsider, "message": _text("接口文档在 workspace 里。")},
               actor=member, key="to-outsider").raise_for_error()

    assert (sent["resident_id"], sent["sender_id"]) == (outsider, facade)
    delivered = repository.message(outsider, sent["message_id"])
    assert delivered["author_id"] == facade
    assert delivered["origin"]["initiated_by"] == {"kind": "resident", "id": member}
    # The facade's Conversation records what the Family sent, and to whom.
    record = repository.message(facade, outward_messages.outward_record_id(facade, sent["message_id"]))
    assert (record["role"], record["author_id"]) == ("agent", facade)
    assert record["recipients"] == [{"kind": "resident", "id": outsider}]
    assert record["origin"]["initiated_by"] == {"kind": "resident", "id": member}
    assert record["content_parts"] == delivered["content_parts"]
    run("DeliverMessage", {"resident_id": outsider, "message": _text("接口文档在 workspace 里。")},
        actor=member, key="to-outsider").raise_for_error()
    assert [item["message_id"] for item in repository.read_messages(facade)] == [record["message_id"]]

    # The Family speaks only where the member itself may send.
    monkeypatch.setattr(resident_access, "can_manage_resident",
                        lambda target, actor, operation="manage": operation == "read" or actor != member)
    refused = run("DeliverMessage", {"resident_id": outsider, "message": _text("再发一条。")}, actor=member)
    assert refused.error["code"] == "instruction.forbidden"
    assert len(repository.read_messages(facade)) == 1


def test_the_newer_of_an_answer_and_a_message_to_the_user_is_what_is_unread(delivery_resident):
    """未读取答复与发给用户的消息中较新的一项

    同一对话中，发给用户的消息比上一轮答复新时，未读的是这条消息；用户之后的请求得到答复时，未读又回到这次答复。

    :module: resident.conversation
    :importance: P2
    :design: decision_b4e5ea4a47ec
    """
    resident = delivery_resident["resident_id"]
    repository = resident_conversation_stream.get_repository()

    def say(message_id, role, at, **fields):
        repository.append_message(resident, {
            "message_id": message_id, "role": role, "created_at": at,
            "content_parts": [{"type": "text", "text": message_id}], **fields,
        })

    def unread():
        return projection.conversation_family_summaries([resident], "CASE-A")[resident]["unread_signature"]

    say("msg_0000000000000d01", "user", "2026-10-06T01:00:00Z", author_id="CASE-A")
    say("msg_0000000000000d02", "agent", "2026-10-06T01:00:30Z", reply_to_message_id="msg_0000000000000d01")
    repository.set_input_state(resident, "msg_0000000000000d01", "completed",
                               updated_at="2026-10-06T01:00:30Z")
    assert unread()["completion_id"] == "msg_0000000000000d02"
    say("msg_0000000000000d03", "agent", "2026-10-06T02:00:00Z", author_id=resident,
        recipients=[{"kind": "user", "id": "CASE-A"}])
    assert (unread()["completion_id"], unread()["input_message_id"]) == ("msg_0000000000000d03", "")
    assert unread()["completed_at"] == "2026-10-06T02:00:00Z"
    say("msg_0000000000000d04", "user", "2026-10-06T03:00:00Z", author_id="CASE-A")
    say("msg_0000000000000d05", "agent", "2026-10-06T03:00:30Z", reply_to_message_id="msg_0000000000000d04")
    repository.set_input_state(resident, "msg_0000000000000d04", "completed",
                               updated_at="2026-10-06T03:00:30Z")
    assert unread()["completion_id"] == "msg_0000000000000d05"


def test_a_conversation_where_the_user_was_sent_a_message_is_an_inbox_entry(monkeypatch):
    """有消息发给用户的对话出现在用户的收件箱中

    用户没有在对话中发过消息、但对话中有发给他的消息时，收件箱列出这个 Resident 条目，更新时间与完成签名取这条消息。

    :module: workbench.chat
    :importance: P2
    :design: decision_b4e5ea4a47ec
    """
    from management_portal import conversation_index_projection as index

    monkeypatch.setattr(index, "resident_state_snapshots", lambda _actor, resident_ids: {
        resident_id: {"state": {"work_state": "idle"}, "cursor": 3} for resident_id in resident_ids
    })
    resident = {
        "resident_id": "resident_111111111111", "title": "周报 Family",
        "actor_addressed_at": "2026-10-06T01:00:00Z",
        "unread_signature": {"schema_version": 1, "request_actor": "CASE-A", "completion_id": "msg_0000000000000e01",
                             "input_message_id": "", "completed_at": "2026-10-06T01:00:00Z"},
    }

    entry, = index.resident_entries("CASE-A", {"residents": [resident]})

    assert (entry["key"], entry["kind"]) == ("resident:resident_111111111111", "resident-main")
    assert entry["updated_at"] == "2026-10-06T01:00:00Z"
    assert '"completion_id":"msg_0000000000000e01"' in entry["completion_signature"]
    assert index.resident_entries("CASE-A", {"residents": [{**resident, "actor_addressed_at": ""}]}) == []


def test_a_facade_speaks_for_the_members_and_stewards_of_the_families_it_fronts(nested_families):
    """门面代表它前面的 Family 中的成员和 steward

    门面代表自己 Family 的成员和 steward，外层门面也代表内层 Family 的成员；不代表只同属外层 Family 的 Resident，也不代表 Family 外的 Resident；成员不代表同一 Family 的其他成员。

    :importance: P2
    :design: decision_44cf587c1721
    """
    speaks = family_facade.speaks_for
    assert speaks(INNER_FACADE, MEMBER) and speaks(OUTER_FACADE, MEMBER) and speaks(INNER_FACADE, STEWARD)
    assert speaks(OUTER_FACADE, COUSIN)
    assert not speaks(INNER_FACADE, COUSIN)
    assert not speaks(OUTER_FACADE, STRANGER)
    assert not speaks(MEMBER, PEER)
    assert not speaks("", MEMBER)


def _with_uploads(text, *uploads):
    """Content parts carrying uploads, each given as (upload ID, file name)."""

    parts = [{"type": "text", "text": text}]
    for upload_id, name in uploads:
        kind, mime = ("image", "image/png") if name.endswith(".png") else ("file", "text/plain")
        parts.append({"type": kind, "media_type": kind, "upload_id": upload_id, "mime_type": mime, "name": name})
    return parts


def test_a_familys_message_out_carries_the_members_own_uploads_and_no_one_elses(delivery_resident, monkeypatch,
                                                                                tmp_path, permissions):
    """Family 以门面对外发的消息按成员本人的权限核对附件

    成员发给 Family 外 Resident 的消息以门面署名送达，成员自己用 CreateUpload 上传的图片和文件随消息送达（图片暂存给收件方，文件写成文件引用）；权限系统开启时门面或其他用户上传的附件被拒绝：只能发送自己上传的附件。

    CASE-A 10-06: what a Family sends out as its facade is read with the member's own access.

    The member's own uploads go out with it.  Anyone else's do not, the
    facade's included: the facade only carries the member's message.

    :module: security.identity-access
    :design: decision_44cf587c1721, decision_c524c775e0be
    """

    from management_portal.society_service import resident_execution_work
    from management_portal.society_service.resident_record_domain import find_resident as _find_resident

    monkeypatch.setenv("PORTAL_UPLOAD_ROOT", str(tmp_path / "uploads"))
    member = delivery_resident["resident_id"]
    facade = _register("Family 门面")["resident_id"]
    outsider = _register("外部协作者")["resident_id"]
    monkeypatch.setattr(outward_messages, "speaking_identity",
                        lambda sender, recipient: facade if sender == member else sender)
    permissions.place(Fact("resident", outsider))
    permissions.grant(member, outsider, "@contributor")
    image = _upload("shot.png", "image/png", PNG, owner=member)
    notes = _upload("notes.txt", "text/plain", b"steps to reproduce", owner=member)
    parts = _with_uploads("登录页报错，截图和日志在附件里。", (image, "shot.png"), (notes, "notes.txt"))

    sent = run("DeliverMessage", {"resident_id": outsider, "message": {"content_parts": parts}},
               actor=member).raise_for_error()

    assert sent["sender_id"] == facade
    delivered = resident_conversation_stream.get_repository().message(outsider, sent["message_id"])
    assert (delivered["author_id"], delivered["content_parts"]) == (facade, parts)
    job = resident_execution_work.read(_find_resident(outsider))
    assert [item["name"] for item in job["input_media"]] == ["shot.png"]
    assert open(job["input_media"][0]["source"], "rb").read() == PNG
    from management_portal.society_service.resident_execution_domain import _prepare_resident_prompt
    job = _prepare_resident_prompt(_find_resident(outsider), job)
    assert f"/{notes}/notes.txt]" in job["actual_sent_prompt"]

    for owner in (facade, "sample_admin"):
        foreign = _upload("other.png", "image/png", PNG, owner=owner)
        refused = run("DeliverMessage", {"resident_id": outsider, "message": {
            "content_parts": _with_uploads("再看这张。", (foreign, "other.png"))}}, actor=member)
        assert refused.error["code"] == "instruction.forbidden"
        assert "只能发送自己上传的附件" in refused.error["message"]


def test_only_a_familys_message_out_has_its_facade_send_a_members_uploads(delivery_resident, monkeypatch, tmp_path,
                                                                          permissions):
    """只有平台让门面代成员对外发送时才按成员身份核对附件

    权限系统开启时，门面以成员为发起人的其他操作（如门面通知 steward）仍按门面自己的身份核对附件，成员的上传被拒绝；只在平台代成员对外发送的那次投递中门面代表该成员，其中嵌套的其他操作不算。

    The facade speaking with a member as initiator is not enough: the platform must be sending the message out.

    :module: security.identity-access
    :design: decision_44cf587c1721
    """

    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity
    from management_portal.society_service import resident_protocol

    monkeypatch.setenv("PORTAL_UPLOAD_ROOT", str(tmp_path / "uploads"))
    member = delivery_resident["resident_id"]
    facade = _register("Family 门面")["resident_id"]
    outsider = _register("外部协作者")["resident_id"]
    image = _upload("shot.png", "image/png", PNG, owner=member)
    payload = {"resident_id": outsider, "message": {
        "message_id": "msg_0000000000000f01", "role": "user", "content_parts": _with_uploads("截图", (image, "shot.png")),
    }}

    # As when the facade tells its steward what a member did: the facade's own operation.
    with operation_identity_scope(OperationIdentity.for_actor(facade, initiated_by=member)):
        assert outward_messages.speaking_for(facade) == ""
        with pytest.raises(PermissionError, match="只能发送自己上传的附件"):
            resident_protocol.deliver_resident_input(payload, facade)
    with outward_messages._speaking_for(facade, member):
        assert (outward_messages.speaking_for(facade), outward_messages.speaking_for(member)) == (member, "")
        with operation_identity_scope(OperationIdentity.for_actor(facade, initiated_by="CASE-A")):
            assert outward_messages.speaking_for(facade) == ""
    assert outward_messages.speaking_for(facade) == ""


def test_another_familys_router_relays_the_members_uploads_its_facade_received(delivery_resident, monkeypatch,
                                                                               tmp_path, permissions):
    """另一个 Family 的 Router 转交时仍按原成员的权限读取附件

    Family 以门面发给另一个 Family 门面的消息到达时按原成员的权限核对附件，等待那边的 Router；Router 原样转交给成员时，成员按原成员的权限读取附件；权限系统开启时，发出它的门面已不代表该成员时转交被拒绝。

    A Family's message out may reach another Family's facade, checked with the member's access.

    The other Family's Router relays it unchanged and the member it picks
    reads it with that same access, as long as the facade it came from still
    fronts the member.

    :module: security.identity-access
    :design: decision_44cf587c1721
    """

    from management_portal.society_service import family_facade, resident_execution_work, resident_protocol
    from management_portal.society_service.resident_record_domain import (
        find_resident as _find_resident,
        save_resident_record as _save_resident_record,
    )

    monkeypatch.setenv("PORTAL_UPLOAD_ROOT", str(tmp_path / "uploads"))
    member = delivery_resident["resident_id"]
    facade = _register("Family 门面")["resident_id"]
    other = _register("另一个 Family 的门面")["resident_id"]
    set_role(other, family_id="fbi_family0002", actor="sample_admin")
    monkeypatch.setattr(outward_messages, "speaking_identity",
                        lambda sender, recipient: facade if sender == member else sender)
    fronted = {(facade, member)}
    monkeypatch.setattr(family_facade, "speaks_for", lambda facade_id, member_id: (facade_id, member_id) in fronted)
    permissions.place(Fact("resident", other))
    permissions.grant(member, other, "@contributor")
    image = _upload("shot.png", "image/png", PNG, owner=member)
    parts = _with_uploads("截图在附件里。", (image, "shot.png"))

    sent = run("DeliverMessage", {"resident_id": other, "message": {"content_parts": parts}},
               actor=member).raise_for_error()

    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(other, sent["message_id"])["state"] == "queued"  # waiting for the Router
    assert repository.message(other, sent["message_id"])["origin"]["initiated_by"] == {"kind": "resident", "id": member}

    # The other Family's Router relays to a distinct member; facade role facts
    # cannot be removed by saving a Resident's read projection.
    recipient = _register("接收附件的成员")["resident_id"]
    mark = family_facade.relay_mark(other, sent["message_id"])
    monkeypatch.setattr(family_facade, "validate_relay",
                        lambda business_ref, actor: (other, sent["message_id"]) if business_ref == mark else None)
    monkeypatch.setattr(family_facade, "relay_delegates", lambda source, member_id: source is not None)

    def relay(message_id):
        return resident_protocol.deliver_resident_input({
            "resident_id": recipient, "message": {"message_id": message_id, "role": "user", "content_parts": parts},
        }, "resident_cccccccccccc", delivery_context=resident_protocol.ResidentDeliveryContext(business_ref=mark))

    # Once that facade no longer fronts the member, the upload is not the facade's to pass on.
    fronted.clear()
    with pytest.raises(PermissionError, match="只能发送自己上传的附件"):
        relay("msg_0000000000000f10")
    fronted.add((facade, member))
    relay("msg_0000000000000f11")

    job = resident_execution_work.read(_find_resident(recipient))
    assert job["user_message_id"] == "msg_0000000000000f11"
    assert [item["name"] for item in job["input_media"]] == ["shot.png"]
