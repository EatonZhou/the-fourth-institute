from __future__ import annotations
from tests.cases.agent_data_test_support import sequence_members
from management_portal.execution.message_projection import unwrap_message
from management_portal.society_service import resident_conversation_stream, resident_lifecycle_steps_domain

import threading

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import invariant_locks
from management_portal.society_service.resident_conversation_stream import (
    MESSAGE_UPDATED, ResidentConversationRepository,
)
from management_portal.society_service.resident_lifecycle import ResidentLifecycleOperations
from management_portal.society_service.state_store import LineageDataStore


@pytest.fixture
def repository(tmp_path, monkeypatch):
    from tests.cases.society_service_test_support import publish_registered_lineage_members
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
    repo = ResidentConversationRepository(
        LineageDataStore(), AgentDataStore(),
    )
    lineage_id = "lineage_111111111111"
    resident_ids = ("resident_111111111111", "resident_222222222222")
    repo.state_store.put_lineage_definition(
        lineage_id,
        {
            "lineage_id": lineage_id,
            "lineage_membership": "formal", "project_id": "", "title": "Test", "description": "",
            "revision": 0, "layout_revision": 0, "leader_resident_id": "",
            "created_at": "", "updated_at": "", "graph_updated_at": "", "layout_updated_at": "", "created_by": "sample_admin",
            "program": {"components": [], "wires": []},
            "nodes": [],
            "edges": [],
        },
        {},
    )
    publish_registered_lineage_members({
        "lineage_id": lineage_id,
        "nodes": [{"id": resident_id, "title": resident_id} for resident_id in resident_ids],
    })
    for resident_id in resident_ids:
        # Runtime fixtures consume the canonical read projection; paths are
        # owned by the Workspace table, never by the input identity record.
        canonical = repo.state_store.resident_record(resident_id)
        assert canonical["default_cwd"] == canonical["owned_workspace"]["path"]
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repo)
    return repo


def message(index):
    return {
        "message_id": f"msg_{index:032x}", "role": "user", "author_id": "alice",
        "created_at": "2026-09-06T00:00:00Z",
        "content_parts": [{"type": "text", "text": f"message {index}"}],
    }


@pytest.mark.parametrize("entrypoint", ["append_message", "accept_user_message", "validate_message_identity"])
def test_replayed_input_validates_its_immutable_source_but_shows_current_content(repository, entrypoint):
    """重放的输入按原始内容校验，显示 Harness 改写后的当前内容，内容不同的重放被拒绝"""
    from management_portal.execution.message_projection import wrap_message
    resident_id = "resident_111111111111"
    original = message(1)
    repository.append_message(resident_id, original)
    stored = repository.agent_data.chat_message(resident_id, original["message_id"])
    repository.agent_data.transform_agent_segments(
        resident_id, [stored["segment_id"]], segment_id="segment_changed_input",
        content=wrap_message("current Harness content"), actor_ref="agent_harness",
        operation_id="replace_input", operation_type="agent_harness.prepare_invocation",
    )
    cursor = repository.current_projection(resident_id)["cursor"]
    validate = getattr(repository, entrypoint)
    replay = validate(resident_id, original)
    assert replay["content_parts"] == [{"type": "text", "text": "current Harness content"}]
    assert repository.current_projection(resident_id)["cursor"] == cursor
    assert repository.agent_data.trace_length(resident_id) == 1
    for change in ({"content_parts": [{"type": "text", "text": "unrelated replacement"}]},
                   {"author_id": "bob"}, {"created_at": "2026-09-07T00:00:00Z"}):
        with pytest.raises(ValueError, match="different immutable content"):
            validate(resident_id, {**original, **change})


def test_harness_input_notification_uses_the_collected_unwrapped_content(repository):
    """Harness 输入通知显示已采集内容去掉包装后的正文，不重复写入 trace"""
    from management_portal.execution.message_projection import wrap_message
    resident_id = "resident_111111111111"
    notification = {**message(2), "role": "system", "author_id": "agent_harness",
        "execution_id": "execution_harness", "harness_input": {"kind": "recovery_continue"},
        "content_parts": [{"type": "text", "text": wrap_message("continue")}]}
    repository.agent_data.display_chat_message(resident_id, notification)
    shown = repository.append_message(resident_id, notification)
    assert shown["content_parts"] == [{"type": "text", "text": "continue"}]
    assert repository.agent_data.trace_length(resident_id) == 1


