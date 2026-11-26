"""Exercise the production registry and both sides of a Resident session reset."""

from dataclasses import replace

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.session_manager import SessionManager

from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.society_service import resident_execution_domain as residents
from tests.cases.test_antigravity import agent, context, invocation, requests


def bound_turn(agent_id, reset):
    manager = SessionManager(agent_data=AgentDataStore())
    binding_id = "resident:" + agent_id
    manager.bind_session(binding_id, provider="antigravity", backend_session_id="previous-session", backend_route_id="one")
    req = ExecutionRequest.new(actor="sample_admin", prompt="First input", service_type="agent_session",
                               provider="antigravity", operation="turn", agent_id=agent_id,
                               session=SessionOptions(mode="persistent", binding_id=binding_id, backend_route_id="one"),
                               metadata={"antigravity_session_reset": reset})
    return manager, req


@pytest.mark.parametrize("native_database,owner,original_portal_only", [
    (False, True, False), (True, False, False), (True, True, False), (True, True, True), (True, False, True),
])
def test_migrate_at_next_input(agent, monkeypatch, native_database, owner, original_portal_only):
    backend = AGENT_PROVIDERS["antigravity"].build_backend()
    old_id = "previous-session"
    store = agent / "antigravity-acp" / "conversations"
    store.mkdir(parents=True)
    (store / (old_id + ".meta")).touch()
    if native_database:
        (store / (old_id + ".db")).touch()
    if original_portal_only:
        from management_portal.execution.antigravity_cli import policy_marker
        marker = policy_marker(agent, old_id)
        marker.parent.mkdir()
        marker.touch()
    binding = {
        "provider": "antigravity", "backend_route_id": "one", "backend_session_id": old_id,
    }
    monkeypatch.setattr(residents.resident_backend, "session_record", lambda record: binding)
    record = {"resident_id": "resident_111111111111"}
    reset = residents._antigravity_session_reset(record, "one", owner)
    call = invocation(agent, "First input", sid=old_id, owner=owner)
    call.request.metadata = {"antigravity_session_reset": reset}
    manager, req = bound_turn(record["resident_id"], reset)
    call.session = manager.prepare(req)
    projector = AgentDataProjector(manager.agent_data)
    projector.begin(req, call.session)
    projector.observe_invocation(req.execution_id, call)
    result = backend.execute(call, context()[0])
    projector.complete(execution_id=req.execution_id, backend_route_id="one",
                       backend_session_id=result.backend_session_id, turn_id="t", response="done", usage={})
    manager.complete(req, call.session, result.backend_session_id)
    assert manager.prepare(replace(req, metadata={})).backend_session_id == result.backend_session_id
    frames = requests(agent)
    prompt = next(row["params"]["prompt"][0]["text"] for row in frames if row["method"] == "session/prompt")
    assert frames[1]["method"] == "session/new" and result.backend_session_id != old_id
    assert prompt == call.user_prompt
    assert (store / (old_id + ".meta")).exists()
    binding["backend_session_id"] = result.backend_session_id
    (store / (result.backend_session_id + ".db")).touch()
    assert residents._antigravity_session_reset(record, "one", owner) == {}
    backend.execute(invocation(agent, "Second input", sid=result.backend_session_id, owner=owner), context()[0])
    assert [r["method"] for r in requests(agent) if r["method"] in {"session/new", "session/load"}] == [
        "session/new", "session/load"]


def test_refuse_stale_reset_before_any_cli_call(agent):
    manager, req = bound_turn("resident_111111111111", {"session_id": "wrong", "route": "one"})
    with pytest.raises(ValueError, match="binding changed"):
        manager.prepare(req)
