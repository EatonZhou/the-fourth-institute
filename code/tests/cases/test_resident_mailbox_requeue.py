"""Putting a stopped or failed round back in the Mailbox, withdrawing a waiting info, and the Mailbox view (decision_15f7df737ed5).

:module: resident.conversation
"""

from __future__ import annotations
from datetime import datetime, timezone

import uuid
import pytest

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
    resident_input_recovery,
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


def _round(resident_id: str, *, state: str = "cancelled", infos: tuple = (), text: str = "整理本周的测试报告", actor: str = ACTOR) -> tuple[str, list[str]]:
    """A request that ran with the infos it carried and ended in ``state``."""

    repository = resident_conversation_stream.get_repository()
    for author, info_text in infos:
        _info(resident_id, author, info_text)
    message_id = "msg_" + uuid.uuid4().hex
    request = resident_input_queue_domain.build_message("user", text, actor, message_id=message_id)
    resident_lifecycle_domain.append_conversation_message(resident_id, request)
    record = _record(resident_id)
    _, carried = resident_held_infos.attach(record, text)
    resident_held_infos.consume(record, carried, request_message_id=message_id)
    job = {
         "user_message_id": message_id, "input_message": request,
        "raw_message": text, "message": text, "actual_sent_prompt": text, "actor": actor,
        "enqueued_at": "2026-10-06T00:00:00Z", "started_at": "2026-10-06T00:00:01Z",
        "execution_id": "execution-" + uuid.uuid4().hex[:12], "execution_request": {"prompt": "x"},
        "stop_requested_at": "2026-10-06T00:00:05Z", "authorization": {"subject_ref": "old"}, "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    store = get_agent_data_store()
    store.begin_execution(execution_id=job["execution_id"], agent_id=resident_id, actor_ref=actor,
                          provider=resident_backend.execution_provider(record), started_at=datetime.now(timezone.utc).isoformat())
    execution_inputs.attach(store, job["execution_id"], [{"message_id": message_id, "role": "primary", "accepted_at": resident_domain_common.now_iso()}])
    resident_execution_work.retain_input_checkpoint(record, job, carried)
    repository.set_input_state(resident_id, message_id, "running")
    if state != "running":
        store.finish_execution(job["execution_id"], status=state, finished_at=resident_domain_common.now_iso())
        repository.set_input_state(resident_id, message_id, state, error={"message": "provider error"} if state == "failed" else None)
    if state == "cancelled":
        notice = resident_input_queue_domain.resident_execution_stopped_event(actor, job)
        resident_lifecycle_domain.append_conversation_message(resident_id, notice)
    return message_id, [item["message_id"] for item in carried]


def test_a_stopped_round_goes_back_first_with_its_infos_and_their_senders(team):
    """放回队列恢复原请求与随行 info

    停止的一轮用 RequeueMessage 放回：原请求以原消息 ID、发送者和正文排到等待队列最前，不带执行和 Job 字段，输入状态回到 queued 且没有回复与错误；随行的两条 info 回到等待列表最前，仍是 steward 与用户各自发出，状态回到 queued，不再折叠在原请求里；之后到达的 info 排在它们后面；停止提示留在对话中，这一轮离开最近记录。

    :design: decision_15f7df737ed5, decision_2a3ff353165c
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

    Resident 空闲时放回失败的一轮，它成为受接单门控的下一项准备，不继承原执行绑定；关闭时输入 queued、不提交 Provider，并在 GetMailbox 等待项中排在最前。

    :design: decision_15f7df737ed5, decision_2a3ff353165c
    """
    resident_id = team.target["resident_id"]
    failed, _ = _round(resident_id, state="failed")
    previous_execution = execution_inputs.latest(get_agent_data_store(), resident_id, failed)['execution_id']

    result = instruction(team, "RequeueMessage", {"resident_id": resident_id, "message_id": failed})

    assert result.status == "committed" and result.result["message_ids"] == [failed]
    job = resident_execution_work.read(_record(resident_id))
    assert job['user_message_id'] == failed
    assert resident_execution_work.is_intake_held(job)
    assert job['queue'] == []
    assert 'execution_request' not in job and not job.get('submitted_at')
    assert get_agent_data_store().execution(previous_execution)['status'] == 'failed'
    assert resident_conversation_stream.get_repository().input_state(resident_id, failed)["state"] == "queued"
    # Intake is closed here: the input waits, and the Mailbox lists it first.
    pending = instruction(team, "GetMailbox", {"resident_id": resident_id}).result["pending"]
    assert [item["message_id"] for item in pending if item["kind"] != "info"] == [failed]


@pytest.mark.parametrize("legacy_inherited_marker", [False, True])
def test_requeued_member_group_advances_completed_head_without_rewriting_old_path(team, monkeypatch, legacy_inherited_marker):
    """原请求重试合并成新群体后，真实完成收尾可推进且原失败路径不变。"""
    from management_portal.society_service import resident_execution_domain, trigger_paths, trigger_path_storage
    resident_id, (head, waiting, tail) = _queue(team, 3)
    failed, _ = _round(resident_id, state="failed", actor=STEWARD)
    store = get_agent_data_store()
    predecessor = execution_inputs.latest(store, resident_id, failed)["execution_id"]
    record = _record(resident_id)
    lineage_id = record["lineage_id"]
    at = resident_domain_common.now_iso()
    event = {"id":"regroup-source-" + uuid.uuid4().hex, "type":"manual.triggered", "occurred_at":at, "payload":{}}
    source = "event_regroup"
    path = trigger_paths.begin_event(lineage_id, source, event, cause_kind="external", cause_id=event["id"])
    with get_lineage_data_store().database.transaction() as c:
        trigger_path_storage.admit_step(c, trigger_path_id=path, node_id=resident_id, kind="resident",
                                      activation_key=failed, input_message_id=failed, created_at=at)
    trigger_paths.link_input(lineage_id, resident_id, failed, predecessor)
    trigger_paths.event_settled(path, source, event["id"])
    original_path = trigger_paths.get(lineage_id, path)
    assert original_path["status"] == "failed"
    assert instruction(team, "RequeueMessage", {"resident_id":resident_id, "message_id":failed}).status == "committed"
    merged = instruction(team, "MergeMailboxItems", {"resident_id":resident_id, "message_ids":[failed, waiting]})
    assert merged.status == "committed", merged.error
    group = merged.result["group_message_id"]
    job = resident_execution_work.read(_record(resident_id))
    grouped = job["queue"][0]
    assert "retry_of_execution_id" not in grouped
    assert grouped["mailbox_members"][0]["retry_of_execution_id"] == predecessor
    if legacy_inherited_marker:
        # Exact previously persisted shape, without changing any old terminal.
        grouped["retry_of_execution_id"] = predecessor
    record = _record(resident_id)
    record["intake_open"] = True
    resident_record_domain.save_resident_record(record, commit_intake=True)
    job.update(resident_execution_work.continuation_fields(record))
    resident_execution_domain._mark_completion_applied(job, at)
    resident_execution_work.write(record, job)
    repository = resident_conversation_stream.get_repository()
    store.finish_execution(job["execution_id"], status="completed")
    repository.set_input_state(resident_id, head, "completed")
    monkeypatch.setattr(resident_input_queue_domain, "acknowledge_execution", lambda *_a: True)
    monkeypatch.setattr(resident_input_queue_domain, "reconcile_queued_turn_inputs", lambda *_a, **_k: True)
    assert resident_execution_domain._finalize_applied_execution(
        resident_id, job["continuation_id"], job) is True
    current = resident_execution_work.read(_record(resident_id))
    assert current["user_message_id"] == group
    assert [item["user_message_id"] for item in current["queue"]] == [tail]
    assert trigger_paths.get(lineage_id, path) == original_path
    from management_portal.execution import agent_data_input_retries
    with get_lineage_data_store().database.read() as c:
        assert agent_data_input_retries.independent_of(c, predecessors=[predecessor],
            execution_id=current["execution_id"], resident_id=resident_id, message_id=failed)
        assert not agent_data_input_retries.independent_of(c, predecessors=[predecessor],
            execution_id=current["execution_id"], resident_id=resident_id, message_id=waiting)
    # Subsequent progress writes are idempotent and the remaining tail starts.
    resident_execution_work.write(_record(resident_id), current)
    store.finish_execution(current["execution_id"], status="completed")
    repository.set_input_state(resident_id, group, "completed")
    resident_mailbox.finish_group(_record(resident_id), group, "completed")
    assert resident_execution_domain._finalize_applied_execution(
        resident_id, current["continuation_id"], current) is True
    assert resident_execution_work.read(_record(resident_id))["user_message_id"] == tail
    assert trigger_paths.get(lineage_id, path) == original_path


@pytest.mark.parametrize("damage", ["completed_source", "foreign_resident", "missing_source_member", "wrong_source_primary", "wrong_source_actor"])
def test_grouped_retry_provenance_rejects_unrelated_or_incomplete_source(team, damage):
    from management_common.portal_database import _database_schema
    from management_portal.execution import agent_data_input_retries as retries
    resident_id, (_head, member, _tail) = _queue(team, 3)
    failed, _ = _round(resident_id, state="failed")
    store = get_agent_data_store()
    predecessor = execution_inputs.latest(store, resident_id, failed)["execution_id"]
    group = "msg_group_" + uuid.uuid4().hex
    successor = "execution-" + uuid.uuid4().hex[:24]
    job = {"execution_id":successor, "user_message_id":group, "actor":ACTOR,
           "started_at":resident_domain_common.now_iso(), "queue":[],
           "mailbox_members":[{"user_message_id":failed}, {"user_message_id":member}]}
    resident_execution_work.write(_record(resident_id), job)
    table = '"' + _database_schema("agent") + '".'
    with pytest.raises(ValueError, match="Grouped retry"):
        with get_lineage_data_store().database.transaction() as c:
            if damage == "completed_source":
                c.execute(f"UPDATE {table}executions SET status='completed' WHERE execution_id=%s", (predecessor,))
            elif damage == "foreign_resident":
                c.execute(f"UPDATE {table}executions SET agent_id='resident_other' WHERE execution_id=%s", (predecessor,))
            elif damage == "missing_source_member":
                c.execute(f"INSERT INTO {table}execution_inputs(execution_id,message_id,role,accepted_at) "
                          "VALUES (%s,'missing-source-member','member',now())", (predecessor,))
            retries.record_member(c, predecessor=predecessor, execution_id=successor, resident_id=resident_id,
                                  source_message_id=member if damage == "wrong_source_primary" else failed,
                                  group_message_id=group, source_actor="wrong" if damage == "wrong_source_actor" else ACTOR,
                                  at=resident_domain_common.now_iso())


def test_grouped_then_exact_retry_preserves_ancestry_and_late_graph_source(team):
    from management_portal.society_service import trigger_paths, resident_execution_storage
    from management_portal.execution import agent_data_input_retries as retries
    resident_id, (_head, waiting) = _queue(team, 2)
    failed, _ = _round(resident_id, state="failed")
    store = get_agent_data_store()
    predecessor = execution_inputs.latest(store, resident_id, failed)["execution_id"]
    assert instruction(team, "RequeueMessage", {"resident_id":resident_id, "message_id":failed}).status == "committed"
    merged = instruction(team, "MergeMailboxItems", {"resident_id":resident_id, "message_ids":[failed, waiting]})
    assert merged.status == "committed", merged.error
    record = _record(resident_id)
    group = resident_execution_storage.queue_input(resident_execution_work.read(record)["queue"][0])
    now = resident_domain_common.now_iso()
    group.update(execution_id="execution-" + uuid.uuid4().hex[:24], started_at=now, queue=[])
    resident_execution_work.write(record, group)
    previous_group = group["execution_id"]
    store.finish_execution(previous_group, status="failed")
    group.update(execution_id="execution-" + uuid.uuid4().hex[:24], retry_of_execution_id=previous_group)
    resident_execution_work.write(record, group)
    with get_lineage_data_store().database.read() as c:
        assert retries.independent_of(c, predecessors=[predecessor], execution_id=group["execution_id"],
                                      resident_id=resident_id, message_id=failed)
        assert retries.original_execution(c, execution_id=group["execution_id"],
            resident_id=resident_id, message_id=failed) == predecessor
    event = {"id":"late-source-" + uuid.uuid4().hex, "type":"manual.triggered", "occurred_at":now, "payload":{}}
    source = "event_late_retry_source"
    path = trigger_paths.begin_event(record["lineage_id"], source, event, cause_kind="external", cause_id=event["id"])
    trigger_paths.admit_resident(lineage_id=record["lineage_id"], trigger_path_id=path,
        resident_id=resident_id, message_id=failed, source_wire_id="wire_late", event=event)
    trigger_paths.event_settled(path, source, event["id"])
    actual = trigger_paths.get(record["lineage_id"], path)
    assert actual["status"] == "failed"
    assert {item["execution_id"] for item in actual["executions"]} == {predecessor}
    with pytest.raises(resident_mailbox.MailboxConflict, match="整组重试"):
        resident_mailbox.expand([group], [group["user_message_id"]])


def test_completed_running_or_missing_inputs_cannot_go_back(team):
    """已完成、进行中或缺失输入不能放回

    已完成的一轮、仍在进行的一轮和没有输入记录的消息都以 instruction.conflict 拒绝，错误说明原因，且不创建任何工作。

    :design: decision_15f7df737ed5, decision_2a3ff353165c
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

    :design: decision_15f7df737ed5, decision_2a3ff353165c
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
    job = resident_execution_work.read(_record(resident_id))
    assert job['user_message_id'] == stopped and job['queue'] == []
    assert resident_execution_work.is_intake_held(job)


def test_only_those_who_organize_the_mailbox_put_a_round_back(team, monkeypatch):
    """放回队列需要整理权限

    没有管理该 Resident 权限的用户调用 RequeueMessage 得到 instruction.forbidden，消息保持 cancelled，这一轮仍在最近记录中。

    :design: decision_15f7df737ed5, decision_2a3ff353165c
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

    :design: decision_15f7df737ed5, decision_2a3ff353165c
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


@pytest.mark.parametrize('intake_open', [False, True])
def test_a_group_requeued_onto_an_idle_resident_finishes_its_members(team, intake_open):
    """空闲时放回的群体，结束时带上成员

    已送达又停止的群体在 Resident 空闲时放回，关闭接单则等待，开启后沿原准备继续：成员按原顺序跟随群体；群体再次送达并完成后，成员一起完成并离开 Mailbox。

    :design: decision_15f7df737ed5, decision_2a3ff353165c
    """
    resident_id, (head, a, b) = _queue(team, 3)
    group_id = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a, b]}).result["group_message_id"]
    record = _record(resident_id)
    group_item = resident_execution_work.read(record)["queue"][0]
    repository = resident_conversation_stream.get_repository()
    repository.commit_user_message(resident_id, group_item["input_message"])
    store = get_agent_data_store()
    store.begin_execution(execution_id="execution-g", agent_id=resident_id, actor_ref=ACTOR, provider=resident_backend.execution_provider(record), started_at=resident_domain_common.now_iso())
    accepted = resident_domain_common.now_iso()
    execution_inputs.attach(store, "execution-g", [{"message_id": group_id, "role": "primary", "accepted_at": accepted},
                                                   *({"message_id": member, "role": "member", "accepted_at": accepted} for member in (a, b))])
    resident_execution_work.retain_input_checkpoint(record, {**group_item, "execution_id": "execution-g"}, [])
    store.finish_execution("execution-g", status="failed")
    # Nothing else runs or waits: the requeued group becomes the current work.
    resident_execution_work.write(record, {"queue": []})
    repository.set_input_state(resident_id, head, "completed")
    repository.set_input_state(resident_id, group_id, "failed")
    resident_mailbox.finish_group(record, group_id, "failed")
    record = _record(resident_id)
    record["intake_open"] = intake_open
    resident_record_domain.save_resident_record(record, commit_intake=True)

    result = instruction(team, "RequeueMessage", {"resident_id": resident_id, "message_id": group_id})
    assert result.status == "committed", result.error

    assert resident_execution_work.read(_record(resident_id))["user_message_id"] == group_id
    if not intake_open:
        job = resident_execution_work.read(_record(resident_id))
        assert resident_execution_work.is_intake_held(job) and not job.get('submitted_at')
        assert store.execution('execution-g')['status'] == 'failed'
        opened = instruction(team, 'OpenIntake', {'resident_id': resident_id, 'open': True})
        assert opened.status == 'committed', opened.error
        assert resident_record_domain.resident_intake_state(_record(resident_id)) == 'open'
        assert resident_execution_work.read(_record(resident_id))['user_message_id'] == group_id
    assert resident_mailbox.members_of(_record(resident_id), group_id) == [a, b]
    state = instruction(team, "GetInputState", {"resident_id": resident_id, "message_id": a}).result["input_state"]
    assert state["grouped_into_message_id"] == group_id
    # The group is delivered again and completes.
    repository.commit_user_message(resident_id, group_item["input_message"])
    repository.set_input_state(resident_id, group_id, "completed")
    assert resident_mailbox.finish_group(_record(resident_id), group_id, "completed") == [a, b]
    assert [repository.input_state(resident_id, member)["state"] for member in (a, b)] == ["completed", "completed"]
    assert get_lineage_data_store().mailbox_entries([resident_id], message_ids=[group_id, a, b]) == []


