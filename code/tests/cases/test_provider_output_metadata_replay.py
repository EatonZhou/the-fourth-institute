"""
:module: execution.persistence
:importance: P1
:design: decision_b1ab45c4b3ec
"""
from copy import deepcopy

import pytest

from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_segments import SegmentIdentityConflict
from management_portal.execution.agent_data_store import AgentDataStore
from tests.cases.test_agent_data_projection import _invocation, _runtime_session, _turn_request


@pytest.mark.parametrize("kind", ["message", "reasoning"])
@pytest.mark.parametrize("difference", ["none", "content", "metadata"])
def test_live_output_replay_requires_identical_content_and_metadata(kind, difference, monkeypatch):
    """输出重放严格校验不可变内容

    相同 ID 的正文和元数据必须一致；不再接受 native_id 元数据兼容分支。
    """
    store = AgentDataStore()
    request = _turn_request("resident_metadata_replay")
    session = _runtime_session(request, "metadata-thread")
    item = {"id": "stable-item", "type": "agentMessage", "phase": "commentary", "text": "checked"}
    if kind == "reasoning":
        item = {"id": "stable-item", "type": "reasoning", "summary": ["checked"]}
    event = {"method": "item/completed", "params": {"turnId": "stable-turn", "item": item}}
    original_put = store.put_segment

    def legacy_put(**fields):
        fields = deepcopy(fields)
        if difference == "content":
            fields["content"] = "different content"
        elif difference == "metadata":
            fields["metadata"]["title"] = "different title"
        return original_put(**fields)

    def start():
        projector = AgentDataProjector(store)
        projector.begin(request, session, created_at="2026-10-07T07:49:48.595Z")
        projector.observe_invocation(request.execution_id, _invocation(request, session), [])
        return projector

    def observe(projector):
        projector.observe(provider="codex", backend_route_id="zero", backend_session_id="metadata-thread",
                          execution_id=request.execution_id, event=event)

    first = start()
    monkeypatch.setattr(store, "put_segment", legacy_put)
    observe(first)
    monkeypatch.setattr(store, "put_segment", original_put)
    before = store.read_sequence(store.trace_sequence_id(request.agent_id))["segments"]
    if kind == "message":
        from management_portal.execution.agent_data_store import stable_segment_id
        model = store.segment(stable_segment_id("provider-model", request.execution_id, "stable-item", "commentary"))
    else:
        model, = [row for row in before if row["segment_type"] == "model"]
    original = store.segment(model["segment_id"])
    resumed = start()
    if difference == "none":
        observe(resumed)
        assert store.read_sequence(store.trace_sequence_id(request.agent_id))["segments"] == before
        assert store.segment(model["segment_id"]) == original
    else:
        with pytest.raises(SegmentIdentityConflict):
            observe(resumed)
        assert store.segment(model["segment_id"]) == original
