"""子 trace 的同名工具回显不能成为自己的父级。

:module: execution.persistence
:design: decision_b1ab45c4b3ec
"""
from __future__ import annotations

import pytest

from tests.cases.test_subagent_trace import _turn


def _item(item_type, item_id, *, method="item/completed", **content):
    return {"method": method, "params": {
        "turnId": "projection-turn", "item": {
            "type": item_type, "id": item_id, **content,
        },
    }}


def _observe(projector, request, event, *, trace=None, parent_call=None, **ancestry):
    if trace is not None:
        event = {"method": "subagent/event", "params": {
            "traceKey": trace,
            "parentToolUseId": parent_call if parent_call is not None else trace,
            **ancestry, "event": event,
        }}
    projector.observe(
        provider="claude", backend_route_id="one", backend_session_id="session-1",
        execution_id=request.execution_id, event=event,
    )


def _start_call(projector, request, call_id, **where):
    _observe(projector, request, _item(
        "toolCall", call_id, method="item/started", name="Agent",
        input={"description": "inspect child"},
    ), **where)


def test_child_tool_echo_does_not_replace_known_root_with_itself():
    """已知 root 下的子事件回显父调用 ID，随后内容仍进入原子 trace。

    :design-point:
    """
    store, projector, request, session_ref = _turn()
    call = "toolu-child"
    _start_call(projector, request, call)
    _start_call(projector, request, call, trace=call)
    child_id = store.subagent_id(request.agent_id, call)
    before = store.agent(child_id)
    assert before["parent_agent_id"] == request.agent_id
    root_ids = store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))
    context_ids = store.session_context_ids(session_ref)

    _observe(projector, request, _item(
        "agentMessage", "child-result", text="child-only answer",
    ), trace=call)

    after = store.agent(child_id)
    assert after["parent_agent_id"] == request.agent_id
    assert after["trace_sequence_id"] == before["trace_sequence_id"]
    child_ids = store.sequence_segment_ids(after["trace_sequence_id"])
    assert len(child_ids) == 1
    assert store.segment(child_ids[0])["content"] == "child-only answer"
    assert store.sequence_segment_ids(store.trace_sequence_id(request.agent_id)) == root_ids
    assert store.session_context_ids(session_ref) == context_ids


def test_known_root_ownership_survives_another_trace_with_the_same_tool_id():
    """root 调用已被观察时，其他子 trace 的同名工具不能改写归属。

    :design-point:
    """
    store, projector, request, _ = _turn()
    _start_call(projector, request, "spawn-C")
    _start_call(projector, request, "spawn-C", trace="B", ancestorTraceKeys=[])
    _observe(projector, request, _item(
        "agentMessage", "first", text="C reply",
    ), trace="C", parent_call="spawn-C", ancestorTraceKeys=[])
    child_id = store.subagent_id(request.agent_id, "C")
    before = store.agent(child_id)

    _observe(projector, request, _item(
        "agentMessage", "second", text="C continues",
    ), trace="C", parent_call="spawn-C")

    after = store.agent(child_id)
    assert after["parent_agent_id"] == request.agent_id
    assert after["trace_sequence_id"] == before["trace_sequence_id"]
    assert len(store.sequence_segment_ids(after["trace_sequence_id"])) == 2


def test_known_nested_parent_survives_child_echo_before_parent_content():
    """孙级内容先到且回显工具 ID，稍后父级内容到达时仍保持原嵌套边。

    :design-point:
    """
    store, projector, request, session_ref = _turn()
    _start_call(projector, request, "spawn-C", trace="C", parent_call="spawn-C",
                parentTraceKey="B")
    child_id = store.subagent_id(request.agent_id, "C")
    parent_id = store.subagent_id(request.agent_id, "B")
    before = store.agent(child_id)
    assert before["parent_agent_id"] == parent_id
    assert store.agent(parent_id)["parent_agent_id"] is None

    _start_call(projector, request, "spawn-C", trace="B", ancestorTraceKeys=[])
    event = _item("agentMessage", "nested-result", text="nested-only answer")
    for _ in range(2):
        _observe(projector, request, event, trace="C", parent_call="spawn-C")

    after = store.agent(child_id)
    assert after["parent_agent_id"] == parent_id
    assert after["trace_sequence_id"] == before["trace_sequence_id"]
    assert store.agent(parent_id)["parent_agent_id"] == request.agent_id
    child_ids = store.sequence_segment_ids(after["trace_sequence_id"])
    assert len(child_ids) == 1
    assert not set(child_ids) & set(store.session_context_ids(session_ref))