@pytest.mark.parametrize('intake_open', [False, True])
def test_requeue_repairs_an_existing_headless_waiting_list_without_reordering_or_reaccepting(team, intake_open):
    """旧版关闭接单时放回的等待表可原位恢复，后到请求不丢失，原 info/失败执行不变。

    :design: decision_15f7df737ed5
    """
    resident_id = team.target['resident_id']
    failed, infos = _round(resident_id, state='failed', infos=((STEWARD, '保留原背景'),))
    previous_execution = execution_inputs.latest(get_agent_data_store(), resident_id, failed)['execution_id']
    assert instruction(team, 'RequeueMessage', {'resident_id': resident_id, 'message_id': failed}).status == 'committed'
    held = resident_execution_work.read(_record(resident_id))
    assert resident_execution_work.is_intake_held(held)
    later = instruction(team, 'DeliverMessage', {'resident_id': resident_id,
        'message': {'content_parts': [{'type': 'text', 'text': '后来接受的请求不能丢失或抢先'}]}})
    assert later.status == 'committed', later.error
    later_id = later.result['message_id']
    job = resident_execution_work.read(_record(resident_id))
    assert job['user_message_id'] == failed
    assert [item['user_message_id'] for item in job['queue']] == [later_id]
    original = resident_input_checkpoints.queue_item(job)
    # Exact persisted shape produced by the earlier closed-intake recovery.
    resident_execution_work.write(_record(resident_id), {'queue': [original, *job['queue']]})
    record = _record(resident_id)
    record['intake_open'] = intake_open
    resident_record_domain.save_resident_record(record, commit_intake=True)
    repository = resident_conversation_stream.get_repository()
    first_message = repository.message(resident_id, failed)
    before_infos = resident_held_infos.peek(_record(resident_id))

    # Asking about the tail must repair the original head, not promote the tail.
    recovered = instruction(team, 'RequeueMessage', {'resident_id': resident_id, 'message_id': later_id})
    assert recovered.status == 'committed', recovered.error
    prepared = resident_execution_work.read(_record(resident_id))
    assert prepared['user_message_id'] == failed
    assert [item['user_message_id'] for item in prepared['queue']] == [later_id]
    assert resident_execution_work.is_intake_held(prepared) == (not intake_open)
    assert prepared['retry_of_execution_id'] == previous_execution
    assert get_agent_data_store().execution(previous_execution)['status'] == 'failed'
    assert repository.message(resident_id, failed) == first_message
    assert resident_held_infos.peek(_record(resident_id)) == before_infos
    assert [info['message_id'] for info in before_infos] == infos
    assert repository.input_state(resident_id, failed)['state'] == ('running' if intake_open else 'queued')
    assert repository.input_state(resident_id, later_id)['state'] == 'queued'
    assert instruction(team, 'RequeueMessage', {'resident_id': resident_id, 'message_id': failed}).status == 'committed'
    again = resident_execution_work.read(_record(resident_id))
    assert again['execution_id'] == prepared['execution_id']
    assert [item['user_message_id'] for item in again['queue']] == [later_id]


