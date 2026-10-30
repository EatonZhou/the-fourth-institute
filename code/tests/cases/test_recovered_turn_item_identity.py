"""A recovered Execution may ask a native Provider again under the same turn.

The Claude daemon counts text and thinking item IDs per Provider request
(``<turn>:<message>:text:<block>``).  The native adapter adds a request key so
a second request under the same turn cannot reuse the first request's IDs for
different content; AgentData keeps rejecting a real conflicting replay.

:module: execution.persistence
:importance: P1
:design: decision_b1ab45c4b3ec
"""
from __future__ import annotations

import pytest

from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.backend_contracts import BackendInvocation
from management_portal.execution.native_socket_backend import request_scoped_item_event
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.session_manager import RuntimeSession


def _item_event(item: dict) -> dict:
    return {"method": "item/completed", "params": {"turnId": "job_a", "item": item}}


def test_request_key_scopes_counted_item_ids_only() -> None:
    """恢复后的执行不因 Provider 条目 ID 重复而失败

    原生 Provider 按请求计数的文字与思考条目 ID 加上本次请求的标记；Provider 自带的工具 ID 和其他事件保持不变。
    """
    text = _item_event({"id": "job_a:1:text:0", "type": "agentMessage", "text": "A"})
    scoped = request_scoped_item_event(text, "job_a", "k1")
    assert scoped["params"]["item"]["id"] == "job_a:k1:1:text:0"
    assert scoped["params"]["item"]["text"] == "A"
    assert text["params"]["item"]["id"] == "job_a:1:text:0"
    child = {"method": "subagent/event", "params": {"traceKey": "spawn", "event": text}}
    first = request_scoped_item_event(child, "job_a", "k1")
    second = request_scoped_item_event(child, "job_a", "k2")
    assert first["params"]["event"]["params"]["item"]["id"] == "job_a:k1:1:text:0"
    assert second["params"]["event"]["params"]["item"]["id"] == "job_a:k2:1:text:0"
    assert child["params"]["event"] == text

    # Provider-issued tool IDs are already unique and stay unchanged.
    tool = {"method": "item/started", "params": {"item": {"id": "toolu_01", "type": "commandExecution"}}}
    assert request_scoped_item_event(tool, "job_a", "k1") == tool
    turn = {"method": "turn/started", "params": {"turn": {"id": "job_a"}}}
    assert request_scoped_item_event(turn, "job_a", "k1") == turn


def _begin(store: AgentDataStore, agent_id: str) -> tuple[AgentDataProjector, ExecutionRequest]:
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="claude",
        operation="turn",
        agent_id=agent_id,
        correlation_id="job_a",
        session=SessionOptions(
            mode="persistent",
            binding_id=f"resident:{agent_id}",
            backend_route_id="claude_one",
        ),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id="claude_session_recovered",
        backend_route_id="claude_one",
        is_new_backend_session=False,
    )
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    projector.observe_invocation(
        request.execution_id,
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        [],
    )
    return projector, request


def test_second_request_of_a_recovered_turn_records_new_messages() -> None:
    """恢复后的执行不因 Provider 条目 ID 重复而失败

    Worker 换代后同一轮再次请求 Provider 时，新请求的文字与思考各自成为新片段，执行不会因片段冲突失败；同一请求内的冲突重放仍被拒绝。
    """
    store = AgentDataStore()
    agent_id = "resident_recovered_turn"
    projector, request = _begin(store, agent_id)

    def observe(event: dict) -> None:
        projector.observe(
            provider="claude",
            backend_route_id="claude_one",
            backend_session_id="claude_session_recovered",
            execution_id=request.execution_id,
            event=event,
        )
        from management_portal.execution.agent_data_store import stable_segment_id
        from tests.cases.test_agent_data_projection import _deliver_captured_message
        item = event.get("params", {}).get("item", {})
        if item.get("type") == "agentMessage":
            _deliver_captured_message(store, agent_id, stable_segment_id(
                "provider-model", request.execution_id, item["id"], item["phase"],
            ))

    first = {"id": "job_a:1:text:0", "type": "agentMessage", "phase": "commentary",
             "text": "Checking the Codex compaction path first."}
    thinking = {"id": "job_a:1:thinking:0", "type": "thinking", "thinking": "Plan A.", "signature": "s1"}
    for item in (first, thinking):
        observe(request_scoped_item_event(_item_event(item), "job_a", "request-one"))
    # After a Worker handoff the same turn is asked again; the daemon counts
    # items from the start, so the raw IDs repeat with different content.
    for item in (
        {**first, "text": "Resuming: reading the test results."},
        {**thinking, "thinking": "Plan after recovery.", "signature": "s2"},
    ):
        observe(request_scoped_item_event(_item_event(item), "job_a", "request-two"))

    contents = [
        store.segment(segment_id)["content"]
        for segment_id in store.sequence_segment_ids(store.trace_sequence_id(agent_id))
        if store.segment(segment_id)["segment_type"] == "model"
    ]
    assert "Checking the Codex compaction path first." in contents
    assert "Resuming: reading the test results." in contents
    assert len([item for item in contents if isinstance(item, dict)]) == 2

    # A real replay with changed content under one request is still rejected.
    with pytest.raises(ValueError, match="immutable content"):
        observe(request_scoped_item_event(
            _item_event({**thinking, "thinking": "changed", "signature": "s3"}),
            "job_a",
            "request-one",
        ))
