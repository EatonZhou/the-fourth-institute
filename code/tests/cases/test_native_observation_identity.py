"""Native audit facts survive replay without exposing command or message bodies."""
import hashlib
import json

import pytest

from management_portal.execution import agent_data_projection as projection_module
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.session_manager import RuntimeSession
from management_portal.society_service.resident_context_evidence import project_context_evidence


def capture():
    store, agent = AgentDataStore(), "agent_native_audit"
    request = ExecutionRequest.new(actor="test-actor", agent_id=agent, provider="codex",
        service_type="agent_session", operation="turn", prompt="private-prompt",
        session=SessionOptions(mode="persistent", binding_id=agent, backend_route_id="one"))
    session = RuntimeSession(mode="persistent", binding_id=agent, backend_route_id="one",
                             backend_session_id="native-thread", is_new_backend_session=True)
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    def send(item):
        projector.observe(provider="codex", backend_session_id="native-thread", backend_route_id="one",
            execution_id=request.execution_id, event={"method": "item/completed", "params": {
                "threadId": "native-thread", "turnId": "native-turn", "startedAtMs": 1791000000000,
                "completedAtMs": 1791000025000, "item": item}})
    def evidence():
        return project_context_evidence(store.context_evidence_snapshot(agent))
    return store, request, projector, send, evidence


def model(item_id):
    return {"type": "agentMessage", "id": item_id, "text": "private-identical-message", "phase": "commentary"}


def command(item_id, **fields):
    return {"type": "commandExecution", "id": item_id, "command": "sleep 25", "status": "completed",
            "aggregatedOutput": "private-command-output", **fields}


def test_native_model_identity_preserves_replay_and_equal_text_outputs():
    """原生消息重放保持同一身份，不同原生 ID 的同文消息仍分别保留"""
    store, request, projector, send, evidence = capture()
    send(model("native-message-one"))
    before = evidence()
    send(model("native-message-one"))
    send(model("native-message-two"))
    rows = [r for r in evidence()["trace"]["positions"]["entries"] if r["segment_type"] == "model"]
    assert [r["provider_item_id"] for r in rows] == ["native-message-one", "native-message-two"]
    assert rows[0] == next(r for r in before["trace"]["positions"]["entries"] if r["segment_type"] == "model")
    assert rows[0]["message_id"] != rows[1]["message_id"]
    assert rows[0]["content_sha256"] == rows[1]["content_sha256"]
    assert all(r["execution_id"] == request.execution_id for r in rows)
    projector.complete(execution_id=request.execution_id, backend_session_id="native-thread",
        backend_route_id="one", turn_id="native-turn", response="private-fallback", usage={})
    fallback = [r for r in evidence()["trace"]["positions"]["entries"] if r["segment_type"] == "model"][-1]
    assert "provider_item_id" not in fallback  # Synthetic final is not a native item.
    assert "private-" not in json.dumps(evidence())


def test_command_audit_preserves_exact_digest_exit_code_and_distinct_calls():
    """原生命令证据保留实际退出码与精确命令摘要，不把重放或同文混为重复调用"""
    _, _, _, send, evidence = capture()
    send(command("native-command-one", exitCode=0))
    before = evidence()["effective_context"]["tool_observations"]["entries"]
    send(command("native-command-one", exitCode=0))
    send(command("native-command-two", exitCode=7, status="failed"))
    tools = evidence()["effective_context"]["tool_observations"]["entries"]
    assert len(tools) == 2 and tools[0] == before[0]
    assert [r["observed_exit_code"] for r in tools] == [0, 7]
    assert [r["provider_item_id"] for r in tools] == ["native-command-one", "native-command-two"]
    assert all(r["call_id"] == r["provider_item_id"] for r in tools)
    assert all(r["command_sha256"] == hashlib.sha256(b"sleep 25").hexdigest() for r in tools)
    rendered = json.dumps(evidence())
    assert "sleep 25" not in rendered and "private-" not in rendered


@pytest.mark.parametrize("exit_code", [None, True, "0", 0.0])
def test_completed_command_without_typed_native_exit_is_unknown(exit_code):
    """完成状态不代替原生退出码，缺失或格式错误仍明确缺证"""
    _, _, _, send, evidence = capture()
    send(command("native-unknown-exit", exitCode=exit_code))
    row = evidence()["effective_context"]["tool_observations"]["entries"][0]
    assert row["status"] == "completed" and "observed_exit_code" not in row


def test_replaying_legacy_segments_does_not_backfill_native_evidence(monkeypatch):
    """旧片段重放不改写不可变元数据，也不追补以前未记录的原生事实"""
    _, _, _, send, evidence = capture()
    with monkeypatch.context() as old_writer:
        old_writer.setattr(projection_module, "_provider_observation_metadata", lambda *args: {})
        send(model("native-old-message"))
        send(command("native-old-command", exitCode=0))
    before = evidence()
    send(model("native-old-message"))
    send(command("native-old-command", exitCode=0))
    after = evidence()
    assert after["trace"]["positions"]["entries"] == before["trace"]["positions"]["entries"]
    assert after["effective_context"] == before["effective_context"]
    assert all("provider_item_id" not in r for r in after["trace"]["positions"]["entries"])
    assert "observed_exit_code" not in after["effective_context"]["tool_observations"]["entries"][0]
