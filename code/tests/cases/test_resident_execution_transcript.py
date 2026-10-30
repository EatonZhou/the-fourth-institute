"""Execution output reaches the Resident's Chat transcript: durable messages, display messages, cursors
and conversation replay.

:module: resident.identity
:importance: P2
"""
from __future__ import annotations
from management_portal.execution import event_projection as execution_events
from management_portal.society_service import resident_backend
from management_portal.society_service import resident_conversation_stream

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.society_service import (
    resident_conversation_projection_domain,
    resident_execution_domain,
    resident_execution_work,
    resident_lifecycle_domain,
    resident_protocol,
    resident_record_domain,
    resident_domain_common,
    resident_input_queue_domain,
    resident_execution_engine_domain,
)
from management_portal.society_service import lifecycle
from management_portal.society_service.resident_execution_work import bind_execution
from management_portal.execution.agent_data_store import stable_segment_id
from management_portal.execution.service import ExecutionService
from management_portal.society_service import resident_conversation_records
from tests.cases.resident_execution_test_support import _message_text
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401 - fixture


def test_snapshot_and_history_pages_do_not_read_full_conversation(
    resident_record: dict[str, object],
) -> None:
    """对话快照与历史分页仅读取所需消息页"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    for index in range(1, 6):
        repository.append_message(
            resident_id,
            {
                "message_id": f"msg_page_{index:02d}",
                "role": "user",
                "content_parts": [{"type": "text", "text": f"message {index}"}],
                "created_at": f"2026-08-30T00:00:0{index}Z",
            },
        )

    with mock.patch.object(
        repository.agent_data,
        "read_chat_messages",
        side_effect=AssertionError("full conversation read"),
    ):
        snapshot = repository.snapshot(resident_id, history_limit=2)
        older = repository.messages_page(
            resident_id,
            page_token=snapshot["next_page_token"],
            limit=2,
        )

    assert [item["message_id"] for item in snapshot["messages"]] == [
        "msg_page_04",
        "msg_page_05",
    ]
    assert [item["message_id"] for item in older["messages"]] == [
        "msg_page_02",
        "msg_page_03",
    ]
    assert older["next_page_token"]


def test_conversation_message_is_persisted_with_its_durable_event(
    resident_record: dict[str, object],
) -> None:
    """对话消息与持久事件一并保存"""
    observed: list[tuple[str, object]] = []
    message = {
        "message_id": "msg_0123456789abcdef",
        "role": "agent",
        "content_parts": [{"type": "text", "text": "完整回答"}],
    }
    repository = resident_conversation_stream.get_repository()
    resident_id = str(resident_record["resident_id"])
    before_cursor = repository.current_projection(resident_id)["cursor"]
    project = repository.agent_data.project_chat_message
    with mock.patch.object(
            repository.agent_data,
            "project_chat_message",
            side_effect=lambda target_id, item, **kwargs: (
                observed.append(("persist", item))
                or project(target_id, item, **kwargs)
            ),
    ):
        result = resident_lifecycle_domain.append_conversation_message(
            resident_id,
            message,
        )

    assert result["message_id"] == message["message_id"]
    assert observed[0][0] == "persist"
    assert observed[0][1]["message_id"] == message["message_id"]
    assert observed[0][1]["content_parts"] == [
        {"type": "text", "text": "完整回答"}
    ]
    assert observed[0][1]["message_id"] == message["message_id"]
    events = repository.events_after(resident_id, before_cursor)["events"]
    assert len(events) == 1
    assert events[0]["event_type"] == "conversation.message.appended"
    assert events[0]["payload"]["message"]["message_id"] == message["message_id"]


def test_execution_display_message_reaches_transcript_and_notification(
    resident_record: dict[str, object],
) -> None:
    """执行展示消息进入记录和通知且最终回答留给结果通道"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "3" * 24
    job = {

        "execution_id": execution_id,
        "status": "running",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    output = {
        "output_id": "msg_30000000000000000000000000000000",
        "phase": "commentary",
        "text": "完整展示消息",
        "created_at": "2026-08-17T00:00:00Z",
    }
    provider_final = {
        "output_id": "msg_30000000000000000000000000000001",
        "phase": "final_answer",
        "text": "最终回答由 ExecutionResult 统一提交",
        "created_at": "2026-08-17T00:00:01Z",
    }

    repository = resident_conversation_stream.get_repository()
    before_cursor = repository.current_projection(resident_id)["cursor"]
    persisted = resident_execution_engine_domain.persist_execution_display_messages(
        resident_id,

        execution_id,
        [output, provider_final],
    )

    assert len(persisted) == 1
    assert persisted[0]["role"] == "agent"
    stored = resident_conversation_stream.get_repository().read_messages(
        resident_id
    )[-1]
    assert stored["message_id"] == persisted[0]["message_id"]
    assert _message_text(stored) == _message_text(persisted[0])
    assert all(
        item["message_id"] != provider_final["output_id"]
        for item in resident_conversation_stream.get_repository().read_messages(
            resident_id
        )
    )
    events = repository.events_after(resident_id, before_cursor)["events"]
    assert len(events) == 1
    published = events[0]["payload"]["message"]
    assert published["message_id"] == persisted[0]["message_id"]


def test_captured_outputs_enter_trace_and_publish_in_one_queue_order(resident_record) -> None:
    """捕获输出按投递队列顺序进入轨迹并发布"""
    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "ordered-delivery", "execution-" + "7" * 24
    resident_execution_work.write(resident_record, {
         "execution_id": execution_id, "actor": "CASE-A", "status": "running",
     "submitted_at": "2026-10-14T00:00:00Z"})
    repository = resident_conversation_stream.get_repository()
    store = repository.agent_data
    store.begin_execution(
        execution_id=execution_id, agent_id=resident_id,
        actor_ref="CASE-A", provider="codex", started_at="2026-10-12T00:00:00Z",
    )
    segment_ids = {}
    # Capture storage may already contain later content; only the delivery
    # queue decides when a visible message enters the Resident trace.
    for number in (1, 2, 4, 3):
        message_id = "msg_" + f"{number:032x}"
        segment_id = stable_segment_id("provider-model", execution_id, str(number), "commentary")
        created_at = f"2026-10-12T00:00:0{number}Z"
        store.put_segment(
            segment_id=segment_id, segment_type="model", content=f"output {number}",
            started_at=created_at, created_by_agent_id=resident_id,
            execution_id=execution_id, created_by_actor_ref=resident_id,
            metadata={"role": "assistant", "kind": "commentary", "message": {
                "message_id": message_id, "role": "agent", "author_id": resident_id,
                "created_at": created_at, "execution_id": execution_id,
                "parts": [{"type": "text"}],
            }},
        )
        segment_ids[number] = segment_id

    outputs = [
        {"output_id": "msg_" + f"{number:032x}", "text": f"output {number}",
         "created_at": f"2026-10-12T00:00:0{number}Z"}
        for number in (1, 2, 3, 4)
    ]

    assert all(store.chat_message(resident_id, item["output_id"]) is None for item in outputs)
    for output in outputs:
        resident_execution_engine_domain.persist_execution_display_messages(
            resident_id,  execution_id, [output],
        )

    expected = [item["output_id"] for item in outputs]
    assert [item["message_id"] for item in repository.snapshot(resident_id)["messages"]] == expected
    assert [event["payload"]["message"]["message_id"]
            for event in repository.events_after(resident_id, 0)["events"]
            if event["event_type"] == "conversation.message.appended"] == expected
    assert store.sequence_segment_ids(store.trace_sequence_id(resident_id)) == [
        segment_ids[number] for number in (1, 2, 3, 4)
    ]


def test_execution_message_callbacks_do_not_hydrate_resources(resident_record, monkeypatch) -> None:
    """消息与执行投影回调不依赖资源服务

    验证中间消息、Final、事件游标及错误投影只使用已绑定执行的本地事实，保留顺序、单调性和旧 execution 隔离。

    :importance: P1
    :aspects: functional, performance, architecture-conformance
    :design: decision_edc470e09f5a, decision_1b2bdf5c33d6
    """
    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "callback-job", "execution-" + "b" * 24
    resident_execution_work.write(resident_record, { "actor": "CASE-A", "execution_id": execution_id, "actor": "CASE-A", "submitted_at": "2026-10-14T00:00:00Z"})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("execution message callbacks must not hydrate resources")

    monkeypatch.setattr(
        execution_events, "execution_projection_from_execution",
        lambda *_args, **_kwargs: {"outputs": [], "details": []},
    )
    resident_execution_engine_domain._project_resident_execution_snapshot(
        resident_id,  execution_id, {"execution_id": execution_id, "status": "running"},
    )
    output = {"output_id": "msg_" + "b" * 32, "text": "intermediate", "phase": "commentary"}
    assert resident_execution_engine_domain.persist_execution_display_messages(resident_id, "old-execution", [output]) == []
    messages = resident_execution_engine_domain.persist_execution_display_messages(resident_id,  execution_id, [output])
    assert len(messages) == 1
    resident_execution_engine_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 7)
    resident_execution_engine_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 3)
    resident_execution_engine_domain._advance_resident_execution_event_cursor(resident_id, "old-execution", 99)
    assert resident_execution_engine_domain._resident_execution_event_cursor(resident_id,  execution_id) == 7
    assert resident_execution_engine_domain._resident_execution_event_cursor(resident_id,  "old-execution") == 0
    final = resident_input_queue_domain.build_message("agent", "final", message_id="msg_" + "c" * 32)
    assert resident_execution_domain._persist_execution_final_message(resident_id,  "old-execution", final) is None
    assert resident_execution_domain._persist_execution_final_message(resident_id,  execution_id, final)["message_id"] == final["message_id"]
    stored = resident_conversation_stream.get_repository().read_messages(resident_id)
    assert [item["message_id"] for item in stored] == [output["output_id"], final["message_id"]]
    poison_message_id = "msg_" + "d" * 32
    poison_output = {
        "output_id": poison_message_id,
        "text": "must remain quarantined",
        "phase": "commentary",
    }
    projection_attempts = 0

    def reject_poison(*_args, **_kwargs):
        nonlocal projection_attempts
        projection_attempts += 1
        raise ValueError("test projection fault")

    monkeypatch.setattr(resident_lifecycle_domain, "append_conversation_message", reject_poison)
    with pytest.raises(ValueError, match="test projection fault") as raised:
        resident_execution_engine_domain.persist_execution_display_messages(
            resident_id,  execution_id, [poison_output],
        )
    projection_error = raised.value
    assert projection_error._resident_projection_message_id == poison_message_id
    resident_execution_engine_domain._record_resident_projection_error(
        resident_id,  execution_id, projection_error,
    )
    stored_job = resident_execution_work.read(resident_record)
    assert stored_job["conversation_projection_error"]["execution_id"] == execution_id
    assert stored_job["conversation_projection_quarantine"] == {
        "execution_id": execution_id,
        "message_ids": [poison_message_id],
        "last_event_sequence": 0,
        "updated_at": stored_job["conversation_projection_error"]["recorded_at"],
    }
    assert resident_execution_engine_domain.persist_execution_display_messages(
        resident_id,  execution_id, [poison_output],
    ) == []
    assert projection_attempts == 1
    stored = resident_conversation_stream.get_repository().read_messages(resident_id)
    assert poison_message_id not in {item["message_id"] for item in stored}


