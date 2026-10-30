"""Family relays follow real Mailbox groups while preserving source identities."""
from copy import deepcopy
from types import SimpleNamespace
import uuid

import pytest

from management_portal.society_service import (
    family_facade,
    lineage_resident_nodes,
    resident_conversation_stream,
    resident_execution_work,
    resident_mailbox,
    resident_record_domain,
)
from tests.cases.family_test_support import set_role
from management_portal.society_service.resident_execution_storage import queue_input
from management_portal.society_service import resident_conversation_records
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import instruction, team  # noqa: F401
from tests.cases.test_resident_mailbox import _queue


@pytest.fixture
def grouped_family(team):
    member_id, (head, first, second, other) = _queue(team)
    facade_id = team.source["resident_id"]
    set_role(facade_id, family_id="fbi_85089d425601", actor=ACTOR)
    set_role(member_id, family_id="fbi_85089d425601", role="member", actor=ACTOR)
    facade = resident_record_domain.find_resident(facade_id)
    repository = resident_conversation_stream.get_repository()
    requests = []
    for member_message in (first, second):
        message = {
            "message_id": "msg_" + uuid.uuid4().hex, "role": "user", "author_id": ACTOR,
            "content_parts": [{"type": "text", "text": "Family request"}],
            "created_at": "2026-10-26T00:00:00Z",
        }
        repository.append_message(facade_id, message)
        repository.set_input_state(facade_id, message["message_id"], "queued")
        family_facade.complete_relay((facade_id, message["message_id"]), member_id, member_message)
        requests.append(message)
    merged = instruction(team, "MergeMailboxItems", {
        "resident_id": member_id, "message_ids": [first, second],
    })
    assert merged.status == "committed"
    group_id = merged.result["group_message_id"]
    return SimpleNamespace(
        team=team, facade=facade, facade_id=facade_id, member_id=member_id,
        originals=(first, second), head=head, group_id=group_id, requests=requests, repository=repository,
    )


def _run_group(case):
    repository = case.repository
    record = resident_record_domain.find_resident(case.member_id)
    work = resident_execution_work.read(record)
    group_work = next(item for item in work["queue"] if item["user_message_id"] == case.group_id)
    group = repository.state_store.mailbox_entry(case.member_id, case.group_id)["message"]
    repository.append_message(case.member_id, group)
    repository.set_input_state(case.member_id, case.head, "completed")
    # Only the remote Worker result is stubbed. The active turn, Mailbox,
    # conversation, group membership and Family relay all use isolated PG.
    execution_id = "mailbox-group-execution"
    case.team.owners.monkeypatch.setattr(case.team.owners.execution, "states", lambda requested: {
        key: {"execution_id": key, "status": "running"} for key in requested
    })
    resident_execution_work.write(record, {
        **queue_input(group_work), "execution_id": execution_id, "submitted_at": "2026-10-26T00:01:00Z",
        "queue": [item for item in work["queue"] if item["user_message_id"] != case.group_id],
    })
    repository.set_input_state(case.member_id, case.group_id, "running")
    repository.set_state(case.member_id, {
        "work_state": "running", "work_phase": "starting_session", "active_message_id": case.group_id,
    })
    reply = {
        "message_id": "msg_" + uuid.uuid4().hex, "role": "agent", "author_id": case.member_id,
        "reply_to_message_id": case.group_id, "created_at": "2026-10-26T00:01:00Z",
        "content_parts": [{"type": "text", "text": "group progress"}],
    }
    repository.append_message(case.member_id, reply)
    return reply


def _messages(case):
    return family_facade.project_messages(
        case.facade, deepcopy(case.requests),
        input_states={message["message_id"]: "completed" for message in case.requests},
    )


