"""Provider events are persisted during a turn, with no second ingestion source.

:module: execution.persistence
:importance: P1
:design: decision_b1ab45c4b3ec
"""
import pytest
from management_portal.execution.agent_data_store import SegmentIdentityConflict
from tests.cases.test_agent_data_projection import _deliver_captured_message

from tests.cases.live_trace_support import AGENT, _turn
from management_portal.execution.control_messages import HARNESS_CONTROL_EVENT, report_harness_control
from management_portal.execution.recovery_messages import RECOVERY_INPUT_EVENT, recovery_input_message, stamp_recovery_input



def _steps(store, agent_id, tool_use_id):
    """The steps a subagent took: the Segments of its own trace."""
    return store.read_sequence(store.trace_sequence_id(store.subagent_id(agent_id, tool_use_id)))["segments"]

def _emit(projector, request, item, *, method="item/completed", child=""):
    event = {"method": method, "params": {"turnId": "turn", "item": item}}
    if child:
        event = {"method": "subagent/event", "params": {
            "traceKey": child, "parentToolUseId": "spawn", "event": event,
        }}
    projector.observe(provider=request.provider, backend_route_id="one", backend_session_id="thread-native",
                      execution_id=request.execution_id, event=event)


def _captured_model_ids(store, request):
    with store.database.read() as connection:
        return {str(row["segment_id"]) for row in connection.execute(
            "SELECT segment_id FROM segments WHERE execution_id=%s AND segment_type='model'",
            (request.execution_id,),
        ).fetchall()}


@pytest.mark.parametrize("kind", ["message", "reasoning", "tool"])
def test_anonymous_equal_content_is_not_an_event_identity(kind):
    """无后端 ID 的相同正文保留不同事件

    无后端事件 ID 的消息、思考和工具观察分别分配身份，不能按正文合并。
    """
    store, projector, request, _ = _turn("codex")
    item = {
        "message": {"type": "agentMessage", "text": "same"},
        "reasoning": {"type": "reasoning", "summary": ["same"]},
        "tool": {"type": "toolCall", "name": "Read", "input": "same", "output": "same", "status": "completed"},
    }[kind]
    before = set(store.sequence_segment_ids(store.trace_sequence_id(request.agent_id)))
    _emit(projector, request, item)
    _emit(projector, request, item)
    if kind == "message":
        assert set(store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))) == before
        rows = [store.segment(segment_id) for segment_id in _captured_model_ids(store, request)]
    else:
        rows = [r for r in store.read_sequence(store.trace_sequence_id(request.agent_id))["segments"] if r["segment_id"] not in before]
    assert len(rows) == 2
    assert rows[0]["segment_id"] != rows[1]["segment_id"]
    if kind != "tool":
        assert rows[0]["content_ref"] == rows[1]["content_ref"]


@pytest.mark.parametrize("kind", ["message", "reasoning", "tool"])
def test_conflicting_event_identity_is_rejected_in_the_same_running_turn(kind):
    """运行中相同 ID 内容冲突被拒绝

    重复事件校验正文与元数据，内存缓存不能跳过不可变约束。
    """
    store, projector, request, _ = _turn("codex")
    item, field = {
        "message": ({"type": "agentMessage", "text": "same"}, "text"),
        "reasoning": ({"type": "reasoning", "summary": ["same"]}, "summary"),
        "tool": ({"type": "toolCall", "name": "Read", "input": "same", "output": "same", "status": "completed"}, "output"),
    }[kind]
    item["id"] = "stable-event"
    _emit(projector, request, item)
    before = store.read_sequence(store.trace_sequence_id(request.agent_id))["segments"]
    with pytest.raises(SegmentIdentityConflict):
        _emit(projector, request, {**item, field: ["changed"] if kind == "reasoning" else "changed"})
    assert store.read_sequence(store.trace_sequence_id(request.agent_id))["segments"] == before