def test_snapshot_concurrent_writes_on_both_attempts_remain_replayable(repository, monkeypatch):
    """快照读取期间持续写入时快照仍能完成，之后的消息可从游标重放补齐"""
    resident_id = "resident_111111111111"
    repository.commit_user_message(resident_id, message(1))
    projection = repository._snapshot_projection
    writes = []

    def append_after_page(*args):
        index = len(writes) + 2
        repository.commit_user_message(resident_id, message(index))
        writes.append(index)
        return projection(*args)

    monkeypatch.setattr(repository, "_snapshot_projection", append_after_page)
    snapshot = repository.snapshot(resident_id)
    assert writes == [2, 3], "snapshot must finish even while writers keep appending"
    visible = {item["message_id"] for item in snapshot["messages"]}
    assert message(3)["message_id"] not in visible
    replay = repository.events_after(resident_id, snapshot["cursor"])
    visible.update(event["payload"]["message"]["message_id"] for event in replay["events"])
    assert visible == {message(index)["message_id"] for index in (1, 2, 3)}
    assert snapshot["cursor"] < replay["cursor"]


def test_slow_conversation_projection_does_not_lock_another_resident(repository, monkeypatch):
    """一个 Resident 的对话投影变慢时不阻塞其他 Resident 写消息"""
    first, second = "resident_111111111111", "resident_222222222222"
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    errors = []
    original = repository.agent_data.project_chat_message

    def project(resident_id, payload, **kwargs):
        result = original(resident_id, payload, **kwargs)
        if resident_id == first:
            entered.set()
            assert release.wait(3)
        return result

    def submit(resident_id, index):
        try:
            # Domain and repository must share one reentrant state authority.
            with invariant_locks.resident_lock(resident_id):
                repository.commit_user_message(resident_id, message(index))
            if resident_id == second:
                completed.set()
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(repository.agent_data, "project_chat_message", project)
    a = threading.Thread(target=submit, args=(first, 1))
    b = threading.Thread(target=submit, args=(second, 2))
    a.start()
    try:
        assert entered.wait(1)
        b.start()
        assert completed.wait(1), "one Resident's slow projection blocked an unrelated Resident"
    finally:
        release.set()
        a.join(3)
        if b.ident is not None:
            b.join(3)
    assert not errors
    assert repository.message(first, message(1)["message_id"])
    assert repository.message(second, message(2)["message_id"])


def test_reset_recovery_preserves_new_messages_and_finishes_archive_projection(repository, monkeypatch):
    """重置中途崩溃后恢复执行补完归档投影，保留重置后到达的新消息且不重复执行"""
    resident_id = "resident_111111111111"
    repository.commit_user_message(resident_id, message(1))
    operations = ResidentLifecycleOperations(repository.state_store)
    step = {
        "kind": "context_archive", "resident_id": resident_id,
        "message_ids": repository.context_reset_boundary(resident_id),
        "archived_at": "2026-09-06T01:00:00Z", "reset_at": "2026-09-06T01:00:00Z",
        "trace_position": repository.agent_data.trace_length(resident_id),
    }
    operation_id = operations.enqueue(resident_id, [step])
    repository.commit_user_message(resident_id, message(2))
    original = repository._record_event

    def broken_projection(*args, **kwargs):
        if args[2] == MESSAGE_UPDATED:
            raise RuntimeError("crash after AgentData archive")
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "_record_event", broken_projection)
    assert not operations.run(operation_id, resident_lifecycle_steps_domain.apply_resident_lifecycle_step)
    assert operations.pending(resident_id)
    assert repository.context_reset_boundary(resident_id) == [message(2)["message_id"]]
    operations.store.database.write(lambda c: c.execute(
        'UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s', (operation_id,),
    ))
    recovered = ResidentConversationRepository(repository.state_store, repository.agent_data)
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: recovered)
    assert operations.run(operation_id, resident_lifecycle_steps_domain.apply_resident_lifecycle_step) == resident_id
    assert not operations.pending(resident_id)
    assert recovered.context_reset_boundary(resident_id) == [message(2)["message_id"]]
    events = recovered.events_after(resident_id, 0)["events"]
    assert any(event["event_type"] == MESSAGE_UPDATED and event["payload"] == {
        "message_id": message(1)["message_id"], "removed": True,
    } for event in events)
    assert not operations.run(operation_id, lambda *_: pytest.fail("finished reset replayed"))


