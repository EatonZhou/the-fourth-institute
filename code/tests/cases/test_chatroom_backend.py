"""Chatrooms: User members, messages with attachments, unread counts and @ Resident delivery (decision_a6bebf4ac283)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from management_portal import chatroom_backend
import management_portal.resources.isa as resource_isa
from tests.cases.resident_execution_test_support import input_delivery, resident_record  # noqa: F401

from management_portal.society_service import resident_conversation_records


ALICE = "alice"
BOB = "bob"
CAROL = "carol"
DAVE = "dave"
HELPER = "resident_0123456789ab"
REVIEWER = "resident_1123456789ab"
REAL_PUBLISH = chatroom_backend._publish
REAL_PUBLISH_INBOX = chatroom_backend._publish_inbox


class FakeLineage:
    """Stands in for SocietyService: records deliveries and answers input reads."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str, str]] = []
        self.inputs: dict[tuple[str, str], dict] = {}
        self.messages: dict[tuple[str, str], dict] = {}
        self.refuse: dict[str, str] = {}

    def __call__(self, instruction, arguments, caller, *, idempotency_key=""):
        self.calls.append((instruction, dict(arguments), caller, idempotency_key))
        resident_id = arguments["resident_id"]
        if instruction == "DeliverMessage":
            if resident_id in self.refuse:
                raise PermissionError(self.refuse[resident_id])
            message_id = arguments["message"]["message_id"]
            self.inputs.setdefault((resident_id, message_id), {"state": "queued", "reply_message_ids": []})
            return {"resident_id": resident_id, "message_id": message_id, "input_state": {"state": "queued"}}
        if instruction == "GetInputState":
            return {"input_state": dict(self.inputs[(resident_id, arguments["message_id"])])}
        if instruction == "GetMessage":
            return {"message": dict(self.messages[(resident_id, arguments["message_id"])])}
        raise AssertionError(instruction)

    def deliveries(self):
        return [call for call in self.calls if call[0] == "DeliverMessage"]

    def reply(self, resident_id: str, input_message_id: str, *texts: str, state: str = "completed") -> None:
        reply_ids = []
        for index, text in enumerate(texts):
            reply_id = f"msg_{index:02d}{input_message_id[4:20]}"
            self.messages[(resident_id, reply_id)] = {
                "message_id": reply_id,
                "role": "agent",
                "author_id": resident_id,
                "content_parts": [{"type": "text", "text": text}, {"type": "tool_call", "tool_name": "shell"}],
            }
            reply_ids.append(reply_id)
        self.inputs[(resident_id, input_message_id)] = {
            "state": state,
            "reply_message_ids": reply_ids,
            "error": {"message": "模型调用失败"} if state == "failed" else {},
        }


@pytest.fixture
def lineage(monkeypatch):
    fake = FakeLineage()
    monkeypatch.setattr(chatroom_backend, "_execute", fake)
    monkeypatch.setattr(chatroom_backend, "_portal_users", lambda: [
        {"username": ALICE, "status": "active"},
        {"username": BOB, "status": "active"},
        {"username": CAROL, "status": "disabled"},
        {"username": DAVE, "status": "active"},
    ])
    monkeypatch.setattr(chatroom_backend, "QUEUED_RETRY_AFTER_SECONDS", 0)
    published: list[tuple[str, list[str]]] = []
    progress_wakes: list[str] = []
    monkeypatch.setattr(
        chatroom_backend, "_publish",
        lambda room, *, fields, inbox=True: (
            published.append((room["chatroom_id"], list(room.get("members") or [])))
            if inbox else progress_wakes.append(room["chatroom_id"])
        ),
    )
    inboxes: list[list[str]] = []
    monkeypatch.setattr(chatroom_backend, "_publish_inbox", lambda users: inboxes.append(sorted(users)))
    uploads: dict[str, dict] = {}
    monkeypatch.setattr(resource_isa, "read_upload", lambda upload_id: dict(uploads[upload_id]))
    fake.published = published
    fake.progress_wakes = progress_wakes
    fake.inboxes = inboxes
    fake.uploads = uploads
    return fake


def text(value: str) -> list[dict]:
    return [{"type": "text", "text": value}]


def _texts(chatroom_id: str, actor: str = ALICE) -> list[tuple[str, str, str]]:
    messages = chatroom_backend.list_messages(actor, chatroom_id)["messages"]
    return [(item["role"], item["author_id"], chatroom_backend._message_text(item)) for item in messages]


def test_members_are_users_and_any_member_renames_and_adds_users(lineage):
    """Chatroom 成员只能是用户，任一成员可改名和邀请用户并留下系统消息"""
    for members, error in (([HELPER], ValueError), ([CAROL], ValueError), (["nobody"], ValueError)):
        with pytest.raises(error):
            chatroom_backend.create_chatroom(ALICE, members)
    room = chatroom_backend.create_chatroom(ALICE, [BOB, "ALICE"], "设计讨论")
    chatroom_id = room["chatroom_id"]
    assert room["members"] == [ALICE, BOB] and room["title"] == "设计讨论"
    assert room["permissions"] == {
        "can_post": True, "can_rename": True, "can_add_members": True, "can_leave": True, "can_delete": True,
    }
    assert chatroom_backend.get_chatroom(BOB, chatroom_id)["permissions"]["can_delete"] is True
    assert chatroom_backend.create_chatroom(DAVE, [])["members"] == [DAVE]

    updated = chatroom_backend.update_chatroom(BOB, chatroom_id, title="新名字", add_members=[DAVE, BOB])
    assert updated["title"] == "新名字" and updated["members"] == [ALICE, BOB, DAVE]
    with pytest.raises(ValueError):
        chatroom_backend.update_chatroom(ALICE, chatroom_id, title="不会生效", add_members=[HELPER])
    assert chatroom_backend.get_chatroom(DAVE, chatroom_id)["permissions"]["can_rename"]
    assert [message for role, _author, message in _texts(chatroom_id) if role == "system"] == [
        "alice 创建了聊天室",
        "bob 把聊天室名称改为「新名字」",
        "bob 邀请 dave 加入了聊天室",
    ]


