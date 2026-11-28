"""Fork selection keeps attached background and the carrier's complete turn."""
from __future__ import annotations

import pytest

from management_portal.execution.agent_data_store import AgentDataStore, chat_segment_id


AT = "2026-10-16T00:00:00Z"
SOURCE = "resident_window_source"


def _message(store, identity, role="user", **facts):
    store.display_chat_message(SOURCE, {
        "message_id": identity, "role": role, "created_at": AT,
        "content_parts": [{"type": "text", "text": identity}], **facts,
    })
    return chat_segment_id(SOURCE, identity)


def _turn(store, number, *, recorded=True, background=False):
    identity = f"question-{number}"
    source_id = _message(store, identity)
    execution_id = f"execution-window-{number}" if recorded else ""
    if recorded:
        store.begin_execution(execution_id=execution_id, agent_id=SOURCE,
                              actor_ref="test-actor", provider="codex", started_at=AT)
        store.record_input_acceptance(execution_id, agent_id=SOURCE,
                                      provider_turn_id=f"turn-{number}")
    effective_id = f"segment_effective_{number}"
    store.transform_agent_segments(
        SOURCE, [source_id], segment_id=effective_id, content=identity,
        actor_ref="test-actor", operation_id=f"effective-{number}",
        operation_type="execution.provider_input", segment_type="harness",
        execution_id=execution_id,
        metadata={"kind": "effective_provider_input", "role": "user",
                  "message_id": identity},
    )
    refs = [effective_id]
    if background:
        refs.append(_message(store, f"background-{number}", released_with=identity))
    refs.append(_message(store, f"reply-{number}", "agent", reply_to_message_id=identity))
    return refs


@pytest.mark.parametrize("recent,first", [(5, 8), (10, 3), (0, 1)])
def test_recent_materializes_complete_turns_and_nested_fork_freezes_them(recent, first):
    """近期上下文保留完整轮次且嵌套分叉冻结所选材料"""
    store = AgentDataStore()
    turns = {i: _turn(store, i, background=i in {2, 6, 7, 9, 11}) for i in range(1, 13)}
    selected = store.agent_context_snapshot(SOURCE, recent_turns=recent)["segments"]
    expected = [ref for i in range(first, 13) for ref in turns[i]]
    assert [item["segment_id"] for item in selected] == expected
    store.fork_agent(source_agent_id=SOURCE, target_agent_id="resident_window_child",
                     actor_ref="test-actor", segment_ids=expected)
    _turn(store, 13, background=True)
    frozen = store.agent_context_snapshot("resident_window_child")
    store.fork_agent(source_agent_id="resident_window_child", target_agent_id="resident_window_nested",
                     actor_ref="test-actor", segment_ids=[item["segment_id"] for item in frozen["segments"]])
    assert [item["segment_id"] for item in store.agent_context_snapshot("resident_window_nested")["segments"]] == expected
    # A selected occurrence must still carry the same immutable body and identity.
    assert frozen["segments"] == selected


@pytest.mark.parametrize("recorded", [True, False])
def test_message_cutoff_includes_background_and_response_without_requiring_provider(recorded):
    """消息截断包含背景信息与回复且不要求 Provider 记录"""
    store = AgentDataStore()
    first = _turn(store, 1, recorded=recorded)
    second = _turn(store, 2, recorded=recorded, background=True)
    _turn(store, 3, recorded=recorded)
    snapshot = store.agent_context_snapshot(SOURCE, through_message_id="question-2")
    assert [item["segment_id"] for item in snapshot["segments"]] == first + second


def test_unrecorded_input_is_still_a_turn_and_reminder_text_does_not_classify_it():
    """未记录的独立提醒输入仍按一轮计入上下文"""
    store = AgentDataStore()
    _turn(store, 1)
    independent = _message(store, "standalone-reminder", author_id="team-facade",
                           business_ref="duty/work-summary-reminder")
    independent_reply = _message(store, "standalone-reply", "agent")
    last = _turn(store, 2)
    selected = store.agent_context_snapshot(SOURCE, recent_turns=2)["segments"]
    assert [item["segment_id"] for item in selected] == [independent, independent_reply] + last
    assert [item["segment_id"] for item in store.agent_context_snapshot(
        SOURCE, through_message_id="standalone-reminder")["segments"]][-2:] == [independent, independent_reply]