@pytest.mark.parametrize('predecessor_proof', [False, True])
def test_accepted_retry_after_a_later_turn_preserves_independently_consumed_infos(team, monkeypatch, predecessor_proof):
    """旧等待表被后到轮次挤掉后，仅有完整本地原件及原失败引用时恢复，不重放已消费 info。

    :design: decision_15f7df737ed5
    """
    resident_id = team.target['resident_id']
    failed, infos = _round(resident_id, state='failed', infos=((STEWARD, '旧轮的原背景'),))
    store = get_agent_data_store()
    previous_execution = execution_inputs.latest(store, resident_id, failed)['execution_id']
    def legacy_prepare(record, job, item, waiting, now):
        item = dict(item)
        if not predecessor_proof:
            item.pop('retry_of_execution_id', None)
        resident_execution_work.write(record, {'queue': [item, *waiting]})
    # Reproduce the older implementation through the real requeue owner. No
    # prepared Execution exists for this retry, just as in the native failure.
    with monkeypatch.context() as legacy:
        legacy.setattr(resident_input_recovery, '_prepare_requeued_input', legacy_prepare)
        result = instruction(team, 'RequeueMessage', {'resident_id': resident_id, 'message_id': failed})
        assert result.status == 'committed', result.error
    record = _record(resident_id)
    record['intake_open'] = True
    resident_record_domain.save_resident_record(record, commit_intake=True)
    later = instruction(team, 'DeliverMessage', {'resident_id': resident_id,
        'message': {'content_parts': [{'type': 'text', 'text': '后到并已完成的交接'}]}})
    assert later.status == 'committed', later.error
    later_id = later.result['message_id']
    job = resident_execution_work.read(_record(resident_id))
    assert job['user_message_id'] == later_id and job['queue'] == []
    repository = resident_conversation_stream.get_repository()
    repository.commit_user_message(resident_id, job['input_message'])
    _, carried = resident_held_infos.attach(_record(resident_id), '后到并已完成的交接')
    resident_held_infos.consume(_record(resident_id), carried, request_message_id=later_id)
    resident_execution_work.retain_input_checkpoint(_record(resident_id), job, carried)
    store.finish_execution(job['execution_id'], status='completed')
    repository.set_input_state(resident_id, later_id, 'completed')
    resident_execution_work.clear(_record(resident_id))
    original = repository.message(resident_id, failed)
    consumed = repository.message(resident_id, infos[0])
    assert consumed['released_with'] == later_id
    assert repository.input_state(resident_id, failed)['state'] == 'queued'
    assert not resident_held_infos.peek(_record(resident_id))

    restored = instruction(team, 'RequeueMessage', {'resident_id': resident_id, 'message_id': failed})
    if not predecessor_proof:
        assert restored.status != 'committed' and restored.error['code'] == 'instruction.conflict'
        assert resident_execution_work.read(_record(resident_id)) == {}
    else:
        assert restored.status == 'committed', restored.error
        current = resident_execution_work.read(_record(resident_id))
        assert current['user_message_id'] == failed and current['queue'] == []
        assert current['retry_of_execution_id'] == previous_execution
        assert repository.message(resident_id, failed) == original
    assert repository.message(resident_id, infos[0]) == consumed
    assert repository.input_state(resident_id, infos[0])['state'] == 'completed'
    assert not resident_held_infos.peek(_record(resident_id))
    assert store.execution(previous_execution)['status'] == 'failed'
    assert store.execution(job['execution_id'])['status'] == 'completed'


def test_a_waiting_info_is_withdrawn_with_cancel_queue_item(team):
    """CancelInput 撤回等待中的 info

    撤回一条等待中的 info：结果状态 cancelled、错误代码 info_withdrawn；它离开等待列表，下一条请求只带上其余 info。

    :design: decision_15f7df737ed5, decision_2a3ff353165c
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

    :design: decision_15f7df737ed5, decision_2a3ff353165c
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
    :design: decision_15f7df737ed5, decision_2a3ff353165c
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

    :design: decision_15f7df737ed5, decision_2a3ff353165c
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

    :design: decision_15f7df737ed5, decision_2a3ff353165c
    """
    message_id = "msg_" + "c" * 32
    first = resident_input_queue_domain.resident_execution_stopped_event(ACTOR, {"user_message_id": message_id, 'execution_id': "job_1"})
    second = resident_input_queue_domain.resident_execution_stopped_event(ACTOR, {"user_message_id": message_id, 'execution_id': "job_2"})

    assert first["message_id"] != second["message_id"]
    assert first["reply_to_message_id"] == second["reply_to_message_id"] == message_id