def test_only_members_read_and_post_and_each_member_has_an_unread_count(lineage):
    """Chatroom 按成员分别统计未读和 @ 提及，静音后普通消息不再提醒"""
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    chatroom_backend.post_message(ALICE, chatroom_id, text("  你好 bob  "))
    assert chatroom_backend.list_messages(DAVE, chatroom_id)["messages"]
    assert chatroom_backend.get_chatroom(DAVE, chatroom_id)["permissions"]["can_post"]
    for parts in ([], text("   "), text("x" * (chatroom_backend.MAX_MESSAGE_CHARS + 1)), [{"type": "tool_call"}]):
        with pytest.raises(ValueError):
            chatroom_backend.post_message(ALICE, chatroom_id, parts)

    # Creation and the author's own messages are read; the other member's are not.
    assert chatroom_backend.list_chatrooms(ALICE)[0]["unread_count"] == 0
    assert chatroom_backend.list_chatrooms(BOB)[0]["unread_count"] == 2
    assert chatroom_backend.list_chatrooms(BOB)[0]["last_message"]["preview"] == "你好 bob"
    assert chatroom_backend.mark_read(BOB, chatroom_id)["unread_count"] == 0
    chatroom_backend.post_message(ALICE, chatroom_id, text("还在吗"))
    assert chatroom_backend.get_chatroom(BOB, chatroom_id)["unread_count"] == 1
    chatroom_backend.post_message(BOB, chatroom_id, text("在"))
    assert chatroom_backend.get_chatroom(BOB, chatroom_id)["unread_count"] == 0
    assert chatroom_backend.get_chatroom(ALICE, chatroom_id)["unread_count"] == 1
    assert lineage.published and all(members == [ALICE, BOB] for _id, members in lineage.published)

    count = len(chatroom_backend.list_messages(BOB, chatroom_id)["messages"])
    chatroom_backend.update_chatroom(BOB, chatroom_id, muted=True)
    assert not chatroom_backend.get_chatroom(ALICE, chatroom_id)["muted"]
    assert len(chatroom_backend.list_messages(BOB, chatroom_id)["messages"]) == count
    ordinary = chatroom_backend.post_message(ALICE, chatroom_id, text("普通消息"))["message"]
    state = chatroom_backend.get_chatroom(BOB, chatroom_id)
    assert state["unread_count"] == 1 and not state["attention_unread"]
    for body in ["`@bob`", "```\n@bob\n```", "> @bob", "contact@bob.com", "[@bob](https://example.test)", "@bobby @dave @alice"]:
        chatroom_backend.post_message(ALICE, chatroom_id, text(body))
    assert chatroom_backend.get_chatroom(BOB, chatroom_id)["unread_mention_count"] == 0
    ping = chatroom_backend.post_message(ALICE, chatroom_id, text("@BOB @bob 请看这里"))["message"]
    assert chatroom_backend.get_chatroom(BOB, chatroom_id)["last_message"]["mentioned_users"] == [BOB]
    state = chatroom_backend.get_chatroom(BOB, chatroom_id)
    assert state["attention_unread"] and state["unread_mention_count"] == 1
    assert state["unread_mentions"] == [{"message_id": ping["message_id"], "ordinal": ping["ordinal"]}]
    # A receipt for an old viewport cannot clear a newly arrived mention.
    assert chatroom_backend.mark_read(BOB, chatroom_id, through=ordinary["ordinal"])["unread_mention_count"] == 1
    assert chatroom_backend.mark_read(BOB, chatroom_id, through=ping["ordinal"])["unread_count"] == 0
    assert chatroom_backend.set_unread(BOB, chatroom_id, True)["unread_mention_count"] == 1
    assert not chatroom_backend.mark_read(BOB, chatroom_id)["attention_unread"]
    chatroom_backend.post_message(ALICE, chatroom_id, text("普通消息再次到达"))
    assert chatroom_backend.update_chatroom(BOB, chatroom_id, muted=False)["attention_unread"]
    with pytest.raises(ValueError):
        chatroom_backend.update_chatroom(DAVE, chatroom_id, muted=True)


def test_all_user_mention_notifies_current_members_once_without_calling_residents(lineage):
    """聊天室全员提及按发送时成员去重并保留免打扰提醒

    @全员提醒当前其他用户，重复或单独点名只计一次；后加入者不补提及，已读和重新标未读沿用现有行为，不召唤 Resident。

    :module: resident.conversation
    :importance: P1
    :design: decision_a6bebf4ac283
    :design-point:
    """
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    chatroom_backend.post_message(ALICE, chatroom_id, text(f"@{HELPER} 请看"))
    calls_before = len(lineage.deliveries())
    chatroom_backend.update_chatroom(BOB, chatroom_id, muted=True)
    chatroom_backend.mark_read(BOB, chatroom_id)

    posted = chatroom_backend.post_message(ALICE, chatroom_id, text("@全员 @全员 @bob 请确认"))
    message = posted["message"]
    state = chatroom_backend.get_chatroom(BOB, chatroom_id)
    assert state["muted"] and state["attention_unread"]
    assert state["last_message"]["mentioned_users"] == [BOB]
    assert state["unread_mention_count"] == 1
    assert state["unread_mentions"] == [{"message_id": message["message_id"], "ordinal": message["ordinal"]}]
    assert chatroom_backend.get_chatroom(ALICE, chatroom_id)["unread_mention_count"] == 0
    assert posted["deliveries"] == [] and len(lineage.deliveries()) == calls_before
    assert not chatroom_backend.mark_read(BOB, chatroom_id)["attention_unread"]
    assert chatroom_backend.set_unread(BOB, chatroom_id, True)["unread_mention_count"] == 1

    # Membership is resolved at send time; joining never creates an old mention.
    chatroom_backend.update_chatroom(ALICE, chatroom_id, add_members=[DAVE])
    assert chatroom_backend.get_chatroom(DAVE, chatroom_id)["unread_mention_count"] == 0
    chatroom_backend.update_chatroom(DAVE, chatroom_id, muted=True)
    posted = chatroom_backend.post_message(BOB, chatroom_id, text("@全员，下一条通知"))
    for user in [ALICE, DAVE]:
        state = chatroom_backend.get_chatroom(user, chatroom_id)
        assert state["attention_unread"] and state["unread_mention_count"] == 1
        assert state["last_message"]["mentioned_users"] == [ALICE, DAVE]
    assert chatroom_backend.get_chatroom(BOB, chatroom_id)["unread_mention_count"] == 0
    assert posted["deliveries"] == [] and len(lineage.deliveries()) == calls_before


@pytest.mark.parametrize("body", [
    "`@全员`", "```\n@全员\n```", "> @全员", "[@全员](https://example.test)",
    "contact@全员.com", "@全员名单", "@@全员", "@全员_extra",
])
def test_all_user_mention_only_matches_authored_prose(lineage, body):
    """聊天室全员提及只识别独立正文标记

    代码、引用块、链接、邮箱和相似词中的 @全员 不产生提醒或 Resident 投递。

    :module: resident.conversation
    :importance: P1
    :design: decision_a6bebf4ac283
    :design-point:
    """
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    chatroom_backend.update_chatroom(BOB, chatroom_id, muted=True)
    chatroom_backend.post_message(ALICE, chatroom_id, text(body))
    state = chatroom_backend.get_chatroom(BOB, chatroom_id)
    assert state["unread_count"] > 0 and not state["attention_unread"]
    assert state["unread_mention_count"] == 0
    assert not lineage.deliveries()


