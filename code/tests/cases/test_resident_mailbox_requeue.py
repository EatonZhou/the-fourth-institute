"""Putting a stopped or failed round back in the Mailbox, withdrawing a waiting info, and the Mailbox view (decision_b5d349190743).

:module: resident.conversation
"""

from __future__ import annotations
from datetime import datetime, timezone

import uuid

from management_portal.society_service import (
    resident_conversation_stream,
    resident_held_infos,
    resident_execution_work,
    resident_lifecycle_domain,
    resident_mailbox,
    resident_input_checkpoints,
    resident_access,
    resident_domain_common,
    resident_record_domain,
    resident_input_queue_domain,
)
from management_portal.society_service.state_store import get_lineage_data_store
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.execution import execution_inputs
from management_portal.society_service import resident_backend
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401 - fixture
from tests.cases.test_resident_mailbox import _queue, _waiting
from tests.cases.test_wall04_residual_20260919 import instruction, team  # noqa: F401 - fixture

STEWARD = "resident_5ee5ee5ee5ee"


def test_mailbox_actor_activity_aggregates_valid_inputs_and_excludes_business_refs(team):
    """Mailbox 按发送者统计各 Resident 的最近输入时间，可排除指定 business_ref 的输入

    :module: resident.identity"""
    resident_id = team.target["resident_id"]
    store = get_lineage_data_store()
    first = resident_input_queue_domain.build_message(
        "user", "first", ACTOR, message_id="msg_" + uuid.uuid4().hex,
        created_at="2026-10-01T00:00:00Z",
    )
    second = resident_input_queue_domain.build_message(
        "user", "second", ACTOR, message_id="msg_" + uuid.uuid4().hex,
        created_at="2026-10-02T00:00:00Z",
    )
    second["business_ref"] = "relay:test"
    store.mailbox_accept(resident_id, first)
    store.mailbox_accept(resident_id, second)

    assert store.mailbox_actor_activity(ACTOR)[resident_id] == "2026-10-02T00:00:00Z"
    assert store.mailbox_actor_activity(ACTOR, excluded_business_ref="relay")[resident_id] == "2026-10-01T00:00:00Z"


def _record(resident_id: str) -> dict:
    return resident_record_domain.find_resident(resident_id)


def _info(resident_id: str, author: str, text: str) -> str:
    """An info message waiting for the next request, as attach_to_next leaves it."""

    info_id = "msg_" + uuid.uuid4().hex
    info = resident_input_queue_domain.build_message("user", text, author, message_id=info_id)
    repository = resident_conversation_stream.get_repository()
    repository.accept_user_message(resident_id, info)
    repository.set_input_state(resident_id, info_id, "queued")
    resident_held_infos.hold(_record(resident_id), info)
    return info_id


