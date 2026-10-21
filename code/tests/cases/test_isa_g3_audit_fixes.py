"""Regressions for the G3 ISA conversation and public discussion audit.

:module: spec.design-governance
"""

from types import SimpleNamespace

import pytest


from management_portal import notice_board_backend, notice_board_web_isa, portal_pulse_backend, pulse_web_isa, web_isa
from management_portal.hatg.catalog import instruction_catalog
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.hatg.kernel import InstructionKernel
from management_portal.society_service import resident_execution_work
from tests.cases.isa_test_support import run
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


def call(arguments, caller="bob"):
    return SimpleNamespace(caller=caller, argument=arguments.get)


def test_bootstrap_delivery_keeps_the_business_reference(delivery_resident):
    """DeliverMessage 启动执行时在执行工作和输入消息中保留 business_ref"""
    ref = "paper_0123456789ab"
    delivered = run("DeliverMessage", {
        "resident_id": delivery_resident["resident_id"],
        "business_ref": ref,
        "message": {"content_parts": [{"type": "text", "text": "Deep read"}]},
    }).raise_for_error()
    job = resident_execution_work.read(delivery_resident)
    assert job["business_ref"] == ref
    assert job["input_message"]["business_ref"] == ref
    assert job["user_message_id"] == delivered["message_id"]





def test_synchronous_delivery_persists_the_business_reference(delivery_resident, monkeypatch):
    """同步投递的消息也保存 business_ref"""
    from management_portal.society_service import resident_conversation_stream, resident_execution_provider

    provider = SimpleNamespace(
        capabilities=resident_execution_provider.ExecutionCapabilities(synchronous_delivery=True),
        deliver=lambda request: resident_execution_provider.DeliveryResult(reply="done"),
    )
    monkeypatch.setattr(resident_execution_provider, "provider_for", lambda record: provider)
    delivered = run("DeliverMessage", {
        "resident_id": delivery_resident["resident_id"], "business_ref": "paper_0123456789ab",
        "message": {"content_parts": [{"type": "text", "text": "One synchronous input"}]},
    }).raise_for_error()
    stored = resident_conversation_stream.get_repository().agent_data.chat_message(
        delivery_resident["resident_id"], delivered["message_id"],
    )
    assert stored["business_ref"] == "paper_0123456789ab"


def test_reusing_a_message_id_with_different_content_reports_conflict(delivery_resident):
    """用同一个 message_id 投递不同内容时返回冲突，原消息不变"""
    resident_id = delivery_resident["resident_id"]
    message_id = "msg_0123456789abcdef0123456789abcdef"
    arguments = {"resident_id": resident_id, "message": {
        "message_id": message_id,
        "content_parts": [{"type": "text", "text": "first"}],
    }}
    run("DeliverMessage", arguments, key="g3-first").raise_for_error()
    arguments["message"]["content_parts"][0]["text"] = "changed"
    retry = run("DeliverMessage", arguments, key="g3-second")
    assert retry.error["code"] == "instruction.conflict"
    original = run("GetMessage", {"resident_id": resident_id, "message_id": message_id}).raise_for_error()
    assert original["message"]["content_parts"][0]["text"] == "first"


def test_search_messages_declares_the_results_used_by_its_consumer(monkeypatch):
    """SearchMessages 在结果 schema 中声明调用方使用的 results 字段"""
    from management_portal.society_service.isa import conversation

    hit = {"resident_id": "resident_aaaaaaaaaaaa", "message": {"message_id": "msg_one"},
           "previous_message_id": "", "next_message_id": ""}
    monkeypatch.setattr(conversation, "protocol", lambda: SimpleNamespace(
        search_conversation_messages=lambda *args, **kwargs: {
            "query": "one", "results": [hit], "next_page_token": "",
        }))
    result = conversation.search_messages(call({"resident_ids": [hit["resident_id"]], "query": "one"}))
    declaration = instruction_catalog()["SearchMessages"].schema()
    assert "results" in {field["name"] for field in declaration["result"]}
    assert result["results"][0]["message"]["message_id"] == "msg_one"