def test_attachments_are_the_authors_uploads_to_this_chatroom(lineage):
    """Chatroom 附件只接受作者上传到本 Chatroom 的就绪文件，所有读者都能打开"""
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    lineage.uploads.update({
        "upload_aa": {"created_by": ALICE, "target": chatroom_id, "ready": True, "file_name": "plan.png", "media_type": "image/png", "size": 12},
        "upload_bb": {"created_by": BOB, "target": chatroom_id, "ready": True, "file_name": "b.txt", "media_type": "text/plain", "size": 3},
        "upload_cc": {"created_by": ALICE, "target": "resident_0123456789ab", "ready": True, "file_name": "c.txt"},
        "upload_dd": {"created_by": ALICE, "target": chatroom_id, "ready": False, "file_name": "d.txt"},
    })
    posted = chatroom_backend.post_message(ALICE, chatroom_id, [{"type": "image", "upload_id": "upload_aa"}])
    assert posted["message"]["content_parts"] == [
        {"type": "image", "upload_id": "upload_aa", "name": "plan.png", "mime_type": "image/png", "size": 12},
    ]
    assert chatroom_backend.list_chatrooms(BOB)[0]["last_message"]["preview"] == "[图片]"
    for upload_id, error in (("upload_dd", ValueError), ("bad", ValueError)):
        with pytest.raises(error):
            chatroom_backend.post_message(ALICE, chatroom_id, [*text("附件"), {"type": "file", "upload_id": upload_id}])
    assert chatroom_backend.can_read(chatroom_id, BOB)
    assert chatroom_backend.can_read(chatroom_id, DAVE)
    # The upload endpoint lets every reader of the Chatroom open its attachments.
    from management_portal.asgi import _upload_readable

    meta = {"created_by": ALICE, "target": chatroom_id, "purpose": "conversation_attachment"}
    assert _upload_readable(meta, BOB) and _upload_readable(meta, DAVE)


def test_message_pages_go_back_and_forward_by_position(lineage):
    """Chatroom 消息分页按位置向前翻旧消息、向后取新消息"""
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    for index in range(5):
        chatroom_backend.post_message(ALICE if index % 2 == 0 else BOB, chatroom_id, text(f"m{index}"))
    newest = chatroom_backend.list_messages(ALICE, chatroom_id, limit=2)
    assert [chatroom_backend._message_text(item) for item in newest["messages"]] == ["m3", "m4"]
    older = chatroom_backend.list_messages(ALICE, chatroom_id, before=newest["next_before"], limit=2)
    assert [chatroom_backend._message_text(item) for item in older["messages"]] == ["m1", "m2"]
    later = chatroom_backend.list_messages(ALICE, chatroom_id, after=4)
    assert [item["ordinal"] for item in later["messages"]] == [5, 6]


def test_a_mention_delivers_the_message_and_chatroom_and_lets_the_resident_read_it(lineage):
    """@ Resident 时把消息和 Chatroom 信息投递给它，它可读取 Chatroom 和附件"""
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB], "评审")["chatroom_id"]
    lineage.uploads["upload_ee"] = {"created_by": BOB, "target": chatroom_id, "ready": True, "file_name": "spec.pdf", "media_type": "application/pdf", "size": 9}
    chatroom_backend.post_message(ALICE, chatroom_id, text("先讨论一下，不 @ 任何人"))
    assert lineage.deliveries() == []
    assert chatroom_backend.list_messages(HELPER, chatroom_id)["messages"]

    posted = chatroom_backend.post_message(BOB, chatroom_id, [*text(f"@{HELPER} 帮忙看看附件"), {"type": "file", "upload_id": "upload_ee"}])
    assert [item["status"] for item in posted["deliveries"]] == ["delivered"]
    (_name, arguments, caller, key), = lineage.deliveries()
    assert caller == BOB and arguments["resident_id"] == HELPER
    assert arguments["activation_policy"] == "enqueue_and_trigger"
    assert arguments["business_ref"] == f"{chatroom_id}/{posted['message']['message_id']}"
    assert key == f"chatroom-delivery:{posted['deliveries'][0]['id']}"
    sent_text, sent_file = arguments["message"]["content_parts"]
    assert chatroom_id in sent_text["text"] and "ListChatroomMessages" in sent_text["text"] and "评审" in sent_text["text"]
    assert sent_text["text"].endswith(f"@{HELPER} 帮忙看看附件")
    assert "先讨论一下" not in sent_text["text"]
    assert sent_file == {"type": "file", "media_type": "file", "upload_id": "upload_ee", "mime_type": "application/pdf", "name": "spec.pdf"}

    # The called Resident reads the Chatroom and its attachments, but does not join or post.
    assert [item[2] for item in _texts(chatroom_id, HELPER)][-1] == f"@{HELPER} 帮忙看看附件"
    assert chatroom_backend.get_chatroom(HELPER, chatroom_id)["called_residents"] == [HELPER]
    assert chatroom_backend.can_read(chatroom_id, HELPER) and chatroom_backend.can_read(chatroom_id, REVIEWER)
    assert chatroom_backend.post_message(HELPER, chatroom_id, text("Resident 直接发言"))["message"]["author_id"] == HELPER


def test_a_quote_names_a_message_of_this_chatroom_and_reaches_the_called_resident(lineage):
    """聊天室发言只给出被引用消息的 ID，平台填写作者和摘录；被召唤的 Resident 先读到引用再读到正文；别处或不存在的消息不能引用

    :module: resident.conversation
    :importance: P1
    :design: decision_a6bebf4ac283, decision_c524c775e0be
    """
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB], "评审")["chatroom_id"]
    first = chatroom_backend.post_message(ALICE, chatroom_id, text("第一版方案：用 PostgreSQL"))["message"]
    other_room = chatroom_backend.create_chatroom(BOB, [])["chatroom_id"]
    elsewhere = chatroom_backend.post_message(BOB, other_room, text("别处的消息"))["message"]

    # The sender names only the message; the platform fills its author and excerpt.
    posted = chatroom_backend.post_message(
        BOB, chatroom_id, text(f"@{HELPER} 这个可行吗"), quote={"message_id": first["message_id"]},
    )
    expected = {"message_id": first["message_id"], "author_id": ALICE, "text": "第一版方案：用 PostgreSQL"}
    assert posted["message"]["quote"] == expected
    assert chatroom_backend.list_messages(ALICE, chatroom_id)["messages"][-1]["quote"] == expected

    # The called Resident reads the quoted author and excerpt before the message itself.
    (_name, arguments, _caller, _key), = lineage.deliveries()
    sent = arguments["message"]["content_parts"][0]["text"]
    assert f'<quoted-message author="{ALICE}" message_id="{first["message_id"]}">\n第一版方案：用 PostgreSQL\n</quoted-message>' in sent
    assert sent.endswith(f"@{HELPER} 这个可行吗")

    for wrong in ({"message_id": "msg_missing"}, {"message_id": elsewhere["message_id"]},
                  {"message_id": first["message_id"], "author_id": BOB}):
        with pytest.raises(ValueError):
            chatroom_backend.post_message(BOB, chatroom_id, text("引用不成立"), quote=wrong)


def test_forwarding_reads_a_chatroom_message_as_a_reader_of_that_chatroom(lineage):
    """ForwardMessages 以调用者的聊天室读取权转发聊天室消息，非成员和不存在的消息被拒绝

    :module: resident.conversation
    :importance: P1
    :design: decision_a6bebf4ac283, decision_3e063aac15b3
    """
    from management_portal.society_service.isa.conversation import _forwarded_item

    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB], "评审")["chatroom_id"]
    message = chatroom_backend.post_message(ALICE, chatroom_id, text("第一段\n\n第二段"))["message"]
    item = {"chatroom_id": chatroom_id, "message_id": message["message_id"]}
    assert _forwarded_item(BOB, item) == {
        "role": "user", "author": ALICE, "title": "聊天室「评审」", "content": "第一段\n\n第二段",
    }
    assert _forwarded_item(DAVE, item)["author"] == ALICE
    with pytest.raises(KeyError):
        _forwarded_item(BOB, {"chatroom_id": chatroom_id, "message_id": "msg_missing"})


