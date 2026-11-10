"""Telepus bridge: Telegram ↔ personal assistant facade and exact routing (decision_66f5be947b67)."""
from __future__ import annotations

import pytest

from management_portal.telepus.router_core import BOT_COMMANDS, HELP_TEXT, RouterCore, conversation_rounds
from management_portal.telepus.router_store import RouterStore
from management_portal.telepus.telegram_adapter import TelegramAdapter
from management_portal.telepus.telegram_format import markdown_to_telegram_html, split_telegram_text
from tests.cases.telepus_bridge_support import (  # noqa: F401
    ACTOR, EXACT, FACADE, FakePortal, bind_contact, telepus_schema,
)


class Chat:
    """One Telegram private chat bound to ``alice``."""

    def __init__(self, schema: str) -> None:
        self.store = RouterStore(schema)
        self.portal = FakePortal()
        self.core = RouterCore(store=self.store, portal=self.portal)
        bind_contact(self.store, "7", ACTOR)
        self.telegram_id = 100

    def send(self, text, *, reply_to=0):
        self.telegram_id += 1
        return self.core.dispatch_message(chat_id="c1", telegram_user_id="7", text=text, portal_actor=ACTOR,
                                          telegram_message_id=self.telegram_id, reply_to_telegram_message_id=reply_to)

    def click(self, button, *, telegram_message_id=0):
        return self.core.dispatch_callback(chat_id="c1", telegram_user_id="7", data=button["callback_data"],
                                           portal_actor=ACTOR, telegram_message_id=telegram_message_id)

    def refresh(self, force=True):
        return self.core.refresh_chat(self.store.get_chat_state("c1"), force=force)


def texts(response):
    return [item["text"] for item in response["messages"]]


@pytest.fixture
def chat(telepus_schema):
    return Chat(telepus_schema)


def test_plain_message_goes_to_the_facade_and_member_replies_come_back_once(chat):
    """Telepus 普通消息交给个人助手门面，成员回复带成员标题且只推送一次"""
    assert texts(chat.send("帮我总结一下")) == ["已收到。"]
    resident_id, policy, message, _ = chat.portal.delivered[0]
    assert (resident_id, policy) == (FACADE, "enqueue_and_trigger")
    assert message["content_parts"] == [{"type": "text", "text": "帮我总结一下"}]
    # The same Telegram message always gets the same Portal message ID.
    assert message["message_id"] == RouterCore._message_id("c1", chat.telegram_id)
    assert chat.refresh() == []
    chat.portal.answer(FACADE, message["message_id"], "reply_1", "总结如下", member="写作助手")
    outgoing = chat.refresh(force=False)
    assert [item["text"] for item in outgoing] == ["**写作助手**\n总结如下"]
    assert outgoing[0]["link"] == {"resident_id": FACADE, "message_id": "reply_1"}
    assert outgoing[0]["reply_to_telegram_message_id"] == chat.telegram_id
    assert chat.refresh() == []
    assert chat.store.open_inputs("c1") == []


def test_reply_projection_lag_keeps_input_open_until_all_replies_are_readable(chat):
    chat.send('reply arrives in a later projection')
    request = chat.portal.delivered[-1][2]['message_id']
    chat.portal.answer(FACADE, request, 'reply_1', 'first reply')
    chat.portal.states[(FACADE, request)]['reply_message_ids'] = ['reply_1', 'reply_2']
    assert chat.refresh() == []
    assert chat.store.input(request)['open']
    chat.portal.answer(FACADE, request, 'reply_2', 'second reply')
    chat.portal.states[(FACADE, request)]['reply_message_ids'] = ['reply_1', 'reply_2']
    assert [item['text'] for item in chat.refresh()] == ['first reply', 'second reply']
    assert not chat.store.input(request)['open']
    assert chat.refresh() == []


