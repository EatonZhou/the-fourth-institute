"""A Chatroom calls a Family's external request, not its routing receipt.

:module: resident.conversation
:importance: P1
:design: decision_a6bebf4ac283, decision_44cf587c1721, decision_2a3ff353165c
"""
from types import SimpleNamespace

import pytest

from management_portal import chatroom_backend as chatroom
from management_portal.society_service import (
    family_facade, resident_conversation_event_relay, resident_conversation_stream,
    resident_protocol, resident_workspace_domain,
)
from tests.cases.family_test_support import set_role
from tests.cases.permission_test_support import permissions  # noqa: F401
from tests.cases.test_family_facade import MEMBER, _register_test_member
from tests.cases.test_chatroom_resident_delivery import chatroom_host  # noqa: F401
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


@pytest.mark.parametrize("outcome", ["completed", "empty", "failed", "cancelled"])
def test_chatroom_waits_for_family_work_and_projects_its_terminal(delivery_resident, chatroom_host, monkeypatch, outcome):
    """聊天室等待 Family 实际工作完成，再投影门面回复

    覆盖转交后排队与运行、实时事件、成功、空回复、失败、取消和重复终态事件。
    """
    facade_id = delivery_resident["resident_id"]
    set_role(facade_id, family_id="fbi_chatroom_family", actor="sample_admin")
    facade = resident_workspace_domain._find_resident(facade_id)
    _register_test_member(facade, "Implementation")
    set_role(MEMBER, family_id="fbi_chatroom_family", role="member", actor="sample_admin")
    room_id = chatroom.create_chatroom("sample_admin", ["bob"], "Family work")["chatroom_id"]
    posted = chatroom.post_message("sample_admin", room_id, [{"type": "text", "text": f"@{facade_id} Fix it"}])
    delivery, = posted["deliveries"]
    assert delivery["status"] == "delivered"
    baseline_ids = [m["message_id"] for m in chatroom.list_messages("sample_admin", room_id)["messages"]]
    request_id = delivery["input_message_id"]
    initial = resident_protocol.get_resident_input_state(facade_id, "sample_admin", request_id, include_progress=True, include_context=True)
    assert initial["state"] == "queued" and initial["in_context"] is False
    assert initial["progress"]["state"] == "routing"
    assert "resident_id" not in initial["progress"]
    repo = resident_conversation_stream.get_repository()
    member_input = "msg_11111111111111111111111111111111"
    repo.append_message(MEMBER, {
        "message_id": member_input, "role": "user", "author_id": "sample_admin",
        "content_parts": [{"type": "text", "text": "Fix it"}],
        "business_ref": family_facade.relay_mark(facade_id, request_id),
    })
    repo.set_input_state(MEMBER, member_input, "queued")
    family_facade.complete_relay((facade_id, request_id), MEMBER, member_input)
    assert repo.input_state(facade_id, request_id)["state"] == "completed"
    projected = chatroom._execute("GetInputState", {"resident_id": facade_id, "message_id": request_id}, "sample_admin")["input_state"]
    assert projected["message_id"] == request_id and projected["state"] == "queued"
    chatroom.reconcile_deliveries(room_id)
    assert chatroom._store().get(room_id)["deliveries"][0]["status"] == "delivered"
    assert [m["message_id"] for m in chatroom.list_messages("sample_admin", room_id)["messages"]] == baseline_ids

    repo.set_input_state(MEMBER, member_input, "running")
    repo.set_state(MEMBER, {"active_message_id": member_input, "work_state": "running", "work_phase": "starting_session"})
    activity = {"activity_id": "act_work", "resident_id": MEMBER, "message_id": member_input,
                "kind": "tool", "status": "running", "title": "Inspecting the issue"}
    repo.set_activities(MEMBER, [activity])
    active, = chatroom.get_chatroom("sample_admin", room_id)["active_deliveries"]
    assert active["resident_id"] == facade_id and active["state"] == "running"
    assert active["work_phase"] == "starting_session" and active["activities"][0]["activity_id"] == "act_work"
    intermediate = "msg_22222222222222222222222222222222"
    if outcome != "empty":
        repo.append_message(MEMBER, {"message_id": intermediate, "role": "agent", "author_id": MEMBER,
            "reply_to_message_id": member_input, "content_parts": [{"type": "text", "text": "Working"}]})
    assert [m["message_id"] for m in chatroom.list_messages("sample_admin", room_id)["messages"]] == baseline_ids

    wakes = []
    monkeypatch.setattr(chatroom, "_publish", lambda room, **kwargs: wakes.append((room["chatroom_id"], kwargs)))
    relay = resident_conversation_event_relay.ResidentConversationEventRelay(object(), SimpleNamespace(publish=lambda *_: None))
    relay._publish_chatroom_progress({"resident_id": MEMBER, "event_type": resident_conversation_stream.ACTIVITY_CHANGED,
                                     "payload": {"activities": [activity]}})
    assert wakes == [(room_id, {"fields": ["progress"], "inbox": False})]
    reply_id = "msg_33333333333333333333333333333333"
    if outcome == "completed":
        repo.append_message(MEMBER, {"message_id": reply_id, "role": "agent", "author_id": MEMBER,
            "reply_to_message_id": member_input, "content_parts": [{"type": "text", "text": "Fixed"}]})
    terminal = "completed" if outcome == "empty" else outcome
    repo.set_input_state(MEMBER, member_input, terminal, error={"message": "Worker stopped"} if terminal != "completed" else {})
    event = {"resident_id": MEMBER, "event_type": resident_conversation_stream.INPUT_STATE_CHANGED,
             "payload": {"input_state": {"message_id": member_input, "state": terminal}}}
    # Completion reaches the Chatroom without a viewer polling; repeated events are harmless.
    relay._publish_chatroom_progress(event)
    relay._publish_chatroom_progress(event)
    assert chatroom._store().get(room_id)["deliveries"][0]["status"] == ("replied" if terminal == "completed" else terminal)
    messages = chatroom.list_messages("sample_admin", room_id)["messages"]
    assert any("没有回复内容" in chatroom._message_text(m) for m in messages) == (outcome == "empty")
    if outcome == "completed":
        answer, = [m for m in messages if m["role"] == "agent"]
        assert answer["author_id"] == facade_id and chatroom._message_text(answer) == "Fixed"
        assert answer["reply_to_message_id"] == posted["message"]["message_id"]
    elif outcome != "empty":
        assert "Worker stopped" in chatroom._message_text(messages[-1])
    assert chatroom.get_chatroom("sample_admin", room_id)["active_deliveries"] == []