def test_the_reply_shows_once_and_failures_are_reported_in_the_chatroom(lineage):
    """Resident 回复在 Chatroom 只显示一次并引用触发消息，失败和拒收以系统消息报告"""
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [])["chatroom_id"]
    original = chatroom_backend.post_message(ALICE, chatroom_id, text("先前的讨论"))["message"]
    posted = chatroom_backend.post_message(ALICE, chatroom_id, text(f"@{HELPER} 你好"), quote={"message_id": original["message_id"]})
    delivery = posted["deliveries"][0]
    lineage.reply(HELPER, delivery["input_message_id"], "第一段回复", "第二段回复")

    assert chatroom_backend.handle_resident_terminal(
        business_ref=f"{chatroom_id}/{posted['message']['message_id']}",
        resident_id=HELPER, message_id=delivery["input_message_id"],
    ) == {"chatroom_id": chatroom_id}
    chatroom_backend.list_messages(ALICE, chatroom_id)
    replies = [item for item in chatroom_backend.list_messages(ALICE, chatroom_id)["messages"] if item["role"] == "agent"]
    assert [(item["author_id"], item["content_parts"]) for item in replies] == [
        (HELPER, [{"type": "text", "text": "第一段回复"}]), (HELPER, [{"type": "text", "text": "第二段回复"}]),
    ]
    assert {item["reply_to_message_id"] for item in replies} == {posted["message"]["message_id"]}
    expected_quote = {"message_id": posted["message"]["message_id"], "author_id": ALICE, "text": f"@{HELPER} 你好"}
    assert all(item["quote"] == expected_quote for item in replies)
    # The trigger can be outside the page. Existing stored replies only carry
    # reply_to_message_id and receive the same quote without rewriting history.
    newest = chatroom_backend.list_messages(ALICE, chatroom_id, limit=1)["messages"]
    assert len(newest) == 1 and newest[0]["quote"] == expected_quote
    assert "quote" not in chatroom_backend._agent_data().chat_message(chatroom_id, newest[0]["message_id"])
    followup = chatroom_backend.post_message(ALICE, chatroom_id, text("继续"), quote={"message_id": replies[0]["message_id"]})
    assert followup["deliveries"][0]["resident_id"] == HELPER
    assert chatroom_backend._message_text(followup["message"]).startswith(f"@{HELPER} ")
    explicit = chatroom_backend.post_message(ALICE, chatroom_id, text(f"@{HELPER} 再确认"), quote={"message_id": replies[0]["message_id"]})
    assert len(explicit["deliveries"]) == 1
    assert chatroom_backend._message_text(explicit["message"]).count(f"@{HELPER}") == 1
    assert chatroom_backend.handle_resident_terminal(
        business_ref="graph_0123456789ab", resident_id=HELPER, message_id=delivery["input_message_id"],
    ) == {}

    failed = chatroom_backend.post_message(ALICE, chatroom_id, text(f"@{HELPER} 再来"))
    lineage.reply(HELPER, failed["deliveries"][0]["input_message_id"], state="failed")
    chatroom_backend.list_messages(ALICE, chatroom_id)
    assert _texts(chatroom_id)[-1] == ("system", "portal", f"{HELPER} 未完成这次回复（失败）。原因：模型调用失败")

    lineage.refuse[HELPER] = "需要有效的 Portal 登录身份"
    refused = chatroom_backend.post_message(ALICE, chatroom_id, text(f"@{HELPER} 第三次"))
    assert refused["deliveries"][0]["status"] == "undeliverable"
    assert _texts(chatroom_id)[-1] == (
        "system", "portal", f"未能把消息交给 {HELPER}：需要有效的 Portal 登录身份",
    )


def test_resident_replies_never_call_other_residents(lineage):
    """Resident 回复里的 @ 不会投递给其他 Resident"""
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [])["chatroom_id"]
    posted = chatroom_backend.post_message(ALICE, chatroom_id, text(f"@{HELPER} 开始"))
    lineage.reply(HELPER, posted["deliveries"][0]["input_message_id"], f"交给 @{REVIEWER} 检查")
    chatroom_backend.list_messages(ALICE, chatroom_id)
    assert [call[1]["resident_id"] for call in lineage.deliveries()] == [HELPER]
    assert _texts(chatroom_id)[-1] == ("agent", HELPER, f"交给 @{REVIEWER} 检查")


def test_a_queued_intent_is_sent_once_by_the_next_reader(lineage, monkeypatch):
    """发送前中断的投递意图由下一次读取补发，且只发一次"""
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [])["chatroom_id"]
    deliver = chatroom_backend._deliver
    # The posting request stops after recording the intent, before sending it.
    monkeypatch.setattr(chatroom_backend, "_deliver", lambda *_args: None)
    posted = chatroom_backend.post_message(ALICE, chatroom_id, text(f"@{HELPER} 中断前的消息"))
    assert posted["deliveries"] == [] and lineage.deliveries() == []
    monkeypatch.setattr(chatroom_backend, "_deliver", deliver)
    chatroom_backend.list_messages(ALICE, chatroom_id)
    chatroom_backend.list_messages(ALICE, chatroom_id)
    assert len(lineage.deliveries()) == 1
    assert lineage.deliveries()[0][2] == ALICE


def test_the_last_member_leaving_deletes_the_chatroom(lineage):
    """最后一名成员退出时删除 Chatroom"""
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    assert chatroom_backend.leave_chatroom(ALICE, chatroom_id)["deleted"] is False
    assert chatroom_backend.list_chatrooms(ALICE) == []
    assert _texts(chatroom_id, BOB)[-1] == ("system", "portal", "alice 退出了聊天室")
    assert chatroom_backend.leave_chatroom(BOB, chatroom_id)["deleted"] is True
    with pytest.raises(KeyError):
        chatroom_backend.list_messages(BOB, chatroom_id)


def test_the_creator_deletes_the_chatroom_for_every_member(lineage):
    """删除 Chatroom 后所有成员和被 @ 的 Resident 都读不到它，迟到的回复不再写回"""
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    chatroom_backend.post_message(BOB, chatroom_id, text(f"请看 @{HELPER}"))
    assert chatroom_backend.delete_chatroom(DAVE, chatroom_id) == {"chatroom_id": chatroom_id, "deleted": True}
    assert chatroom_backend.list_chatrooms(ALICE) == chatroom_backend.list_chatrooms(BOB) == []
    assert lineage.published[-1] == (chatroom_id, [ALICE, BOB])
    for reader in (ALICE, BOB, HELPER):
        with pytest.raises(KeyError):
            chatroom_backend.list_messages(reader, chatroom_id)
    assert chatroom_backend._agent_data().read_chat_message_page(chatroom_id, limit=10)["messages"] == []
    # A Resident finishing its reply after the deletion brings nothing back.
    delivery = lineage.deliveries()[0][1]
    lineage.reply(HELPER, delivery["message"]["message_id"], "迟到的回复")
    assert chatroom_backend.handle_resident_terminal(
        business_ref=delivery["business_ref"], resident_id=HELPER, message_id=delivery["message"]["message_id"],
    ) == {}