@pytest.mark.parametrize('work_state,label', [('failed', '处理失败'), ('cancelled', '已停止')])
def test_delivered_input_reports_execution_failure_or_cancellation(chat, work_state, label):
    chat.send('delivered but execution did not complete')
    request = chat.portal.delivered[-1][2]['message_id']
    chat.portal.states[(FACADE, request)] = {
        'state': 'completed', 'reply_message_ids': [], 'work': {'state': work_state},
    }
    assert label in chat.refresh()[0]['text']
    assert not chat.store.input(request)['open']


def test_completed_work_without_a_reply_closes_input(chat):
    chat.send('no answer needed')
    request = chat.portal.delivered[-1][2]['message_id']
    chat.portal.states[(FACADE, request)] = {
        'state': 'completed', 'reply_message_ids': [], 'work': {'state': 'completed'},
    }
    assert chat.refresh() == []
    assert not chat.store.input(request)['open']


def test_a_telegram_reply_quotes_the_portal_message_it_shows(chat):
    """Telepus 在 Telegram 中回复一条消息时，投递带上它对应 Portal 消息的引用"""
    chat.send("第一个问题")
    first = chat.portal.delivered[0][2]["message_id"]
    chat.portal.answer(FACADE, first, "reply_1", "答复", member="写作助手")
    chat.store.link_message("c1", 555, FACADE, "reply_1")
    chat.send("接着这个说", reply_to=555)
    resident_id, _policy, message, _ = chat.portal.delivered[-1]
    assert resident_id == FACADE
    assert message["quote"] == {"message_id": "reply_1"}


def test_exact_route_by_id_and_home(chat):
    """Telepus 发送 Resident ID 切到精准路由，/home 回到个人助手"""
    assert "Portal Maintainer" in texts(chat.send(EXACT))[0]
    assert texts(chat.send("看下日志")) == ["已发送到 Portal Maintainer。"]
    assert chat.portal.delivered[-1][0] == EXACT
    chat.send("/home")
    chat.send("回到助手")
    assert chat.portal.delivered[-1][0] == FACADE
    assert "/route <resident_id>" in texts(chat.send("/route resident_bad"))[0]


def test_a_removed_exact_target_falls_back_to_the_assistant(chat):
    """Telepus 精准路由目标不存在时提示并回到个人助手模式"""
    chat.send(f"/route {EXACT}")
    del chat.portal.residents[EXACT]
    assert "已回到个人助手" in texts(chat.send("还在吗"))[0]
    assert chat.store.get_chat_state("c1")["mode"] == "assistant"


def test_assistant_not_ready(chat):
    """Telepus 个人助手尚未就绪时说明情况，不投递消息"""
    chat.portal.assistant = {}
    assert "个人助手尚未就绪" in texts(chat.send("你好"))[0]
    assert chat.portal.delivered == []


def test_help_explains_usage_and_the_menu_lists_every_command(chat):
    """Telepus /help 给出完整使用说明，命令菜单覆盖说明中的每个命令"""
    response = texts(chat.send("/help"))
    assert response[0] == HELP_TEXT
    for topic in ("发消息", "引用", "问答卡片", "网页上的对话", "命令"):
        assert f"**{topic}**" in HELP_TEXT
    listed = {line.split()[0][1:] for line in HELP_TEXT.splitlines() if line.startswith("/")}
    assert listed == {name for name, _ in BOT_COMMANDS}
    assert texts(chat.send("/start"))[0] == HELP_TEXT
    assert "/help" in texts(chat.send("/unknown"))[0]


def test_solo_notice_once_per_transition_and_exact_route_is_unaffected(chat, monkeypatch):
    policy = {'solo_member_id': EXACT, 'solo_notice_token': 'first'}
    monkeypatch.setattr(chat.portal, 'family_chat_preferences', lambda *a: policy)
    assert '网页上你对 Portal Maintainer 开了 Solo' in texts(chat.send('phone request'))[0]
    assert texts(chat.send('another request')) == ['已收到。']
    policy['solo_notice_token'] = 'reopened'
    assert 'Solo' in texts(chat.send('next request'))[0]
    chat.send('/route ' + EXACT)
    monkeypatch.setattr(chat.portal, 'family_chat_preferences', lambda *a: pytest.fail('exact route read Solo'))
    assert '已发送到' in texts(chat.send('exact request'))[0]