def test_execution_cursor_checkpoint_does_not_republish_runtime(
    resident_record, monkeypatch,
) -> None:
    """执行游标检查点单调推进且不重复发布运行状态"""
    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "cursor-job", "execution-" + "d" * 24
    resident_execution_work.write(resident_record, {

        "execution_id": execution_id,
        "actor": "CASE-A",
     "submitted_at": "2026-10-14T00:00:00Z"})
    before = resident_execution_work.read(resident_record)
    store = resident_execution_work.get_lineage_data_store()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("internal cursor progress must not republish runtime state")

    monkeypatch.setattr(resident_execution_work, "publish_conversation_state", forbidden)
    monkeypatch.setattr(resident_execution_work, "notify_changed", forbidden)
    monkeypatch.setattr(lifecycle, "notify_execution_binding", forbidden)
    monkeypatch.setattr(lifecycle, "notify_execution_cancellation", forbidden)
    with mock.patch.object(
        store, "advance_resident_execution_cursor",
        wraps=store.advance_resident_execution_cursor,
    ) as persist:
        resident_execution_engine_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 7)
        resident_execution_engine_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 7)
        resident_execution_engine_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 3)
        resident_execution_engine_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 0)
        resident_execution_engine_domain._advance_resident_execution_event_cursor(resident_id, "old-execution", 99)
        resident_execution_engine_domain._advance_resident_execution_event_cursor(resident_id,  "old-execution", 99)
    assert persist.call_count == 5
    assert resident_execution_work.read(resident_record) == {**before, "execution_event_cursor": 7}
    assert resident_execution_engine_domain._resident_execution_event_cursor(resident_id,  execution_id) == 7