def test_a_reset_keeps_cleared_messages_in_the_trace_and_excludes_new_input(repository):
    """重置上下文后清掉的消息仍留在 trace 中，重置期间到达的新输入保留在 Chat

    :design: decision_c524c775e0be
    :design-point:
    """
    resident_id = "resident_111111111111"
    for index in (1, 2):
        repository.commit_user_message(resident_id, message(index))
    boundary = repository.context_reset_boundary(resident_id)
    position = len(repository.agent_data.sequence_segment_ids(repository.agent_data.trace_sequence_id(resident_id)))
    repository.commit_user_message(resident_id, message(3))
    reset_at = "2026-09-07T04:02:33Z"

    resident_lifecycle_steps_domain.apply_resident_lifecycle_step("operation_reset", {
        "kind": "context_archive", "resident_id": resident_id, "message_ids": boundary,
        "archived_at": reset_at, "reset_at": reset_at, "trace_position": position,
    })

    # The Chat is what follows the mark; what it cleared stays in the trace.
    assert repository.context_reset_boundary(resident_id) == [message(3)["message_id"]]
    assert repository.message(resident_id, message(1)["message_id"]) is None
    trace = sequence_members(repository.agent_data, repository.agent_data.trace_sequence_id(resident_id))
    assert [item["segment_type"] for item in trace] == ["input", "input", "input"]
    assert [
        [{"type": "text", "text": unwrap_message(repository.agent_data.segment(item["segment_id"])["content"])}]
        for item in trace if item["segment_type"] == "input"
    ] == [message(index)["content_parts"] for index in (1, 2, 3)]


def test_reset_intent_rollback_never_archives_messages(repository):
    """重置意图所在事务回滚时不归档任何消息"""
    resident_id = "resident_111111111111"
    repository.commit_user_message(resident_id, message(1))
    operations = ResidentLifecycleOperations(repository.state_store)

    def fail():
        operations.enqueue(resident_id, [{
            "kind": "context_archive", "resident_id": resident_id,
            "message_ids": repository.context_reset_boundary(resident_id),
            "archived_at": "2026-09-06T01:00:00Z", "reset_at": "2026-09-06T01:00:00Z",
            "trace_position": repository.agent_data.trace_length(resident_id),
        }])
        raise RuntimeError("identity commit failed")

    with pytest.raises(RuntimeError, match="identity commit failed"):
        repository.state_store.transaction(fail)
    assert operations.ready() == []
    assert repository.context_reset_boundary(resident_id) == [message(1)["message_id"]]


def test_snapshot_is_lock_free_even_while_the_execution_work_scope_is_held(
    repository,
    monkeypatch,
) -> None:
    """执行持有 Resident 锁时对话快照仍可无锁读取"""
    resident_id = "resident_111111111111"
    repository.commit_user_message(resident_id, message(1))
    monkeypatch.setattr(
        repository,
        "_protocol_scope",
        lambda *_args, **_kwargs: pytest.fail("snapshot acquired the writer scope"),
    )
    job_entered = threading.Event()
    release_job = threading.Event()
    snapshot_completed = threading.Event()
    failures: list[BaseException] = []

    def hold_job_scope() -> None:
        with invariant_locks.resident_lock(resident_id):
            job_entered.set()
            release_job.wait(timeout=2)

    def read_snapshot() -> None:
        try:
            job_entered.wait(timeout=2)
            repository.snapshot(resident_id)
            snapshot_completed.set()
        except BaseException as error:
            failures.append(error)

    job_thread = threading.Thread(target=hold_job_scope)
    snapshot_thread = threading.Thread(target=read_snapshot)
    job_thread.start()
    snapshot_thread.start()
    try:
        assert snapshot_completed.wait(timeout=1)
    finally:
        release_job.set()
        job_thread.join(timeout=2)
        snapshot_thread.join(timeout=2)
    assert not failures


