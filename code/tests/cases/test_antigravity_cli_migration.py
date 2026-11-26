"""Exercise the production registry and both sides of a Resident session reset."""

import pytest

from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.society_service import resident_execution_domain as residents
from tests.cases.test_antigravity import agent, context, invocation, requests


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
    result = backend.execute(call, context()[0])
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
    call = invocation(agent, sid="bound")
    call.request.metadata = {"antigravity_session_reset": {
        "session_id": "previous", "route": "one",
    }}
    with pytest.raises(ValueError, match="binding changed"):
        AGENT_PROVIDERS["antigravity"].build_backend().execute(call, context()[0])
    assert not (agent / "requests.jsonl").exists()
