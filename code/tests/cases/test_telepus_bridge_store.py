"""Telepus bridge state in its PostgreSQL schema (router schema 7)."""
from __future__ import annotations

import pytest

from management_portal.telepus.router_store import RouterStore
from tests.cases.telepus_bridge_support import telepus_schema  # noqa: F401


def test_old_router_marker_is_rejected_without_silently_upgrading_it(telepus_schema):
    """Telepus 遇到旧版本标记时拒绝启动，不自行升级"""
    store = RouterStore(telepus_schema)
    with store.connect() as connection:
        connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO meta VALUES ('schema_version', '6')")
    with pytest.raises(RuntimeError, match="coordinated schema migration"):
        store.init_schema()
    with store.connect() as connection:
        assert connection.execute("SELECT value FROM meta").fetchone()[0] == "6"
        assert connection.execute("SELECT to_regclass(current_schema() || '.chat_state')").fetchone()[0] is None


def test_chat_state_inputs_links_and_documents_round_trip(telepus_schema):
    """Telepus 聊天状态、输入、消息对应、问题会话与按钮载荷读写一致"""
    store = RouterStore(telepus_schema)
    chat = store.upsert_chat_state("chat", telegram_user_id="7", portal_actor="ET", mode="exact",
                                   exact_resident_id="resident_aaaaaaaaaaaa")
    assert (chat["mode"], chat["exact_resident_id"], chat["watch_resident_id"]) == ("exact", "resident_aaaaaaaaaaaa", "")
    store.upsert_chat_state("chat", watch_resident_id="resident_aaaaaaaaaaaa", watch_cursor=5)
    store.watch_cursor("chat", "resident_aaaaaaaaaaaa", 3)  # never moves back
    store.watch_cursor("chat", "resident_bbbbbbbbbbbb", 99)  # not the watched Resident
    assert store.get_chat_state("chat")["watch_cursor"] == 5
    store.rewind_watch_cursor("chat", "resident_aaaaaaaaaaaa", 2)  # after a failed send
    assert store.get_chat_state("chat")["watch_cursor"] == 2
    assert [item["chat_id"] for item in store.watched_chats()] == ["chat"]
    with pytest.raises(ValueError):
        store.upsert_chat_state("chat", mode="router")

    store.record_input("chat", "resident_aaaaaaaaaaaa", "msg_1", 11, excerpt="你好")
    assert [item["excerpt"] for item in store.open_inputs("chat")] == ["你好"]
    store.close_input("msg_1")
    assert store.open_inputs("chat") == []
    store.reopen_input("msg_1")
    assert [item["message_id"] for item in store.open_inputs("chat")] == ["msg_1"]
    assert store.telegram_input_ids("chat", ["msg_1", "msg_2"]) == {"msg_1"}

    assert store.claim_delivery("chat", "reply_1") and not store.claim_delivery("chat", "reply_1")
    store.release_delivery("chat", "reply_1")
    assert store.claim_delivery("chat", "reply_1")
    store.link_message("chat", 21, "resident_aaaaaaaaaaaa", "reply_1")
    assert store.linked_message("chat", 21) == {"resident_id": "resident_aaaaaaaaaaaa", "message_id": "reply_1"}

    session = store.open_question_session(chat_id="chat", resident_id="resident_aaaaaaaaaaaa", source_message_id="reply_1",
                                          answer_message_id="answer_1", questions=[{"title": "Q", "options": ["A"]}],
                                          lendable=True)
    assert store.open_question_session(chat_id="chat", resident_id="resident_aaaaaaaaaaaa", source_message_id="reply_1",
                                       answer_message_id="answer_1", questions=[], lendable=True) is None
    store.update_question_session(session["session_id"], answers={"0": "A"}, telegram_message_id=31)
    assert store.question_session_for_message("chat", 31)["answers"] == {"0": "A"}
    store.drop_question_session(session["session_id"])  # its prompt reached Telegram, so it stays
    assert store.question_session(session["session_id"], "chat") is not None

    token = store.create_callback_token("chat", {"kind": "expand", "n": 2})
    assert store.get_callback_payload("chat", token) == {"kind": "expand", "n": 2}
    attachment = store.create_attachment_delivery(chat_id="chat", telegram_user_id="7", portal_actor="ET",
                                                  source_message_id="m1", attachment={"name": "a.txt"})
    assert store.get_attachment_delivery(chat_id="chat", telegram_user_id="7", portal_actor="ET",
                                         token=attachment)["attachment"] == {"name": "a.txt"}


def test_a_nul_character_in_a_payload_is_still_saved(telepus_schema):
    """Telepus 审计载荷中的 NUL 字符改存为 U+FFFD，记录仍能保存"""
    store = RouterStore(telepus_schema)
    store.append_audit("message", chat_id="chat", payload={"text": "before\x00after"})
    with store.connect() as connection:
        payload = connection.execute("SELECT payload_json FROM audit_events").fetchone()[0]
    assert payload == {"text": "before�after"}