@pytest.mark.parametrize('command', ['/help', '/start', '/where', '/home'])
def test_local_commands_do_not_read_portal(chat, monkeypatch, command):
    monkeypatch.setattr(chat.portal, 'personal_assistant', lambda *a: pytest.fail('local command read Portal'))
    assert '个人助手 · 手机助手' in '\n'.join(texts(chat.send(command)))


def test_messages_addressed_to_the_user_are_pushed_and_others_stay(chat):
    """Telepus 只推送发给本人的主动消息，网页上的往来不推送"""
    chat.send("开始")
    web = {"message_id": "web_reply", "role": "agent", "reply_to_message_id": "web_input",
           "content_parts": [{"type": "text", "text": "网页上的回复"}]}
    notice = {"message_id": "notice", "role": "agent", "sender_label": "Steward",
              "recipients": [{"kind": "user", "id": "ALICE"}],
              "content_parts": [{"type": "text", "text": "建议新增成员"}]}
    for message in (web, notice):
        chat.portal.event_log[FACADE].append({"event_type": "conversation.message.appended", "payload": {"message": message}})
    assert [item["text"] for item in chat.refresh(force=False)] == ["📨 **Steward**\n建议新增成员"]


def test_question_set_is_answered_in_telegram_and_submitted_to_the_facade(chat):
    """Telepus 问答卡片逐题作答，按钮与回复自填都可用，答完提交给门面"""
    chat.send("帮我选")
    input_id = chat.portal.delivered[0][2]["message_id"]
    questions = {"type": "async_questions", "answer_message_id": "answer_x", "questions": [
        {"question_index": 0, "title": "颜色？", "options": ["红", "蓝"]},
        {"question_index": 1, "title": "大小？", "options": []},
    ]}
    chat.portal.answer(FACADE, input_id, "reply_q", "请回答", member="写作助手", parts=[questions])
    prompt = chat.refresh()[-1]
    assert "问题 1/2" in prompt["text"]
    chat.store.update_question_session(prompt["question_session"], telegram_message_id=900)
    button = prompt["reply_markup"]["inline_keyboard"][1][0]
    assert button["text"] == "蓝"
    step = chat.click(button, telegram_message_id=900)
    assert "问题 2/2" in step["edit"]["text"]
    # A question without options is answered by replying to its message.
    done = chat.send("中号", reply_to=900)
    assert "已提交回答" in done["edit"]["text"]
    resident_id, _policy, message, extra = chat.portal.delivered[-1]
    assert resident_id == FACADE
    assert message["message_id"] == "answer_x"
    assert message["content_parts"][1] == {"type": "async_question_answer", "source_message_id": "reply_q",
                                           "answers": [{"question_index": 0, "answer": "蓝"},
                                                       {"question_index": 1, "answer": "中号"}]}
    assert extra["credentials"] is None
    assert any(item["message_id"] == "answer_x" for item in chat.store.open_inputs("c1"))


def test_member_permission_request_cannot_be_approved_through_the_facade(chat):
    """Telepus 门面上的成员权限请求只能选不交出，关闭问题集会通知 Portal"""
    chat.send("发布")
    input_id = chat.portal.delivered[0][2]["message_id"]
    questions = {"type": "async_questions", "answer_message_id": "answer_c", "questions": [
        {"question_index": 0, "title": "交出权限？", "options": ["同意交出这些权限", "不交出"],
         "credentials": [{"a": "@contributor", "resource_id": "workbench_0123456789ab"}]}]}
    chat.portal.answer(FACADE, input_id, "reply_c", "需要权限", member="发布助手", parts=[questions])
    prompt = chat.refresh()[-1]
    options = [row[0]["text"] for row in prompt["reply_markup"]["inline_keyboard"][:-1]]
    assert options == ["不交出"]
    close = prompt["reply_markup"]["inline_keyboard"][-1][-1]
    chat.store.update_question_session(prompt["question_session"], telegram_message_id=901)
    assert "已关闭" in chat.click(close)["edit"]["text"]
    assert chat.portal.closed == [(FACADE, "reply_c")]