def test_list_inbox_exposes_the_read_revision_as_its_version(monkeypatch):
    """Mailbox 列表以阅读状态的修订号作为每项的 version"""
    monkeypatch.setattr(web_isa, "_inbox_entries", lambda *args: [
        {"key": "resident:resident_aaaaaaaaaaaa", "read_revision": 12, "unread": True},
    ])
    monkeypatch.setattr(web_isa, "_access", lambda: SimpleNamespace(user=lambda _user: {"configured": True}))
    result = web_isa.get_user_mailbox(call({"user_id": "bob"}))
    assert result["items"][0]["version"] == "12"
    assert result["unread_count"] == 1


def test_mark_inbox_read_accepts_the_scalar_version_and_rejects_a_changed_row(monkeypatch):
    """MarkMailboxItemRead 接受字符串版本，阅读状态的修订号已变化时返回版本冲突"""
    from management_portal.conversation_read_state import VersionConflict

    key = "notice-board:post_aaaaaaaaaaaa"
    current = {"read_revision": 7}
    changes = []

    def set_read(actor, exact, *, unread, expected_revision):
        if str(expected_revision) != str(current["read_revision"]):
            raise VersionConflict({"read_revision": current["read_revision"]})
        changes.append((actor, exact, unread))
        return {"key": exact, "unread": unread, "read_revision": current["read_revision"] + 1}

    from management_portal import conversation_read_state

    monkeypatch.setattr(conversation_read_state, "read_rows", lambda *args: {})
    monkeypatch.setattr(web_isa, "_inbox_entry", lambda *args: {"key": key})
    monkeypatch.setattr(web_isa, "web", lambda: SimpleNamespace(_set_agent_conversation_read_state=set_read))
    kernel = InstructionKernel(web_isa.binding(), store=None, authorize=lambda *args: None)
    instruction = InstructionCall.from_payload({
        "instruction": "MarkMailboxItemRead", "arguments": {"key": key}, "expected_version": "7",
    })
    accepted = kernel.execute(instruction, InstructionContext(caller="bob", entry="web"))
    assert accepted.status == "committed", accepted.error
    current["read_revision"] = 8
    stale = kernel.execute(instruction, InstructionContext(caller="bob", entry="web"))
    assert stale.error["code"] == "instruction.version_conflict"
    assert changes == [("bob", key, False)]


def test_opening_a_chat_reads_through_the_latest_alert(monkeypatch):
    """打开 Resident Chat 不带版本调用 MarkMailboxItemRead，先核对身份再读到最新提醒"""
    resident_id = "resident_aaaaaaaaaaaa"
    key = "resident:" + resident_id
    calls = []

    def set_read(actor, exact, *, unread, expected_revision):
        calls.append((actor, exact, unread, expected_revision))
        return {"key": exact, "unread": unread, "read_revision": 9}

    resolved = []
    web = SimpleNamespace(
        _set_agent_conversation_read_state=set_read,
        resident_api=SimpleNamespace(resolve_resident_identity=lambda *args: resolved.append(args) or {}),
    )
    monkeypatch.setattr(web_isa, "web", lambda: web)
    kernel = InstructionKernel(web_isa.binding(), store=None, authorize=lambda *args: None)
    result = kernel.execute(
        InstructionCall.from_payload({"instruction": "MarkMailboxItemRead", "arguments": {"key": key}}),
        InstructionContext(caller="bob", entry="web"),
    )
    assert result.status == "committed", result.error
    assert resolved == [(resident_id, "bob")]
    assert calls == [("bob", key, False, None)]
    assert result.result["item"] == {"key": key, "unread": False, "read_revision": 9}