@pytest.mark.parametrize("parent", ["root", "nested"])
def test_self_echo_stays_unknown_until_external_parent_evidence(parent):
    """只有子 trace 的同名工具时父级保持未知，外部证据到达后补边且重放稳定。

    子输入、工具结果和回复留在子 trace；主会话仍能继续记录自己的工具结果。

    :design-point:
    """
    store, projector, request, session_ref = _turn()
    root_trace = store.trace_sequence_id(request.agent_id)
    root_before = store.sequence_segment_ids(root_trace)
    context_before = store.session_context_ids(session_ref)
    call = "toolu-child"
    _start_call(projector, request, call, trace=call)
    child_id = store.subagent_id(request.agent_id, call)
    before = store.agent(child_id)
    assert before["parent_agent_id"] is None

    _observe(projector, request, _item(
        "toolCall", call, name="Agent", output="child tool result", status="completed",
    ), trace=call)
    _observe(projector, request, _item(
        "userMessage", "child-input", text="child-only task",
    ), trace=call)
    assert store.agent(child_id)["parent_agent_id"] is None

    if parent == "root":
        _start_call(projector, request, call)
        expected_parent = request.agent_id
    else:
        _start_call(projector, request, call, trace="B", ancestorTraceKeys=[])
        expected_parent = store.subagent_id(request.agent_id, "B")
    reply = _item("agentMessage", "child-result", text="child-only answer")
    for _ in range(2):
        _observe(projector, request, reply, trace=call)
    # A reconnect can omit the parent call without erasing the proven edge.
    _observe(projector, request, reply, trace=call, parent_call="")

    after = store.agent(child_id)
    assert after["parent_agent_id"] == expected_parent
    assert after["trace_sequence_id"] == before["trace_sequence_id"]
    child_ids = store.sequence_segment_ids(after["trace_sequence_id"])
    assert len(child_ids) == 3
    segments = [store.segment(segment_id) for segment_id in child_ids]
    assert {row["segment_type"] for row in segments} == {"tool", "input", "model"}
    assert store.sequence_segment_ids(root_trace) == root_before
    assert store.session_context_ids(session_ref) == context_before

    # Root model messages enter the trace through the ordered Conversation
    # consumer. A root tool completion exercises this projector directly.
    _observe(projector, request, _item(
        "toolCall", "root-result", name="Read", output="root-only result", status="completed",
    ))
    root_after = store.sequence_segment_ids(root_trace)
    assert len(root_after) == len(root_before) + 1
    assert store.segment(root_after[-1])["content"]["result"] == {"output": "root-only result"}
    assert not set(child_ids) & set(root_after)
    assert not set(child_ids) & set(store.session_context_ids(session_ref))


@pytest.mark.parametrize("ancestry", [
    {"parentTraceKey": "C"}, {"ancestorTraceKeys": ["C"]},
])
def test_explicit_self_parent_is_still_rejected(ancestry):
    """明确上报的自环仍由 AgentData 合同拒绝，不因回显修复而被吞掉。

    :design-point:
    """
    store, projector, request, _ = _turn()
    event = _item("agentMessage", "before", text="valid child output")
    _observe(projector, request, event, trace="C")
    child_id = store.subagent_id(request.agent_id, "C")
    sequence = store.agent(child_id)["trace_sequence_id"]
    segment_ids = store.sequence_segment_ids(sequence)

    with pytest.raises(ValueError, match="Invalid Agent parent"):
        _observe(projector, request, _item(
            "agentMessage", "invalid", text="invalid output",
        ), trace="C", **ancestry)
    assert store.agent(child_id)["trace_sequence_id"] == sequence
    assert store.sequence_segment_ids(sequence) == segment_ids


def test_explicit_cycle_is_still_rejected_without_persisting_output():
    """已有 C→B 时拒绝补 B→C，失败事件不追加段也不改变父边。

    :design-point:
    """
    store, projector, request, _ = _turn()
    _observe(projector, request, _item(
        "agentMessage", "child-result", text="nested result",
    ), trace="C", parentTraceKey="B")
    parent_id = store.subagent_id(request.agent_id, "B")
    child_id = store.subagent_id(request.agent_id, "C")
    before = store.agent(parent_id)

    with pytest.raises(ValueError, match="cycle"):
        _observe(projector, request, _item(
            "agentMessage", "invalid", text="invalid output",
        ), trace="B", parentTraceKey="C")

    assert store.agent(parent_id)["parent_agent_id"] is None
    assert store.agent(child_id)["parent_agent_id"] == parent_id
    assert store.sequence_segment_ids(before["trace_sequence_id"]) == []