def test_nested_family_state_and_events_follow_the_original_request(monkeypatch):
    """嵌套 Family 的状态与通知回到原始聊天室请求

    沿已记录的转交链保留外部门面输入 ID，复用实际成员进度，并唤醒原业务入口。
    """
    outer, inner, worker = (f"resident_{x * 12}" for x in "abc")
    original, forwarded, leaf = (f"msg_{x * 32}" for x in "123")
    records = {rid: {"resident_id": rid, "title": rid} for rid in (outer, inner, worker)}
    links = {outer: {original: {"resident_id": inner, "message_id": forwarded}},
             inner: {forwarded: {"resident_id": worker, "message_id": leaf}}}
    refs = {(outer, original): "chatroom_aaaaaaaaaaaa/msg_trigger",
            (inner, forwarded): family_facade.relay_mark(outer, original),
            (worker, leaf): family_facade.relay_mark(inner, forwarded)}
    class Repository:
        def input_state(self, rid, mid):
            return {"message_id": mid, "state": "running" if rid == worker else "completed", "reply_message_ids": []}
        def message_business_ref(self, rid, mid):
            return refs.get((rid, mid), "")
        def current_projection(self, rid):
            assert rid == worker
            return {"state": {"active_message_id": leaf, "work_state": "running", "work_phase": "starting_session"}, "activities": []}
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: Repository())
    monkeypatch.setattr(resident_workspace_domain, "_find_resident", lambda rid: records[rid])
    monkeypatch.setattr(family_facade, "is_facade", lambda r: r["resident_id"] in links)
    monkeypatch.setattr(family_facade, "relay_links", lambda r: links.get(r["resident_id"], {}))
    monkeypatch.setattr(resident_protocol.resident_mailbox, "project_input_state", lambda r, mid, state: state)
    state = resident_protocol._stored_resident_input_state(outer, original, include_progress=True)
    assert state["message_id"] == original and state["state"] == "running"
    assert state["progress"]["work_phase"] == "starting_session"
    wakes = []
    monkeypatch.setattr(chatroom, "handle_resident_progress", lambda **kw: wakes.append(kw))
    relay = resident_conversation_event_relay.ResidentConversationEventRelay(object(), SimpleNamespace(publish=lambda *_: None))
    relay._publish_chatroom_progress({"resident_id": worker, "event_type": resident_conversation_stream.STATE_CHANGED,
                                     "payload": {"state": {"active_message_id": leaf}}})
    assert wakes == [{"resident_id": outer, "message_id": original, "business_ref": refs[(outer, original)]}]