def test_a_creator_who_left_can_no_longer_delete(lineage):
    """创建者退出 Chatroom 后仍可删除该 Chatroom"""
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    chatroom_backend.leave_chatroom(ALICE, chatroom_id)
    assert chatroom_backend.delete_chatroom(ALICE, chatroom_id)["deleted"]


def test_the_instructions_run_for_members_and_called_residents(lineage, monkeypatch):
    """Chatroom 的 ISA 指令供成员和被 @ 的 Resident 调用，同一幂等键重放返回原消息"""
    from management_portal import web_isa
    from tests.cases.isa_test_support import run

    # ListChatrooms names a registered User.
    monkeypatch.setattr(web_isa, "_access", lambda: SimpleNamespace(user=lambda name: {"configured": name in (ALICE, BOB, DAVE)}))

    created = run("CreateChatroom", {"members": [BOB], "title": "ISA"}, actor=ALICE).raise_for_error()
    chatroom_id = created["chatroom"]["chatroom_id"]
    arguments = {"chatroom_id": chatroom_id, "content_parts": text(f"大家好 @{HELPER}")}
    posted = run("CreateChatroomMessage", arguments, actor=ALICE, key="post-1").raise_for_error()
    replay = run("CreateChatroomMessage", arguments, actor=ALICE, key="post-1").raise_for_error()
    assert replay["message"]["message_id"] == posted["message"]["message_id"]
    assert len(lineage.deliveries()) == 1
    # The called Resident reads the whole history through the same instruction.
    history = run("ListChatroomMessages", {"chatroom_id": chatroom_id}, actor=HELPER, entry="harness").raise_for_error()
    assert chatroom_backend._message_text(history["messages"][-1]) == f"大家好 @{HELPER}"
    assert run("CreateChatroomMessage", {**arguments, "content_parts": text("可以")}, actor=HELPER, entry="harness").raise_for_error()["message"]["author_id"] == HELPER

    listed = run("ListChatrooms", {"user_id": BOB}, actor=BOB).raise_for_error()
    assert [(item["chatroom_id"], item["unread_count"]) for item in listed["chatrooms"]] == [(chatroom_id, 3)]
    # An author's edit compares the envelope's expected_version with the message version, when given.
    message = posted["message"]
    stale = run("UpdateChatroomMessage", {"chatroom_id": chatroom_id, "message_id": message["message_id"], "content": "改"},
                actor=ALICE, version="seg_stale")
    assert stale.error["code"] == "instruction.version_conflict"
    assert stale.error["details"]["current_version"] == message["version"]
    edited = run("UpdateChatroomMessage", {"chatroom_id": chatroom_id, "message_id": message["message_id"], "content": "改"},
                 actor=ALICE, version=message["version"]).raise_for_error()["message"]
    assert run("UpdateChatroomMessage", {"chatroom_id": chatroom_id, "message_id": message["message_id"],
                                         "content": f"大家好 @{HELPER}"}, actor=ALICE).raise_for_error()["message"]["version"] != edited["version"]
    assert run("UpdateChatroom", {"chatroom_id": chatroom_id, "add_members": [DAVE]}, actor=BOB).raise_for_error()["chatroom"]["members"] == [ALICE, BOB, DAVE]
    assert run("MarkChatroomRead", {"chatroom_id": chatroom_id}, actor=BOB).raise_for_error()["unread_count"] == 0
    assert run("UpdateChatroom", {"chatroom_id": chatroom_id, "muted": True}, actor=BOB).raise_for_error()["chatroom"]["muted"]
    run("MarkChatroomRead", {"chatroom_id": chatroom_id, "through": posted["message"]["ordinal"]}, actor=BOB).raise_for_error()
    # A quote names one message of this Chatroom; a replay returns the same message.
    quoting = {"chatroom_id": chatroom_id, "content_parts": text("同意"), "quote": {"message_id": posted["message"]["message_id"]}}
    quoted = run("CreateChatroomMessage", quoting, actor=BOB, key="post-2").raise_for_error()["message"]
    assert quoted["quote"]["author_id"] == ALICE
    assert run("CreateChatroomMessage", quoting, actor=BOB, key="post-2").raise_for_error()["message"]["message_id"] == quoted["message_id"]
    assert run("CreateChatroomMessage", {**quoting, "quote": {"message_id": "msg_missing"}}, actor=BOB, key="post-3").error["code"] == "instruction.invalid_arguments"
    assert run("GetChatroom", {"chatroom_id": chatroom_id}, actor=DAVE).raise_for_error()["chatroom"]["chatroom_id"] == chatroom_id
    assert run("GetChatroom", {"chatroom_id": chatroom_id}, actor=BOB).raise_for_error()["chatroom"]["permissions"]["can_delete"]
    assert run("LeaveChatroom", {"chatroom_id": chatroom_id}, actor=ALICE).raise_for_error() == {"chatroom_id": chatroom_id, "deleted": False}
    other = run("CreateChatroom", {"members": [BOB]}, actor=ALICE).raise_for_error()["chatroom"]["chatroom_id"]
    assert run("DeleteChatroom", {"chatroom_id": other}, actor=BOB).raise_for_error() == {"chatroom_id": other, "deleted": True}


def test_the_chatroom_id_resolves_to_a_summary_and_its_chatroom_route(lineage):
    """Chatroom ID 解析为摘要记录和 /lineage?chatroom= 页面地址"""
    from management_portal.resources.providers import chatroom as provider

    registered = {}
    provider.install_resolver(SimpleNamespace(
        register_resolver=lambda kind, resolve, read_content: registered.update(kind=kind, resolve=resolve),
    ))
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    resolved = registered["resolve"](chatroom_id, actor=ALICE)
    assert registered["kind"] == "chatroom"
    assert resolved["portal_url"] == f"/lineage?chatroom={chatroom_id}"
    assert resolved["record"]["resource_id"] == chatroom_id
    assert resolved["record"]["title"] == "alice、bob"
    assert resolved["record"]["members"] == [ALICE, BOB]
    with pytest.raises(KeyError):
        registered["resolve"]("chatroom_ffffffffffff", actor=ALICE)


def _inbox(monkeypatch, actor: str) -> list[dict]:
    """The viewer's Inbox with no Resident conversations or Notice Board mentions."""

    from management_portal import notice_board_backend
    from management_portal import serve_management_portal as portal
    from management_portal import web_inbox

    monkeypatch.setattr(web_inbox.agent_conversation_index_backend, "build_entries_page", lambda *_a, **_k: {
        "entries": [], "next_page_token": "", "page_size": 50, "paged": True,
    })
    monkeypatch.setattr(web_inbox.agent_conversation_index_backend, "build_entries", lambda *_a, **_k: [])
    monkeypatch.setattr(notice_board_backend.notice_board, "mention_events", lambda _actor: [])
    return web_inbox._agent_conversation_payload(actor)["entries"]