def test_history_shows_web_and_telegram_rounds_with_expand_and_more(chat):
    """Telepus /history 按轮显示含网页端的对话，可展开全文和向前翻页"""
    chat.portal.messages[FACADE] = [
        {"message_id": "w1", "role": "user", "created_at": "2026-11-06T09:00:00Z",
         "content_parts": [{"type": "text", "text": "网页问题" * 80}]},
        {"message_id": "r1", "role": "agent", "reply_to_message_id": "w1", "family_reply": {"title": "研究助手"},
         "content_parts": [{"type": "text", "text": "网页回复"}]},
        {"message_id": "w2", "role": "user", "created_at": "2026-11-06T10:00:00Z",
         "content_parts": [{"type": "text", "text": "第二轮"}]},
    ]
    response = chat.send("/history 1")
    assert len(response["messages"]) == 1
    assert "第二轮" in response["messages"][0]["text"]
    more = response["messages"][0]["reply_markup"]["inline_keyboard"][-1][0]
    assert more["text"] == "更早"
    first = chat.click(more)["messages"][0]
    assert "网页" in first["text"]
    assert "**研究助手**：网页回复" in first["text"]
    assert first["link"] == {"resident_id": FACADE, "message_id": "r1"}
    expand = first["reply_markup"]["inline_keyboard"][0][0]
    assert expand["text"] == "展开"
    assert chat.click(expand)["messages"][0]["text"] == "**你**：" + "网页问题" * 80


def test_stop_cancels_this_chats_unfinished_messages_on_the_facade(chat):
    """Telepus /stop 取消本聊天在门面上未完成的消息，并发出停止说明"""
    chat.send("任务一")
    chat.send("任务二")
    assert "2 条" in texts(chat.send("/stop"))[0]
    assert [item[0] for item in chat.portal.cancelled] == [FACADE, FACADE]
    chat.portal.states = {key: {"state": "cancelled", "reply_message_ids": []} for key in chat.portal.states}
    assert all("已停止" in item["text"] for item in chat.refresh())


def test_exact_route_now_injects_and_stop_clears_the_queue(chat):
    """Telepus 精准路由下 /now 插入当前工作，/stop 停止当前任务并清空队列"""
    chat.send(EXACT)
    chat.send("/now 补充一句")
    assert chat.portal.delivered[-1][1] == "inject_active_turn"
    chat.portal.active[EXACT] = "m_active"
    chat.portal.mailboxes[EXACT] = [{"message_id": "m_q1", "preview": "排队"}]
    chat.send("/stop")
    assert chat.portal.cancelled == [(EXACT, "m_active"), (EXACT, "m_q1")]