def _round(resident_id: str, *, state: str = "cancelled", infos: tuple = (), text: str = "整理本周的测试报告") -> tuple[str, list[str]]:
    """A request that ran with the infos it carried and ended in ``state``."""

    repository = resident_conversation_stream.get_repository()
    for author, info_text in infos:
        _info(resident_id, author, info_text)
    message_id = "msg_" + uuid.uuid4().hex
    request = resident_input_queue_domain.build_message("user", text, ACTOR, message_id=message_id)
    resident_lifecycle_domain.append_conversation_message(resident_id, request)
    record = _record(resident_id)
    _, carried = resident_held_infos.attach(record, text)
    resident_held_infos.consume(record, carried, request_message_id=message_id)
    job = {
         "user_message_id": message_id, "input_message": request,
        "raw_message": text, "message": text, "actual_sent_prompt": text, "actor": ACTOR,
        "enqueued_at": "2026-10-06T00:00:00Z", "started_at": "2026-10-06T00:00:01Z",
        "execution_id": "execution-" + uuid.uuid4().hex[:12], "execution_request": {"prompt": "x"},
        "stop_requested_at": "2026-10-06T00:00:05Z", "authorization": {"subject_ref": "old"}, "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    store = get_agent_data_store()
    store.begin_execution(execution_id=job["execution_id"], agent_id=resident_id, actor_ref=ACTOR,
                          provider=resident_backend.execution_provider(record), started_at=datetime.now(timezone.utc).isoformat())
    execution_inputs.attach(store, job["execution_id"], [{"message_id": message_id, "role": "primary", "accepted_at": resident_domain_common.now_iso()}])
    resident_execution_work.retain_input_checkpoint(record, job, carried)
    repository.set_input_state(resident_id, message_id, "running")
    if state != "running":
        store.finish_execution(job["execution_id"], status=state, finished_at=resident_domain_common.now_iso())
        repository.set_input_state(resident_id, message_id, state, error={"message": "provider error"} if state == "failed" else None)
    if state == "cancelled":
        notice = resident_input_queue_domain.resident_execution_stopped_event(ACTOR, job)
        resident_lifecycle_domain.append_conversation_message(resident_id, notice)
    return message_id, [item["message_id"] for item in carried]


def test_a_stopped_round_goes_back_first_with_its_infos_and_their_senders(team):
    """放回队列恢复原请求与随行 info

    停止的一轮用 RequeueMessage 放回：原请求以原消息 ID、发送者和正文排到等待队列最前，不带执行和 Job 字段，输入状态回到 queued 且没有回复与错误；随行的两条 info 回到等待列表最前，仍是 steward 与用户各自发出，状态回到 queued，不再折叠在原请求里；之后到达的 info 排在它们后面；停止提示留在对话中，这一轮离开最近记录。

    :design: decision_b5d349190743, decision_2a3ff353165c
    """
    resident_id, (head, waiting) = _queue(team, 2)
    stopped, infos = _round(resident_id, infos=((STEWARD, "记忆库更正了发布界限。"), (ACTOR, "临时放行没有有效期。")))
    _info(resident_id, STEWARD, "之后到达的 info")
    repository = resident_conversation_stream.get_repository()
    assert [repository.input_state(resident_id, info)["state"] for info in infos] == ["completed", "completed"]

    result = instruction(team, "RequeueMessage", {"resident_id": resident_id, "message_id": stopped})

    assert result.status == "committed"
    assert result.result["message_ids"] == [stopped, *infos]
    queue = _waiting(resident_id)
    assert [item["user_message_id"] for item in queue] == [stopped, waiting]
    requeued = queue[0]
    assert requeued["actor"] == ACTOR and requeued["message"] == "整理本周的测试报告"
    assert requeued["input_message"]["message_id"] == stopped
    for job_field in ("execution_id", "execution_request", "stop_requested_at", "authorization", "queue"):
        assert job_field not in requeued
    assert resident_execution_work.read(_record(resident_id))["user_message_id"] == head
    state = repository.input_state(resident_id, stopped)
    assert state["state"] == "queued" and state["reply_message_ids"] == [] and "error" not in state
    # The same messages wait again, first and in order, each from its own sender.
    held = resident_held_infos.peek(_record(resident_id))
    assert [item["message_id"] for item in held][:2] == infos
    assert [item["author_id"] for item in held][:2] == [STEWARD, ACTOR]
    assert repository.message(resident_id, infos[0])["author_id"] == STEWARD
    assert [repository.input_state(resident_id, info)["state"] for info in infos] == ["queued", "queued"]
    # They no longer show inside the stopped request; they go with the next one.
    shown = resident_held_infos.project_snapshot(_record(resident_id), repository.snapshot(resident_id))["messages"]
    request = next(item for item in shown if item["message_id"] == stopped)
    assert not set(infos) & {item["message_id"] for item in request.get("attached_infos") or []}
    assert [item["held_info"] for item in shown if item["message_id"] in infos] == [True, True]
    # The stop notice of that round stays in the Conversation; the round leaves the recent list.
    messages = repository.read_messages(resident_id)
    assert any(item.get("role") == "system" and item.get("reply_to_message_id") == stopped for item in messages)
    assert stopped not in [item["message_id"] for item in resident_input_checkpoints.checkpoints(_record(resident_id))]
    # Requeue preserves the completed Execution and gives the input a new queue position.
    assert execution_inputs.latest(get_agent_data_store(), resident_id, stopped)["status"] == "cancelled"


def test_an_idle_resident_takes_a_failed_round_back_as_its_next_input(team):
    """空闲 Resident 把放回的一轮作为下一项工作

    Resident 空闲时放回失败的一轮，它直接成为当前 Job（不带原执行绑定），输入状态 queued；接单关闭时它在 GetMailbox 的等待项中排在最前。

    :design: decision_b5d349190743, decision_2a3ff353165c
    """
    resident_id = team.target["resident_id"]
    failed, _ = _round(resident_id, state="failed")

    result = instruction(team, "RequeueMessage", {"resident_id": resident_id, "message_id": failed})

    assert result.status == "committed" and result.result["message_ids"] == [failed]
    job = resident_execution_work.read(_record(resident_id))
    assert not resident_execution_work.is_active(job)
    assert [item["user_message_id"] for item in job["queue"]] == [failed]
    assert "execution_id" not in job and "execution_request" not in job
    assert resident_conversation_stream.get_repository().input_state(resident_id, failed)["state"] == "queued"
    # Intake is closed here: the input waits, and the Mailbox lists it first.
    pending = instruction(team, "GetMailbox", {"resident_id": resident_id}).result["pending"]
    assert [item["message_id"] for item in pending if item["kind"] != "info"] == [failed]


def test_completed_running_or_missing_inputs_cannot_go_back(team):
    """已完成、进行中或缺失输入不能放回

    已完成的一轮、仍在进行的一轮和没有输入记录的消息都以 instruction.conflict 拒绝，错误说明原因，且不创建任何工作。

    :design: decision_b5d349190743, decision_2a3ff353165c
    """
    resident_id = team.target["resident_id"]
    completed, _ = _round(resident_id, state="completed")
    running, _ = _round(resident_id, state="running")

    for message_id, words in ((completed, "已经完成"), (running, "正在进行"), ("msg_" + "0" * 32, "输入记录已不在")):
        result = instruction(team, "RequeueMessage", {"resident_id": resident_id, "message_id": message_id})
        assert result.status != "committed"
        assert result.error["code"] == "instruction.conflict" and words in result.error["message"]
    assert resident_execution_work.read(_record(resident_id)) == {}


def test_requeue_retried_with_its_key_returns_the_first_result(team):
    """放回队列按幂等键重放

    同一幂等键重试 RequeueMessage 返回第一次的结果（replayed），不重复放回；换一个键再放回同一条已在等待的消息也返回原输入，不重复入队。

    :design: decision_b5d349190743, decision_2a3ff353165c
    """
    resident_id = team.target["resident_id"]
    stopped, _ = _round(resident_id)
    arguments = {"resident_id": resident_id, "message_id": stopped}

    first = instruction(team, "RequeueMessage", arguments, key="requeue-" + stopped)
    again = instruction(team, "RequeueMessage", arguments, key="requeue-" + stopped)
    another = instruction(team, "RequeueMessage", arguments)

    # A retry with the same key replays the recorded references instead of running again.
    assert first.status == again.status == "committed" and again.replayed
    assert again.result["message_ids"] == first.result["message_ids"] == [stopped]
    assert another.status == "committed" and another.result["message_ids"] == [stopped]
    assert len(resident_execution_work.read(_record(resident_id))["queue"]) == 1
    assert resident_execution_work.read(_record(resident_id))["queue"][0]["user_message_id"] == stopped


def test_only_those_who_organize_the_mailbox_put_a_round_back(team, monkeypatch):
    """放回队列需要整理权限

    没有管理该 Resident 权限的用户调用 RequeueMessage 得到 instruction.forbidden，消息保持 cancelled，这一轮仍在最近记录中。

    :design: decision_b5d349190743, decision_2a3ff353165c
    """
    from management_portal.society_service import resident_directory_domain

    resident_id = team.target["resident_id"]
    stopped, _ = _round(resident_id)
    manages = resident_access.can_manage
    monkeypatch.setattr(resident_access, "can_manage",
                        lambda record, actor: actor != "storage-outsider" and manages(record, actor))

    result = instruction(team, "RequeueMessage", {"resident_id": resident_id, "message_id": stopped}, actor="storage-outsider")

    assert result.status != "committed" and result.error["code"] == "instruction.forbidden"
    assert resident_conversation_stream.get_repository().input_state(resident_id, stopped)["state"] == "cancelled"
    assert stopped in [item["message_id"] for item in resident_input_checkpoints.checkpoints(_record(resident_id))]


def test_a_stopped_group_goes_back_with_its_members(team):
    """放回的群体保留成员

    停止的 Mailbox 群体放回后仍带全部成员，群体与成员的输入状态回到 queued，成员仍报告所属群体；单独放回群体中的成员按冲突拒绝。

    :design: decision_b5d349190743, decision_2a3ff353165c
    """
    resident_id, (_head, a, b) = _queue(team, 3)
    group_id = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a, b]}).result["group_message_id"]
    record = _record(resident_id)
    job = resident_execution_work.read(record)
    group_item = job["queue"][0]
    repository = resident_conversation_stream.get_repository()
    # The group runs, entering the trace with its members, and is stopped.
    repository.commit_user_message(resident_id, group_item["input_message"])
    store = get_agent_data_store()
    store.begin_execution(execution_id="execution-g", agent_id=resident_id, actor_ref=ACTOR, provider=resident_backend.execution_provider(record), started_at=resident_domain_common.now_iso())
    execution_inputs.attach(store, "execution-g", [{"message_id": group_id, "role": "primary", "accepted_at": resident_domain_common.now_iso()}])
    resident_execution_work.retain_input_checkpoint(record, {**group_item, "execution_id": "execution-g"}, [])
    store.finish_execution("execution-g", status="cancelled")
    job["queue"] = []
    resident_execution_work.write(record, job)
    repository.set_input_state(resident_id, group_id, "cancelled")
    resident_mailbox.finish_group(record, group_id, "cancelled")
    assert [repository.input_state(resident_id, member)["state"] for member in (a, b)] == ["cancelled", "cancelled"]

    result = instruction(team, "RequeueMessage", {"resident_id": resident_id, "message_id": group_id})
    member = instruction(team, "RequeueMessage", {"resident_id": resident_id, "message_id": a})

    assert result.status == "committed"
    requeued = _waiting(resident_id)[0]
    assert requeued["user_message_id"] == group_id
    assert [item["user_message_id"] for item in requeued["mailbox_members"]] == [a, b]
    for message_id in (group_id, a, b):
        assert repository.input_state(resident_id, message_id)["state"] == "queued"
    state = instruction(team, "GetInputState", {"resident_id": resident_id, "message_id": a}).result["input_state"]
    assert state["grouped_into_message_id"] == group_id
    assert member.error["code"] == "instruction.conflict"