def test_reset_storage_work_runs_outside_the_conversation_writer_scope(
    repository,
    monkeypatch,
) -> None:
    """重置上下文的存储写入在对话写锁之外执行"""
    resident_id = "resident_111111111111"
    repository.commit_user_message(resident_id, message(1))
    record = repository.agent_data.record_context_reset
    observed: list[tuple[str, bool]] = []

    def record_without_writer_scope(*args, **kwargs):
        observed.append(("reset", invariant_locks.lock_held()))
        return record(*args, **kwargs)

    monkeypatch.setattr(repository.agent_data, "record_context_reset", record_without_writer_scope)
    resident_lifecycle_steps_domain.apply_resident_lifecycle_step("operation_reset", {
        "kind": "context_archive", "resident_id": resident_id,
        "message_ids": repository.context_reset_boundary(resident_id),
        "archived_at": "2026-09-07T05:00:00Z", "reset_at": "2026-09-07T05:00:00Z",
        "trace_position": repository.agent_data.trace_length(resident_id),
    })

    assert observed == [("reset", False)]
    assert repository.context_reset_boundary(resident_id) == []


def test_a_delivered_message_with_a_business_ref_replays_as_the_same_identity():
    """带 business_ref 的投递消息经存储往返后不可变字段不变，重投视为同一条

    :design: decision_2fe6fd5367fb"""
    from management_portal.execution.agent_data_store import _canonical_chat_message
    from management_portal.society_service import resident_conversation_stream as stream

    # business_ref is kept with the input (job / input state), not on the Message itself,
    # so an idempotent redelivery compares equal to what storage returns.
    delivered = {"message_id": "msg_0123456789abcdef0123456789abcdef", "role": "user",
                 "content_parts": [{"type": "text", "text": "推荐一个 Resident"}],
                 "created_at": "2026-09-29T08:00:00Z", "business_ref": "which-resident:job-1"}
    stored = stream._message_from_storage(_canonical_chat_message(stream._message_for_storage(delivered)))
    assert stream._immutable_message_fields(stored) == stream._immutable_message_fields(delivered)


def test_activity_is_current_state_and_never_an_event_record(repository):
    """活动只是当前状态，不写入 Conversation 事件记录

    set_activities 只更新 conversation_states，返回不推进 cursor 的实时事件；events_after 中没有 resident.activity.changed，Snapshot 读到最新活动。

    :design: decision_c524c775e0be
    """
    resident_id = "resident_111111111111"
    repository.commit_user_message(resident_id, message(1))
    cursor = repository.snapshot(resident_id)["cursor"]
    for index in range(3):
        event = repository.set_activities(resident_id, [{
            "activity_id": f"activity-{index}", "resident_id": resident_id,
            "kind": "command", "status": "running", "content": f"step {index}",
        }])
        assert event["live"] is True and event["cursor"] == cursor
        assert event["payload"]["activities"][0]["content"] == f"step {index}"
    events = repository.events_after(resident_id, 0)["events"]
    assert resident_conversation_stream.ACTIVITY_CHANGED not in {item["event_type"] for item in events}
    snapshot = repository.snapshot(resident_id)
    assert snapshot["cursor"] == cursor
    assert snapshot["activities"][0]["content"] == "step 2"


def test_relay_work_items_are_pruned_and_events_are_kept(repository):
    """事件转发工作项按时间清理，事件记录保留

    prune_event_deliveries 只删除早于截止时间的 conversation_event_delivery 行，conversation_events 不变。

    :design: decision_c524c775e0be
    """
    resident_id = "resident_111111111111"
    repository.commit_user_message(resident_id, message(1))
    repository.commit_user_message(resident_id, message(2))

    def deliveries(connection):
        return connection.execute("SELECT COUNT(*) AS n FROM conversation_event_delivery").fetchone()["n"]

    before = repository.state_store.database.write(deliveries)
    events = repository.events_after(resident_id, 0)["events"]
    assert repository.prune_event_deliveries(older_than=min(item["occurred_at"] for item in events)) == 0
    assert repository.prune_event_deliveries(older_than="9999-01-01T00:00:00.000Z", limit=1) == 1
    assert repository.prune_event_deliveries(older_than="9999-01-01T00:00:00.000Z") == before - 1
    assert repository.state_store.database.write(deliveries) == 0
    assert repository.events_after(resident_id, 0)["events"] == events
