"""Delivered turn inputs keep work on their exact Execution, on both public reads."""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from management_portal.execution import execution_inputs
from management_portal.execution.appended_input_projection import materialize_delivered_input
from management_portal.execution.contracts import ExecutionInputControl
from management_portal.society_service import resident_conversation_stream, resident_mailbox
from management_portal.society_service.resident_conversation_records import INPUT_STATE_CHANGED
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import instruction, team  # noqa: F401
from tests.cases.test_resident_mailbox import _queue


def _message(identity):
    return {"message_id": identity, "role": "user", "author_id": ACTOR,
            "content_parts": [{"type": "text", "text": identity}]}


@pytest.fixture
def delivered_work(team):
    rid = team.target["resident_id"]
    repo = resident_conversation_stream.get_repository()
    store = repo.agent_data
    main, appended = "msg_work_head", "msg_work_append"
    eid, turn = "execution-" + "a" * 24, "provider-work-old"
    for mid in (main, appended):
        repo.append_message(rid, _message(mid))
        repo.set_input_state(rid, mid, "queued")
    store.begin_execution(execution_id=eid, agent_id=rid, actor_ref=ACTOR,
                          provider="codex", started_at="2026-10-01T00:00:00Z")
    execution_inputs.attach(store, eid, [{"message_id": main, "role": "primary"}])
    session = store.ensure_provider_session(provider="codex", backend_route_id="zero",
        backend_session_id="work-context", agent_id=rid, actor_ref=ACTOR)
    store.bind_execution_provider_session(eid, provider_session_ref=session["provider_session_ref"])
    store.record_input_acceptance(eid, agent_id=rid, provider_turn_id=turn)
    source = store.chat_message_provenance(rid, appended)["segment_id"]
    control = ExecutionInputControl.new(execution_id=eid, actor=ACTOR,
        expected_turn_id=turn, source_segment_id=source, input=[{"type": "text", "text": "append"}])
    materialize_delivered_input(store, {**control.to_payload(), "status": "delivered",
                                       "updated_at": "2026-10-01T00:00:01Z"})
    repo.set_input_state(rid, appended, "completed", updated_at="2026-10-01T00:00:02Z")
    return rid, repo, store, main, appended, eid, turn


def _public_reads(team, rid, mid):
    state = instruction(team, "GetInputState", {"resident_id": rid, "message_id": mid})
    assert state.status == "committed", state.error
    snapshot = instruction(team, "GetConversation", {"resident_id": rid, "input_message_id": mid})
    assert snapshot.status == "committed", snapshot.error
    selected = next(item for item in snapshot.result["input_states"] if item["message_id"] == mid)
    assert selected["work"] == state.result["input_state"]["work"]
    return state.result["input_state"]


@pytest.mark.parametrize("status", ["running", "completed", "failed", "cancelled"])
def test_public_reads_separate_delivery_from_execution_work(team, delivered_work, status):
    rid, repo, store, main, appended, eid, turn = delivered_work
    delivered_at = repo.input_state(rid, appended)["updated_at"]
    if status != "running":
        store.finish_execution(eid, status=status, finished_at="2026-10-01T00:00:03Z")
    state = _public_reads(team, rid, appended)
    assert state["state"] == "completed"
    assert state["updated_at"] == delivered_at
    work = state["work"]
    assert work["state"] == status and work["kind"] == "appended"
    assert work["execution_id"] == eid and work["primary_message_id"] == main
    assert work["provider_turn_id"] == turn and work["delivery"] == "delivered"
    assert work["updated_at"]


