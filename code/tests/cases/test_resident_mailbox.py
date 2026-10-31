"""Mailbox merge and split (decision_15f7df737ed5).

:module: resident.session
"""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from management_portal.society_service import (
    resident_conversation_stream,
    resident_execution_work,
    resident_lifecycle_domain,
    resident_mailbox,
    resident_record_domain,
    resident_execution_domain,
    resident_input_queue_domain,
    resident_queue_control_domain,
)
from management_portal.society_service import resident_protocol
from management_portal.society_service import resident_conversation_records
from management_portal.resources.portal import portal_route_script
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401 - fixture
from tests.cases.test_wall04_residual_20260919 import instruction, team  # noqa: F401 - fixture

LOCAL_POPEN = subprocess.Popen


def _queue(team, count: int = 4, *, actors: list[str] | None = None) -> tuple[str, list[str]]:
    resident_id = team.target["resident_id"]
    entries = []
    for index in range(count):
        message_id = "msg_" + uuid.uuid4().hex
        actor = (actors or [])[index] if actors else ACTOR
        user_item = resident_input_queue_domain.build_message("user", f"第 {index} 条：{message_id}", actor, message_id=message_id)
        # The head runs, so it is in the trace; the others wait in the Mailbox.
        if index == 0:
            resident_lifecycle_domain.append_conversation_message(resident_id, user_item)
        else:
            resident_lifecycle_domain.accept_resident_user_message(resident_id, user_item)
        resident_conversation_stream.get_repository().set_input_state(resident_id, message_id, "queued")
        entries.append({
            **({"execution_id": f"mailbox-execution-{index}"} if index == 0 else {}), "user_message_id": message_id, "input_message": user_item,
            "message": f"第 {index} 条", "actor": actor,
            "enqueued_at": (datetime(2026, 10, 3, tzinfo=timezone.utc) + timedelta(minutes=index)).isoformat(),
        })
    record = resident_record_domain.find_resident(resident_id)
    resident_execution_work.write(record, {**entries[0], "queue": entries[1:]})
    return resident_id, [item["user_message_id"] for item in entries]


def _waiting(resident_id: str) -> list[dict]:
    return resident_execution_work.read(resident_record_domain.find_resident(resident_id)).get("queue", [])


@pytest.mark.parametrize("source", ["events", "history"])
def test_delivered_mailbox_group_has_original_bodies_before_snapshot_refresh(team, source, monkeypatch):
    """合并消息的实时追加、更新及历史分页均展开原文、作者和时间，无需刷新完整对话。"""
    resident_id, (head, a, b, _other) = _queue(team)
    repository = resident_conversation_stream.get_repository()
    originals = [repository.message(resident_id, member_id) for member_id in (a, b)]
    expected = [{key: member[key] for key in (
        "message_id", "author_id", "origin", "created_at", "content_parts",
    ) if key in member} for member in originals]
    merged = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a, b]})
    assert merged.status == "committed"
    group_id = merged.result["group_message_id"]
    before = resident_protocol.get_resident_conversation_snapshot(resident_id, ACTOR)
    group = repository.state_store.mailbox_entry(resident_id, group_id)["message"]
    repository.append_message(resident_id, group)
    repository.set_input_state(resident_id, group_id, "running")

    if source == "events":
        repository.update_message(resident_id, group_id, message={**group, "name": "Mailbox"})
        events = repository.events_after(resident_id, before["cursor"])["events"]
        delivered = [event["payload"]["message"] for event in events
                     if event["event_type"] in {
                         resident_conversation_records.MESSAGE_APPENDED, resident_conversation_records.MESSAGE_UPDATED,
                     } and event.get("payload", {}).get("message", {}).get("message_id") == group_id]
        assert len(delivered) == 2  # appended and updated must both keep the body
    else:
        tail = resident_input_queue_domain.build_message("agent", "后续回复", resident_id)
        repository.append_message(resident_id, tail)
        latest = resident_protocol.get_resident_conversation_snapshot(resident_id, ACTOR, history_limit=1)
        page = resident_protocol.get_conversation_messages(resident_id, ACTOR, latest["next_page_token"])
        delivered = [message for message in page["messages"] if message["message_id"] == group_id]
        assert len(delivered) == 1

    for message in delivered:
        assert message.get("expanded_members") == expected
        assert message["content_parts"] == group["content_parts"]
    refreshed = resident_protocol.get_resident_conversation_snapshot(resident_id, ACTOR)
    assert next(message for message in refreshed["messages"] if message["message_id"] == group_id)["expanded_members"] == expected
    # Expansion is a read projection; the stored group only references its source members.
    assert "expanded_members" not in repository.message(resident_id, group_id)
    assert [message["message_id"] for message in refreshed["messages"]].count(a) == 0
    assert [message["message_id"] for message in refreshed["messages"]].count(b) == 0
    plain = repository.message(resident_id, head)
    command = ["node", str(Path(__file__).with_name("javascript") / "chat_mailbox_delivery.cjs")]

    def local_dom_only(args, *positional, **kwargs):
        assert args == command, "Only the local Chat DOM fixture may run a subprocess"
        return LOCAL_POPEN(args, *positional, **kwargs)

    with monkeypatch.context() as browser:
        browser.setattr(subprocess, "Popen", local_dom_only)
        result = subprocess.run(
            command,
            input=json.dumps({"routeScript": portal_route_script().decode(), "delivered": delivered,
                              "plain": plain, "expected": expected}),
            capture_output=True, text=True, timeout=30,
        )
    assert result.returncode == 0, result.stdout + result.stderr


