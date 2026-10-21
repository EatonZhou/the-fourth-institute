"""
:module: resident.conversation
:importance: P1
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import resident_fork_inheritance


def message(identity, body):
    return {"message_id": identity, "role": "user", "author_id": "sample_admin",
            "created_at": "2026-10-12T05:00:00Z", "content_parts": [{"type": "text", "text": body}]}


def test_fork_inheritance_names_the_opening_compaction_and_the_inherited_chat(isolated_portal_case):
    """Fork 回执给出继承窗口开头的压缩与继承的对话消息

    完整上下文 Fork 的子轨迹以来源的压缩开头：AgentData 按 forked 回执给出继承片段数、压缩时间（按需带正文）、继承的对话消息数和最后一条继承消息；子 Resident 自己的消息不计入；之后清除上下文时标记已清除；不是 Fork 的轨迹没有回执。

    :design: decision_5bf5bfbd52cb
    :design-point:
    """
    store = AgentDataStore()
    source, child = "resident_inherit_source", "resident_inherit_child"
    store.display_chat_message(source, message("msg_before", "before the compaction"))
    compaction = store.put_segment(segment_type="compaction", content_type="application/json",
                                  finished_at="2026-10-12T05:00:01Z", content={
        "replacement_history": [{"type": "message", "role": "user",
                                 "content": [{"type": "input_text", "text": "Summary: earlier work"}]}],
    })
    store.append_agent_segments(source, [compaction], actor_ref="test-actor")
    after = store.display_chat_message(source, message("msg_after", "after the compaction"))
    tail = store.put_segment(segment_type="model", content="tool work")
    store.append_agent_segments(source, [tail], actor_ref="test-actor")
    # A full Fork takes the effective window: the compaction and what follows it.
    store.fork_agent(source_agent_id=source, target_agent_id=child, actor_ref="sample_admin",
                     segment_ids=[compaction, after["segment_id"], tail])
    store.display_chat_message(child, message("msg_own", "the child's own work"))

    facts = store.fork_inheritance(child)
    assert facts == {
        "source_agent_id": source, "segment_count": 3,
        "opening_compaction": {"segment_id": compaction, "created_at": facts["opening_compaction"]["created_at"]},
        "message_count": 1, "last_message_id": "msg_after", "context_cleared": False,
    }
    assert facts["opening_compaction"]["created_at"] == "2026-10-12T05:00:01Z"
    read = store.fork_inheritance(child, include_compaction=True)["opening_compaction"]["content"]
    assert read["replacement_history"][0]["content"][0]["text"] == "Summary: earlier work"
    # Only a Fork has a receipt; the source's own trace inherited nothing.
    assert store.fork_inheritance(source) == {}

    store.record_context_reset(child, reset_at="2026-10-12T06:00:00Z", actor_ref="sample_admin",
                               trace_position=store.trace_length(child))
    assert store.fork_inheritance(child)["context_cleared"] is True


def test_recent_turn_fork_has_no_opening_compaction(isolated_portal_case):
    """最近 N 轮 Fork 的继承事实不含开头压缩

    只继承最近几轮对话的 Fork 没有开头压缩，继承事实给出继承的消息数和最后一条继承消息。

    :design: decision_5bf5bfbd52cb
    :design-point:
    """
    store = AgentDataStore()
    source, child = "resident_recent_source", "resident_recent_child"
    first = store.display_chat_message(source, message("msg_first", "first"))
    second = store.display_chat_message(source, message("msg_second", "second"))
    store.fork_agent(source_agent_id=source, target_agent_id=child, actor_ref="sample_admin",
                     segment_ids=[first["segment_id"], second["segment_id"]])
    facts = store.fork_inheritance(child)
    assert facts["opening_compaction"] is None
    assert (facts["message_count"], facts["last_message_id"]) == (2, "msg_second")


def _fork_record(**extra):
    return {"resident_id": "resident_child", "source_resident_id": "resident_parent",
            "context_source_resident_id": "resident_parent", "fork_mode": "full",
            "forked_from_at": "2026-10-11T21:33:44Z", **extra}


def test_inheritance_view_names_sources_only_for_callers_who_can_see_them(monkeypatch):
    """Fork 继承卡片只向能看到来源的调用者给出来源标题与 Lineage

    非 Fork Resident 没有继承视图且不读取 AgentData；能看到来源时给出标题和所在 Lineage，看不到时只给来源 ID；默认不读取压缩正文。

    :design: decision_2fe6fd5367fb, decision_5bf5bfbd52cb
    :design-point:
    """
    visible = Mock(return_value=[{"resident_id": "resident_parent", "title": "Parent", "lineage_id": "lineage_a"}])
    monkeypatch.setattr(resident_fork_inheritance.resident_access, "visible_resident_records", visible)
    facts = Mock(return_value={"source_agent_id": "resident_parent", "segment_count": 23, "message_count": 1,
                               "last_message_id": "msg_last", "context_cleared": False,
                               "opening_compaction": {"segment_id": "segment_c", "created_at": "2026-10-11T21:31:14Z"}})
    monkeypatch.setattr(resident_fork_inheritance, "execution_port", SimpleNamespace(fork_inheritance=facts))

    assert resident_fork_inheritance.inheritance_view("resident_plain", {"resident_id": "resident_plain"}, "sample_admin") is None
    facts.assert_not_called()

    view = resident_fork_inheritance.inheritance_view("resident_child", _fork_record(), "sample_admin")
    visible.assert_called_once_with(["resident_parent", "resident_parent"], "sample_admin", "portal")
    facts.assert_called_once_with("resident_child", include_compaction=False)
    assert view == {
        "source_resident_id": "resident_parent", "source_visible": True, "source_title": "Parent",
        "source_lineage_id": "lineage_a",
        "context_source_resident_id": "resident_parent", "context_source_visible": True,
        "context_source_title": "Parent", "context_source_lineage_id": "lineage_a",
        "fork_mode": "full", "forked_from_at": "2026-10-11T21:33:44Z", "source_message_id": "",
        "segment_count": 23, "message_count": 1, "last_message_id": "msg_last", "context_cleared": False,
        "recorded": True, "opening_compaction": {"created_at": "2026-10-11T21:31:14Z"},
    }

    visible.return_value = []
    hidden = resident_fork_inheritance.inheritance_view("resident_child", _fork_record(), "outsider")
    assert (hidden["source_visible"], hidden["source_title"], hidden["source_lineage_id"]) == (False, "", "")
    assert hidden["source_resident_id"] == "resident_parent"


def test_inherited_compaction_shows_only_its_readable_messages(monkeypatch):
    """继承的压缩只返回可读的用户与 Agent 消息

    按需读取继承的压缩时，返回 replacement history 中的用户与 Agent 文字及图片数量，不返回 developer、system 会话指令；Provider 加密的摘要只标记为 encrypted。

    :design: decision_2fe6fd5367fb
    :design-point:
    """
    monkeypatch.setattr(resident_fork_inheritance.resident_access, "visible_resident_records", Mock(return_value=[]))
    content = {"replacement_history": [
        {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "session rules"}]},
        {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "earlier request"}, {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
        ]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "earlier reply"}]},
        {"type": "compaction", "encrypted_content": "opaque"},
    ]}
    facts = Mock(return_value={"segment_count": 4, "opening_compaction": {
        "segment_id": "segment_c", "created_at": "2026-10-11T21:31:14Z", "content": content}})
    monkeypatch.setattr(resident_fork_inheritance, "execution_port", SimpleNamespace(fork_inheritance=facts))
    view = resident_fork_inheritance.inheritance_view("resident_child", _fork_record(), "sample_admin", include_context=True)
    facts.assert_called_once_with("resident_child", include_compaction=True)
    assert view["opening_compaction"] == {
        "created_at": "2026-10-11T21:31:14Z",
        "items": [{"role": "user", "text": "earlier request", "image_count": 1},
                  {"role": "agent", "text": "earlier reply", "image_count": 0}],
        "encrypted": True,
    }


@pytest.mark.parametrize("include", [False, True])
def test_get_conversation_reads_the_inherited_compaction_only_on_request(monkeypatch, include):
    """GetConversation 总带继承事实，只在显式请求时读取继承的压缩

    GetConversation 为 Fork 出的 Resident 返回 inheritance；只有 include_inherited_context=true 时才读取开头压缩的正文。

    :design: decision_2fe6fd5367fb
    :design-point:
    """
    from management_portal.society_service.isa import conversation
    snapshot = Mock(return_value={"messages": []})
    monkeypatch.setattr(conversation, "protocol", lambda: SimpleNamespace(get_resident_conversation_snapshot=snapshot))
    args = {"resident_id": "resident_child", "include_inherited_context": include}
    conversation.get_conversation(SimpleNamespace(caller="sample_admin", argument=args.get))
    assert snapshot.call_args.kwargs["include_inheritance"] is True
    assert snapshot.call_args.kwargs["include_inherited_context"] is include