def test_attached_info_does_not_split_recorded_appended_inputs():
    """附带 info 不拆分同轮追加输入和回复"""
    store = AgentDataStore()
    first = _turn(store, 1, background=True)
    appended = store.put_segment(segment_type="harness", content="same turn appended input",
        execution_id="execution-window-1", created_by_agent_id=SOURCE,
        metadata={"kind": "effective_provider_input", "role": "user", "message_id": "append-1"})
    store.append_agent_segments(SOURCE, [appended], actor_ref="test-actor")
    first += [appended, _message(store, "appended-reply", "agent")]
    last = _turn(store, 2)
    snapshot = store.agent_context_snapshot(SOURCE, recent_turns=2)
    assert [item["segment_id"] for item in snapshot["segments"]] == first + last
    assert [item["segment_id"] for item in store.agent_context_snapshot(
        SOURCE, through_message_id="question-1")["segments"]] == first


def test_delivery_uses_receipts_and_native_turns_within_the_effective_window():
    store = AgentDataStore()
    session = store.ensure_provider_session(provider="codex", backend_route_id="zero",
        backend_session_id="delivery-window", agent_id=SOURCE, actor_ref="test-actor")
    ref = session["provider_session_ref"]
    binding = "resident:" + SOURCE
    store.bind_provider_session(binding, ref)

    def append(kind, content, *, metadata=None, execution_id=""):
        sid = store.put_segment(segment_type=kind, content=content, metadata=metadata,
                                content_type="text/plain" if isinstance(content, str) else "application/json",
                                execution_id=execution_id, created_by_agent_id=SOURCE)
        store.append_agent_segments(SOURCE, [sid], actor_ref="test-actor")
        return sid

    def ids(binding_id=""):
        return [s["segment_id"] for s in store.delivery_context_snapshot(
            SOURCE, message_id="", binding_id=binding_id)["segments"]]

    append("input", "before compaction")
    summary = append("compaction", {"replacement_history": ["summary"]})
    media = {"type": "input_image", "image_url": "data:image/png;base64,YWJj"}
    picture = append("input", media, metadata={"kind": "provider_injected"})
    logic = append("model", {"encrypted_content": "native-reasoning"})
    store.record_context_received(SOURCE, ref, [summary], actor_ref="test-actor")
    store.record_context_received(SOURCE, ref, [logic], actor_ref="test-actor")
    store.begin_execution(execution_id="native", agent_id=SOURCE,
                          actor_ref="test-actor", provider="codex", started_at=AT)
    store.bind_execution_provider_session("native", provider_session_ref=ref)
    native = append("model", "native response", execution_id="native")
    startup = append("input", "developer instructions", metadata={"kind": "system_instructions"})
    assert ids() == [summary, picture, logic, native, startup]
    assert ids(binding) == [startup]
    store.record_context_received(SOURCE, ref, [picture], actor_ref="test-actor")
    assert ids(binding) == [startup]
    delta = store.delivery_context_snapshot(SOURCE, message_id="", binding_id=binding)["segments"]
    assert [s["content"] for s in delta] == ["developer instructions"]
    assert store.agent_context_snapshot(SOURCE)["segments"][1]["content"] == media
    assert ids("unknown-binding") == ids()

    store.record_context_received(SOURCE, ref, [summary, picture, logic], actor_ref="test-actor")
    compacted = append("compaction", "native summary", execution_id="native")
    store.place_in_trace_before(SOURCE, compacted, [logic], actor_ref="test-actor")
    assert ids(binding) == [startup]
    store.record_context_received(SOURCE, ref, [startup], actor_ref="test-actor")
    new = append("input", "after compaction")
    assert ids() == [compacted, logic, native, startup, new]
    assert ids(binding) == [new]
    store.reset_provider_context(SOURCE, reset_at=AT, actor_ref="test-actor")
    assert ids() == ids(binding) == []


def test_cutoff_scans_multiple_pages_without_losing_the_carrier_reply():
    """消息截断跨页扫描且保留载体输入的完整回复"""
    store = AgentDataStore()
    refs = _turn(store, 1)
    for i in range(257):
        refs.append(_message(store, f"held-{i}", released_with="question-1"))
    refs.append(_message(store, "delayed-reply", "agent"))
    _turn(store, 2)
    assert [item["segment_id"] for item in store.agent_context_snapshot(
        SOURCE, through_message_id="question-1")["segments"]] == refs