def test_quiescing_cancels_waiting_group_members(team):
    """取消当前执行且不继续队列时，等待中的群体消息及其成员全部取消"""
    resident_id, (head, a, b, c) = _queue(team)
    merged = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a, b]})
    group_id = merged.result["group_message_id"]
    resident_execution_domain.cancel_resident_execution_for_target(resident_id, ACTOR, head, continue_queue=False)
    repository = resident_conversation_stream.get_repository()
    for message_id in (group_id, a, b, c):
        assert repository.input_state(resident_id, message_id)["state"] == "cancelled"
    assert _waiting(resident_id) == []


def test_withdraw_rolls_back_terminal_states_if_queue_write_fails(team, monkeypatch):
    """撤回群体消息时队列写入失败，群体和成员的状态回滚为 queued"""
    resident_id, (_head, a, b, _c) = _queue(team)
    merged = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a, b]})
    group_id = merged.result["group_message_id"]
    monkeypatch.setattr(resident_execution_work, "write", lambda *_: (_ for _ in ()).throw(RuntimeError("write failed")))
    with pytest.raises(RuntimeError, match="write failed"):
        resident_queue_control_domain._cancel_resident_queue_item(resident_id, ACTOR, group_id)
    repository = resident_conversation_stream.get_repository()
    for message_id in (group_id, a, b):
        assert repository.input_state(resident_id, message_id)["state"] == "queued"
    assert _waiting(resident_id)[0]["user_message_id"] == group_id


def test_merge_puts_one_group_where_the_first_member_waited_and_split_restores_the_members(team):
    """合并后群体放在最早成员的位置，拆分后成员原样回到队列

    MergeMailboxItems 生成署名为 Mailbox 的群体消息；SplitMailboxItem 把原消息按顺序放回，群体以 mailbox_split 结束。

    :design: decision_15f7df737ed5, decision_2a3ff353165c
    """
    resident_id, (head, a, x, b) = _queue(team)

    merged = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [b, a]})

    assert merged.status == "committed"
    group_id = merged.result["group_message_id"]
    assert merged.result["member_message_ids"] == [a, b]
    waiting = _waiting(resident_id)
    assert [item["user_message_id"] for item in waiting] == [group_id, x]
    assert [member["user_message_id"] for member in waiting[0]["mailbox_members"]] == [a, b]
    assert "第 1 条" in waiting[0]["message"] and "第 3 条" in waiting[0]["message"]
    repository = resident_conversation_stream.get_repository()
    group = repository.message(resident_id, group_id)
    assert group["author_id"] == "mailbox"
    assert group["origin"]["schema"] == "tfi.message-origin.v2"
    assert group["origin"]["initiated_by"]["id"] == ACTOR
    assert group["content_parts"][0]["members"] == [{"message_id": a}, {"message_id": b}]
    member_state = instruction(team, "GetInputState", {"resident_id": resident_id, "message_id": a}).result["input_state"]
    assert member_state["state"] == "queued" and member_state["grouped_into_message_id"] == group_id
    record = resident_record_domain.find_resident(resident_id)
    assert resident_execution_work.read(record)["user_message_id"] == head

    split = instruction(team, "SplitMailboxItem", {"resident_id": resident_id, "message_id": group_id})

    assert split.status == "committed"
    assert [item["user_message_id"] for item in _waiting(resident_id)] == [a, b, x]
    ended = repository.input_state(resident_id, group_id)
    assert ended["state"] == "cancelled" and ended["error"]["code"] == "mailbox_split"
    assert "grouped_into_message_id" not in instruction(
        team, "GetInputState", {"resident_id": resident_id, "message_id": a},
    ).result["input_state"]