def test_old_delivered_input_does_not_follow_a_retried_main_message(team, delivered_work):
    rid, _repo, store, main, appended, old, _turn = delivered_work
    store.finish_execution(old, status="failed", finished_at="2026-10-01T00:00:03Z")
    new = "execution-" + "b" * 24
    store.begin_execution(execution_id=new, agent_id=rid, actor_ref=ACTOR,
                          provider="codex", started_at="2026-10-01T00:01:00Z")
    execution_inputs.attach(store, new, [{"message_id": main, "role": "primary"}])
    work = _public_reads(team, rid, appended)["work"]
    assert (work["execution_id"], work["state"]) == (old, "failed")
    # A late notification for the old attempt cannot publish its primary's
    # failed outcome over the same main message now running in the retry.
    before = _repo.events_after(rid, 0)["cursor"]
    _repo.inputs.publish_execution_work(rid, old)
    states = [event["payload"]["input_state"] for event in _repo.events_after(rid, before)["events"]
              if event["event_type"] == INPUT_STATE_CHANGED]
    assert not any(state["message_id"] == main for state in states)


def test_work_completion_has_ordered_events_without_rewriting_input_terminal(team, delivered_work):
    rid, repo, store, _main, appended, eid, _turn = delivered_work
    before = repo.input_state(rid, appended)
    repo.inputs.publish_execution_work(rid, eid)
    running = repo.events_after(rid, 0)["cursor"]
    store.finish_execution(eid, status="completed", finished_at="2026-10-01T00:00:03Z")
    repo.inputs.publish_execution_work(rid, eid)
    changes = repo.events_after(rid, running)
    states = [event["payload"]["input_state"] for event in changes["events"]
              if event["event_type"] == INPUT_STATE_CHANGED
              and event.get("payload", {}).get("input_state", {}).get("message_id") == appended]
    assert len(states) == 1
    assert states[0]["state"] == "completed" and states[0]["work"]["state"] == "completed"
    assert states[0]["updated_at"] == before["updated_at"]
    assert states[0]["work"]["updated_at"] != before["work"]["updated_at"]
    # Reconnection or a late running callback consults the same current owner
    # fact, neither duplicating completion nor reviving the former running work.
    repo.inputs.publish_execution_work(rid, eid)
    assert repo.events_after(rid, changes["cursor"])["events"] == []
    assert repo.input_state(rid, appended)["updated_at"] == before["updated_at"]


def test_legacy_delivery_without_evidence_is_explicitly_unknown(team):
    rid = team.target["resident_id"]
    repo = resident_conversation_stream.get_repository()
    repo.append_message(rid, _message("msg_legacy_delivery"))
    repo.set_input_state(rid, "msg_legacy_delivery", "completed")
    work = _public_reads(team, rid, "msg_legacy_delivery")["work"]
    assert work == {"state": "unknown", "kind": "unknown", "delivery": "unknown", "updated_at": ""}


def test_unreserved_mailbox_queue_and_exact_withdrawal_are_not_unknown(team):
    rid, (_head, pending, _b, _c) = _queue(team)
    state = _public_reads(team, rid, pending)
    assert state["work"]["state"] == "queued"
    assert state["work"]["delivery"] == "pending"
    assert "execution_id" not in state["work"]
    stopped = instruction(team, "CancelInput", {"resident_id": rid, "message_id": pending})
    assert stopped.status == "committed", stopped.error
    state = instruction(team, "GetInputState", {"resident_id": rid, "message_id": pending}).result["input_state"]
    assert state["state"] == "cancelled" and state["work"]["state"] == "cancelled"
    assert state["work"]["delivery"] == "pending"
    # A request withdrawn before delivery leaves Chat entirely; retaining work
    # evidence must not resurrect its discarded body in a conversation page.
    snapshot = instruction(team, "GetConversation", {"resident_id": rid})
    assert snapshot.status == "committed", snapshot.error
    assert not any(message["message_id"] == pending for message in snapshot.result["messages"])