def test_execution_cursor_checkpoint_failure_keeps_retry_position(
    resident_record,
) -> None:
    """游标检查点保存失败时保留原重试位置"""
    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "cursor-retry", "execution-" + "e" * 24
    resident_execution_work.write(resident_record, {

        "execution_id": execution_id,
        "execution_event_cursor": 3,
        "actor": "CASE-A",
     "submitted_at": "2026-10-14T00:00:00Z"})
    store = resident_execution_work.get_lineage_data_store()
    with mock.patch.object(
        store, "advance_resident_execution_cursor", side_effect=OSError("checkpoint unavailable"),
    ), pytest.raises(OSError, match="checkpoint unavailable"):
        resident_execution_engine_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 7)
    assert resident_execution_engine_domain._resident_execution_event_cursor(resident_id,  execution_id) == 3
    resident_execution_engine_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 7)
    assert resident_execution_engine_domain._resident_execution_event_cursor(resident_id,  execution_id) == 7


def test_execution_display_message_replay_is_idempotent(
    resident_record: dict[str, object],
) -> None:
    """执行展示消息重放仅保存一份记录"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "7" * 24
    job = {

        "execution_id": execution_id,
        "status": "running",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    output = {
        "output_id": "msg_70000000000000000000000000000000",
        "phase": "commentary",
        "text": "durable update",
        "created_at": "2026-08-17T00:00:00Z",
    }

    first = resident_execution_engine_domain.persist_execution_display_messages(
        resident_id,

        execution_id,
        [output],
    )
    replay = resident_execution_engine_domain.persist_execution_display_messages(
        resident_id,

        execution_id,
        [output],
    )

    assert replay == first
    assert len(
        resident_conversation_stream.get_repository().read_messages(
            resident_id
        )
    ) == 1


def test_execution_display_batch_releases_state_lock_between_messages(resident_record, monkeypatch):
    """批量展示消息在逐条处理之间释放状态锁

    :design: decision_c524c775e0be
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "incremental-batch", "execution-" + "c" * 24
    resident_execution_work.write(resident_record, { "actor": "CASE-A", "execution_id": execution_id, "submitted_at": "2026-10-14T00:00:00Z"})
    original_lock = resident_record_domain.state_lock
    depth = 0

    @contextmanager
    def observed_lock(target):
        nonlocal depth
        with original_lock(target):
            depth += 1
            try:
                yield
            finally:
                depth -= 1

    def outputs():
        for index in range(3):
            assert depth == 0, "history iteration must not occupy the state command lock"
            yield {"output_id": "msg_" + f"{index:032x}", "text": f"update-{index}"}

    monkeypatch.setattr(resident_record_domain, "state_lock", observed_lock)
    assert len(resident_execution_engine_domain.persist_execution_display_messages(resident_id,  execution_id, outputs())) == 3


