"""CancelInput follows Family relays and current Mailbox ownership.

:module: resident.session
:design: decision_15f7df737ed5
"""
import pytest

from management_portal.society_service import resident_execution_work
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import instruction, team  # noqa: F401
from tests.cases.test_family_mailbox_projection import grouped_family, _nest, _run_group  # noqa: F401
from tests.cases.test_resident_mailbox import _waiting


def _cancel(case):
    return instruction(case.team, "CancelInput", {
        "resident_id": case.facade_id, "message_id": case.requests[0]["message_id"],
    }).raise_for_error()


@pytest.mark.parametrize("nested", [False, True])
def test_withdraw_original_group_member_preserves_other_requests(grouped_family, nested):
    case = grouped_family
    if nested:
        _nest(case)
    first, second = case.originals
    original_second = case.repository.message(case.member_id, second)
    other = _waiting(case.member_id)[-1]["user_message_id"]
    result = _cancel(case)
    assert result["member_message_id"] == first
    assert result["input_state"]["state"] == "cancelled"
    assert [item["user_message_id"] for item in _waiting(case.member_id)] == [second, other]
    assert case.repository.input_state(case.member_id, second)["state"] == "queued"
    assert case.repository.message(case.member_id, second) == original_second
    assert case.repository.input_state(case.member_id, case.group_id)["error"]["code"] == "mailbox_split"
    assert _cancel(case)["already_terminal"]
    assert [item["user_message_id"] for item in _waiting(case.member_id)] == [second, other]


@pytest.mark.parametrize("nested", [False, True])
def test_stop_original_group_member_targets_current_execution(grouped_family, nested):
    case = grouped_family
    if nested:
        _nest(case)
    _run_group(case)
    waiting_before = _waiting(case.member_id)
    receipt = _cancel(case)
    assert receipt["cancellation_requested"]
    assert receipt["selected_message_id"] == case.group_id
    control = resident_execution_work.read_control(case.member_id)["state"]
    assert control["user_message_id"] == case.group_id
    assert control["stop_requested_by"] == ACTOR
    assert _waiting(case.member_id) == waiting_before


def test_cancel_resolves_regrouped_member_not_old_group(grouped_family):
    case = grouped_family
    other = _waiting(case.member_id)[-1]["user_message_id"]
    latest = instruction(case.team, "MergeMailboxItems", {
        "resident_id": case.member_id, "message_ids": [case.group_id, other],
    }).raise_for_error()["group_message_id"]
    _cancel(case)
    assert [item["user_message_id"] for item in _waiting(case.member_id)] == [case.originals[1], other]
    assert case.repository.input_state(case.member_id, latest)["error"]["code"] == "mailbox_split"


def test_terminal_member_cannot_cancel_the_group(grouped_family):
    case = grouped_family
    case.repository.set_input_state(case.member_id, case.originals[0], "cancelled")
    before = _waiting(case.member_id)
    assert _cancel(case)["already_terminal"]
    assert _waiting(case.member_id) == before


def test_member_withdrawal_rolls_back_queue_and_terminal_states(grouped_family, monkeypatch):
    case = grouped_family
    before = _waiting(case.member_id)
    store = type(case.repository)
    original = store.set_input_state

    def fail_group(self, resident_id, message_id, *args, **kwargs):
        if message_id == case.group_id:
            raise ValueError("synthetic group-state write failure")
        return original(self, resident_id, message_id, *args, **kwargs)

    monkeypatch.setattr(store, "set_input_state", fail_group)
    result = instruction(case.team, "CancelInput", {
        "resident_id": case.facade_id, "message_id": case.requests[0]["message_id"],
    })
    assert result.status == "failed"
    assert _waiting(case.member_id) == before
    for mid in (*case.originals, case.group_id):
        assert case.repository.input_state(case.member_id, mid)["state"] == "queued"


def test_concurrent_split_and_original_withdrawal_keep_other_member(grouped_family):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    case = grouped_family
    barrier = Barrier(2)

    def cancel():
        barrier.wait(timeout=10)
        return _cancel(case)

    def split():
        barrier.wait(timeout=10)
        return instruction(case.team, "SplitMailboxItem", {
            "resident_id": case.member_id, "message_id": case.group_id,
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancellation, splitting = pool.submit(cancel), pool.submit(split)
        assert cancellation.result(timeout=30)["input_state"]["state"] == "cancelled"
        result = splitting.result(timeout=30)
    assert result.status == "committed" or result.error["code"] == "instruction.conflict"
    assert _waiting(case.member_id)[0]["user_message_id"] == case.originals[1]
    assert case.repository.input_state(case.member_id, case.originals[1])["state"] == "queued"


def test_relay_cancellation_still_requires_holder_management(grouped_family, monkeypatch):
    from management_portal.society_service import lineage_aggregate

    case = grouped_family
    before = _waiting(case.member_id)

    def deny(*args, **kwargs):
        raise PermissionError("no management authority")

    monkeypatch.setattr(lineage_aggregate, "ensure_manager", deny)
    result = instruction(case.team, "CancelInput", {
        "resident_id": case.facade_id, "message_id": case.requests[0]["message_id"],
    })
    assert result.status == "failed"
    assert "no management authority" in str(result.error)
    assert _waiting(case.member_id) == before
    assert case.repository.input_state(case.member_id, case.originals[0])["state"] == "queued"
