"""Chatroom instruction declarations (decision_a6bebf4ac283), executed by Portal Web.

Chatrooms are public-discussion threads shared by Users, each keeping their
own read position, that can call any Resident with @.  This is the
Chatroom's part of the instruction catalog (``isa.instructions`` in
``portal_features.py``); every service reads it, Portal Web runs the
handlers in ``chatroom_web_isa.py``.  A Portal without Chatrooms has none of
these instructions.
"""
from __future__ import annotations

from management_portal.hatg.declarations import CURSOR, FIXED, NEXT_CURSOR, ON_USER, InstructionDeclaration as D, On, P, R

CATEGORIES = {"discussion": "公共讨论"}


def _d(name, decides, description, parameters=(), result=(), *, mutates=False, target=""):
    return D(name=name, category="discussion", owner="web", description=description,
             parameters=tuple(parameters), result=tuple(result), mutates=mutates, target=target,
             decides=decides)


ON_CHATROOM = (On("chatroom_id", "chatroom"),)


CHATROOM = R("chatroom", "object", "聊天室记录：chatroom_id、title、members、called_residents、last_message、未读状态、"
             "permissions；GetChatroom 另有 active_deliveries")

DECLARATIONS = (
    _d("ListChatrooms", ON_USER, "列出一位 User 所在的聊天室，按最近活动排序并给出未读数。", (
        R("user_id", description="User 的用户名；列出谁的聊天室由权限系统判定"),
    ), (R("chatrooms", "array", "聊天室记录：chatroom_id、title、members、last_message、unread_count…"),), target="user_id"),
    _d("GetChatroom", ON_CHATROOM, "读取一个聊天室的名称、成员、最后一条消息、调用者可执行的操作，以及还没回复的 Resident 召唤的进度（chatroom.active_deliveries：Resident、触发消息、状态与排队位置或实时活动）。", (R("chatroom_id", description="聊天室 ID"),), (
        CHATROOM,
    ), target="chatroom_id"),
    _d("CreateChatroom", FIXED, "创建一个聊天室，调用者和所选用户成为成员。", (
        P("members", "array", "其他成员的用户名", items={"type": "string"}), P("title", description="聊天室名称", max_length=80),
    ), (CHATROOM,), mutates=True),
    _d("UpdateChatroom", ON_CHATROOM, "修改聊天室名称、加入新的用户成员，或设置调用者自己的消息免打扰。", (
        R("chatroom_id", description="聊天室 ID"), P("title", description="聊天室名称", max_length=80),
        P("add_members", "array", "要加入的用户名", items={"type": "string"}),
        P("muted", "boolean", "仅设置调用者自己的消息免打扰；直接 @ 自己的消息仍提醒"),
    ), (CHATROOM,), mutates=True, target="chatroom_id"),
    _d("LeaveChatroom", FIXED, "调用者退出聊天室；最后一位成员退出后聊天室被删除。", (R("chatroom_id", description="聊天室 ID"),), (
        R("chatroom_id", description="聊天室 ID"), R("deleted", "boolean", "调用者是最后一位成员、聊天室随之删除时为 true"),
    ), mutates=True, target="chatroom_id"),
    _d("DeleteChatroom", ON_CHATROOM, "删除聊天室：所有成员都不再看到它，消息随之归档；成员与创建者身份不限制此操作。", (R("chatroom_id", description="聊天室 ID"),), (
        R("chatroom_id", description="聊天室 ID"), R("deleted", "boolean", "恒为 true：聊天室已删除"),
    ), mutates=True, target="chatroom_id"),
    _d("ListChatroomMessages", ON_CHATROOM, "按顺序读取聊天室消息：默认最新一页，可向前翻页或读取某个位置之后的新消息。", (
        R("chatroom_id", description="聊天室 ID"),
        CURSOR("上一页返回的 next_cursor，原样交回，读更早的一页；不填时读最新一页"),
        P("after", "integer", "只读取这个位置之后的新消息，不与 cursor 同用", minimum=0),
        P("limit", "integer", "默认 100", minimum=1, maximum=200),
        P("change_cursor", description="上次读取的消息变更游标；返回该游标之后的编辑、撤回和回应"),
    ), (R("messages", "array", "本页消息，从旧到新：message_id、role、author_id、content_parts、ordinal、version、reactions…"),
        NEXT_CURSOR("还有更早的消息时返回"),
        R("updated_messages", "array", "change_cursor 之后被编辑、撤回或改了回应的消息；未给 change_cursor 时为空"),
        R("change_cursor", description="最新的消息变更游标，下次读取时交回")),
       target="chatroom_id"),
    _d("UpdateChatroomMessage", FIXED, "作者编辑自己的消息正文，保留附件与不可变历史版本；不重新触发提及或 Resident 投递。"
       "调用信封的 expected_version 写读取消息返回的 version，防止覆盖并发修改。", (
        R("chatroom_id", description="聊天室 ID"), R("message_id", description="聊天室中的消息 ID"),
        R("content", description="新的正文，替换原有文字", max_length=8000),
    ), (R("message", "object", "编辑后的消息：message_id、content_parts、edited_at、version…"),), mutates=True, target="message_id"),
    _d("RetractChatroomMessage", FIXED, "作者撤回自己的消息，保留位置和撤回标记；不取消已经交给 Resident 的工作。"
       "调用信封的 expected_version 写读取消息返回的 version。", (
        R("chatroom_id", description="聊天室 ID"), R("message_id", description="聊天室中的消息 ID"),
    ), (R("message", "object", "撤回后的消息：message_id、retracted_at、version…"),), mutates=True, target="message_id"),
    _d("UpdateChatroomReaction", ON_CHATROOM, "设置或取消调用者对消息的一种表情回应。", (
        R("chatroom_id", description="聊天室 ID"), R("message_id", description="聊天室中的消息 ID"), R("emoji", description="回应的表情", enum=("👍", "❤️", "😂", "🎉", "👀", "✅")),
        R("active", "boolean", "true 设置这个回应，false 取消"),
    ), (R("message", "object", "更新后的消息，reactions 每项含 emoji、users、count、active"),), mutates=True, target="message_id"),
    _d("CreateChatroomMessage", ON_CHATROOM, "在聊天室中发言；正文中 @resident_<ID> 的 Resident 会收到这条消息。", (
        R("chatroom_id", description="聊天室 ID"),
        R("content_parts", "array", "文字（text）与上传到本聊天室的附件（image、file 加 upload_id）",
          items={"type": "object"}),
        P("quote", "object", "引用本聊天室中的一条消息，只给出 {message_id}；作者和摘录由平台填写",
          properties={"message_id": {"type": "string"}}),
    ), (R("message", "object", "新消息：message_id、author_id、content_parts、ordinal、version…"),
        R("deliveries", "array", "@ 到的 Resident 的投递：id、resident_id、input_message_id、status（delivered、undeliverable…）、error")),
       mutates=True, target="chatroom_id"),
    _d("MarkChatroomRead", ON_CHATROOM, "把聊天室标为调用者已读到指定位置；省略位置时读到最新消息。", (
        R("chatroom_id", description="聊天室 ID"), P("through", "integer", "浏览器实际展示到的消息位置", minimum=0),
    ), (
        R("chatroom_id", description="聊天室 ID"), R("unread_count", "integer", "调用者剩余的未读消息数"),
        P("muted", "boolean", "调用者是否设置了免打扰"),
        P("attention_unread", "boolean", "有需要提醒的未读：有未读且未免打扰，或有未读的 @"),
        P("unread_mentions", "array", "未读的 @ 调用者的消息：message_id、ordinal"),
        P("unread_mention_count", "integer", "未读的 @ 调用者的消息数"),
    ), mutates=True, target="chatroom_id"),
)