def test_execution_display_batch_refences_each_message(resident_record, monkeypatch):
    """批量展示消息每条重新核对当前执行身份

    :design: decision_c524c775e0be
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "incremental-fence", "execution-" + "d" * 24
    resident_execution_work.write(resident_record, { "actor": "CASE-A", "execution_id": execution_id, "submitted_at": "2026-10-14T00:00:00Z"})
    append = resident_lifecycle_domain.append_conversation_message

    def replace_job_after_append(target, message):
        committed = append(target, message)
        resident_execution_work.write(resident_record, { "actor": "CASE-A", "execution_id": "replacement-execution", "submitted_at": "2026-10-14T00:00:00Z"})
        return committed

    monkeypatch.setattr(resident_lifecycle_domain, "append_conversation_message", replace_job_after_append)
    outputs = [{"output_id": "msg_" + f"{index:032x}", "text": f"update-{index}"} for index in range(3)]
    assert len(resident_execution_engine_domain.persist_execution_display_messages(resident_id,  execution_id, outputs)) == 1


def test_execution_display_batch_allows_stop_before_recovery_finishes(resident_record):
    """批量展示消息恢复完成前仍允许停止任务

    :design: decision_c524c775e0be
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_" + "e" * 32
    job_id, execution_id = "incremental-stop", "execution-" + "e" * 24
    resident_execution_work.write(resident_record, { "actor": "CASE-A", "execution_id": execution_id,
                                         "actor": "CASE-A", "user_message_id": message_id, "submitted_at": "2026-10-14T00:00:00Z"})
    stopped = threading.Event()
    errors = []

    def stop():
        try:
            resident_execution_domain.cancel_resident_execution_for_target(resident_id, "CASE-A", message_id)
        except Exception as error:
            errors.append(error)
        finally:
            stopped.set()

    thread = threading.Thread(target=stop)

    def outputs():
        yield {"output_id": "msg_" + "1" * 32, "text": "first recovery message"}
        thread.start()
        assert stopped.wait(2), "Stop was blocked by the remaining recovery batch"
        assert not errors
        assert resident_execution_work.read(resident_record)["stop_requested_at"]
        yield {"output_id": "msg_" + "2" * 32, "text": "second recovery message"}

    try:
        assert len(resident_execution_engine_domain.persist_execution_display_messages(resident_id,  execution_id, outputs())) == 2
    finally:
        if thread.ident is not None:
            thread.join(timeout=3)