def test_a_reply_whose_send_failed_is_sent_on_the_next_refresh(chat):
    """Telepus 发送 Telegram 失败的回复在下一轮补发，已送达的不重复"""
    class Flaky:
        def __init__(self):
            self.fail, self.sent = True, []

        def send_message(self, **kwargs):
            if self.fail:
                self.fail = False
                raise RuntimeError("Telegram unavailable")
            self.sent.append(kwargs["text"])
            return {"result": {"message_id": 800 + len(self.sent)}}

    chat.send("问一下")
    input_id = chat.portal.delivered[0][2]["message_id"]
    chat.portal.answer(FACADE, input_id, "reply_f", "答案", member="写作助手")
    chat.portal.event_log[FACADE].append({"event_type": "conversation.message.appended", "payload": {"message": {
        "message_id": "notice_f", "role": "agent", "recipients": [{"kind": "user", "id": ACTOR}],
        "content_parts": [{"type": "text", "text": "通知"}]}}})
    adapter = TelegramAdapter(chat.core, client=Flaky())
    assert adapter.refresh_routes()["errors"] == 1
    chat.core._last_input_check.clear()
    assert adapter.refresh_routes()["errors"] == 0
    assert sorted(adapter.client.sent) == sorted(["📨 通知", "<b>写作助手</b>\n答案"])
    chat.core._last_input_check.clear()
    adapter.refresh_routes()
    assert len(adapter.client.sent) == 2


def test_adapter_links_each_sent_message_to_its_portal_message(chat):
    """Telepus 发出的每条 Telegram 消息登记对应的 Portal 消息，并以 HTML 发送"""
    class Client:
        def __init__(self):
            self.sent = []

        def send_message(self, **kwargs):
            self.sent.append(kwargs)
            return {"result": {"message_id": 700 + len(self.sent)}}

    client = Client()
    adapter = TelegramAdapter(chat.core, client=client)
    adapter._send("c1", [{"text": "回复", "link": {"resident_id": FACADE, "message_id": "reply_9"}}],
                  dry_run=False, status="refreshed")
    assert chat.store.linked_message("c1", 701) == {"resident_id": FACADE, "message_id": "reply_9"}
    assert client.sent[0]["parse_mode"] == "HTML"


def test_both_services_start_from_the_portal_package():
    """Telepus 的两个服务由 Portal 内置启动器运行 management_portal.telepus 中的入口"""
    import importlib

    from management_common.service_components import MODULES, is_builtin

    for component, module in (("telepus-telegram-router", "management_portal.telepus.router_service"),
                              ("telepus-portal-refresh", "management_portal.telepus.refresh_service")):
        assert MODULES[component] == module and is_builtin(component)
        assert callable(importlib.import_module(module).main)


def test_chat_markdown_maps_to_telegram_html():
    """Telepus 把 Chat 页面的 Markdown 转成 Telegram 支持的 HTML"""
    html = markdown_to_telegram_html("# 标题\n> 引用一行\n- 项目 `code`\n\n| a | b |\n|---|---|\n| 1 | 2 |\n**粗** <x>")
    assert "<b>标题</b>" in html
    assert "<blockquote>引用一行</blockquote>" in html
    assert "• 项目 <code>code</code>" in html
    assert "<pre>a | b\n--+--\n1 | 2</pre>" in html
    assert "<b>粗</b> &lt;x&gt;" in html


def test_split_keeps_code_fences_balanced():
    """Telepus 分段发送长消息时每段的代码块围栏成对"""
    text = "前言\n```python\n" + "print(1)\n" * 800 + "```\n结尾"
    chunks = split_telegram_text(text, limit=1000)
    assert len(chunks) > 3
    for chunk in chunks:
        assert len(chunk) <= 1000
        assert chunk.count("```") % 2 == 0, chunk[:80]


def test_rounds_pair_requests_with_their_last_text_reply():
    """Telepus 对话轮次取每条输入的最后一条文字回复"""
    rounds = conversation_rounds([
        {"message_id": "u", "role": "user", "content_parts": [{"type": "text", "text": "q"}]},
        {"message_id": "a1", "role": "agent", "reply_to_message_id": "u", "content_parts": [{"type": "text", "text": "x"}]},
        {"message_id": "a2", "role": "agent", "reply_to_message_id": "u", "content_parts": [{"type": "native_turn"}]},
        {"message_id": "a3", "role": "agent", "reply_to_message_id": "u", "content_parts": [{"type": "text", "text": "y"}]},
    ])
    assert rounds[0]["reply"]["message_id"] == "a3"
