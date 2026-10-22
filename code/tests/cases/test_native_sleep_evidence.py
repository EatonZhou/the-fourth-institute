"""The installed Codex sleep item must reach the same observers as other tools."""
import json
from copy import deepcopy

import pytest

from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.backends import BackendContext
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.native_tool_events import NativeToolEventTranslator, normalize_native_tool_event
from management_portal.execution.session_manager import RuntimeSession
from management_portal.execution import event_projection
from management_portal.society_service.resident_context_evidence import project_context_evidence


def sleep_event(method, item_id="sleep-native-1", duration=25000):
    return {"method": method, "params": {"threadId": "native-thread", "turnId": "native-turn",
        "item": {"type": "sleep", "id": item_id, "durationMs": duration}}}


def test_native_sleep_reaches_canonical_activity_and_harness_without_replay_duplication():
    """原生等待保留 ID、时长和终态，开始事件不造结果，重放不重复写入"""
    store, agent = AgentDataStore(), "resident_native_sleep"
    req = ExecutionRequest.new(actor="test-actor", agent_id=agent, provider="codex",
        service_type="agent_session", operation="turn", prompt="private-prompt",
        session=SessionOptions(mode="persistent", binding_id="resident:" + agent, backend_route_id="zero"))
    session = RuntimeSession(mode="persistent", binding_id=req.session.binding_id,
        backend_session_id="native-thread", backend_route_id="zero", is_new_backend_session=True)
    projector = AgentDataProjector(store)
    projector.begin(req, session)
    activity = []
    context = BackendContext(emit_event=lambda kind, value: activity.append({"type": kind, "details": value}),
        cancellation_requested=lambda: False, set_interrupt_target=lambda *_: None,
        provider_event_observer=lambda provider, thread, route, event: projector.observe(
            provider=provider, backend_session_id=thread, backend_route_id=route,
            execution_id=req.execution_id, event=event))
    started, completed = sleep_event("item/started"), sleep_event("item/completed")
    original = deepcopy(started)
    def send(event):
        context.observe_provider_event(event, provider="codex", backend_session_id="native-thread", backend_route_id="zero")
    send(started)
    before = project_context_evidence(store.context_evidence_snapshot(agent))
    assert before["effective_context"]["tool_observations"]["entries"] == []
    send(completed)
    one = project_context_evidence(store.context_evidence_snapshot(agent))
    send(completed)  # Reconnect replays the same native identity.
    send(sleep_event("item/started", "sleep-native-2"))
    send(sleep_event("item/completed", "sleep-native-2", 1200))
    after = project_context_evidence(store.context_evidence_snapshot(agent))
    tools = after["effective_context"]["tool_observations"]["entries"]
    assert [(r["call_id"], r["tool_name"], r["status"], r["observed_duration_ms"]) for r in tools] == [
        ("sleep-native-1", "clock.sleep", "completed", 25000),
        ("sleep-native-2", "clock.sleep", "completed", 1200)]
    assert tools[0] == one["effective_context"]["tool_observations"]["entries"][0]
    assert tools[0]["execution_id"] == req.execution_id
    assert all(r["started_at"] and r["finished_at"] for r in tools)
    assert after["trace"]["segment_types"]["tool"] == 2
    details = event_projection.runtime_details_from_objects(enumerate(activity, 1))
    assert any(r["kind"] == "tool_call" and r["status"] == "running" for r in details)
    assert any(r["kind"] == "tool_output" and r["status"] == "completed" for r in details)
    translator = NativeToolEventTranslator()
    assert translator.translate(started)[0]["params"]["item"]["tool"] == "clock.sleep"
    assert translator.translate(completed)[0]["params"]["item"]["status"] == "completed"
    assert started == original
    rendered = json.dumps(after)
    assert "private-prompt" not in rendered and '"arguments"' not in rendered


@pytest.mark.parametrize("duration", [-1, True, "25000", None])
def test_malformed_native_sleep_does_not_fabricate_a_completed_call(duration):
    """不完整的等待事件不能伪造成功工具调用"""
    with pytest.raises(ValueError, match="durationMs"):
        normalize_native_tool_event(sleep_event("item/completed", duration=duration))


def test_tool_evidence_is_bounded_and_excludes_private_arguments_and_results():
    """原生审计只暴露有界身份与结果状态，非等待参数及输出保持私有"""
    segments = [{"segment_type": "tool", "segment_id": str(i), "status": "failed",
        "content": {"call_id": str(i), "tool_name": "command", "input": {"command": "private-command"},
                    "result": {"output": "private-output"}}} for i in range(201)]
    result = project_context_evidence({"segments": segments, "trace_counts": {"tool": 201},
                                      "imports": [], "imports_truncated": False})
    tools = result["effective_context"]["tool_observations"]
    assert len(tools["entries"]) == 200 and tools["truncated"]
    assert tools["entries"][0]["status"] == "failed"
    assert "private-" not in json.dumps(result)