@pytest.mark.parametrize("fail_once", [False, True])
def test_execution_waiter_uses_incremental_durable_projection(resident_record, monkeypatch, tmp_path, fail_once):
    """执行等待者增量投影持久消息且失败后可重试

    :design: decision_c524c775e0be
    :design-point:
    """
    from tests.cases.test_execution_activity_incremental import _record

    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "incremental-waiter", "execution-" + "a" * 24
    resident_execution_work.write(resident_record, { "actor": "CASE-A", "execution_id": execution_id, "submitted_at": "2026-10-14T00:00:00Z"})
    path = tmp_path / f"{execution_id}.jsonl"
    path.write_bytes(_record(1))
    monkeypatch.setattr(execution_events, "execution_activity_path", lambda _id: path)
    monkeypatch.setattr(execution_events, "execution_projection_from_execution",
                        lambda *_args, **_kwargs: pytest.fail("waiter scanned full historical projection"))
    batches = []
    persist = resident_execution_engine_domain.persist_execution_display_messages

    def observe(*args):
        batches.append(len(args[-1]))
        if fail_once and len(batches) == 1:
            raise RuntimeError("temporary projection failure")
        return persist(*args)

    monkeypatch.setattr(resident_execution_engine_domain, "persist_execution_display_messages", observe)

    class Service:
        @staticmethod
        def wait(_execution_id, **kwargs):
            snapshot = {"status": "running"}
            kwargs["on_events"]([{"sequence": 1, "type": "started", "details": {}}])
            kwargs["on_snapshot"](snapshot)
            kwargs["on_snapshot"](snapshot)
            with path.open("ab") as stream:
                stream.write(_record(2))
            kwargs["on_snapshot"](snapshot)
            kwargs["on_snapshot"](snapshot)
            return snapshot

    result = resident_execution_engine_domain.wait_for_resident_execution(Service(), execution_id=execution_id,
                                                 actor="CASE-A", resident_id=resident_id)
    assert len(result["projection_errors"]) == int(fail_once)
    assert batches == ([1, 1, 1, 0] if fail_once else [1, 0, 1, 0])
    messages = resident_conversation_stream.get_repository().read_messages(resident_id)
    assert [_message_text(message) for message in messages] == ["message-1", "message-2"]