def test_large_groups_merge_regroup_and_split_without_losing_members(team):
    """上百条消息合并、再合并和拆分后成员不丢失，队列恢复原状

    :module: resident.identity"""
    resident_id, (head, before, *tail) = _queue(team, 104)
    members, after = tail[:-1], tail[-1]
    original = _waiting(resident_id)

    merged = instruction(team, "MergeMailboxItems", {
        "resident_id": resident_id, "message_ids": list(reversed(members[:51])),
    })
    assert merged.status == "committed"
    first_group = merged.result["group_message_id"]
    assert merged.result["member_message_ids"] == members[:51]
    assert [item["user_message_id"] for item in _waiting(resident_id)] == [
        before, first_group, *members[51:], after,
    ]

    regrouped = instruction(team, "MergeMailboxItems", {
        "resident_id": resident_id, "message_ids": [*reversed(members[51:]), first_group],
    })
    assert regrouped.status == "committed"
    group_id = regrouped.result["group_message_id"]
    assert regrouped.result["member_message_ids"] == members
    waiting = _waiting(resident_id)
    assert [item["user_message_id"] for item in waiting] == [before, group_id, after]
    group = waiting[1]
    assert group["mailbox_members"] == original[1:-1]
    assert all(member_id in group["message"] for member_id in members)
    repository = resident_conversation_stream.get_repository()
    assert repository.message(resident_id, group_id)["content_parts"][0]["members"] == [
        {"message_id": member_id} for member_id in members
    ]
    assert repository.input_state(resident_id, first_group)["state"] == "cancelled"
    assert resident_execution_work.read(resident_record_domain.find_resident(resident_id))["user_message_id"] == head

    split = instruction(team, "SplitMailboxItem", {"resident_id": resident_id, "message_id": group_id})
    assert split.status == "committed"
    assert split.result["member_message_ids"] == members
    assert _waiting(resident_id) == original
    assert repository.input_state(resident_id, group_id)["state"] == "cancelled"
    for member_id in members:
        state = repository.input_state(resident_id, member_id)
        assert state["state"] == "queued"
        assert not state.get("grouped_into_message_id")


def test_merging_a_group_keeps_one_level_and_the_finished_group_finishes_its_members(team):
    """群体再合并仍是一层，群体完成后成员一起完成

    选中已有群体时按成员展开再合并；群体完成后每个成员得到同样的终态和回复。

    :design: decision_15f7df737ed5
    """
    resident_id, (_head, a, b, c) = _queue(team)
    first = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a, b]}).result

    second = instruction(team, "MergeMailboxItems", {
        "resident_id": resident_id, "message_ids": [first["group_message_id"], c],
    }).result

    group_id = second["group_message_id"]
    assert second["member_message_ids"] == [a, b, c]
    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(resident_id, first["group_message_id"])["state"] == "cancelled"
    # The group is delivered with its members; its reply answers them all.
    repository.append_message(resident_id, repository.state_store.mailbox_entry(resident_id, group_id)["message"])
    reply = resident_input_queue_domain.build_message(
        "agent", "一起处理完了", "", message_id="msg_" + uuid.uuid4().hex, reply_to_message_id=group_id,
    )
    resident_lifecycle_domain.append_conversation_message(resident_id, reply)
    repository.set_input_state(resident_id, group_id, "completed")

    record = resident_record_domain.find_resident(resident_id)
    assert resident_mailbox.finish_group(record, group_id, "completed") == [a, b, c]
    for member in (a, b, c):
        state = repository.input_state(resident_id, member)
        assert state["state"] == "completed" and state["reply_message_ids"] == [reply["message_id"]]


def test_messages_from_different_senders_merge_and_run_as_the_organizer(team):
    """不同发送者的消息可以合并，群体按整理者执行

    群体输入逐条写明发送者并提示由 Resident 自行判断；多发送者的群体以整理者身份执行，单一发送者保持原身份。

    :design: decision_15f7df737ed5
    """
    other = "resident_eeeeeeeeeeee"
    resident_id, (_head, a, b, c) = _queue(team, actors=[ACTOR, ACTOR, other, ACTOR])

    merged = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a, b]})

    assert merged.status == "committed"
    group = _waiting(resident_id)[0]
    assert [member["actor"] for member in group["mailbox_members"]] == [ACTOR, other]
    # Each member keeps its sender in the input; the Resident judges each request by it.
    assert "来自不同发送者" in group["message"] and f"· {other} ·" in group["message"]
    assert group["actor"] == ACTOR
    regrouped = instruction(team, "MergeMailboxItems", {
        "resident_id": resident_id, "message_ids": [c, merged.result["group_message_id"]],
    })
    assert regrouped.status == "committed" and regrouped.result["member_message_ids"] == [a, b, c]


