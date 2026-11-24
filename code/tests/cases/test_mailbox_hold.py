"""Durable single-item holds share Mailbox ordering and admission authority.

:module: resident.session
:design: decision_15f7df737ed5, decision_2a3ff353165c
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from management_portal.society_service import (
    resident_execution_work as work,
    resident_execution_domain as executions,
    resident_input_queue_domain as inputs,
    resident_intake_domain as intake,
    resident_protocol as protocol,
    resident_queue_control_domain as controls,
    resident_record_domain as records,
    resident_conversation_stream,
    lifecycle,
)
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import instruction, team  # noqa: F401
from tests.cases.test_resident_queue_reorder import _queue
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401


def _hold(team, resident_id, message_id, held=True):
    return instruction(team, "SetMailboxItemHeld", {
        "resident_id": resident_id, "message_id": message_id, "held": held,
    })


def _read(resident_id):
    return work.read(records.find_resident(resident_id))


def test_hold_resume_persists_original_envelope_order_and_public_revision(team):
    resident_id, entries = _queue(team)
    message_id = entries[2]["user_message_id"]
    original = copy.deepcopy(_read(resident_id)["queue"])
    before = instruction(team, "GetMailbox", {"resident_id": resident_id}).result
    held = _hold(team, resident_id, message_id)
    assert held.status == "committed", held.error
    assert held.result["held"] is True and held.result["changed"] is True
    assert _hold(team, resident_id, message_id).result["changed"] is False
    current = _read(resident_id)
    assert current["user_message_id"] == entries[0]["user_message_id"]
    for actual, previous in zip(current["queue"], original):
        actual.pop("mailbox_held", None)
        assert actual == previous
    view = instruction(team, "GetMailbox", {"resident_id": resident_id}).result
    assert view["revision"] != before["revision"]
    assert [item["held"] for item in view["pending"]] == [False, True, False]
    resumed = _hold(team, resident_id, message_id, False)
    assert resumed.status == "committed" and resumed.result["held"] is False
    assert [item["user_message_id"] for item in _read(resident_id)["queue"]] == [e["user_message_id"] for e in entries[1:]]


def test_held_queue_is_skipped_after_failure_and_all_held_waits_without_admission(team):
    resident_id, entries = _queue(team, 3)
    first, last = (e["user_message_id"] for e in entries[1:])
    assert _hold(team, resident_id, first).status == "committed"
    job = _read(resident_id)
    job["failure_message_persisted_at"] = "2026-11-16T09:00:00Z"
    assert inputs.promote_queue_after_failed_execution(job, resident_id, "2026-11-16T09:00:01Z", "failed", {})
    assert job["user_message_id"] == last
    assert [i["user_message_id"] for i in job["queue"]] == [first]
    job["failure_message_persisted_at"] = "2026-11-16T09:00:02Z"
    assert inputs.promote_queue_after_failed_execution(job, resident_id, "2026-11-16T09:00:03Z", "failed", {})
    work.write(records.find_resident(resident_id), job)
    persisted = _read(resident_id)
    assert persisted["user_message_id"] == first
    assert work.is_execution_held(persisted)
    assert not persisted.get("intake_admitted_at")
    assert not work.continuation_needed(records.find_resident(resident_id), persisted)
    assert _hold(team, resident_id, first, False).status == "committed"
    assert not work.is_mailbox_held(_read(resident_id))
    # Resuming one message must not override the Resident's separate closed gate.
    assert work.is_intake_held(_read(resident_id))


def test_hold_refuses_admitted_head_and_unknown_message(team):
    resident_id, entries = _queue(team)
    snapshot = _read(resident_id)
    for message_id in (entries[0]["user_message_id"], "msg_0123456789abcdef"):
        denied = _hold(team, resident_id, message_id)
        assert denied.status == "failed" and denied.error["code"] == "instruction.conflict"
    assert _read(resident_id) == snapshot


def test_held_merging_splitting_and_reorder_preserve_hold_and_injection_refuses(team):
    resident_id, entries = _queue(team, 4)
    a, b, c = (e["user_message_id"] for e in entries[1:])
    assert _hold(team, resident_id, a).status == "committed"
    mixed = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a, b]})
    assert mixed.status == "failed" and mixed.error["code"] == "instruction.conflict"
    assert _hold(team, resident_id, b).status == "committed"
    merged = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a, b]})
    assert merged.status == "committed", merged.error
    group_id = merged.result["group_message_id"]
    assert instruction(team, "MoveMailboxItems", {"resident_id": resident_id, "message_id": group_id, "after_message_id": c}).status == "committed"
    injected = instruction(team, "InjectMailboxItem", {"resident_id": resident_id, "message_id": group_id})
    assert injected.status == "failed" and "恢复" in injected.error["message"]
    split = instruction(team, "SplitMailboxItem", {"resident_id": resident_id, "message_id": group_id})
    assert split.status == "committed", split.error
    queue = _read(resident_id)["queue"]
    assert [i["user_message_id"] for i in queue] == [c, a, b]
    assert [work.is_mailbox_held(i) for i in queue] == [False, True, True]


def test_clear_and_cancel_include_held_items(team):
    resident_id, entries = _queue(team, 4)
    a, b, c = (e["user_message_id"] for e in entries[1:])
    assert _hold(team, resident_id, a).status == "committed"
    cancelled = instruction(team, "CancelInput", {"resident_id": resident_id, "message_id": a})
    assert cancelled.status == "committed", cancelled.error
    assert instruction(team, "CancelInput", {"resident_id": resident_id, "message_id": a}).status == "committed"
    assert _hold(team, resident_id, b).status == "committed"
    cleared = instruction(team, "ClearMailbox", {"resident_id": resident_id})
    assert cleared.status == "committed", cleared.error
    assert cleared.result["cancelled"] == 2
    assert _read(resident_id)["queue"] == []
    assert _hold(team, resident_id, b, False).status == "failed"


def _open_without_worker(team, monkeypatch):
    # Keep the real intake/admission and durable owner; only Worker wake is
    # suppressed so these tests cannot issue a Provider/model request.
    monkeypatch.setattr(lifecycle, "notify_execution_binding", lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, "wake_execution_binding", lambda *a, **k: None)
    protocol.change_resident_state(team.target["resident_id"], ACTOR, state_domain="intake", transition="open")


def _finish_current(resident_id):
    record = records.find_resident(resident_id)
    with records.state_lock(resident_id):
        job = work.read(record)
        job["continuation_id"] = "hold-test-finished"
        job["completion_applied_at"] = "2026-11-16T09:00:00Z"
        work.write(record, job)
    return executions._finalize_applied_execution(resident_id, "hold-test-finished", job)


def test_open_intake_normal_completion_skips_held_then_new_delivery_skips_held_head(team, monkeypatch):
    from tests.cases.test_resident_mailbox import _queue as queue_senders
    _open_without_worker(team, monkeypatch)
    resident_id, (_, held_id, human_id) = queue_senders(
        team, 3, actors=[team.source["resident_id"], team.source["resident_id"], ACTOR],
    )
    assert _hold(team, resident_id, held_id).status == "committed"
    # Real completion owner, not its queue selection helper.
    assert _finish_current(resident_id)
    admitted = _read(resident_id)
    assert admitted["user_message_id"] == human_id
    assert admitted["intake_admitted_at"]
    assert [i["user_message_id"] for i in admitted["queue"]] == [held_id]
    assert _finish_current(resident_id)
    held_head = _read(resident_id)
    assert held_head["user_message_id"] == held_id and work.is_execution_held(held_head)
    assert not work.continuation_needed(records.find_resident(resident_id), held_head)
    # Ordinary actual intake owner receives the next human input, keeping its
    # canonical envelope and original ID, while the Agent's held input waits.
    delivered = intake.persist_resident_input(resident_id, ACTOR, "human next request")
    new_id = delivered["message_id"]
    admitted = _read(resident_id)
    assert admitted["user_message_id"] == new_id and admitted["intake_admitted_at"]
    assert admitted["input_message"]["author_id"] == ACTOR
    assert [i["user_message_id"] for i in admitted["queue"]] == [held_id]
    assert work.is_mailbox_held(admitted["queue"][0])
    assert not admitted["queue"][0].get("execution_id")
    assert _hold(team, resident_id, held_id, False).status == "committed"


@pytest.mark.parametrize("hold_first", [True, False], ids=["hold-wins", "admission-wins"])
def test_hold_and_actual_completion_admission_are_mutually_exclusive(team, monkeypatch, hold_first):
    _open_without_worker(team, monkeypatch)
    resident_id, entries = _queue(team, 3)
    first, last = (e["user_message_id"] for e in entries[1:])
    started = threading.Event()
    def competing_hold():
        started.set()
        return controls.set_mailbox_item_hold(resident_id, ACTOR, first, True)

    if hold_first:
        assert _hold(team, resident_id, first).status == "committed"
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(_finish_current, resident_id).result(10)
        assert _read(resident_id)["user_message_id"] == last
        assert work.is_mailbox_held(_read(resident_id)["queue"][0])
    else:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with records.state_lock(resident_id):
                waiting = pool.submit(competing_hold)
                assert started.wait(2)
                assert _finish_current(resident_id)
            with pytest.raises(ValueError, match="接单"):
                waiting.result(10)
        assert _read(resident_id)["user_message_id"] == first
        assert _read(resident_id)["intake_admitted_at"]
        assert not work.is_mailbox_held(_read(resident_id))


def test_all_held_head_reorders_merges_splits_and_cancels_as_waiting_input(team, monkeypatch):
    _open_without_worker(team, monkeypatch)
    resident_id, entries = _queue(team, 4)
    a, b, c = (e["user_message_id"] for e in entries[1:])
    for mid in (a, b, c):
        assert _hold(team, resident_id, mid).status == "committed"
    assert _finish_current(resident_id)
    moved = instruction(team, "MoveMailboxItems", {"resident_id": resident_id, "message_id": a, "after_message_id": c})
    assert moved.status == "committed", moved.error
    pending = instruction(team, "GetMailbox", {"resident_id": resident_id}).result["pending"]
    assert [i["message_id"] for i in pending] == [b, c, a]
    assert all(i["held"] for i in pending)
    merged = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [b, c]})
    assert merged.status == "committed", merged.error
    group_id = merged.result["group_message_id"]
    assert _read(resident_id)["user_message_id"] == group_id
    assert work.is_mailbox_held(_read(resident_id))
    queue = instruction(team, "GetConversation", {"resident_id": resident_id}).result["queue"]["items"]
    assert [item["message_id"] for item in queue] == [group_id, a]
    assert queue[0]["held"] is True
    summary = queue[0]["content_preview"]
    assert summary.startswith("Mailbox · 2 条消息\n")
    assert summary.index(b) < summary.index(c)
    assert queue[0]["content_parts"] == [{"type": "text", "text": summary}]
    split = instruction(team, "SplitMailboxItem", {"resident_id": resident_id, "message_id": group_id})
    assert split.status == "committed", split.error
    assert [i["message_id"] for i in instruction(team, "GetMailbox", {"resident_id": resident_id}).result["pending"]] == [b, c, a]
    assert instruction(team, "CancelInput", {"resident_id": resident_id, "message_id": b}).status == "committed"
    assert _read(resident_id)["user_message_id"] == c
    assert work.is_execution_held(_read(resident_id))


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("legacy_text", [False, True], ids=["jsonb", "legacy-text"])
def test_family_relay_held_comes_only_from_current_waiting_holder(grouped_family, nested, legacy_text):
    from tests.cases.test_family_mailbox_projection import _messages, _nest
    case = grouped_family
    if legacy_text:
        # Upgraded installations retain TEXT JSON columns; fresh test schemas
        # use JSONB. Exercise the same Family conversation projection on both.
        from management_portal.society_service.state_store import get_lineage_data_store
        from management_portal.society_service.resident_execution_storage import RECOVERY_INDEXES
        with get_lineage_data_store().database.transaction() as connection:
            # PostgreSQL simplifies a JSONB-to-JSONB cast in index expressions;
            # rebuild that index after recreating the legacy column type.
            connection.execute("DROP INDEX resident_failed_execution_idx")
            connection.execute("ALTER TABLE resident_execution_work ALTER COLUMN progress_json TYPE TEXT USING progress_json::text")
            connection.execute("CREATE INDEX resident_failed_execution_idx " + RECOVERY_INDEXES["resident_failed_execution_idx"])
            connection.execute("ALTER TABLE resident_mailbox ALTER COLUMN item_json TYPE TEXT USING item_json::text")
    if nested:
        _nest(case)
    assert _hold(case.team, case.member_id, case.group_id).status == "committed"
    shown = [m for m in _messages(case) if m.get("role") == "user"]
    assert all(m["relay"]["held"] for m in shown)
    assert all(m["relay"]["resident_id"] == case.member_id for m in shown)
    assert _hold(case.team, case.member_id, case.group_id, False).status == "committed"
    assert all(not m["relay"]["held"] for m in _messages(case) if m.get("role") == "user")


def test_real_runner_finishes_human_followup_and_stops_before_held_agent_input(resident_record, monkeypatch):
    from management_portal.society_service import resident_backend
    resident_id = resident_record["resident_id"]
    monkeypatch.setattr(lifecycle, "notify_execution_binding", lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, "wake_execution_binding", lambda *a, **k: None)
    first = {"execution_id": "execution-" + "5" * 24, "bootstrap_session": True, "actor": "CASE-A",
             "user_message_id": "message_first_turn", "message": "first", "raw_message": "first", "queue": [],
             **work.continuation_fields(resident_record)}
    work.bind_execution(first, execution_id=first["execution_id"], now="2026-11-16T09:00:00Z")
    work.write(resident_record, first)
    sent, waiting = [], []
    def send(record, job, **_options):
        sent.append(job["user_message_id"])
        if len(sent) == 1:
            waiting.append(intake.persist_resident_input(resident_id, resident_id, "Agent queued request")["message_id"])
            waiting.append(intake.persist_resident_input(resident_id, "CASE-A", "Human queued request")["message_id"])
            controls.set_mailbox_item_hold(resident_id, "CASE-A", waiting[0], True)
        return {"role": "agent", "execution_id": job["execution_id"], "created_at": "2026-11-16T09:01:00Z",
                "content_parts": [{"type": "text", "text": "done"}]}, dict(record, session_state="active")
    monkeypatch.setattr(resident_backend, "record_session_id", lambda *a, **k: "")
    monkeypatch.setattr(executions, "send_to_resident_backend", send)
    monkeypatch.setattr(inputs, "acknowledge_execution", lambda *a, **k: True)
    executions.run_resident_execution(resident_id, first["continuation_id"])
    assert sent == ["message_first_turn", waiting[1]]
    held = work.read(resident_record)
    assert held["user_message_id"] == waiting[0] and work.is_execution_held(held)
    assert not held.get("intake_admitted_at")
    assert not work.continuation_needed(resident_record, held)


# Import the formal isolated-PG Family fixture; no production Family or route
# is created or invoked by these tests.
from tests.cases.test_family_mailbox_projection import grouped_family  # noqa: E402,F401