def test_a_chatroom_is_an_inbox_conversation_with_its_own_unread(lineage, monkeypatch):
    """聊天室是 Inbox 中的一条对话：未读取自聊天室，读聊天室、Inbox 双击和清除未读都同步

    :module: resident.conversation
    :importance: P1
    :design: decision_a6bebf4ac283, decision_15f7df737ed5
    """
    from management_portal import serve_management_portal as portal
    from management_portal import web_inbox

    quiet = chatroom_backend.create_chatroom(DAVE, [])["chatroom_id"]
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB], "周会")["chatroom_id"]
    chatroom_backend.post_message(BOB, chatroom_id, text("明天  十点开会"))
    [entry] = _inbox(monkeypatch, ALICE)
    assert {key: entry[key] for key in ("key", "kind", "title", "subtitle", "surface", "url", "unread", "unread_count")} == {
        "key": f"chatroom:{chatroom_id}", "kind": "chatroom", "title": "周会", "subtitle": "bob：明天 十点开会",
        "surface": "lineage", "url": f"/lineage?chatroom={chatroom_id}", "unread": True, "unread_count": 1,
    }
    assert [item["unread"] for item in _inbox(monkeypatch, BOB)] == [False]
    assert [item["key"] for item in _inbox(monkeypatch, DAVE)] == [f"chatroom:{quiet}"]

    # Reading the Chatroom reads its Inbox entry; the Inbox read toggle moves the Chatroom's position.
    chatroom_backend.mark_read(ALICE, chatroom_id)
    assert lineage.inboxes[-1] == [ALICE]
    assert _inbox(monkeypatch, ALICE)[0]["unread"] is False
    result = web_inbox._set_agent_conversation_read_state(ALICE, entry["key"], unread=True)
    assert result["unread"] is True
    assert chatroom_backend.get_chatroom(ALICE, chatroom_id)["unread_count"] == 1
    web_inbox._set_agent_conversation_read_state(ALICE, entry["key"], unread=False)
    assert chatroom_backend.get_chatroom(ALICE, chatroom_id)["unread_count"] == 0

    # A new message brings the entry back up as unread; clearing unread reads the Chatroom.
    chatroom_backend.post_message(BOB, chatroom_id, text("改到十一点"))
    chatroom_backend.post_message(BOB, chatroom_id, text("别迟到"))
    [entry] = _inbox(monkeypatch, ALICE)
    assert (entry["subtitle"], entry["unread"], entry["unread_count"]) == ("bob：别迟到", True, 2)

    def clear_unread():
        return web_inbox._clear_agent_conversations(ALICE, mode="unread", entries=chatroom_backend.inbox_entries(ALICE))

    result = clear_unread()
    assert (result["clear_mode"], result["cleared_count"]) == ("unread", 1)
    assert chatroom_backend.get_chatroom(ALICE, chatroom_id)["unread_count"] == 0
    chatroom_backend.update_chatroom(ALICE, chatroom_id, muted=True)
    chatroom_backend.post_message(BOB, chatroom_id, text("普通更新"))
    muted = web_inbox._agent_conversation_payload(ALICE)
    assert muted["unread_count"] == 0 and muted["entries"][0]["unread_count"] == 1
    assert muted["entries"][0]["muted"] and not muted["entries"][0]["attention_unread"]
    chatroom_backend.post_message(BOB, chatroom_id, text("@alice 请确认"))
    pinged = web_inbox._agent_conversation_payload(ALICE)
    assert pinged["unread_count"] == 1 and pinged["entries"][0]["unread_mention_count"] == 1
    result = clear_unread()
    assert result["cleared_count"] == 1 and chatroom_backend.get_chatroom(ALICE, chatroom_id)["unread_count"] == 0
    chatroom_backend.post_message(BOB, chatroom_id, text("仅灰色未读"))
    result = clear_unread()
    assert result["cleared_count"] == 1
    web_inbox._set_agent_conversation_read_state(DAVE, entry["key"], unread=True)


def test_clearing_all_hides_a_chatroom_until_its_next_message(lineage, monkeypatch):
    """Inbox 清除全部后聊天室条目隐藏，直到出现新消息

    :module: resident.conversation
    :design: decision_a6bebf4ac283, decision_15f7df737ed5
    """
    from management_portal import serve_management_portal as portal
    from management_portal import web_inbox

    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    chatroom_backend.post_message(BOB, chatroom_id, text("在吗"))
    entries = _inbox(monkeypatch, ALICE)
    web_inbox._clear_agent_conversations(ALICE, mode="all", entries=entries)
    assert _inbox(monkeypatch, ALICE) == []
    assert chatroom_backend.get_chatroom(ALICE, chatroom_id)["unread_count"] == 0
    chatroom_backend.post_message(BOB, chatroom_id, text("在吗？"))
    assert [(item["key"], item["unread"]) for item in _inbox(monkeypatch, ALICE)] == [(f"chatroom:{chatroom_id}", True)]


def test_a_members_read_state_lives_in_their_mailbox_row(lineage):
    """聊天室成员的已读位置、免打扰和 @ 保存在各自的 Mailbox 行中

    别人发言推进成员行的提醒和修订号，自己发言读到这条消息；阅读、免打扰、@ 都写入同一行并推进修订号，
    聊天室返回的修订号就是这一行的；退出删除自己的行，删除聊天室删除全部成员的行。

    :module: resident.conversation
    :design: decision_a6bebf4ac283, decision_15f7df737ed5
    """
    from management_portal import conversation_read_state as reads

    def row(actor):
        return reads.row(reads.load(actor), f"chatroom:{chatroom_id}")

    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB, DAVE])["chatroom_id"]
    created = row(ALICE)
    chatroom_backend.post_message(BOB, chatroom_id, text("@alice 看一下"))
    assert row(ALICE)["unread"] and row(ALICE)["read_revision"] > created["read_revision"]
    assert not row(BOB)["unread"]
    room = chatroom_backend.get_chatroom(ALICE, chatroom_id)
    assert (room["unread_count"], room["unread_mention_count"], room["read_revision"]) == (
        1, 1, row(ALICE)["read_revision"])
    muted = chatroom_backend.update_chatroom(ALICE, chatroom_id, muted=True)
    assert muted["muted"] and muted["read_revision"] == row(ALICE)["read_revision"]
    read = chatroom_backend.mark_read(ALICE, chatroom_id)
    assert read["unread_count"] == read["unread_mention_count"] == 0 and not row(ALICE)["unread"]
    assert read["read_revision"] == row(ALICE)["read_revision"] > muted["read_revision"]
    assert chatroom_backend.get_chatroom(ALICE, chatroom_id)["muted"]
    chatroom_backend.leave_chatroom(DAVE, chatroom_id)
    assert row(DAVE)["read_revision"] == 0
    chatroom_backend.delete_chatroom(ALICE, chatroom_id)
    assert row(ALICE)["read_revision"] == row(BOB)["read_revision"] == 0