def test_create_reply_returns_the_new_reply(monkeypatch):
    """CreateReply 返回新建的回复和通知修订号"""
    backend = notice_board_backend.NoticeBoardBackend()
    monkeypatch.setattr(notice_board_web_isa, "_board", lambda: backend)
    post_id = backend.create_post("alice", "Title", "body")["post"]["post_id"]
    result = notice_board_web_isa.create_reply(call({"post_id": post_id, "content": "hello"}))
    assert result["reply"]["reply_id"].startswith("reply_")
    assert result["reply"]["content"] == "hello"
    assert result["notification_revision"] == 2


def test_mark_post_unread_overrides_the_initial_read_baseline(monkeypatch):
    """把帖子标为未读会覆盖初始的已读基线"""
    backend = notice_board_backend.NoticeBoardBackend()
    monkeypatch.setattr(notice_board_web_isa, "_board", lambda: backend)
    post_id = backend.create_post("alice", "Title", "body")["post"]["post_id"]
    # Existing read stores may have seeded this post as already read.
    reads = backend._load_reads(backend._load_record())
    reads["baseline_revisions"][post_id] = 1
    backend._write_reads(reads)
    assert backend.list_posts("bob")["unread_count"] == 0
    result = notice_board_web_isa.mark_post_read(call({"post_id": post_id, "unread": True}))
    assert backend.list_posts("bob")["posts"][0]["unread"] is True
    assert result == {"read_revision": 0, "unread_count": 1}


def test_mark_post_read_returns_the_saved_revision_and_remaining_unread_count(monkeypatch):
    """标记帖子已读返回实际保存的已读修订号和剩余未读数，旧修订号不回退"""
    backend = notice_board_backend.NoticeBoardBackend()
    monkeypatch.setattr(notice_board_web_isa, "_board", lambda: backend)
    backend.list_posts("bob")
    first = backend.create_post("alice", "One", "body")["post"]["post_id"]
    backend.create_post("alice", "Two", "body")
    read = notice_board_web_isa.mark_post_read(call({"post_id": first, "notification_revision": 1}))
    assert read == {"read_revision": 1, "unread_count": 1}
    backend.add_reply("alice", first, "next")
    backend.mark_post_read("bob", first, 2)
    older = notice_board_web_isa.mark_post_read(call({"post_id": first, "notification_revision": 1}))
    assert older == {"read_revision": 2, "unread_count": 1}


def test_publishing_a_pulse_item_returns_a_stable_publication(monkeypatch):
    """重复发布同一 Pulse 条目返回同一个发布记录"""
    pulse = portal_pulse_backend
    monkeypatch.setattr(pulse, "_find_private_item", lambda *args: {"id": "item-one", "text": "work"})
    monkeypatch.setattr(pulse, "get_home_payload", lambda *args: {"publications": []})
    arguments = {"source_period": "daily", "source_key": "2026-09-29", "item_id": "item-one", "published": True}
    result = pulse_web_isa.publish_pulse_item(call(arguments))
    assert result["publication"]["publication_id"].startswith("pub_")
    assert result["publication"]["owner"] == "bob"
    assert pulse_web_isa.publish_pulse_item(call(arguments))["publication"] == result["publication"]
    assert len(pulse._read_publications()["items"]) == 1


def test_publish_pulse_item_published_false_retracts_by_item(monkeypatch):
    """PublishPulseItem published=false 按来源条目撤回调用者自己的发布，结果不带 publication"""
    pulse = portal_pulse_backend
    monkeypatch.setattr(pulse, "_find_private_item", lambda *args: {"id": "item-one", "text": "work"})
    monkeypatch.setattr(pulse, "get_home_payload", lambda *args: {"publications": []})
    item = {"source_period": "daily", "source_key": "2026-09-29", "item_id": "item-one"}
    pulse_web_isa.publish_pulse_item(call({**item, "published": True}))
    with pytest.raises(PermissionError):
        pulse_web_isa.publish_pulse_item(call({**item, "published": False}, caller="alice"))
    assert len(pulse._read_publications()["items"]) == 1
    retracted = pulse_web_isa.publish_pulse_item(call({**item, "published": False}))
    assert retracted == {"pulse": {"publications": []}}
    assert pulse._read_publications()["items"] == []