def test_execution_snapshot_recovers_display_messages_from_durable_transcript(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行快照从持久记录恢复展示消息且不重复发布"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "8" * 24
    job = {
        'execution_id': execution_id,
        "actor": "CASE-A",
        "user_message_id": "msg_snapshot_input",
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id=execution_id,


        now="2026-08-17T00:00:00Z",
    )
    resident_execution_work.write(resident_record, job)
    commentary = {
        "output_id": "msg_80000000000000000000000000000000",
        "phase": "commentary",
        "text": "restart-safe update",
        "created_at": "2026-08-17T00:00:01Z",
    }
    provider_final = {
        "output_id": "msg_80000000000000000000000000000001",
        "phase": "final_answer",
        "text": "final stays on the result path",
        "created_at": "2026-08-17T00:00:02Z",
    }
    monkeypatch.setattr(
        execution_events,
        "execution_projection_from_execution",
        lambda *args, **kwargs: {
            "execution_id": execution_id,
            "outputs": [commentary, provider_final],
            "runtime_details": [],
        },
    )

    repository = resident_conversation_stream.get_repository()
    before_cursor = repository.current_projection(resident_id)["cursor"]
    for _ in range(2):
        resident_execution_engine_domain._project_resident_execution_snapshot(
            resident_id,

            execution_id,
            {"status": "running"},
        )

    messages = repository.read_messages(resident_id)
    assert [item["message_id"] for item in messages].count(
        commentary["output_id"]
    ) == 1
    assert all(item["message_id"] != provider_final["output_id"] for item in messages)
    appended = [
        event
        for event in repository.events_after(resident_id, before_cursor)["events"]
        if event["event_type"] == "conversation.message.appended"
        and event["payload"]["message"]["message_id"] == commentary["output_id"]
    ]
    assert len(appended) == 1


def test_execution_snapshot_replay_keeps_message_time_and_unblocks_later_updates(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """快照重放保留消息时间且允许后续更新继续写入

    :design: decision_c524c775e0be
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "9" * 24
    job_id = "job_replay_observation_time"
    job = {
        'execution_id': execution_id,
        "actor": "CASE-A",
        "user_message_id": "msg_90000000000000000000000000000002",
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(job, execution_id=execution_id,
                    now="2026-09-07T07:49:00Z")
    resident_execution_work.write(resident_record, job)
    first = {
        "output_id": "msg_90000000000000000000000000000000", "phase": "commentary",
        "text": "same message content", "created_at": "2026-09-07T07:49:42.724000Z",
    }
    repository = resident_conversation_stream.get_repository()
    resident_execution_engine_domain.persist_execution_display_messages(resident_id,  execution_id, [first])
    cursor = repository.current_projection(resident_id)["cursor"]
    replay = {**first, "created_at": "2026-09-07T07:52:43.881Z"}
    later = {**first, "output_id": "msg_90000000000000000000000000000001",
             "created_at": "2026-09-07T08:18:18.251Z"}
    monkeypatch.setattr(execution_events, "execution_projection_from_execution",
                        lambda *args, **kwargs: {"outputs": [replay, later], "runtime_details": []})
    for _ in range(2):
        resident_execution_engine_domain._project_resident_execution_snapshot(resident_id,  execution_id,
                                                      {"status": "running"})
    messages = repository.read_messages(resident_id)
    assert [m["message_id"] for m in messages] == [first["output_id"], later["output_id"]]
    assert messages[0]["created_at"] == first["created_at"]
    assert messages[1]["created_at"] == later["created_at"]
    appended = [e for e in repository.events_after(resident_id, cursor)["events"]
                if e["event_type"] == "conversation.message.appended"]
    assert len(appended) == 1
    with pytest.raises(ValueError, match="different immutable content"):
        resident_execution_engine_domain.persist_execution_display_messages(
            resident_id,  execution_id, [{**replay, "text": "changed body"}])
    resident_execution_work.write(resident_record, {**resident_execution_work.read(resident_record),
                                         "user_message_id": "msg_90000000000000000000000000000003"})
    with pytest.raises(ValueError, match="different immutable content"):
        resident_execution_engine_domain.persist_execution_display_messages(resident_id,  execution_id, [replay])


def test_execution_display_message_replay_rejects_identity_conflict(
    resident_record: dict[str, object],
) -> None:
    """展示消息重放拒绝相同标识对应不同内容"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "8" * 24
    job = {

        "execution_id": execution_id,
        "status": "running",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    output = {
        "output_id": "msg_80000000000000000000000000000000",
        "phase": "commentary",
        "text": "first content",
        "created_at": "2026-08-17T00:00:00Z",
    }
    resident_execution_engine_domain.persist_execution_display_messages(
        resident_id,

        execution_id,
        [output],
    )

    with pytest.raises(ValueError, match="different immutable content"):
        resident_execution_engine_domain.persist_execution_display_messages(
            resident_id,

            execution_id,
            [{
                **output,
                "text": "conflicting content",
            }],
        )


def test_execution_display_messages_append_after_injected_user_message(
    resident_record: dict[str, object],
) -> None:
    """执行展示消息排在已注入的用户消息之后"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "4" * 24
    user_message_id = "msg_40000000000000000000000000000000"
    job = {

        "execution_id": execution_id,
        "user_message_id": user_message_id,
        "status": "running",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    resident_lifecycle_domain.append_conversation_message(resident_id, {
        "message_id": user_message_id,
        "role": "user",
        "author_id": "CASE-A",
        "content_parts": [{"type": "text", "text": "question"}],
        "created_at": "2026-08-17T00:00:00Z",
    })
    resident_lifecycle_domain.append_conversation_message(resident_id, {
        "message_id": "msg_40000000000000000000000000000003",
        "role": "user",
        "content_parts": [{"type": "text", "text": "injected follow-up"}],
        "created_at": "2026-08-17T00:00:03Z",
    })

    for output in (
        {
            "output_id": "msg_40000000000000000000000000000001",
            "phase": "commentary",
            "text": "first update",
            "created_at": "2026-08-17T00:00:01Z",
        },
        {
            "output_id": "msg_40000000000000000000000000000002",
            "phase": "commentary",
            "text": "second update",
            "created_at": "2026-08-17T00:00:02Z",
        },
    ):
        resident_execution_engine_domain.persist_execution_display_messages(
            resident_id,

            execution_id,
            [output],
        )

    assert [
        _message_text(item)
        for item in resident_conversation_stream.get_repository().read_messages(
            resident_id
        )
    ] == ["question", "injected follow-up", "first update", "second update"]


def test_execution_final_message_follows_projected_updates(
    resident_record: dict[str, object],
) -> None:
    """执行最终消息排在已投影的进度更新之后"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "5" * 24
    user_message_id = "msg_50000000000000000000000000000000"
    job = {

        "execution_id": execution_id,
        "user_message_id": user_message_id,
        "status": "running",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    resident_lifecycle_domain.append_conversation_message(resident_id, {
        "message_id": user_message_id,
        "role": "user",
        "author_id": "CASE-A",
        "content_parts": [{"type": "text", "text": "question"}],
        "created_at": "2026-08-17T00:00:00Z",
    })
    resident_execution_engine_domain.persist_execution_display_messages(
        resident_id,

        execution_id,
        [{
            "output_id": "msg_50000000000000000000000000000001",
            "phase": "commentary",
            "text": "update",
            "created_at": "2026-08-17T00:00:01Z",
        }],
    )
    resident_execution_engine_domain.persist_execution_display_messages(
        resident_id,

        execution_id,
        [{
            "output_id": "msg_50000000000000000000000000000002",
            "phase": "resident_reply",
            "text": "final",
            "created_at": "2026-08-17T00:00:02Z",
        }],
    )

    assert [
        _message_text(item)
        for item in resident_conversation_stream.get_repository().read_messages(
            resident_id
        )
    ] == ["question", "update", "final"]


def test_conversation_replay_does_not_acquire_writer_locks(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已提交对话消息重放无需获取写锁"""
    repository = resident_conversation_stream.get_repository()
    resident_id = str(resident_record["resident_id"])
    message = resident_input_queue_domain.build_message("agent", "durable update")
    persisted = repository.append_message(resident_id, message)

    def forbidden_write(*args, **kwargs):
        raise AssertionError("Committed replay must not acquire a writer lock")

    monkeypatch.setattr(repository.events, "protocol_scope", forbidden_write)
    monkeypatch.setattr(repository.state_store.database, "write", forbidden_write)
    monkeypatch.setattr(repository.agent_data.database, "write", forbidden_write)
    assert repository.append_message(resident_id, message) == persisted
    with pytest.raises(ValueError, match="immutable"):
        repository.append_message(resident_id, {**message, "content_parts": [
            {"type": "text", "text": "different content"},
        ]})


@pytest.mark.parametrize("gate", [
    {"state": "open", "reason": ""},
    {"state": "closed", "reason": "quota_used_up"},
    {"state": "unknown", "reason": "execution_unavailable"},
])
def test_conversation_native_controls_follow_snapshot_and_events(resident_record, monkeypatch, gate):
    """对话快照和事件中的原生控制状态保持一致"""
    from copy import deepcopy

    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    quota_calls = []

    def quota_states(_self, bindings):
        quota_calls.append(dict(bindings))
        return {key: dict(gate) for key in bindings}

    monkeypatch.setattr(ExecutionService, "binding_quota_states", quota_states)
    for intake, active_message_id in [("closed", ""), ("open", "active-input"), ("open", "")]:
        state = {
            "intake_state": intake,
            "work_state": "running" if active_message_id else "idle",
            "active_message_id": active_message_id,
            "queued_count": 0,
        }
        repository.state_store.database.write(lambda c: c.execute('UPDATE residents SET intake_open=%s WHERE resident_id=%s',(intake == "open",resident_id)))
        if active_message_id:
            resident_execution_work.write(resident_record, { 'actor':'CASE-A', 'execution_id':"controls", 'user_message_id':active_message_id, "submitted_at": "2026-10-14T00:00:00Z"})
        else:
            resident_execution_work.clear(resident_record)
        event = repository.set_state(resident_id, state)
        if event is None:
            event = repository.events_after(resident_id,0)['events'][-1]
        original_projection = deepcopy(repository.current_projection(resident_id))
        original_events = deepcopy(repository.events_after(resident_id, event["cursor"] - 1))
        snapshot = resident_protocol.get_resident_conversation_snapshot(resident_id, "CASE-A")
        events = resident_protocol.get_resident_events(resident_id, "CASE-A", event["cursor"] - 1)["events"]
        expected_gate = {"state": "closed", "reason": "manual"} if intake == "closed" else gate
        assert snapshot["state"]["execution_gate"] == expected_gate
        assert events[0]["payload"]["state"]["execution_gate"] == expected_gate
        assert bool(snapshot["controls"]["send_hint"]) is bool(expected_gate["reason"])
        assert snapshot["controls"] == events[0]["payload"]["controls"]
        assert snapshot["controls"]["can_send"] is True
        assert snapshot["controls"]["can_stop"] is bool(active_message_id)
        assert resident_protocol.get_resident_state(resident_id, "CASE-A")["controls"] == snapshot["controls"]
        if active_message_id:
            with monkeypatch.context() as cancellation:
                cancellation.setattr(resident_protocol, "_stored_resident_input_state", lambda *_: {"state": "running"})
                for with_state in (False, True):
                    raw_receipt = {"resident_id": resident_id, "cancellation_requested": True, "backend_cancelled": False}
                    if with_state:
                        raw_receipt["state"] = deepcopy(state)
                    before_receipt = deepcopy(raw_receipt)
                    cancellation.setattr(resident_execution_domain, "request_resident_execution_cancellation",
                                         lambda *_args, **_kwargs: raw_receipt)
                    before_calls = len(quota_calls)
                    receipt = resident_protocol.cancel_resident_execution(
                        resident_id, "CASE-A", active_message_id, continue_queue=False)
                    assert receipt["message_id"] == active_message_id
                    assert receipt["cancellation_requested"] is True
                    assert receipt["backend_cancelled"] is False
                    if with_state:
                        assert receipt["state"]["execution_gate"] == expected_gate
                        assert receipt["controls"] == snapshot["controls"]
                    else:
                        assert receipt == {**raw_receipt, "message_id": active_message_id}
                        assert len(quota_calls) == before_calls
                    assert raw_receipt == before_receipt
        assert repository.current_projection(resident_id) == original_projection
        assert repository.events_after(resident_id, event["cursor"] - 1) == original_events
        notification = resident_conversation_records.conversation_event_notification(event)
        assert notification["payload_omitted"] is True
        assert "payload" not in notification
        assert "controls" not in repository.events_after(resident_id, event["cursor"] - 1)["events"][0]["payload"]
    with pytest.raises(PermissionError):
        resident_protocol.get_resident_conversation_snapshot(resident_id, "")


def test_conversation_replay_repairs_missing_event(
    resident_record: dict[str, object],
) -> None:
    """对话消息重放补齐缺失事件且仅发布一次"""
    repository = resident_conversation_stream.get_repository()
    resident_id = str(resident_record["resident_id"])
    message = resident_input_queue_domain.build_message("agent", "committed before event failure")
    repository.agent_data.project_chat_message(resident_id, message)
    before = repository.current_projection(resident_id)["cursor"]
    repository.append_message(resident_id, message)
    repository.append_message(resident_id, message)
    events = repository.events_after(resident_id, before)["events"]
    assert len(events) == 1
    assert events[0]["event_type"] == "conversation.message.appended"
    assert events[0]["payload"]["message"]["message_id"] == message["message_id"]


def test_concurrent_conversation_replays_commit_one_message_and_event(
    resident_record: dict[str, object],
) -> None:
    """并发对话重放仅提交一条消息和一个事件"""
    repository = resident_conversation_stream.get_repository()
    resident_id = str(resident_record["resident_id"])
    message = resident_input_queue_domain.build_message("agent", "concurrent durable update")
    before = repository.current_projection(resident_id)["cursor"]
    ready = threading.Barrier(8)

    def replay(_index):
        ready.wait(timeout=5)
        return repository.append_message(resident_id, message)

    with ThreadPoolExecutor(max_workers=8) as pool:
        messages = list(pool.map(replay, range(8)))
    assert all(item == messages[0] for item in messages)
    assert len(repository.events_after(resident_id, before)["events"]) == 1
    assert len(repository.read_messages(resident_id)) == 1


def test_resident_timeline_keeps_canonical_messages_without_attached_session(
    resident_record: dict[str, object],
) -> None:
    """Resident 未绑定会话时仍保留规范对话时间线"""
    resident_backend.delete_session_identity(resident_record)
    resident_lifecycle_domain.append_conversation_message(str(resident_record["resident_id"]), {
        "message_id": "msg_33333333333333333333333333333333",
        "role": "user",
        "content_parts": [{"type": "text", "text": "start"}],
    })
    resident_lifecycle_domain.append_conversation_message(str(resident_record["resident_id"]), {
        "message_id": "msg_44444444444444444444444444444444",
        "role": "agent",
        "content_parts": [
            {"type": "text", "text": "reply survived restart"}
        ],
    })

    messages = resident_conversation_projection_domain.resident_conversation_messages(
        resident_record,
    )

    assert [resident_domain_common.protocol_message_text(item) for item in messages] == [
        "start",
        "reply survived restart",
    ]