@pytest.mark.parametrize("provider", ["codex", "claude", "kimi", "dsh"])
def test_completed_events_persist_before_turn_end_and_equal_text_keeps_occurrences(provider):
    """运行期间单入口采集与子轨迹隔离"""
    store, projector, request, _ = _turn(provider)
    for item_id in ("first", "second", "first"):
        before = _captured_model_ids(store, request)
        _emit(projector, request, {"id": item_id, "type": "agentMessage", "text": "checked", "phase": "commentary"})
        for segment_id in _captured_model_ids(store, request) - before:
            _deliver_captured_message(store, request.agent_id, segment_id)
    rows = [r for r in store.read_sequence(store.trace_sequence_id(request.agent_id))["segments"] if r["segment_type"] == "model"]
    assert len(rows) == 2
    assert rows[0]["segment_id"] != rows[1]["segment_id"]
    assert rows[0]["content_ref"] == rows[1]["content_ref"]
    assert all("native_id" not in r["metadata"] for r in rows)
    assert store.execution(request.execution_id)["status"] == "running"


def test_progress_and_unfinished_tools_are_not_fabricated_at_turn_end():
    """运行期间单入口采集与子轨迹隔离"""
    store, projector, request, _ = _turn("kimi")
    projector.observe(provider="kimi", backend_route_id="", backend_session_id="thread-native",
                      execution_id=request.execution_id, event={"type": "event_msg", "payload": {
                          "type": "agent_message", "update_id": "snapshot", "message": "partial", "replace_previous": True,
                      }})
    _emit(projector, request, {"id": "tool", "type": "toolCall", "name": "Read", "input": "a.py"}, method="item/started")
    _emit(projector, request, {"id": "tool", "type": "toolCall", "status": "unknown"})
    final_segment_id = projector.complete(execution_id=request.execution_id, backend_session_id="thread-native", backend_route_id="",
                                          turn_id="turn", response="done")
    _deliver_captured_message(store, request.agent_id, final_segment_id)
    rows = store.read_sequence(store.trace_sequence_id(request.agent_id))["segments"]
    assert not any(r["segment_type"] == "tool" for r in rows)
    assert [r["content"] for r in rows if r["segment_type"] == "model"] == ["done"]


@pytest.mark.parametrize("provider", ["codex", "claude", "kimi"])
def test_child_events_share_writer_but_never_enter_parent_context(provider):
    """运行期间单入口采集与子轨迹隔离"""
    store, projector, request, session_ref = _turn(provider)
    for child in ("spawn/a", "spawn/b"):
        _emit(projector, request, {"id": "input", "type": "userMessage", "text": "inspect"}, child=child)
        _emit(projector, request, {"id": "text", "type": "agentMessage", "text": "child output"}, child=child)
        _emit(projector, request, {"id": "reason", "type": "reasoning", "summary": ["child reasoning"]}, child=child)
        _emit(projector, request, {"id": "tool", "type": "toolCall", "name": "Read", "input": "a.py"}, method="item/started", child=child)
        _emit(projector, request, {"id": "tool", "type": "toolCall", "output": "file", "status": "completed"}, child=child)
    a, b = (_steps(store, request.agent_id, child) for child in ("spawn/a", "spawn/b"))
    assert [r["segment_type"] for r in a] == ["input", "model", "model", "tool"]
    assert len(b) == len(a)
    ids_a, ids_b = ({r["segment_id"] for r in rows} for rows in (a, b))
    assert ids_a.isdisjoint(ids_b)
    assert (ids_a | ids_b).isdisjoint(store.session_context_ids(session_ref))
    assert not projector._active[request.execution_id].final_segment_id
    assert all("native_id" not in r["metadata"] for r in a + b)