def test_the_mailbox_read_toggle_checks_membership_and_the_seen_revision_in_the_room(lineage):
    """Mailbox 标记聊天室已读时直接检查成员资格，并在房间锁内比较看到的修订号

    成员的聊天室在自己的 Mailbox 中，非成员、已删除或格式不对的房间不在；带旧修订号标记已读被拒绝，
    这一行不变，新消息保持未读；带当前修订号才读到最新消息。

    :module: resident.conversation
    :design: decision_a6bebf4ac283, decision_15f7df737ed5
    """
    from management_portal.conversation_receipt_store import VersionConflict

    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    assert chatroom_backend.in_mailbox(chatroom_id, ALICE) and chatroom_backend.in_mailbox(chatroom_id, BOB)
    assert not chatroom_backend.in_mailbox(chatroom_id, DAVE)
    assert not chatroom_backend.in_mailbox("not-a-room", ALICE)
    seen = chatroom_backend.get_chatroom(ALICE, chatroom_id)["read_revision"]
    chatroom_backend.post_message(BOB, chatroom_id, text("新消息"))
    with pytest.raises(VersionConflict):
        chatroom_backend.set_unread(ALICE, chatroom_id, False, expected_revision=seen)
    room = chatroom_backend.get_chatroom(ALICE, chatroom_id)
    assert room["unread_count"] == 1 and room["read_revision"] > seen
    read = chatroom_backend.set_unread(ALICE, chatroom_id, False, expected_revision=room["read_revision"])
    assert read["unread_count"] == 0 and read["read_revision"] > room["read_revision"]
    chatroom_backend.delete_chatroom(ALICE, chatroom_id)
    assert not chatroom_backend.in_mailbox(chatroom_id, ALICE)


def test_a_chatroom_change_wakes_its_members_chatroom_view_and_inbox(lineage, monkeypatch):
    """聊天室变化同时唤醒成员的聊天室视图和 Inbox

    :module: resident.conversation
    :design: decision_a6bebf4ac283, decision_15f7df737ed5
    """
    import management_portal.notifications as notifications
    from management_portal.notifications import business_projection_subject

    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(notifications, "get_notification_publisher", lambda: SimpleNamespace(
        publish=lambda subject, payload: sent.append((subject, payload)),
    ))
    room = {"chatroom_id": "chatroom_aaaaaaaaaaaa", "members": ["CASE-A", BOB]}
    REAL_PUBLISH(room, fields=["messages"])
    assert lineage.inboxes == [["CASE-A", BOB]]
    REAL_PUBLISH_INBOX(["CASE-A", BOB])
    assert [subject for subject, _payload in sent] == [
        business_projection_subject("chatroom", "chatroom_aaaaaaaaaaaa"),
        business_projection_subject("agent-conversation-index", "current"),
    ]
    assert sent[-1][1]["actors"] == ["bob", "case-a"]


def test_each_called_resident_shows_its_progress_until_it_replies(lineage, monkeypatch):
    """GetChatroom 列出每个还没回复的被召唤 Resident 的进度：以发言者身份读取排队位置或启动阶段与实时活动；完成的立即消失，尚未投递的显示投递中

    Under a message that called Residents, each shows its startup and live work, as a Resident Chat does (CASE-A 10-07).

    :module: resident.conversation
    :importance: P1
    :design: decision_a6bebf4ac283, decision_44cf587c1721
    """

    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    posted = chatroom_backend.post_message(BOB, chatroom_id, text(f"@{HELPER} @{REVIEWER} 一起看看"))
    trigger = posted["message"]["message_id"]
    helper, reviewer = posted["deliveries"]
    # Being handed the message wakes only the Chatroom view, not the Inbox.
    assert lineage.progress_wakes == [chatroom_id, chatroom_id]
    lineage.inputs[(HELPER, helper["input_message_id"])] = {"state": "queued", "progress": {"ahead": 1}}
    activity = {"activity_id": "act_1", "message_id": reviewer["input_message_id"], "kind": "tool", "label": "Read SPEC.md"}
    lineage.inputs[(REVIEWER, reviewer["input_message_id"])] = {"state": "running", "progress": {
        "work_state": "running", "work_phase": "", "activities": [activity],
    }}

    assert chatroom_backend.get_chatroom(ALICE, chatroom_id)["active_deliveries"] == [
        {"id": helper["id"], "resident_id": HELPER, "trigger_message_id": trigger, "state": "queued", "ahead": 1},
        {"id": reviewer["id"], "resident_id": REVIEWER, "trigger_message_id": trigger, "state": "running",
         "work_state": "running", "work_phase": "", "activities": [activity]},
    ]
    # Progress is read like the reply: as the member who called the Resident.
    reads = [call for call in lineage.calls if call[0] == "GetInputState"]
    assert {(call[1]["resident_id"], call[1]["include_progress"], call[2]) for call in reads} == {(HELPER, True, BOB), (REVIEWER, True, BOB)}

    # A finished Resident drops out at once; its reply comes back with the next read.
    lineage.reply(HELPER, helper["input_message_id"], "看完了")
    assert [item["resident_id"] for item in chatroom_backend.get_chatroom(ALICE, chatroom_id)["active_deliveries"]] == [REVIEWER]

    # A message not yet handed over shows that it is being handed over.
    monkeypatch.setattr(chatroom_backend, "_deliver", lambda *_args: None)
    waiting = chatroom_backend.post_message(ALICE, chatroom_id, text(f"@{HELPER} 再看一处"))
    assert chatroom_backend._active_deliveries(chatroom_backend._store().get(chatroom_id))[-1] == {
        "id": chatroom_backend._store().get(chatroom_id)["deliveries"][-1]["id"], "resident_id": HELPER,
        "trigger_message_id": waiting["message"]["message_id"], "state": "delivering",
    }


def test_a_called_residents_progress_wakes_only_its_chatroom_view(lineage):
    """被召唤 Resident 的进度变化只唤醒对应聊天室视图、不唤醒 Inbox；别的 Resident、消息、业务来源和已回复的投递不唤醒

    :module: resident.conversation
    :importance: P1
    :design: decision_a6bebf4ac283
    """
    chatroom_id = chatroom_backend.create_chatroom(ALICE, [BOB])["chatroom_id"]
    posted = chatroom_backend.post_message(ALICE, chatroom_id, text(f"@{HELPER} 开始"))
    input_message_id = posted["deliveries"][0]["input_message_id"]
    business_ref = f"{chatroom_id}/{posted['message']['message_id']}"
    lineage.progress_wakes.clear()
    inboxes = list(lineage.inboxes)

    assert chatroom_backend.handle_resident_progress(
        business_ref=business_ref, resident_id=HELPER, message_id=input_message_id,
    ) == {"chatroom_id": chatroom_id}
    assert lineage.progress_wakes == [chatroom_id] and lineage.inboxes == inboxes
    for ref, resident_id, message_id in [
        (business_ref, REVIEWER, input_message_id),
        (business_ref, HELPER, "msg_00000000000000000000000000000000"),
        ("graph_0123456789ab", HELPER, input_message_id),
    ]:
        assert chatroom_backend.handle_resident_progress(business_ref=ref, resident_id=resident_id, message_id=message_id) == {}
    lineage.reply(HELPER, input_message_id, "完成")
    chatroom_backend.list_messages(ALICE, chatroom_id)
    assert chatroom_backend.handle_resident_progress(
        business_ref=business_ref, resident_id=HELPER, message_id=input_message_id,
    ) == {}
    assert lineage.progress_wakes == [chatroom_id]