def test_single_unknown_or_non_group_items_are_not_organized(team):
    """单条、未知或非群体消息不能合并或拆分

    合并少于两条、包含不在队列中的消息，或拆分普通消息时返回冲突，队列不变。

    :design: decision_15f7df737ed5, decision_2a3ff353165c
    """
    resident_id, (_head, a, b, _c) = _queue(team)

    single = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a]})
    gone = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a, "msg_" + "0" * 32]})
    not_group = instruction(team, "SplitMailboxItem", {"resident_id": resident_id, "message_id": a})

    assert single.error["code"] == "instruction.conflict"
    assert gone.error["code"] == "instruction.conflict"
    assert not_group.error["code"] == "instruction.conflict"
    assert [item["user_message_id"] for item in _waiting(resident_id)] == [a, b, _c]


def test_cancelling_a_group_cancels_its_members_and_its_terminal_fact_finishes_them(team):
    """取消群体消息会取消全部成员，群体终态带给成员

    取消等待中的群体时成员一起取消；群体执行结束的终态事实把同样的终态写给每个成员。

    :design: decision_15f7df737ed5
    """
    from management_portal.society_service.runtime import backend_adapter
    from management_portal.society_service.runtime.contracts import ResidentTerminalFact

    resident_id, (_head, a, b, c, d) = _queue(team, 5)
    repository = resident_conversation_stream.get_repository()
    cancelled_group = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a, b]}).result
    instruction(team, "CancelInput", {"resident_id": resident_id, "message_id": cancelled_group["group_message_id"]})
    assert [repository.input_state(resident_id, member)["state"] for member in (a, b)] == ["cancelled", "cancelled"]

    finished_group = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [c, d]}).result
    group_id = finished_group["group_message_id"]
    repository.set_input_state(resident_id, group_id, "failed", error={"message": "provider error"})
    members = backend_adapter._mailbox_terminal(ResidentTerminalFact(
        resident_id=resident_id, actor=ACTOR, message_id=group_id, terminal_status="failed",
    ))
    assert members == [c, d]
    assert [repository.input_state(resident_id, member)["state"] for member in (c, d)] == ["failed", "failed"]


def test_cancel_input_ends_an_input_nothing_holds_and_leaves_held_ones(team):
    """队列丢下的请求和没有任何工作在处理的 running 输入可以用 CancelInput 结束

    回执停在 queued 或 running、却不在队列里、不是当前工作、也没有未结束的 Execution 的输入永远不会结束；
    CancelInput 把它标为 cancelled 并移出 Mailbox。队列里仍在等待的请求照常撤回。
    """
    from management_portal.society_service.state_store import get_lineage_data_store

    resident_id, (_head, waiting) = _queue(team, 2)
    repository = resident_conversation_stream.get_repository()
    dropped = "msg_" + uuid.uuid4().hex
    resident_lifecycle_domain.accept_resident_user_message(
        resident_id, resident_input_queue_domain.build_message("user", "队列丢下的请求", ACTOR, message_id=dropped))
    repository.set_input_state(resident_id, dropped, "queued")
    stalled = "msg_" + uuid.uuid4().hex
    repository.set_input_state(resident_id, stalled, "running")
    assert get_lineage_data_store().mailbox_entry(resident_id, dropped)["place"] == "pending"

    for message_id in (dropped, stalled):
        result = instruction(team, "CancelInput", {"resident_id": resident_id, "message_id": message_id})
        assert result.result, result.error
        assert result.result["input_state"]["state"] == "cancelled"
        assert repository.input_state(resident_id, message_id)["error"]["code"] == "lost_input"
    assert get_lineage_data_store().mailbox_entry(resident_id, dropped) is None

    instruction(team, "CancelInput", {"resident_id": resident_id, "message_id": waiting})
    assert "error" not in repository.input_state(resident_id, waiting)
    assert _waiting(resident_id) == []


def test_cancel_input_leaves_a_finished_run_to_its_own_outcome(team):
    """CancelInput 不把已有 Execution 的输入记成丢失

    运行结束后，结果要在状态锁释放后才写进输入状态；这期间输入已经不在当前工作里，看起来没有任何工作在处理。
    这时 CancelInput 不把它记成 lost_input，输入状态随后按这次运行的结果结束。
    """
    from tests.cases.society_service_test_support import completed_execution

    resident_id, _ = _queue(team, 1)
    repository = resident_conversation_stream.get_repository()
    finished = "msg_" + uuid.uuid4().hex
    repository.set_input_state(resident_id, finished, "running")
    completed_execution(resident_id, finished)

    instruction(team, "CancelInput", {"resident_id": resident_id, "message_id": finished})
    assert repository.input_state(resident_id, finished)["state"] == "running"

    repository.set_input_state(resident_id, finished, "completed")
    assert repository.input_state(resident_id, finished)["state"] == "completed"
