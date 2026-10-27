"""Family task identities retain their first accepted envelope across callers.

:module: lineage.team
:design: decision_44cf587c1721
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from management_portal.society_service import (
    resident_conversation_stream,
    resident_execution_work,
    resident_input_queue_domain,
    resident_intake_domain,
)
from management_portal.society_service.resident_intake_domain import deliver_resident_message_content
from management_portal.society_service.resident_record_domain import find_resident
from tests.cases.isa_test_support import ACTOR, ok, run
from tests.cases.test_family_tasks import (  # noqa: F401
    charters, configured_family, future_creation_store, lineage,
)


@pytest.fixture
def real_family(configured_family, monkeypatch):
    """Keep the isolated PG/Family fixture and restore real input admission."""
    monkeypatch.setattr(
        resident_intake_domain, "deliver_resident_message_content",
        deliver_resident_message_content,
    )
    return configured_family


def _snapshot(family, message_id):
    repository = resident_conversation_stream.get_repository()
    target = family["steward"]
    return copy.deepcopy({
        "message": repository.message(target, message_id),
        "state": repository.input_state(target, message_id),
        "work": resident_execution_work.read(find_resident(target)),
        "messages": repository.read_messages(target),
        "accepted": repository.state_store.mailbox_entries([target], message_ids=[message_id]),
    })


@pytest.mark.parametrize("terminal", [None, "completed", "cancelled", "failed"])
def test_summary_replay_by_different_actor_keeps_first_envelope(real_family, terminal):
    _, block = real_family
    family = block["family"]
    message_id = "msg_10000000000000000000000000000001"
    arguments = {"resident_id": family["facade"], "trigger": "summary", "message_id": message_id}
    first = ok("DispatchFamilyTask", arguments, actor=family["steward"])
    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(family["steward"], message_id)["state"] == "queued"
    if terminal:
        # Model/Worker completion is outside this test: retain real accepted
        # history, archive the work head and persist its real terminal state.
        repository.append_message(family["steward"], repository.message(family["steward"], message_id))
        repository.set_input_state(family["steward"], message_id, terminal)
        resident_execution_work.clear(find_resident(family["steward"]))
    before = _snapshot(family, message_id)
    assert before["message"]["author_id"] == family["facade"]
    assert before["message"]["origin"]["initiated_by"]["id"] == family["steward"]

    repeated = ok("DispatchFamilyTask", arguments, actor=ACTOR)

    assert first["message_id"] == repeated["message_id"] == message_id
    assert _snapshot(family, message_id) == before
    assert len(before["accepted"]) + sum(m["message_id"] == message_id for m in before["messages"]) == 1


@pytest.mark.parametrize("changed", [
    {"trigger": "review"},
    {"facts": {"purpose": "changed"}},
    {"source_message_id": "msg_20000000000000000000000000000099"},
    {"event": {"id": "event_changed", "type": "timer.tick"}},
])
def test_same_message_id_rejects_different_family_request(real_family, changed):
    _, block = real_family
    family = block["family"]
    message_id = "msg_20000000000000000000000000000001"
    arguments = {
        "resident_id": family["facade"], "trigger": "summary", "message_id": message_id,
        "facts": {"purpose": "summary"},
        "source_message_id": "msg_20000000000000000000000000000002",
        "event": {"id": "event_original", "type": "timer.tick"},
    }
    ok("DispatchFamilyTask", arguments, actor=family["steward"])
    before = _snapshot(family, message_id)

    rejected = run("DispatchFamilyTask", {**arguments, **changed}, actor=family["steward"])

    assert rejected.status == "failed"
    assert rejected.error["commit_state"] == "not_committed"
    assert _snapshot(family, message_id) == before


def test_concurrent_callers_accept_one_family_input(real_family):
    _, block = real_family
    family = block["family"]
    message_id = "msg_30000000000000000000000000000001"
    arguments = {"resident_id": family["facade"], "trigger": "summary", "message_id": message_id}
    barrier = Barrier(2)

    def dispatch(actor):
        barrier.wait(timeout=10)
        return ok("DispatchFamilyTask", arguments, actor=actor)

    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(dispatch, actor) for actor in (family["steward"], ACTOR)]
        receipts = [call.result(timeout=30) for call in calls]

    assert [receipt["message_id"] for receipt in receipts] == [message_id, message_id]
    snapshot = _snapshot(family, message_id)
    assert snapshot["state"]["state"] == "queued"
    assert snapshot["message"]["origin"]["initiated_by"]["id"] in {family["steward"], ACTOR}
    assert len(snapshot["accepted"]) == 1
    work = snapshot["work"]
    assert work["user_message_id"] == message_id
    assert not work.get("queue")


def test_changed_trigger_to_router_cannot_reuse_steward_task_identity(real_family):
    _, block = real_family
    family = block["family"]
    repository = resident_conversation_stream.get_repository()
    message_id = "msg_31000000000000000000000000000001"
    source_id = "msg_31000000000000000000000000000002"
    repository.accept_user_message(family["facade"], resident_input_queue_domain.build_message(
        "user", "A real original request", ACTOR, message_id=source_id,
    ))
    arguments = {"resident_id": family["facade"], "trigger": "summary",
                 "message_id": message_id, "source_message_id": source_id}
    ok("DispatchFamilyTask", arguments, actor=family["steward"])
    before = _snapshot(family, message_id)
    router_work = copy.deepcopy(resident_execution_work.read(find_resident(family["router"])))

    rejected = run("DispatchFamilyTask", {**arguments, "trigger": "route"}, actor=ACTOR)

    assert rejected.status == "failed"
    assert rejected.error["commit_state"] == "not_committed"
    assert _snapshot(family, message_id) == before
    assert repository.message(family["router"], message_id) is None
    assert resident_execution_work.read(find_resident(family["router"])) == router_work


def test_new_identity_accepts_new_input_and_old_replay_keeps_queue(real_family):
    _, block = real_family
    family = block["family"]
    first_id = "msg_40000000000000000000000000000001"
    second_id = "msg_40000000000000000000000000000002"
    arguments = {"resident_id": family["facade"], "trigger": "summary", "message_id": first_id}
    ok("DispatchFamilyTask", arguments, actor=family["steward"])
    second = ok("DispatchFamilyTask", {**arguments, "message_id": second_id}, actor=ACTOR)
    repository = resident_conversation_stream.get_repository()
    before = _snapshot(family, first_id)
    second_state = repository.input_state(family["steward"], second_id)
    assert second["message_id"] == second_id
    assert second_state["state"] == "queued"
    assert [item["user_message_id"] for item in before["work"]["queue"]] == [second_id]

    ok("DispatchFamilyTask", arguments, actor=ACTOR)

    assert _snapshot(family, first_id) == before
    assert repository.input_state(family["steward"], second_id) == second_state


def test_template_edit_does_not_rewrite_accepted_task_but_new_input_uses_edit(real_family):
    body, block = real_family
    family = block["family"]
    message_id = "msg_50000000000000000000000000000001"
    arguments = {"resident_id": family["facade"], "trigger": "summary", "message_id": message_id}
    ok("DispatchFamilyTask", arguments, actor=family["steward"])
    before = _snapshot(family, message_id)
    wire_id = next(element["id"] for element in block["elements"] if element["key"] == "request_summary")
    wire = next(wire for wire in body["program"]["wires"] if wire["id"] == wire_id)
    profiles = copy.deepcopy(wire["config"]["invoke_profiles"])
    task = profiles["effect"]["arguments"]["family_tasks"]["value"]["summary"]
    task["prompt"] = "Updated summary contract ${facts} ${guide_path} ${guide_version}"
    ok("ApplyLineageChanges", {"lineage_id": body["lineage_id"], "operations": [{
        "op": "update", "kind": "wire", "id": wire_id,
        "changes": {"config.invoke_profiles": profiles},
    }]})

    ok("DispatchFamilyTask", arguments, actor=ACTOR)

    assert _snapshot(family, message_id) == before
    new_id = "msg_50000000000000000000000000000002"
    ok("DispatchFamilyTask", {**arguments, "message_id": new_id}, actor=ACTOR)
    new_message = resident_conversation_stream.get_repository().message(family["steward"], new_id)
    assert new_message["content_parts"][0]["text"].startswith("Updated summary contract ")


def test_equivalent_fact_key_order_replays_without_new_admission(real_family):
    _, block = real_family
    family = block["family"]
    message_id = "msg_60000000000000000000000000000001"
    arguments = {"resident_id": family["facade"], "trigger": "summary", "message_id": message_id,
                 "facts": {"count": 2, "members": ["A", "B"]}}
    ok("DispatchFamilyTask", arguments, actor=family["steward"])
    before = _snapshot(family, message_id)

    ok("DispatchFamilyTask", {**arguments, "facts": {"members": ["A", "B"], "count": 2}}, actor=ACTOR)

    assert _snapshot(family, message_id) == before


@pytest.mark.parametrize("edit_template", [False, True])
def test_legacy_input_without_dispatch_snapshot_keeps_original_or_rejects_ambiguity(real_family, edit_template):
    body, block = real_family
    family = block["family"]
    message_id = "msg_70000000000000000000000000000001"
    arguments = {"resident_id": family["facade"], "trigger": "summary", "message_id": message_id}
    ok("DispatchFamilyTask", arguments, actor=family["steward"])
    repository = resident_conversation_stream.get_repository()
    repository.append_message(family["steward"], repository.message(family["steward"], message_id))
    repository.set_input_state(family["steward"], message_id, "completed")
    resident_execution_work.clear(find_resident(family["steward"]))
    # Model the pre-upgrade database: preserve the actual accepted message and
    # terminal state, removing only the new owner's request snapshot in test PG.
    repository.state_store.database.write(lambda connection: connection.execute(
        "DELETE FROM family_task_dispatches WHERE lineage_id=%s AND message_id=%s",
        (body["lineage_id"], message_id),
    ))
    before = _snapshot(family, message_id)
    if edit_template:
        wire_id = next(e["id"] for e in block["elements"] if e["key"] == "request_summary")
        wire = next(w for w in body["program"]["wires"] if w["id"] == wire_id)
        profiles = copy.deepcopy(wire["config"]["invoke_profiles"])
        profiles["effect"]["arguments"]["family_tasks"]["value"]["summary"]["prompt"] = "Changed legacy ${facts}"
        ok("ApplyLineageChanges", {"lineage_id": body["lineage_id"], "operations": [{
            "op": "update", "kind": "wire", "id": wire_id,
            "changes": {"config.invoke_profiles": profiles},
        }]})

    replay = run("DispatchFamilyTask", arguments, actor=ACTOR)

    assert replay.status == ("failed" if edit_template else "committed")
    if edit_template:
        assert replay.error["commit_state"] == "not_committed"
    else:
        assert replay.result["message_id"] == message_id
    assert _snapshot(family, message_id) == before


def test_cancelled_waiting_task_without_trace_is_not_admitted_again(real_family):
    _, block = real_family
    family = block["family"]
    repository = resident_conversation_stream.get_repository()
    head = "msg_80000000000000000000000000000001"
    cancelled_id = "msg_80000000000000000000000000000002"
    arguments = {"resident_id": family["facade"], "trigger": "summary", "message_id": head}
    ok("DispatchFamilyTask", arguments, actor=family["steward"])
    arguments["message_id"] = cancelled_id
    ok("DispatchFamilyTask", arguments, actor=family["steward"])
    receipt = ok("CancelInput", {"resident_id": family["steward"], "message_id": cancelled_id})
    assert receipt["input_state"]["state"] == "cancelled"
    assert repository.message(family["steward"], cancelled_id) is None
    before = _snapshot(family, cancelled_id)

    ok("DispatchFamilyTask", arguments, actor=ACTOR)
    changed = run("DispatchFamilyTask", {**arguments, "facts": {"changed": True}}, actor=ACTOR)

    assert changed.status == "failed"
    assert changed.error["commit_state"] == "not_committed"
    assert _snapshot(family, cancelled_id) == before
    assert before["work"]["user_message_id"] == head
    assert not before["work"].get("queue")


def test_fact_number_changed_to_boolean_is_a_different_request(real_family):
    _, block = real_family
    family = block["family"]
    message_id = "msg_90000000000000000000000000000001"
    arguments = {"resident_id": family["facade"], "trigger": "summary", "message_id": message_id,
                 "facts": {"count": 1}}
    ok("DispatchFamilyTask", arguments, actor=family["steward"])
    before = _snapshot(family, message_id)

    changed = run("DispatchFamilyTask", {**arguments, "facts": {"count": True}}, actor=ACTOR)

    assert changed.status == "failed"
    assert changed.error["commit_state"] == "not_committed"
    assert _snapshot(family, message_id) == before


@pytest.mark.parametrize("fault", ["before_intent_commit", "after_intent_commit", "after_admission"])
def test_interrupted_dispatch_resumes_first_committed_intent_once(real_family, monkeypatch, fault):
    body, block = real_family
    family = block["family"]
    message_id = "msg_a0000000000000000000000000000001"
    arguments = {"resident_id": family["facade"], "trigger": "summary", "message_id": message_id}
    repository = resident_conversation_stream.get_repository()
    database = repository.state_store.database

    def read_intent(connection):
        row = connection.execute(
            "SELECT intent_json FROM family_task_dispatches WHERE lineage_id=%s AND message_id=%s",
            (body["lineage_id"], message_id),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["intent_json"]) if isinstance(row["intent_json"], str) else row["intent_json"]

    def delivery_fault(*args, **kwargs):
        if fault == "after_admission":
            deliver_resident_message_content(*args, **kwargs)
        raise RuntimeError("Injected dispatch interruption: " + fault)

    with monkeypatch.context() as failure:
        if fault == "before_intent_commit":
            original_write = database.write

            def fail_before_commit(operation, *args, **kwargs):
                def apply_then_abort(connection):
                    result = operation(connection)
                    if read_intent(connection) is not None:
                        # The real SQL insert ran; abort its transaction before
                        # commit. No intent or message should become durable.
                        raise RuntimeError("Injected interruption before intent commit")
                    return result
                return original_write(apply_then_abort, *args, **kwargs)

            failure.setattr(database, "write", fail_before_commit)
        else:
            failure.setattr(resident_intake_domain, "deliver_resident_message_content", delivery_fault)
        interrupted = run("DispatchFamilyTask", arguments, actor=family["steward"])
        assert interrupted.status == "failed"
        assert "Injected" in interrupted.error["message"]

    with database.read() as connection:
        intent = read_intent(connection)
    assert (intent is None) == (fault == "before_intent_commit")
    first_message = repository.message(family["steward"], message_id)
    assert (first_message is not None) == (fault == "after_admission")
    before = _snapshot(family, message_id) if fault == "after_admission" else None
    if before is None:
        assert not resident_execution_work.read(find_resident(family["steward"]))

    wire_id = next(e["id"] for e in block["elements"] if e["key"] == "request_summary")
    wire = next(w for w in body["program"]["wires"] if w["id"] == wire_id)
    profiles = copy.deepcopy(wire["config"]["invoke_profiles"])
    profiles["effect"]["arguments"]["family_tasks"]["value"]["summary"]["prompt"] = "New after interruption ${facts}"
    ok("ApplyLineageChanges", {"lineage_id": body["lineage_id"], "operations": [{
        "op": "update", "kind": "wire", "id": wire_id,
        "changes": {"config.invoke_profiles": profiles},
    }]})

    resumed = ok("DispatchFamilyTask", arguments, actor=ACTOR)

    assert resumed["message_id"] == message_id
    after = _snapshot(family, message_id)
    assert after["state"]["state"] == "queued"
    assert len(after["accepted"]) == 1
    assert after["work"]["user_message_id"] == message_id
    assert not after["work"].get("queue")
    if fault == "before_intent_commit":
        assert after["message"]["origin"]["initiated_by"]["id"] == ACTOR
        assert after["message"]["content_parts"][0]["text"].startswith("New after interruption ")
    else:
        assert after["message"]["origin"]["initiated_by"]["id"] == family["steward"]
        assert after["message"]["content_parts"][0]["text"] == intent["text"]
    if before is not None:
        assert after == before