def test_the_society_service_tells_a_chatroom_when_a_called_residents_input_moves(monkeypatch):
    """SocietyService 在 business_ref 指向聊天室的输入出现活动或队列移动时通知聊天室；每条消息的 business_ref 只读一次，通知失败不影响事件发布

    :module: resident.conversation
    :importance: P1
    :aspects: functional, performance
    :design: decision_a6bebf4ac283
    """
    from management_portal.society_service import resident_conversation_event_relay as relay_module
    from management_portal.society_service import resident_conversation_stream as stream

    refs = {(HELPER, "msg_called"): "chatroom_aaaaaaaaaaaa/msg_1", (HELPER, "msg_direct"): ""}
    reads: list[tuple[str, str]] = []

    class Repository:
        def message_business_ref(self, resident_id, message_id):
            reads.append((resident_id, message_id))
            return refs.get((resident_id, message_id), "")

        def message(self, resident_id, message_id):
            return {"message_id": message_id} if (resident_id, message_id) in refs else None

    woken: list[dict] = []
    monkeypatch.setattr(stream, "get_repository", lambda: Repository())
    monkeypatch.setattr(chatroom_backend, "handle_resident_progress", lambda **kw: woken.append(kw) or {})
    relay = relay_module.ResidentConversationEventRelay(object(), SimpleNamespace(publish=lambda *_: None))

    def event(event_type, payload):
        return {"event_type": event_type, "resident_id": HELPER, "payload": payload}

    activity = event(resident_conversation_records.ACTIVITY_CHANGED, {"activities": [{"activity_id": "act_1", "message_id": "msg_called"}]})
    relay._publish_chatroom_progress(activity)
    relay._publish_chatroom_progress(event(resident_conversation_records.QUEUE_CHANGED, {"queue": {"items": [
        {"message_id": "msg_direct"}, {"message_id": "msg_called"},
    ]}}))
    relay._publish_chatroom_progress(event(resident_conversation_records.STATE_CHANGED, {"state": {"active_message_id": "msg_direct"}}))
    assert woken == [{"business_ref": "chatroom_aaaaaaaaaaaa/msg_1", "resident_id": HELPER, "message_id": "msg_called"}] * 2
    # A message's business_ref never changes, so each is read once.
    assert sorted(reads) == [(HELPER, "msg_called"), (HELPER, "msg_direct")]

    monkeypatch.setattr(chatroom_backend, "handle_resident_progress", lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    relay._publish_chatroom_progress(activity)


def test_get_input_state_includes_the_progress_on_request(input_delivery):
    """GetInputState 的 include_progress 返回运行中消息的启动阶段与活动、排队消息前面还有几条；不请求时不返回进度

    :module: resident.conversation
    :importance: P1
    :design: decision_2a3ff353165c, decision_a6bebf4ac283
    """
    from management_portal.society_service import resident_domain, resident_protocol
    from tests.cases.resident_delivery_test_support import published_delivery

    resident_id = input_delivery["resident_id"]
    first, second, third = (
        published_delivery(resident_domain.deliver_resident_message_content(resident_id, "CASE-A", text))
        for text in ("First", "Second", "Third")
    )
    assert "progress" not in resident_protocol.get_resident_input_state(resident_id, "CASE-A", third["message_id"])
    progress = {
        item["message_id"]: resident_protocol.get_resident_input_state(resident_id, "CASE-A", item["message_id"], include_progress=True)
        for item in (first, second, third)
    }
    # The running one shows how it is starting; the waiting ones their place behind it.
    assert progress[first["message_id"]]["progress"] == {"work_state": "queued", "work_phase": "starting_session", "activities": []}
    assert [progress[item["message_id"]]["progress"] for item in (second, third)] == [{"ahead": 1}, {"ahead": 2}]


def test_search_residents_lists_visible_residents_and_whether_the_caller_can_send_to_each(monkeypatch):
    """SearchResidents 只列出调用者看得到、已登记完成的 Resident，按关键词或最近工作排序，并说明能否给它发消息及原因；resident_ids 保持给出的顺序

    The Chatroom's @ list (sample_admin 10-07): called Residents first, then the ones the member can send to.

    :module: resident.conversation
    :importance: P1
    :design: decision_d19b9909a674, decision_a6bebf4ac283
    """

    from management_portal.society_service import (
        resident_access, resident_catalog, resident_protocol,
    )
    from management_portal.society_service.isa.residents import HANDLERS

    def record(suffix, title, at="", **extra):
        return {"resident_id": f"resident_00000000000{suffix}", "title": title, "lineage_id": "lineage_000000000001",
                "lineage_title": "Portal", "last_turn_usage_at": at, "registration_state": "ready", **extra}

    records = [
        record("1", "Chatroom", "2026-10-07T01:00:00Z"),
        record("2", "Chat Workspace", "2026-10-07T03:00:00Z"),
        record("3", "Hidden chat", "2026-10-07T04:00:00Z"),
        record("4", "Reader", "2026-10-07T02:00:00Z"),
        record("5", "Deleted", "2026-10-06T00:00:00Z", deleted_at="2026-10-08T00:00:00Z"),
        record("6", "Pending", "2026-10-07T05:00:00Z", registration_state="resource_pending"),
    ]
    hidden, reader, deleted = (f"resident_00000000000{suffix}" for suffix in "345")
    reads = []

    def all_records():
        reads.append("all")
        return [dict(item) for item in records]

    def selected_records(ids):
        reads.append(list(ids))
        return [dict(item) for item in records if item["resident_id"] in ids]

    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: SimpleNamespace(all=all_records, get_many=selected_records))
    monkeypatch.setattr(resident_access, "can_manage_resident", lambda item, actor, operation="manage": (
        actor == ALICE and item["resident_id"] != hidden and (operation == "read" or item["resident_id"] != reader)
    ))

    def search(**kwargs):
        return [(item["resident_id"][-1], item["can_send"], item["disabled_reason"])
                for item in resident_protocol.search_residents(ALICE, **kwargs)["residents"]]

    # Without a keyword the most recently working come first; hidden and unregistered ones never show.
    assert search() == [
        ("2", True, ""), ("4", False, "没有给这个 Resident 发消息的权限。"), ("1", True, ""),
    ]
    assert search(can_send=True) == [("2", True, ""), ("1", True, "")]
    assert [item[0] for item in search(q="@chat")] == ["2", "1"]
    assert [item[0] for item in search(q="000000000001")] == ["1"]
    assert [item[0] for item in search(q="portal", limit=1)] == ["2"]
    # Given Residents keep their order.
    reads.clear()
    assert [item[0] for item in search(resident_ids=[reader, "resident_000000000001", hidden, "resident_0000000000ff"])] == ["4", "1"]
    assert reads == [[reader, "resident_000000000001", hidden, "resident_0000000000ff"]]
    assert resident_protocol.search_residents(BOB)["residents"] == []
    assert "SearchResidents" in HANDLERS