def test_a_waiting_info_is_withdrawn_with_cancel_queue_item(team):
    """CancelInput 撤回等待中的 info

    撤回一条等待中的 info：结果状态 cancelled、错误代码 info_withdrawn；它离开等待列表，下一条请求只带上其余 info。

    :design: decision_b5d349190743, decision_2a3ff353165c
    """
    resident_id = team.target["resident_id"]
    kept = _info(resident_id, STEWARD, "保留的 info")
    withdrawn = _info(resident_id, STEWARD, "要撤回的 info")

    result = instruction(team, "CancelInput", {"resident_id": resident_id, "message_id": withdrawn})

    assert result.status == "committed"
    assert result.result["input_state"]["state"] == "cancelled"
    assert result.result["input_state"]["error"]["code"] == "info_withdrawn"
    assert [item["message_id"] for item in resident_held_infos.peek(_record(resident_id))] == [kept]
    prompt, carried = resident_held_infos.attach(_record(resident_id), "下一条请求")
    assert "要撤回的 info" not in prompt and [item["message_id"] for item in carried] == [kept]


def test_the_mailbox_lists_what_waits_what_runs_and_the_recent_rounds(team, monkeypatch):
    """GetMailbox 返回待处理、处理中与最近几轮

    GetMailbox 先列队列中的请求、再列等待中的 info（带发送者与摘要）；处理中是当前请求及随它送达的 info；最近几轮新的在前，停止的一轮可以放回、完成的不能，并附随行 info；返回整理权限与 revision。

    :design: decision_b5d349190743, decision_2a3ff353165c
    """
    resident_id = team.target["resident_id"]
    finished, _ = _round(resident_id, state="completed", text="已完成的一轮")
    stopped, _ = _round(resident_id, infos=((STEWARD, "随停止的一轮送达"),), text="被停止的一轮")
    _, (head, waiting) = _queue(team, 2)
    # The head is the round in progress; it carried one info.
    record = _record(resident_id)
    running_info = _info(resident_id, STEWARD, "随当前一轮送达")
    _, carried = resident_held_infos.attach(record, "第 0 条")
    resident_held_infos.consume(record, carried, request_message_id=head)
    resident_execution_work.retain_input_checkpoint(record, resident_execution_work.read(record), carried)
    job = resident_execution_work.read(record)
    job["submitted_at"] = resident_domain_common.now_iso()
    get_agent_data_store().begin_execution(execution_id=job["execution_id"], agent_id=resident_id, actor_ref=ACTOR,
        provider=resident_backend.execution_provider(record), started_at=resident_domain_common.now_iso())
    resident_execution_work.write(record, job)
    info = _info(resident_id, STEWARD, "等待中的 info")
    repository = resident_conversation_stream.get_repository()
    projection = repository.current_projection(resident_id)
    monkeypatch.setattr(repository, "current_state", lambda _resident_id: {
        **dict(projection.get("state") or {}), "active_message_id": head,
    })

    view = instruction(team, "GetMailbox", {"resident_id": resident_id}).result

    assert [(item["kind"], item["message_id"]) for item in view["pending"]] == [("request", waiting), ("info", info)]
    assert view["pending"][1]["author_id"] == STEWARD and view["pending"][1]["preview"] == "等待中的 info"
    assert view["active"]["message_id"] == head and view["active"]["preview"].startswith("第 0 条")
    assert [item["message_id"] for item in view["active"]["infos"]] == [running_info]
    assert [(item["message_id"], item["state"], item["can_requeue"]) for item in view["recent"]] == [
        (stopped, "cancelled", True), (finished, "completed", False),
    ]
    assert [(item["author_id"], item["preview"]) for item in view["recent"][0]["infos"]] == [(STEWARD, "随停止的一轮送达")]
    assert view["can_organize"] is True and view["revision"].startswith("queue-")