def _nest(case):
    inner_id = case.facade_id
    outer = lineage_resident_nodes.create_lineage_resident(case.team.lineage_id, {
        "title": "Outer facade", "backend": "codex", "mode": "new_session",
    }, ACTOR)["resident"]
    outer_id = outer["resident_id"]
    set_role(outer_id, family_id="fbi_85089d425602", actor=ACTOR)
    set_role(inner_id, family_id="fbi_85089d425602", role="member", actor=ACTOR)
    requests = []
    for inner in case.requests:
        message = {**deepcopy(inner), "message_id": "msg_" + uuid.uuid4().hex}
        case.repository.append_message(outer_id, message)
        case.repository.set_input_state(outer_id, message["message_id"], "queued")
        family_facade.complete_relay((outer_id, message["message_id"]), inner_id, inner["message_id"])
        requests.append(message)
    case.facade_id = outer_id
    case.facade = resident_record_domain.find_resident(outer_id)
    case.requests = requests
    return inner_id


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
def test_running_group_projects_relay_reply_progress_and_runtime(grouped_family, nested, monkeypatch):
    """真实合并输入运行后，原请求保留原成员 ID，并显示组的回复和执行进度。"""
    case = grouped_family
    inner_id = _nest(case) if nested else None
    reply = _run_group(case)
    messages = _messages(case)
    request = next(item for item in messages if item["message_id"] == case.requests[0]["message_id"])
    relay = request["relay"]
    assert relay["state"] == "running"
    assert relay["message_id"] == case.originals[0]
    assert relay["grouped_into_message_id"] == case.group_id
    assert relay["work_state"] == "running"
    assert relay["work_phase"] == "starting_session"
    if inner_id:
        assert [item["resident_id"] for item in relay["via"]] == [inner_id]
    assert any(item.get("role") == "agent" and item["message_id"] == reply["message_id"] for item in messages)
    runtime = family_facade._project_runtime(case.facade, {"state": {"work_state": "idle"}}, {})
    assert runtime["state"]["work_state"] == "running"
    assert runtime["state"]["active_message_id"] in {item["message_id"] for item in case.requests}
    activity = family_facade.relay_activity(case.facade_id)
    assert activity["active"] is True
    assert activity["member_resident_id"] == case.member_id
    # The by-ID lookup uses the same shared reply as the conversation page.
    looked_up = family_facade.projected_reply(case.facade, reply["message_id"])
    assert looked_up["message_id"] == reply["message_id"]
    assert looked_up["author_id"] == case.facade_id
    assert looked_up["reply_to_message_id"] in {item["message_id"] for item in case.requests}

    # complete_relay is called below intake in this storage fixture. Supply
    # intake's immutable forward marks at the metadata boundary. Group
    # membership and reverse-link validation still read the real PG owners.
    links = family_facade.relay_links(case.facade)
    marks = []
    for request, original_id in zip(case.requests, case.originals):
        request_id = request["message_id"]
        if inner_id:
            inner_message = links[request_id]["message_id"]
            marks.append((inner_id, inner_message, family_facade.relay_mark(case.facade_id, request_id)))
            source = (inner_id, inner_message)
        else:
            source = (case.facade_id, request_id)
        marks.append((case.member_id, original_id, family_facade.relay_mark(*source)))
    metadata = {(resident_id, message_id): business_ref for resident_id, message_id, business_ref in marks}
    repository_type = type(case.repository)
    original_business_ref = repository_type.message_business_ref

    def business_ref(self, resident_id, message_id):
        if (resident_id, message_id) in metadata:
            return metadata[(resident_id, message_id)]
        return original_business_ref(self, resident_id, message_id)

    monkeypatch.setattr(repository_type, "message_business_ref", business_ref)
    for resident_id, message_id in metadata:
        family_facade._UNMARKED.pop((resident_id, message_id), None)
    targets = family_facade.projection_targets({
        "resident_id": case.member_id,
        "event_type": resident_conversation_records.MESSAGE_APPENDED,
        "payload": {"message": reply},
    })
    assert set(targets) == ({case.facade_id, inner_id} if inner_id else {case.facade_id})
    state_targets = family_facade.projection_targets({
        "resident_id": case.member_id,
        "event_type": resident_conversation_records.INPUT_STATE_CHANGED,
        "payload": {"input_state": {"message_id": case.group_id, "state": "running"}},
    })
    assert set(state_targets) == set(targets)


def test_queued_group_reports_group_queue_position(grouped_family):
    case = grouped_family
    case.repository.set_state(case.member_id, {"work_state": "running", "active_message_id": case.head})
    for message in _messages(case):
        if message["role"] == "user":
            relay = message["relay"]
            assert relay["state"] == "queued"
            assert relay["message_id"] in case.originals
            assert relay["grouped_into_message_id"] == case.group_id
            assert relay["ahead"] == 1
    assert family_facade.relay_activity(case.facade_id) == {"active": False}
    runtime = family_facade._project_runtime(case.facade, {"state": {}}, {})
    assert runtime["state"]["work_state"] == "queued"


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_group_terminal_projects_before_and_after_member_receipts(grouped_family, terminal):
    """组终态到达和成员落盘两个阶段均保留原身份及共享回复。"""
    case = grouped_family
    reply = _run_group(case)
    case.repository.set_input_state(case.member_id, case.group_id, terminal)
    case.repository.set_state(case.member_id, {"work_state": "idle"})
    for durable in (False, True):
        if durable:
            record = resident_record_domain.find_resident(case.member_id)
            resident_mailbox.finish_group(record, case.group_id, terminal)
        messages = _messages(case)
        for request, original_id in zip(case.requests, case.originals):
            projected = next(item for item in messages if item["message_id"] == request["message_id"])
            assert projected["relay"]["state"] == terminal
            assert projected["relay"]["message_id"] == original_id
            assert any(item.get("reply_to_message_id") == request["message_id"]
                       and item["message_id"] == reply["message_id"] for item in messages)
        assert family_facade.relay_activity(case.facade_id) == {"active": False}
        assert family_facade._project_runtime(case.facade, {"state": {}}, {}) == {}


