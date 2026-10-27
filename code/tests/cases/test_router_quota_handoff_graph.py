"""A Router input keeps its graph identity across an unstarted session handoff."""
import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import (
    resident_deferred_restart, resident_execution_work, resident_restart_domain,
    trigger_paths,
)
from tests.cases.test_lineage_catalogs import LINEAGE_ID
from tests.cases.test_quota_wait_restart_handoff import RID, MID, execution, resident

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")


@pytest.mark.parametrize("finish", ["completed", "failed", "cancelled"])
def test_router_input_handoff_preserves_graph_until_replacement_finishes(resident, execution, finish):
    store, repo, operations, original, native = resident
    scheduler, _, request, _ = execution
    event = {"id": "event-router-input", "type": "time.reached",
             "occurred_at": "2026-10-19T00:00:00Z", "payload": {}}
    path = trigger_paths.begin_event(LINEAGE_ID, "event-source", event,
                                     cause_kind="input", cause_id=MID)
    trigger_paths.admit_resident(LINEAGE_ID, resident_id=RID, message_id=MID,
                                trigger_path_id=path, source_wire_id="wire-router", event=event)
    trigger_paths.event_settled(path, "event-source", event["id"])
    assert resident_deferred_restart.advance(store.resident_record(RID)) is True
    assert scheduler.snapshot(request.execution_id)["status"] == "cancelled"
    # Internal withdrawal must not settle the graph or hide the pending input.
    assert trigger_paths.settle(path) is False
    pending = trigger_paths.resident_inputs(LINEAGE_ID, resident_id=RID, pending=True)
    assert [row["message_id"] for row in pending] == [MID]
    assert pending[0]["status"] == "queued"
    if finish == "failed":
        native.resume = lambda *a, **k: {"status": "failed", "result": {"status": "failed"},
                                         "error": "native preparation failed"}
    elif finish == "cancelled":
        record = store.resident_record(RID)
        record["session_restart"]["status"] = "cancelling"
        store.put_resident_record(RID, record)
    resident_restart_domain.resume_resident_session_restart(operations[-1]["step"])
    resident_deferred_restart.advance(store.resident_record(RID))
    record = store.resident_record(RID)
    job = resident_execution_work.read(record)
    assert record["session_restart"]["status"] == finish
    assert not record["session_restart"].get("deferred")
    assert job["user_message_id"] == MID
    assert repo.input_state(RID, MID)["state"] == "queued"
    assert job["execution_id"] != request.execution_id
    # Replayed publication retains both audit links without creating another turn.
    trigger_paths.link_input(LINEAGE_ID, RID, MID, job["execution_id"])
    graph = trigger_paths.get(LINEAGE_ID, path)
    assert graph["status"] == "active"
    assert {row["execution_id"] for row in graph["execution_links"]} == {
        request.execution_id, job["execution_id"]}
    AgentDataStore().finish_execution(job["execution_id"], status="completed",
                                      finished_at="2026-10-19T00:30:00Z")
    assert trigger_paths.settle(path) is True
    assert trigger_paths.get(LINEAGE_ID, path)["status"] == "completed"
    assert trigger_paths.resident_inputs(LINEAGE_ID, resident_id=RID, pending=True) == []
