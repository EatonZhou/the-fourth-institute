"""Business completion records gate periodic review without idle model work.

:module: lineage.family
:design: decision_89b9df038a95, decision_a64b3ea2c3c5, decision_b77fde81e7a0
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.society_service import family_review, resident_conversation_stream
from management_portal.society_service import resident_input_queue_domain, resident_intake_domain
from management_portal.society_service.resident_intake_domain import deliver_resident_message_content
from management_portal.society_service.state_store import get_lineage_data_store
from management_portal.society_service.templates import familize
from management_portal.society_service.lineage_program_effect_execution import _family_note_context
from management_portal.hatg.contracts import InstructionContext
from tests.cases.isa_test_support import ACTOR, run
from tests.cases.test_family_tasks import (  # noqa: F401
    charters, configured_family, future_creation_store, lineage,
)


@pytest.fixture
def team(configured_family, monkeypatch):
    body, block = configured_family
    monkeypatch.setattr(resident_intake_domain, "deliver_resident_message_content", deliver_resident_message_content)
    monkeypatch.setattr(family_review, "_idle", lambda rid: True)
    repo = resident_conversation_stream.get_repository()
    f = block["family"]
    def completed(number, *, state="completed"):
        mid = "msg_" + f"{number:032x}"
        member = f["members"][0]
        msg = resident_input_queue_domain.build_message("user", "Business task", ACTOR, message_id=mid)
        repo.append_message(member, msg)
        repo.set_input_state(member, mid, state, updated_at=f"2026-11-01T00:00:{number:02d}Z")
        return {"type": "resident.work_finished", "subject_resident_id": member,
                "payload": {"message_id": mid}}
    def check(event=None):
        args = {"lineage_id": body["lineage_id"], "family_id": block["id"]}
        if event is not None:
            args["event"] = event
        return run("CheckFamilyReview", args, actor=ACTOR).raise_for_error()
    return SimpleNamespace(body=body, block=block, family=f, repo=repo, completed=completed, check=check)


def test_idle_family_records_only_then_busy_defers_and_many_completions_coalesce(team, monkeypatch):
    t = team
    monkeypatch.setattr(family_review, "_idle", lambda _: pytest.fail("unchanged activity must not read runtime"))
    assert t.check()["status"] == "unchanged"
    first, second = t.completed(1), t.completed(2)
    for event in [second, first, second]:
        assert t.check(event)["status"] == "recorded"
    monkeypatch.setattr(family_review, "_idle", lambda _: False)
    assert t.check()["status"] == "busy"
    monkeypatch.setattr(family_review, "_idle", lambda _: True)
    sent = t.check()
    assert sent["status"] == "requested"
    message = t.repo.message(t.family["steward"], sent["message_id"])
    assert message["author_id"] == t.family["facade"]
    assert t.repo.input_state(t.family["steward"], sent["message_id"])["state"] == "queued"
    assert t.check(first)["status"] == "recorded"
    assert t.check()["status"] == "unchanged"
    ledger = get_lineage_data_store().family_relays.get(t.body["lineage_id"], t.family["facade"])["activity_review"]
    assert ledger["activity"] == ledger["reviewed"]
    assert ledger["activity"][t.family["members"][0]][1] == second["payload"]["message_id"]


def test_crash_after_acceptance_reuses_intent_and_keeps_work_arriving_during_dispatch(team, monkeypatch):
    t = team
    t.check(t.completed(1))
    second = t.completed(2)
    dispatch = family_review.dispatch
    receipt = []
    def interrupted(*args, **kwargs):
        t.check(second)  # independent recording is not held by the dispatch lock
        receipt.append(dispatch(*args, **kwargs))
        raise OSError("crash after admission, before watermark consumption")
    monkeypatch.setattr(family_review, "dispatch", interrupted)
    with pytest.raises(Exception, match="crash after admission"):
        # Call the owner directly to retain the original exception for this crash probe.
        family_review.check(t.body["lineage_id"], t.block["id"], ACTOR)
    mid = receipt[0]["message_id"]
    original = deepcopy(t.repo.message(t.family["steward"], mid))
    assert original is not None
    pending = get_lineage_data_store().family_relays.get(t.body["lineage_id"], t.family["facade"])["activity_review"]["pending"]
    assert pending and pending["message_id"] == mid
    t.repo.set_input_state(t.family["steward"], mid, "cancelled")
    after_cancel = deepcopy(t.repo.message(t.family["steward"], mid))
    monkeypatch.setattr(family_review, "dispatch", dispatch)
    monkeypatch.setattr(family_review, "_idle", lambda _: False)
    resumed = t.check()
    assert resumed.get("message_id") == mid, resumed
    assert t.repo.message(t.family["steward"], mid) == after_cancel
    assert t.repo.input_state(t.family["steward"], mid)["state"] == "cancelled"
    assert t.check()["status"] == "busy"  # second completion remains unconsumed
    monkeypatch.setattr(family_review, "_idle", lambda _: True)
    assert t.check()["message_id"] != mid
    assert t.check()["status"] == "unchanged"


def test_concurrent_timer_checks_admit_one_review(team):
    t = team
    t.check(t.completed(1))
    def check(_):
        return family_review.check(t.body["lineage_id"], t.block["id"], ACTOR)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(check, range(2)))
    assert sum(x["status"] == "requested" for x in results) == 1
    assert all(x["status"] in {"requested", "unchanged", "busy"} for x in results)


def test_clock_rollback_and_repeated_terminal_updates_use_first_completion_order(team):
    t = team
    first = t.completed(1)
    assert t.check(first)["status"] == "recorded"
    first_review = t.check()["message_id"]
    second = t.completed(2)
    member = second["subject_resident_id"]
    # Later durable completion, but a lower wall clock than the first input.
    t.repo.set_input_state(member, second["payload"]["message_id"], "completed", updated_at="2026-10-01T00:00:00Z")
    assert t.check(second)["status"] == "recorded"
    assert t.check()["message_id"] != first_review
    t.repo.set_input_state(member, first["payload"]["message_id"], "completed", updated_at="2027-01-01T00:00:00Z")
    assert t.check(first)["status"] == "recorded"
    assert t.check()["status"] == "unchanged"


@pytest.mark.parametrize("column_type", ["text", "jsonb"])
def test_completed_cursor_reads_installed_text_and_new_jsonb_columns(team, column_type):
    t = team
    event = t.completed(1)
    member, mid = event["subject_resident_id"], event["payload"]["message_id"]
    database = get_lineage_data_store().database
    with database.read() as c:
        original = c.execute("SELECT data_type FROM information_schema.columns WHERE "
                             "table_schema=current_schema() AND table_name='conversation_events' "
                             "AND column_name='payload_json'").fetchone()["data_type"]
    assert original in {"text", "jsonb"}
    expected = t.repo.completed_input_cursor(member, mid)
    try:
        database.write(lambda c: c.execute("ALTER TABLE conversation_events ALTER COLUMN payload_json "
                                           f"TYPE {column_type} USING payload_json::{column_type}"))
        assert t.repo.completed_input_cursor(member, mid) == expected
        assert t.check(event)["status"] == "recorded"
        assert t.check()["status"] == "requested"
        assert t.check()["status"] == "unchanged"
    finally:
        database.write(lambda c: c.execute("ALTER TABLE conversation_events ALTER COLUMN payload_json "
                                           f"TYPE {original} USING payload_json::{original}"))


def test_legacy_timestamp_pending_keeps_facts_and_migrates_only_recorded_positions(team, monkeypatch):
    t = team
    event = t.completed(1)
    t.check(event)
    dispatch = family_review.dispatch
    receipts = []
    def interrupted(*args, **kwargs):
        receipts.append(dispatch(*args, **kwargs))
        raise OSError("after admission")
    monkeypatch.setattr(family_review, "dispatch", interrupted)
    # Emulate the previously published timestamp ledger before creating intent.
    store = get_lineage_data_store().family_relays
    with store.transaction(t.body["lineage_id"], t.family["facade"]) as relay:
        ledger = relay["activity_review"]
        ledger["position_format"] = "conversation_cursor"  # permit creating the original timestamp intent
        ledger["activity"][event["subject_resident_id"]] = [1793491201.0, event["payload"]["message_id"]]
    with pytest.raises(OSError, match="after admission"):
        family_review.check(t.body["lineage_id"], t.block["id"], ACTOR)
    with store.transaction(t.body["lineage_id"], t.family["facade"]) as relay:
        ledger = relay["activity_review"]
        del ledger["position_format"]
        frozen = deepcopy(ledger["pending"])
    monkeypatch.setattr(family_review, "dispatch", lambda *a, **k: pytest.fail("already accepted"))
    assert t.check()["message_id"] == frozen["message_id"] == receipts[0]["message_id"]
    ledger = store.get(t.body["lineage_id"], t.family["facade"])["activity_review"]
    assert ledger["position_format"] == "conversation_cursor"
    assert ledger["activity"] == ledger["reviewed"]
    assert t.check()["status"] == "unchanged"


def test_accepted_review_consumes_snapshot_after_steward_change_without_readmission(team, monkeypatch):
    t = team
    t.check(t.completed(1))
    dispatch = family_review.dispatch
    receipts = []
    def interrupted(*args, **kwargs):
        receipts.append(dispatch(*args, **kwargs))
        raise OSError("after admission")
    monkeypatch.setattr(family_review, "dispatch", interrupted)
    with pytest.raises(OSError, match="after admission"):
        family_review.check(t.body["lineage_id"], t.block["id"], ACTOR)
    mid = receipts[0]["message_id"]
    old_steward = t.family["steward"]
    t.repo.set_input_state(old_steward, mid, "cancelled")
    replacement = deepcopy(t.block)
    replacement["family"]["steward"] = t.family["members"][0]
    monkeypatch.setattr(family_review, "read_family", lambda *_: (t.body, replacement))
    monkeypatch.setattr(family_review, "dispatch", lambda *a, **k: pytest.fail("accepted work cannot be readmitted"))
    monkeypatch.setattr(family_review, "_idle", lambda _: pytest.fail("accepted work does not need idle"))
    assert family_review.check(t.body["lineage_id"], t.block["id"], ACTOR)["message_id"] == mid
    assert t.repo.input_state(old_steward, mid)["state"] == "cancelled"
    assert t.repo.message(replacement["family"]["steward"], mid) is None
    assert family_review.check(t.body["lineage_id"], t.block["id"], ACTOR)["status"] == "unchanged"


def test_steward_change_between_snapshot_and_dispatch_does_not_admit_wrong_target(team, monkeypatch):
    from management_portal.society_service import family_tasks
    t = team
    t.check(t.completed(1))
    replacement = deepcopy(t.block)
    replacement["family"]["steward"] = t.family["members"][0]
    monkeypatch.setattr(family_tasks, "context", lambda *_: (t.body, replacement))
    assert t.check()["status"] == "target_changed"
    ledger = get_lineage_data_store().family_relays.get(t.body["lineage_id"], t.family["facade"])["activity_review"]
    mid = ledger["pending"]["message_id"]
    assert not ledger["reviewed"]
    assert t.repo.message(t.family["steward"], mid) is None
    assert t.repo.message(replacement["family"]["steward"], mid) is None


def test_member_departure_keeps_recorded_work_but_rejects_new_records(team, monkeypatch):
    t = team
    first = t.completed(1)
    t.check(first)
    later = t.completed(2)
    replacement = deepcopy(t.block)
    replacement["family"]["members"] = []
    monkeypatch.setattr(family_review, "read_family", lambda *_: (t.body, replacement))
    assert t.check(later)["status"] == "skipped"
    assert t.check()["status"] == "requested"
    ledger = get_lineage_data_store().family_relays.get(t.body["lineage_id"], t.family["facade"])["activity_review"]
    assert ledger["activity"] == ledger["reviewed"]
    assert ledger["reviewed"][first["subject_resident_id"]][1] == first["payload"]["message_id"]
    assert t.check()["status"] == "unchanged"


def test_receipt_from_different_facts_cannot_consume_pending_activity(team, monkeypatch):
    from management_portal.society_service.resident_message_contract import MessageIdentityConflict
    t = team
    t.check(t.completed(1))
    dispatch = family_review.dispatch
    def interrupted(*args, **kwargs):
        dispatch(*args, **kwargs)
        raise OSError("after admission")
    monkeypatch.setattr(family_review, "dispatch", interrupted)
    with pytest.raises(OSError, match="after admission"):
        family_review.check(t.body["lineage_id"], t.block["id"], ACTOR)
    store = get_lineage_data_store().family_relays
    with store.transaction(t.body["lineage_id"], t.family["facade"]) as relay:
        pending = relay["activity_review"]["pending"]
        pending["activity"][t.family["members"][0]][0] += 1
    with pytest.raises(MessageIdentityConflict):
        family_review.check(t.body["lineage_id"], t.block["id"], ACTOR)
    ledger = store.get(t.body["lineage_id"], t.family["facade"])["activity_review"]
    assert ledger["pending"] and not ledger["reviewed"]


def test_failed_admission_preserves_intent_and_paused_family_does_not_consume(team, monkeypatch):
    t = team
    t.check(t.completed(1))
    dispatch = family_review.dispatch
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(kwargs["message_id"])
        raise OSError("before admission")
    monkeypatch.setattr(family_review, "dispatch", fail)
    with pytest.raises(OSError, match="before admission"):
        family_review.check(t.body["lineage_id"], t.block["id"], ACTOR)
    monkeypatch.setattr(family_review, "dispatch", dispatch)
    with monkeypatch.context() as paused:
        paused.setattr(family_review.resident_record_domain, "resident_intake_state", lambda _: "closed")
        assert t.check()["status"] == "paused"
    assert t.check()["message_id"] == attempts[0]
    assert t.check()["status"] == "unchanged"


@pytest.mark.parametrize("state", [{}, {"work_state": "unknown", "queued_count": 0},
                                  {"work_state": "idle"}, {"work_state": "idle", "queued_count": 1}])
def test_unknown_or_queued_runtime_is_not_idle(monkeypatch, state):
    monkeypatch.setattr(family_review.resident_protocol, "read_resident_state_snapshots",
                        lambda _: {"steward": {"state": state}})
    assert not family_review._idle("steward")


@pytest.mark.parametrize("kind", ["failed", "maintenance", "info", "role", "unknown"])
def test_only_completed_business_members_record_activity(team, monkeypatch, kind):
    t = team
    event = t.completed(1, state="failed" if kind == "failed" else "completed")
    if kind == "maintenance":
        monkeypatch.setattr(t.repo, "message_business_ref", lambda *_: "duty/work-summary-reminder")
    if kind == "info":
        monkeypatch.setattr(family_review.resident_held_infos, "is_info", lambda *_: True)
    if kind == "role":
        event["subject_resident_id"] = t.family["steward"]
    if kind == "unknown":
        event["payload"]["message_id"] = "msg_" + "f" * 32
    assert t.check(event)["status"] == "skipped"
    assert t.check()["status"] == "unchanged"


def test_structure_upgrade_preserves_interval_custom_prompt_and_user_workflows(team):
    t = team
    body = deepcopy(t.body)
    block = next(x for x in body["function_blocks"] if x["id"] == t.block["id"])
    ids = {x["key"]: x["id"] for x in block["elements"]}
    timer = next(x for x in body["program"]["components"] if x["id"] == ids["periodic_review"])
    timer["config"]["interval_seconds"] = 12345
    route = next(x for x in body["program"]["wires"] if x["id"] == ids["notify_periodic_review"])
    route["config"]["invoke_profiles"]["effect"]["arguments"]["family_tasks"]["value"]["review"]["prompt"] = "Custom ${facts}"
    workflow = {"id": "effect_ffffffffffff", "kind": "effect", "title": "User timer task", "config": {"script": "output ok=true", "endpoints": []}}
    body["program"]["components"].append(deepcopy(workflow))
    block["elements"].append({"id": workflow["id"], "kind": "component", "key": "workflow:" + workflow["id"]})
    # Simulate the old unconditional generated Effect while retaining its stable ID.
    old = next(x for x in body["program"]["components"] if x["id"] == ids["notify_periodic_review_task"])
    old["config"]["script"] = "DispatchFamilyTask resident_id=\"${arguments.facade}\" trigger=\"review\""
    familize.reconcile_in_body(body, block, ACTOR)
    assert "CheckFamilyReview" in old["config"]["script"]
    assert timer["config"]["interval_seconds"] == 12345
    assert route["config"]["invoke_profiles"]["effect"]["arguments"]["family_tasks"]["value"]["review"]["prompt"] == "Custom ${facts}"
    assert next(x for x in body["program"]["components"] if x["id"] == workflow["id"]) == workflow
    snapshot = deepcopy(body)
    familize.reconcile_in_body(body, block, ACTOR)
    assert body == snapshot


def test_generated_check_binds_own_family_but_not_external_or_other_components(team):
    t = team
    f = t.family
    context = InstructionContext(caller="resident_eeeeeeeeeeee", entry="lineage")
    ids = {x["key"]: x["id"] for x in t.block["elements"]}
    args = {"lineage_id": t.body["lineage_id"], "family_id": t.block["id"]}
    event = SimpleNamespace(type="time.reached", payload={}, subject_resident_id="")
    def scoped(key, arguments=args, entry=context):
        return _family_note_context(t.body, ids[key], "CheckFamilyReview", arguments, entry,
                                    lineage_id=t.body["lineage_id"], event=event)
    assert run("CheckFamilyReview", args, actor=context.caller).error
    assert scoped("notify_periodic_review_task").caller == f["facade"]
    assert run("CheckFamilyReview", args, actor=f["facade"], entry="lineage").raise_for_error()["status"] == "unchanged"
    assert scoped("router").caller == context.caller
    assert scoped("notify_periodic_review_task", {**args, "family_id": "fbi_ffffffffffff"}).caller == context.caller
    assert scoped("notify_periodic_review_task", entry=InstructionContext(caller=context.caller, entry="web")).caller == context.caller
    recorded = t.completed(1)
    event.type, event.payload, event.subject_resident_id = recorded["type"], recorded["payload"], recorded["subject_resident_id"]
    assert scoped("review_activity", {**args, "event": recorded}).caller == f["facade"]
    for payload in ["bad", [], {"message_id": "other"}, None]:
        assert scoped("review_activity", {**args, "event": {**recorded, "payload": payload}}).caller == context.caller
    assert t.check({**recorded, "payload": "bad"})["status"] == "skipped"