def test_independently_cancelled_member_does_not_inherit_running_group(grouped_family):
    case = grouped_family
    # The original has an independent withdrawal fact; the sibling still runs.
    reply = _run_group(case)
    case.repository.set_input_state(case.member_id, case.originals[0], "cancelled")
    messages = _messages(case)
    first, second = [item for item in messages if item["role"] == "user"]
    assert first["relay"]["state"] == "cancelled"
    assert first["relay"]["message_id"] == case.originals[0]
    assert "work_state" not in first["relay"]
    assert not any(item.get("message_id") == reply["message_id"]
                   and item.get("reply_to_message_id") == first["message_id"] for item in messages)
    assert second["relay"]["state"] == "running"
    assert family_facade.relay_activity(case.facade_id)["message_id"] == second["message_id"]
    # The group's later cancellation has the same status as the independent
    # withdrawal. Status/reply equality must not manufacture a shared receipt.
    case.repository.set_input_state(case.member_id, case.group_id, "cancelled")
    for durable in (False, True):
        if durable:
            resident_mailbox.finish_group(
                resident_record_domain.find_resident(case.member_id), case.group_id, "cancelled",
            )
        terminal_messages = _messages(case)
        assert not any(item.get("message_id") == reply["message_id"]
                       and item.get("reply_to_message_id") == first["message_id"] for item in terminal_messages)
        assert any(item.get("message_id") == reply["message_id"]
                   and item.get("reply_to_message_id") == second["message_id"] for item in terminal_messages)
        assert all(item["relay"]["state"] == "cancelled"
                   for item in terminal_messages if item["role"] == "user")
    assert family_facade.relay_activity(case.facade_id) == {"active": False}
    assert family_facade._project_runtime(case.facade, {"state": {}}, {}) == {}


def test_split_group_restores_original_queue_positions(grouped_family):
    case = grouped_family
    split = instruction(case.team, "SplitMailboxItem", {
        "resident_id": case.member_id, "message_id": case.group_id,
    })
    assert split.status == "committed"
    assert case.repository.input_state(case.member_id, case.group_id)["error"]["code"] == "mailbox_split"
    case.repository.set_state(case.member_id, {"work_state": "running", "active_message_id": case.head})
    for position, message in enumerate(_messages(case), start=1):
        relay = message["relay"]
        assert relay["state"] == "queued"
        assert relay["message_id"] == case.originals[position - 1]
        assert not relay.get("grouped_into_message_id")
        assert relay["ahead"] == position
    assert family_facade.relay_activity(case.facade_id) == {"active": False}


def test_multiple_relay_requests_batch_group_and_reply_reads(grouped_family, monkeypatch):
    """同一页的两个原请求共用组，读取真实存储时仅批量读取一次。"""
    case = grouped_family
    _run_group(case)
    calls = {"rounds": [], "states": [], "groups": []}
    repository_type = type(case.repository)
    inputs_type = type(case.repository.inputs)
    original_rounds = repository_type.reply_rounds
    original_states = inputs_type.input_states_by_id
    data_type = type(case.repository.agent_data)
    original_groups = data_type.message_groups

    def rounds(self, requests, **kwargs):
        requests = list(requests)
        calls["rounds"].append(requests)
        return original_rounds(self, requests, **kwargs)

    def states(self, requests):
        requests = list(requests)
        calls["states"].append(requests)
        return original_states(self, requests)

    def groups(self, resident_id, message_ids):
        message_ids = list(message_ids)
        calls["groups"].append((resident_id, message_ids))
        return original_groups(self, resident_id, message_ids)

    monkeypatch.setattr(repository_type, "reply_rounds", rounds)
    monkeypatch.setattr(inputs_type, "input_states_by_id", states)
    monkeypatch.setattr(data_type, "message_groups", groups)
    projected = _messages(case)
    assert all(item["relay"]["state"] == "running" for item in projected if item["role"] == "user")
    expected = {(case.member_id, message_id) for message_id in (*case.originals, case.group_id)}
    assert len(calls["rounds"]) == len(calls["states"]) == len(calls["groups"]) == 1
    assert set(calls["rounds"][0]) == set(calls["states"][0]) == expected
    assert len(calls["rounds"][0]) == len(expected)
    assert calls["groups"][0][0] == case.member_id
    assert set(calls["groups"][0][1]) == set(case.originals)
