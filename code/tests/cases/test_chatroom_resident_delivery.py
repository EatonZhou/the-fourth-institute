"""Chatroom @ delivery through the real Resident input path (decision_a6bebf4ac283)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from management_portal import chatroom_backend
from management_portal.society_service import resident_execution_work
from management_portal.society_service.runtime import backend_adapter
from management_portal.society_service.runtime.contracts import ResidentTerminalFact
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401


def _input_ids(job):
    return [job.get("user_message_id"), *[item.get("user_message_id") for item in resident_execution_work.normalized_queue(job)]]


@pytest.fixture
def chatroom_host(monkeypatch, delivery_resident, permissions):
    monkeypatch.setattr(chatroom_backend, "_portal_users", lambda: [
        {"username": "sample_admin", "status": "active"},
        {"username": "bob", "status": "active"},
    ])
    monkeypatch.setattr(chatroom_backend, "_publish", lambda *args, **kwargs: None)
    # The default template: messaging a Resident needs contributor on it, which
    # sample_admin holds and bob does not; everyone reads it.
    resident_id = delivery_resident["resident_id"]
    permissions.place(Fact("resident", resident_id))
    permissions.grant("sample_admin", resident_id, "@contributor")


def test_a_mention_queues_one_input_with_the_chatroom_business_reference(delivery_resident, chatroom_host):
    """@ Resident 只排入一条带 Chatroom 业务引用的输入，重放投递不重复入队"""
    resident_id = delivery_resident["resident_id"]
    chatroom_id = chatroom_backend.create_chatroom("sample_admin", ["bob"], "评审")["chatroom_id"]
    chatroom_backend.post_message("bob", chatroom_id, [{"type": "text", "text": "背景：先看第三节"}])
    posted = chatroom_backend.post_message("sample_admin", chatroom_id, [{"type": "text", "text": f"@{resident_id} 请给出意见"}])

    delivery, = posted["deliveries"]
    assert delivery["status"] == "delivered", delivery
    job = resident_execution_work.read(delivery_resident)
    assert job["business_ref"] == f"{chatroom_id}/{posted['message']['message_id']}"
    assert delivery["input_message_id"] in _input_ids(job)
    text = chatroom_backend._execute(
        "GetMessage", {"resident_id": resident_id, "message_id": delivery["input_message_id"]}, "sample_admin",
    )["message"]["content_parts"][0]["text"]
    assert chatroom_id in text and text.endswith(f"@{resident_id} 请给出意见")
    assert "背景：先看第三节" not in text

    # Reading the still-queued input changes nothing; replaying the delivery adds no second input.
    chatroom_backend.list_messages("sample_admin", chatroom_id)
    with chatroom_backend._Transaction(chatroom_id) as rooms:
        rooms[chatroom_id]["deliveries"][0]["status"] = "queued"
    chatroom_backend.reconcile_deliveries(chatroom_id)
    assert _input_ids(resident_execution_work.read(delivery_resident)).count(delivery["input_message_id"]) == 1


def test_a_member_who_may_not_message_the_resident_is_told_so_in_the_chatroom(delivery_resident, chatroom_host, permissions):
    """无权给 Resident 发消息的成员 @ 它时，Chatroom 显示未能投递的系统消息"""
    # The Chatroom sends in-process with the author's identity, so the
    # permission system refuses bob as it would on any other entry; the
    # default template lets everyone message, so a deny entry takes it away.
    resident_id = delivery_resident["resident_id"]
    permissions.deny("bob", resident_id, "@message")
    chatroom_id = chatroom_backend.create_chatroom("sample_admin", ["bob"])["chatroom_id"]
    posted = chatroom_backend.post_message("bob", chatroom_id, [{"type": "text", "text": f"@{resident_id} 你好"}])
    assert posted["deliveries"][0]["status"] == "undeliverable"
    last = chatroom_backend.list_messages("sample_admin", chatroom_id)["messages"][-1]
    assert last["role"] == "system" and last["content_parts"][0]["text"].startswith(f"未能把消息交给 {resident_id}")


def test_terminal_facts_reach_the_chatroom_only_for_chatroom_business_references(monkeypatch):
    """只有带 Chatroom 业务引用的 Resident 结束事件才转交给 Chatroom"""
    seen = []
    monkeypatch.setattr(chatroom_backend, "handle_resident_terminal", lambda **kwargs: seen.append(kwargs) or {"chatroom_id": "x"})
    fact = ResidentTerminalFact(resident_id="resident_0123456789ab", actor="sample_admin", message_id="msg_00000000000000000000000000000000")
    assert backend_adapter._chatroom_terminal(fact) == {}
    chatroom_fact = ResidentTerminalFact(
        resident_id="resident_0123456789ab", actor="sample_admin", message_id="msg_00000000000000000000000000000000",
        business_ref="chatroom_0123456789ab/msg_11111111111111111111111111111111",
    )
    assert backend_adapter._chatroom_terminal(chatroom_fact) == {"chatroom_id": "x"}
    assert seen == [{
        "business_ref": "chatroom_0123456789ab/msg_11111111111111111111111111111111",
        "resident_id": "resident_0123456789ab",
        "message_id": "msg_00000000000000000000000000000000",
    }]