def test_sdk_child_messages_are_routed_live_without_file_reads():
    """运行期间单入口采集与子轨迹隔离"""
    from tests.cases.claude_sdk_support import load_claude_module
    daemon = load_claude_module("claude_code_daemon")
    AssistantMessage, TextBlock, ThinkingBlock, ToolUseBlock, UserMessage, ToolResultBlock = (
        getattr(daemon, name) for name in ("AssistantMessage", "TextBlock", "ThinkingBlock", "ToolUseBlock", "UserMessage", "ToolResultBlock")
    )
    _subagent_events = daemon._subagent_events

    store, projector, request, _ = _turn("claude")
    messages = [
        AssistantMessage(model="test", content=[TextBlock(text="child"), ThinkingBlock(thinking="thinking", signature="sig"),
                                                ToolUseBlock(id="call", name="Read", input={"path": "a"})], parent_tool_use_id="spawn"),
        UserMessage(content=[ToolResultBlock(tool_use_id="call", content="ok")], parent_tool_use_id="spawn"),
    ]
    for index, message in enumerate(messages):
        for event in _subagent_events(message, "turn", index):
            projector.observe(provider="claude", backend_route_id="one", backend_session_id="thread-native",
                              execution_id=request.execution_id, event=event)
    steps = _steps(store, request.agent_id, "spawn")
    assert [r["segment_type"] for r in steps] == ["model", "model", "tool"]
    assert steps[-1]["content"]["result"]["output"] == "ok"
    assert not any(r["metadata"].get("subagent") for r in store.read_sequence(store.trace_sequence_id(request.agent_id))["segments"])


def test_harness_reminders_and_recovery_input_are_what_the_session_received() -> None:
    """Harness 提醒和恢复输入算会话收到的内容

    一轮进行中送达的 Harness 提醒记为轨迹中的一段，恢复执行发出的输入（continue）也计入会话 context；Provider 记录中同样的输入不再重复记录。Worker 交接后检查点从 1 重新编号时，同一 control_id 的不同正文各记一段，同一提醒重复送达只记一次。
    """
    store, projector, request, session_ref = _turn()
    events: list[tuple[str, dict]] = []

    class _Context:
        def emit(self, event_type: str, details: dict) -> None:
            events.append((event_type, details))
            projector.observe_runtime_event(request.execution_id, event_type, details)

    report_harness_control(_Context(), {
        "control_id": "native-checkpoint-1", "reason": "tool_call_checkpoint",
        "input": [{"type": "text", "text": "<agent-harness-runtime-checkpoint>check</agent-harness-runtime-checkpoint>"}],
        "metadata": {"kind": "safety"},
    })
    assert [event for event, _ in events] == [HARNESS_CONTROL_EVENT]
    details = stamp_recovery_input(request.execution_id, {
        "provider": "claude", "reason": "worker_handover", "prompt": "continue",
        "input_delivery": "continued", "turn_id": "turn-1",
    })
    projector.observe_runtime_event(request.execution_id, RECOVERY_INPUT_EVENT, details)
    recovery_message = recovery_input_message(details, execution_id=request.execution_id)
    assert recovery_message is not None
    store.project_chat_message(AGENT, recovery_message)

    context = store.read_sequence(store.trace_sequence_id(AGENT))["segments"]
    control = next(item for item in context if item["metadata"].get("kind") == "harness_control")
    recovery = next(item for item in context if (item["metadata"].get("message") or {}).get("harness_input"))
    held = store.session_context_ids(session_ref)
    assert control["segment_id"] in held and recovery["segment_id"] in held

    # After a handover the next Worker's governor numbers its checkpoints from 1 again:
    # the same control id with other text is another reminder, and the same one again is not.
    later = {
        "control_id": "native-checkpoint-1", "reason": "tool_call_checkpoint",
        "input": [{"type": "text", "text": "<agent-harness-runtime-checkpoint>later</agent-harness-runtime-checkpoint>"}],
        "metadata": {"kind": "safety"},
    }
    report_harness_control(_Context(), later)
    report_harness_control(_Context(), later)
    context = store.read_sequence(store.trace_sequence_id(AGENT))["segments"]
    assert [item["content"] for item in context if item["metadata"].get("kind") == "harness_control"] == [
        "<agent-harness-runtime-checkpoint>check</agent-harness-runtime-checkpoint>",
        "<agent-harness-runtime-checkpoint>later</agent-harness-runtime-checkpoint>",
    ]