def test_rounds_keep_the_last_ten_and_drop_what_completed_rounds_no_longer_need(team):
    """轮次记录只保留最近 10 轮

    记录 12 轮后只剩最近 10 轮；/compact 不是一轮；失败的轮次保留原队列项，完成的轮次在下一轮记录时只留摘要。

    :aspects: functional, performance
    :design: decision_b5d349190743, decision_2a3ff353165c
    """
    resident_id = team.target["resident_id"]
    for index in range(12):
        _round(resident_id, state="completed" if index % 2 else "failed", text=f"第 {index} 轮")
    compact = {'execution_id': "job_compact", "user_message_id": "msg_" + uuid.uuid4().hex, 'operation': "backend_compact"}
    resident_execution_work.retain_input_checkpoint(_record(resident_id), compact, [])

    recorded = resident_input_checkpoints.checkpoints(_record(resident_id))

    assert len(recorded) == resident_input_checkpoints.MAX_CHECKPOINTS
    assert compact["user_message_id"] not in [item["message_id"] for item in recorded]
    # A completed round can never go back, so only its summary stays; the newest keeps its item until the next round.
    states = {item["message_id"]: resident_conversation_stream.get_repository().input_state(resident_id, item["message_id"])["state"]
              for item in recorded}
    assert all("item" in item for item in recorded if states[item["message_id"]] == "failed")
    assert not any("item" in item for item in recorded[:-1] if states[item["message_id"]] == "completed")
    # Every round stays in the round records; the Mailbox view reads the last ten from them.
    assert len(execution_inputs.recent(get_agent_data_store(), resident_id, limit=100)) == 12
    assert [item["message_id"] for item in resident_input_checkpoints.recent_executions(resident_id)] == [
        item["message_id"] for item in recorded
    ]