def test_unreserved_merged_withdrawal_freezes_member_cause(team):
    rid, (_head, a, b, _other) = _queue(team)
    merged = instruction(team, "MergeMailboxItems", {"resident_id": rid, "message_ids": [a, b]})
    assert merged.status == "committed", merged.error
    group = merged.result["group_message_id"]
    repo = resident_conversation_stream.get_repository()
    # An original's independently finished work is not a group withdrawal.
    repo.set_input_state(rid, b, "failed", error={"code": "independent_failure"})
    stopped = instruction(team, "CancelInput", {"resident_id": rid, "message_id": group})
    assert stopped.status == "committed", stopped.error
    state = instruction(team, "GetInputState", {"resident_id": rid, "message_id": a}).result["input_state"]
    assert state["state"] == state["work"]["state"] == "cancelled"
    assert state["work"]["primary_message_id"] == group
    assert state["work"]["delivery"] == "pending" and "execution_id" not in state["work"]
    assert state["error"]["grouped_into_message_id"] == group
    assert state["error"]["code"] == "queue_withdrawn"
    assert resident_mailbox._project_group_state(state, group, {
        "state": "running", "work": {"state": "running", "execution_id": "retry"},
    })["work"] == state["work"]
    independent = instruction(team, "GetInputState", {"resident_id": rid, "message_id": b}).result["input_state"]
    assert independent["state"] == "failed"
    assert independent["error"]["code"] == "independent_failure"
    assert independent["work"]["state"] == "unknown"


def test_delivered_input_emits_work_even_when_execution_stays_running(team, delivered_work):
    rid, repo, store, _main, _appended, eid, turn = delivered_work
    repo.inputs.publish_execution_work(rid, eid)
    mid = "msg_second_work_append"
    repo.append_message(rid, _message(mid))
    repo.set_input_state(rid, mid, "queued")
    source = store.chat_message_provenance(rid, mid)["segment_id"]
    control = ExecutionInputControl.new(execution_id=eid, actor=ACTOR, expected_turn_id=turn,
        source_segment_id=source, input=[{"type": "text", "text": "second append"}])
    materialize_delivered_input(store, {**control.to_payload(), "status": "delivered",
                                       "updated_at": "2026-10-01T00:00:04Z"})
    repo.set_input_state(rid, mid, "completed")
    before = repo.events_after(rid, 0)["cursor"]
    repo.inputs.publish_execution_work(rid, eid)
    updates = [event["payload"]["input_state"] for event in repo.events_after(rid, before)["events"]
               if event["event_type"] == INPUT_STATE_CHANGED]
    assert any(state["message_id"] == mid and state["work"]["state"] == "running" for state in updates)


def test_turn_change_invalidates_previously_published_running_append(team, delivered_work):
    rid, repo, store, _main, appended, eid, _turn = delivered_work
    repo.inputs.publish_execution_work(rid, eid)
    before = repo.events_after(rid, 0)["cursor"]
    store.record_execution_output(eid, provider_turn_id="provider-new-turn-same-execution")
    repo.inputs.publish_execution_work(rid, eid)
    states = [event["payload"]["input_state"] for event in repo.events_after(rid, before)["events"]
              if event["event_type"] == INPUT_STATE_CHANGED]
    state = next(state for state in states if state["message_id"] == appended)
    assert state["work"]["state"] == "unknown"
    assert state["state"] == "completed"
    assert _public_reads(team, rid, appended)["work"]["state"] == "unknown"


