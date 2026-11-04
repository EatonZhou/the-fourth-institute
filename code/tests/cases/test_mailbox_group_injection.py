"""Send now follows current Mailbox membership, including Family copy IDs."""
import pytest

from management_portal.society_service import (
    resident_conversation_stream, resident_execution_work,
    resident_record_domain,
)
from tests.cases.test_resident_mailbox import _queue, _waiting
from tests.cases.test_wall04_residual_20260919 import instruction, team  # noqa: F401
from tests.cases.test_cross_owner_storage_integration import owners  # noqa: F401


@pytest.fixture
def grouped(team):
    resident_id, ids = _queue(team)
    record = resident_record_domain.find_resident(resident_id)
    job = resident_execution_work.read(record)
    job["submitted_at"] = "2026-11-04T00:00:00Z"
    resident_execution_work.write(record, job)
    group = instruction(team, "MergeMailboxItems", {
        "resident_id": resident_id, "message_ids": ids[1:3],
    }).raise_for_error()["group_message_id"]
    return resident_id, ids, group


@pytest.mark.parametrize("member_index", [1, 2])
def test_original_copy_injects_group_once(team, grouped, member_index):
    """The original copy stays intact; either constituent selects one whole group."""
    resident_id, ids, group = grouped
    repository = resident_conversation_stream.get_repository()
    originals = [repository.message(resident_id, mid) for mid in ids[1:3]]
    result = instruction(team, "InjectMailboxItem", {
        "resident_id": resident_id, "message_id": ids[member_index],
    }).raise_for_error()
    assert result["message_id"] == group
    record = resident_record_domain.find_resident(resident_id)
    job = resident_execution_work.read(record)
    pending = resident_execution_work.send_now_inputs(job)
    assert [entry["user_message_id"] for entry in pending] == [group]
    assert [entry["user_message_id"] for entry in pending[0]["mailbox_members"]] == ids[1:3]
    assert [entry["user_message_id"] for entry in _waiting(resident_id)] == [ids[3]]
    again = instruction(team, "InjectMailboxItem", {
        "resident_id": resident_id, "message_id": ids[3 - member_index],
    }).raise_for_error()
    assert again["already_injected"]
    assert len(resident_execution_work.send_now_inputs(resident_execution_work.read(record))) == 1
    assert [repository.message(resident_id, mid) for mid in ids[1:3]] == originals


def test_split_uses_current_original_queue_item(team, grouped):
    resident_id, ids, group = grouped
    instruction(team, "SplitMailboxItem", {"resident_id": resident_id, "message_id": group}).raise_for_error()
    result = instruction(team, "InjectMailboxItem", {
        "resident_id": resident_id, "message_id": ids[1],
    }).raise_for_error()
    assert result["message_id"] == ids[1]
    assert [item["user_message_id"] for item in _waiting(resident_id)] == ids[2:]


def test_regroup_resolves_latest_group(team, grouped):
    resident_id, ids, group = grouped
    latest = instruction(team, "MergeMailboxItems", {
        "resident_id": resident_id, "message_ids": [group, ids[3]],
    }).raise_for_error()["group_message_id"]
    result = instruction(team, "InjectMailboxItem", {
        "resident_id": resident_id, "message_id": ids[1],
    }).raise_for_error()
    assert result["message_id"] == latest
    assert _waiting(resident_id) == []


def test_cancelled_group_is_not_reopened(team, grouped):
    resident_id, ids, group = grouped
    instruction(team, "CancelInput", {"resident_id": resident_id, "message_id": group}).raise_for_error()
    result = instruction(team, "InjectMailboxItem", {"resident_id": resident_id, "message_id": ids[1]})
    assert result.status == "failed"
    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(resident_id, group)["state"] == "cancelled"
    assert not resident_execution_work.send_now_inputs(resident_execution_work.read(
        resident_record_domain.find_resident(resident_id)))


def test_facade_projected_original_copy_selects_current_group(team, grouped):
    """Use precisely the projected payload sent by the facade's Send now button."""
    from management_portal.society_service import family_facade
    from tests.cases.family_test_support import set_role
    from tests.cases.test_cross_owner_storage_integration import ACTOR

    resident_id, ids, group = grouped
    facade_id = team.source["resident_id"]
    set_role(facade_id, family_id="fbi_112233445566", actor=ACTOR)
    request_id = instruction(team, "DeliverMessage", {
        "resident_id": facade_id, "message": {"content_parts": [{"type": "text", "text": "补充要求"}]},
    }).raise_for_error()["message_id"]
    family_facade.complete_relay((facade_id, request_id), resident_id, ids[1])
    repository = resident_conversation_stream.get_repository()
    facade = resident_record_domain.find_resident(facade_id)
    shown = family_facade.project_messages(facade, [repository.message(facade_id, request_id)], input_states={})
    relay = shown[0]["relay"]
    assert relay["message_id"] == ids[1]
    assert relay["grouped_into_message_id"] == group
    assert relay["state"] == "queued"
    receipt = instruction(team, "InjectMailboxItem", {
        "resident_id": relay["resident_id"], "message_id": relay["message_id"],
    }).raise_for_error()
    assert receipt["message_id"] == group


def test_group_injection_respects_stopping_turn(team, grouped):
    resident_id, ids, group = grouped
    record = resident_record_domain.find_resident(resident_id)
    job = resident_execution_work.read(record)
    job["stop_requested_at"] = "2026-11-04T00:01:00Z"
    resident_execution_work.write(record, job)
    result = instruction(team, "InjectMailboxItem", {
        "resident_id": resident_id, "message_id": ids[1],
    }).raise_for_error()
    assert result["scheduled_next_turn"]
    assert result["message_id"] == group
    assert not resident_execution_work.send_now_inputs(resident_execution_work.read(record))
    assert [item["user_message_id"] for item in _waiting(resident_id)] == [group, ids[3]]


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_original_terminal_state_does_not_drive_its_old_group(team, grouped, state):
    """Independent original terminal facts take precedence over shared progress."""
    resident_id, ids, group = grouped
    repository = resident_conversation_stream.get_repository()
    repository.set_input_state(resident_id, ids[1], state)
    before = _waiting(resident_id)
    result = instruction(team, "InjectMailboxItem", {"resident_id": resident_id, "message_id": ids[1]})
    if state == "completed":
        receipt = result.raise_for_error()
        assert receipt["already_injected"] and receipt["message_id"] == ids[1]
    else:
        assert result.error["code"] == "instruction.conflict"
    assert repository.input_state(resident_id, ids[1])["state"] == state
    assert repository.input_state(resident_id, group)["state"] == "queued"
    assert _waiting(resident_id) == before
    assert not resident_execution_work.send_now_inputs(resident_execution_work.read(
        resident_record_domain.find_resident(resident_id)))