def test_a_promoted_input_carries_its_own_reference_source_and_group():
    """排队消息开始时带上自己的业务引用与发起者

    队列中的下一条消息成为当前工作时，业务引用、发起者换成它自己的，上一条的 run_id 与 Mailbox 成员不会留下。

    :design: decision_b5d349190743, decision_2a3ff353165c
    """
    job = {
        'execution_id': "job_a", "user_message_id": "msg_" + "a" * 32, "actor": ACTOR, "business_ref": "ref-a",
        "run_id": "run-a", "mailbox_members": [{"user_message_id": "x"}],
    }
    item = {
        'execution_id': "job_b", "user_message_id": "msg_" + "b" * 32, "actor": STEWARD, "message": "b",
        "business_ref": "ref-b",
    }

    resident_execution_work.promote_queue_item(job, item, [], "2026-10-06T00:00:00Z")

    assert job["user_message_id"] == item["user_message_id"] and job["business_ref"] == "ref-b"
    assert job["actor"] == STEWARD
    assert "run_id" not in job and "mailbox_members" not in job


def test_each_stop_of_a_message_has_its_own_notice():
    """同一消息每次停止各有一条停止提示

    同一条消息在两个 Job 中被停止，两条停止提示的消息 ID 不同，都回复这条消息。

    :design: decision_b5d349190743, decision_2a3ff353165c
    """
    message_id = "msg_" + "c" * 32
    first = resident_input_queue_domain.resident_execution_stopped_event(ACTOR, {"user_message_id": message_id, 'execution_id': "job_1"})
    second = resident_input_queue_domain.resident_execution_stopped_event(ACTOR, {"user_message_id": message_id, 'execution_id': "job_2"})

    assert first["message_id"] != second["message_id"]
    assert first["reply_to_message_id"] == second["reply_to_message_id"] == message_id