def test_same_status_execution_callbacks_publish_turn_invalidation(delivered_work, monkeypatch):
    from management_portal.society_service import resident_execution_engine_domain as engine
    rid, repo, store, main, appended, eid, turn = delivered_work
    monkeypatch.setattr(engine, "_project_resident_execution_snapshot", lambda *_a, **_k: None)
    boundary = []
    fresh = "msg_observer_fresh_append"

    def wait(_execution, **options):
        assert options["on_snapshot"]({"status": "running"})
        boundary.append(repo.events_after(rid, 0)["cursor"])
        repo.append_message(rid, _message(fresh))
        repo.set_input_state(rid, fresh, "queued")
        source = store.chat_message_provenance(rid, fresh)["segment_id"]
        control = ExecutionInputControl.new(execution_id=eid, actor=ACTOR,
            expected_turn_id=turn, source_segment_id=source,
            input=[{"type": "text", "text": "observer append"}])
        materialize_delivered_input(store, {**control.to_payload(), "status": "delivered",
                                           "updated_at": "2026-10-01T00:00:04Z"})
        repo.set_input_state(rid, fresh, "completed")
        assert options["on_snapshot"]({"status": "running"})
        store.record_execution_output(eid, provider_turn_id="replacement-with-same-running-status")
        assert options["on_snapshot"]({"status": "running"})
        store.finish_execution(eid, status="completed", finished_at="2026-10-01T00:00:05Z")
        assert options["on_snapshot"]({"status": "completed"})
        return {"status": "completed"}

    result = engine.wait_for_resident_execution(SimpleNamespace(wait=wait), execution_id=eid,
        actor=ACTOR, resident_id=rid, persist_messages=False)
    assert not result.get("projection_errors")
    states = [event["payload"]["input_state"] for event in repo.events_after(rid, boundary[0])["events"]
              if event["event_type"] == INPUT_STATE_CHANGED]
    assert any(state["message_id"] == appended and state["work"]["state"] == "unknown" for state in states)
    assert [state["work"]["state"] for state in states
            if state["message_id"] == fresh and "work" in state] == ["running", "unknown"]
    assert any(state["message_id"] == main and state["work"]["state"] == "completed" for state in states)


def test_mailbox_does_not_steal_an_independent_terminal_or_bound_attempt():
    old = {"state": "completed", "kind": "appended", "execution_id": "old",
           "primary_message_id": "main", "delivery": "delivered", "updated_at": "old-time"}
    other = {**old, "state": "running", "execution_id": "new", "updated_at": "new-time"}
    for state in ("completed", "failed", "cancelled", "running"):
        projected = resident_mailbox._project_group_state(
            {"state": state, "work": old}, "group", {"state": "running", "work": other})
        assert projected["work"] == old


def test_family_preserves_worker_work_on_both_read_surfaces(team, delivered_work):
    from management_portal.society_service import family_facade
    from tests.cases.family_test_support import set_role

    rid, repo, _store, _main, appended, _eid, _turn = delivered_work
    facade = team.source["resident_id"]
    set_role(facade, family_id="fbi_work_projection", actor=ACTOR)
    set_role(rid, family_id="fbi_work_projection", role="member", actor=ACTOR)
    root = "msg_facade_work"
    repo.append_message(facade, _message(root))
    repo.set_input_state(facade, root, "queued")
    family_facade.complete_relay((facade, root), rid, appended)
    work = _public_reads(team, facade, root)["work"]
    assert work == repo.input_state(rid, appended)["work"]
    snapshot = instruction(team, "GetConversation", {"resident_id": facade, "input_message_id": root})
    request = next(message for message in snapshot.result["messages"] if message["message_id"] == root)
    assert request["relay"]["work"] == work


def test_snapshot_resolves_visible_input_work_in_one_batch(team, monkeypatch):
    rid = team.target["resident_id"]
    repo = resident_conversation_stream.get_repository()
    identities = ["msg_work_page_" + str(index) for index in range(12)]
    for identity in identities:
        repo.append_message(rid, _message(identity))
        repo.set_input_state(rid, identity, "completed")
    calls = []
    original = repo.agent_data.input_work_states

    def read(agent, selected):
        calls.append((agent, list(selected)))
        return original(agent, selected)

    monkeypatch.setattr(repo.agent_data, "input_work_states", read)
    snapshot = instruction(team, "GetConversation", {"resident_id": rid, "history_limit": 12})
    assert snapshot.status == "committed", snapshot.error
    assert len(calls) == 1
    assert calls[0][0] == rid and set(calls[0][1]) == set(identities)
