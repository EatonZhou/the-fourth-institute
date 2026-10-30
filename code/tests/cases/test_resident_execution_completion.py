"""Completion of a Resident's turn: wake-ups, applied completions, final messages, failures and the
projection intents of job writes.

:module: resident.identity
:importance: P2
"""
from __future__ import annotations
from tests.cases.resident_delivery_test_support import published_delivery as _published_delivery
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service.domain_ports import execution_port
from management_portal.execution import slash_commands

from types import SimpleNamespace
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.society_service import (
    resident_execution_domain,
    resident_intake_domain,
    resident_execution_work,
    resident_lifecycle_domain,
    resident_record_domain,
    resident_session_backend_domain,
    resident_domain_common,
    resident_input_queue_domain,
    resident_execution_engine_domain,
)
from management_portal.society_service import lifecycle
from management_portal.society_service import resident_domain as backend
from management_portal.society_service.resident_execution_work import bind_execution
from tests.cases.resident_execution_test_support import _message_text, _deliver_session
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401 - fixture


def test_running_job_projection_does_not_run_recovery(
    resident_record: dict[str, object],
) -> None:
    """读取运行中任务投影不启动恢复"""
    resident_execution_work.write(resident_record, {

        "execution_id": "execution-projection-read",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})

    with mock.patch.object(
        resident_execution_domain,
        "_prepare_resident_continuation",
        side_effect=AssertionError("read projection invoked recovery"),
    ):
        projected = resident_input_queue_domain.active_resident_execution(resident_record)

    assert projected['execution_id'] == 'execution-projection-read'


def test_reopened_job_failure_appends_a_new_terminal_event(
    resident_record: dict[str, object],
) -> None:
    """任务重新执行失败时追加独立终态消息"""
    resident_id = str(resident_record["resident_id"])
    job = {

        "execution_id": "execution-first",
        "finished_at": "2026-08-25T01:00:00Z",
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_input_queue_domain.persist_failed_execution_message(
        resident_id,
        job,
        "first failure",
        {"message": "first failure"},
    )
    first_message_id = str(job["failure_message_id"])

    job.pop("failure_message_id")
    job.pop("failure_message_persisted_at")
    job["execution_id"] = "execution-second"
    job["finished_at"] = "2026-08-25T02:00:00Z"
    resident_input_queue_domain.persist_failed_execution_message(
        resident_id,
        job,
        "second failure",
        {"message": "second failure"},
    )

    assert job["failure_message_id"] != first_message_id
    assert resident_conversation_stream.get_repository().message(
        resident_id,
        str(job["failure_message_id"]),
    )["content_parts"][0]["text"] == "second failure"


def test_promoted_failures_each_persist_their_own_notice(resident_record):
    """队列逐项提升失败时分别保存通知并清除前轮状态

    :module: resident.conversation"""
    resident_id = str(resident_record["resident_id"])
    job = { "execution_id": "execution-first",
           "finished_at": "2026-10-11T00:00:00Z", "runtime_error": {"message": "first"},
           "execution_event_cursor": 23, "submitted_at": "2026-10-14T00:00:00Z"}
    resident_input_queue_domain.persist_failed_execution_message(resident_id, job, "first", {"message": "first"})
    first_id = job["failure_message_id"]
    resident_execution_work.promote_queue_item(job, {'execution_id': "job_second", "user_message_id": "second"}, [], "now")
    assert not set(job) & {"failure_message_id", "failure_message_persisted_at", "runtime_error", "execution_event_cursor"}
    resident_input_queue_domain.persist_failed_execution_message(resident_id, job, "second", {"message": "second"})
    assert job["failure_message_id"] != first_id
    repository = resident_conversation_stream.get_repository()
    assert repository.message(resident_id, first_id)["content_parts"][0]["text"] == "first"
    assert repository.message(resident_id, job["failure_message_id"])["content_parts"][0]["text"] == "second"


def test_runnable_local_jobs_wake_the_lineage_coordinator(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """可运行的本地任务唤醒 Lineage 协调者"""
    notifications: list[str] = []
    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        lambda resident_id: notifications.append(str(resident_id)),
    )
    resident_execution_work.write(
        resident_record,
        {
            'execution_id': "main_local_wake",
            'operation': "main_bootstrap",
            "actor": "CASE-A",
            "status": "queued",
            "queue_reason": "starting",
        },
    )
    assert notifications == [
        str(resident_record["resident_id"]),
    ]
    execution_work = resident_execution_work.read(resident_record)
    assert execution_work["execution_id"] == "main_local_wake"
    assert resident_execution_work.execution_id(execution_work)
    assert resident_execution_work.continuation_needed(resident_record, execution_work)


def test_execution_bound_job_wakes_completion_continuation(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已绑定执行的任务唤醒完成续接"""
    notifications: list[str] = []
    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        lambda resident_id: notifications.append(str(resident_id)),
    )

    resident_execution_work.write(
        resident_record,
        {

            "actor": "CASE-A",
            "execution_id": "execution-" + "8" * 24,
            "queue": [],
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    assert notifications == [str(resident_record["resident_id"])]


def test_applied_completion_retries_ack_without_reapplying_completion(
    resident_record: dict[str, object],
) -> None:
    """完成结果已应用时仅重试确认且不重复应用"""
    job = {
        'execution_id': "execution-" + "6" * 24,
        "actor": "CASE-A",
        "queue": [],
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id="execution-" + "6" * 24,


        now="2026-08-17T00:00:00Z",
    )
    resident_execution_domain._mark_completion_applied(job, "2026-08-17T00:01:00Z")
    continuation_id = str(job["continuation_id"])
    resident_execution_work.write(resident_record, job)

    with mock.patch.object(
        resident_input_queue_domain,
        "acknowledge_execution",
        side_effect=[False, True],
    ):
        assert resident_execution_domain._finalize_applied_execution(
            str(resident_record["resident_id"]),
            continuation_id,
            job,
        ) is False
        assert resident_execution_work.completion_applied(
            resident_execution_work.read(resident_record)
        )
        assert resident_execution_domain._finalize_applied_execution(
            str(resident_record["resident_id"]),
            continuation_id,
            job,
        ) is False

    assert resident_execution_work.read(resident_record) == {}


def test_a_second_finalizer_of_one_completion_keeps_the_promoted_queue_item(
    resident_record: dict[str, object],
) -> None:
    """同一完成结果被二次收尾时保留已提升的队列输入

    :module: resident.conversation
    :aspects: functional, performance
    """
    # Two runners can finalize the same applied completion (the runner and a recovery).
    # Promotion keeps the continuation, so only the job ID shows the queue already advanced.
    resident_id = str(resident_record["resident_id"])
    completed = {
        'execution_id': "execution-" + "7" * 24,
        "actor": "test-actor",
        "user_message_id": "message_first_batch",
        "queue": [{'execution_id': "job_second_batch", "user_message_id": "message_second_batch",
                   "message": "second batch", "actor": "test-actor",
                   "input_message": {"message_id": "message_second_batch", "role": "user", "author_id": "test-actor",
                                     "content_parts": [{"type": "text", "text": "second batch"}]}}],
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(completed, execution_id="execution-" + "7" * 24,
                    now="2026-09-29T08:25:35Z")
    resident_execution_domain._mark_completion_applied(completed, "2026-09-29T08:26:01Z")
    continuation_id = str(completed["continuation_id"])
    resident_execution_work.write(resident_record, completed)

    with mock.patch.object(resident_input_queue_domain, "acknowledge_execution", return_value=True), \
            mock.patch.object(resident_input_queue_domain, "reconcile_queued_turn_inputs", return_value=True):
        assert resident_execution_domain._finalize_applied_execution(resident_id, continuation_id, completed) is True
        assert resident_execution_domain._finalize_applied_execution(resident_id, continuation_id, completed) is False

    current = resident_execution_work.read(resident_record)
    assert current['execution_id'].startswith('execution-')
    assert current["user_message_id"] == "message_second_batch"


def test_resident_success_uses_shared_applied_completion_finalizer(
    resident_record: dict[str, object],
) -> None:
    """Resident 成功执行通过共享收尾器处理已应用结果"""
    resident_id = str(resident_record["resident_id"])
    resident_conversation_stream.get_repository().append_message(
        resident_id,
        {
            "message_id": "message_main_next",
            "role": "user",
            "content_parts": [{"type": "text", "text": "continue"}],
            "created_at": "2026-08-17T00:00:00Z",
            "author_id": "CASE-A",
        },
    )
    job = {
        'execution_id': "execution-" + "9" * 24,
        "actor": "CASE-A",
        "queue": [{
            'execution_id': "job_main_next",
            "user_message_id": "message_main_next",
            "message": "continue",
            "raw_message": "continue",
            "actor": "CASE-A",
        }],
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id="execution-" + "9" * 24,


        now="2026-08-17T00:00:00Z",
    )
    continuation_id = str(job["continuation_id"])
    resident_execution_work.write(resident_record, job)
    updated_record = dict(resident_record)
    updated_record["session_state"] = "active"

    with (
        mock.patch.object(
            resident_execution_domain,
            "send_to_resident_backend",
            return_value=(
                {
                    'execution_id': job['execution_id'],
                    "created_at": "2026-08-17T00:01:00Z",
                },
                updated_record,
            ),
        ) as send,
        mock.patch.object(
            resident_execution_domain,
            "_finalize_applied_execution",
            return_value=False,
        ) as finalize,
        mock.patch.object(resident_execution_domain, "_notify_lineage_message_completed"),
    ):
        resident_execution_domain.run_resident_execution(resident_id, continuation_id)

    send.assert_called_once()
    finalize.assert_called_once()
    persisted = resident_execution_work.read(resident_record)
    assert resident_execution_work.completion_applied(persisted)
    assert finalize.call_args.args[:2] == (resident_id, continuation_id)


def test_exiting_continuation_rewakes_job_persisted_during_shutdown(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """续接退出时重新唤醒关闭期间持久化的任务"""
    resident_id = str(resident_record["resident_id"])
    notifications: list[str] = []
    resident_execution_work.track_current_thread(resident_record)
    resident_execution_work.write(
        resident_record,
        {
            'execution_id': "job_shutdown_rewake",
            "actor": "CASE-A",
            "status": "queued",
            "queue_reason": "starting",
            "queue": [],
        },
    )
    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        lambda target_id: notifications.append(str(target_id)),
    )

    resident_execution_domain.finish_resident_continuation(resident_id)

    assert resident_execution_work.thread(resident_record) is None
    assert notifications == [resident_id]


def test_exiting_continuation_wakes_the_next_job(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """续接退出清除线程并唤醒下一任务"""
    resident_id = str(resident_record["resident_id"])
    notifications: list[str] = []
    resident_execution_work.track_current_thread(resident_record)
    resident_execution_work.write(
        resident_record,
        {
            'execution_id': "job_release_after_dev",
            "actor": "CASE-A",
            "status": "queued",
            "queue_reason": "starting",
            "queue": [],
        },
    )
    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        lambda target_id: notifications.append(str(target_id)),
    )

    resident_execution_domain.finish_resident_continuation(resident_id)

    assert resident_execution_work.thread(resident_record) is None
    assert notifications == [resident_id]


def test_execution_projection_failure_is_quarantined_without_terminalizing_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行投影失败被隔离记录且不改变执行终态"""
    recorded: list[tuple[str, int]] = []
    advanced: list[int] = []

    class Service:
        @staticmethod
        def wait(_execution_id, **kwargs):
            assert kwargs["after_sequence"] == 11
            kwargs["on_snapshot"]({"status": "running"})
            kwargs["on_events"]([{
                "sequence": 17,
                "type": "provider_event",
                "details": {},
            }])
            return {"status": "completed", "result": {"status": "completed"}}

    monkeypatch.setattr(
        resident_execution_engine_domain,
        "_project_resident_execution_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("Conversation message identity refers to different immutable content")
        ),
    )
    monkeypatch.setattr(
        resident_execution_engine_domain,
        "_record_resident_projection_error",
        lambda *_args, event_sequence=0, **_kwargs: (
            recorded.append((str(_args[1]), event_sequence))
            or {"message": "projection failed", "event_sequence": event_sequence}
        ),
    )
    monkeypatch.setattr(
        resident_execution_engine_domain,
        "_resident_execution_event_cursor",
        lambda *_args: 11,
    )
    monkeypatch.setattr(
        resident_execution_engine_domain,
        "_advance_resident_execution_event_cursor",
        lambda *_args: advanced.append(int(_args[-1])),
    )

    snapshot = resident_execution_engine_domain.wait_for_resident_execution(
        Service(),
        execution_id="execution-000000000000000000000706",
        actor="sample_admin",
        resident_id="resident_000000000706",

    )

    assert snapshot["status"] == "completed"
    assert snapshot["projection_errors"] == [{
        "message": "projection failed",
        "event_sequence": 0,
    }]
    assert recorded == [("execution-000000000000000000000706", 0)]
    assert advanced == [17]


def test_completed_resident_turn_reuses_its_stable_final_message(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 完成轮复用稳定最终消息并按顺序发布终态"""
    repository = resident_conversation_stream.get_repository()
    resident_id = str(resident_record["resident_id"])
    user_message = resident_input_queue_domain.resident_user_message(
        resident_id, "question", "CASE-A", message_id="msg_" + "9" * 32,
    )
    repository.commit_user_message(resident_id, user_message)
    execution_id = "execution-" + "9" * 24
    job = {

        "user_message_id": user_message["message_id"],
        "execution_id": execution_id,
        "status": "running",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    request = ExecutionRequest.new(
        actor="CASE-A", prompt="continue", provider="codex", service_type="agent_session", operation="turn",
        session=SessionOptions(mode="persistent", binding_id="resident:main"),
    )

    def wait_for_result(*args, **kwargs):
        snapshot = {
            "status": "completed",
            "scheduler": {"updated_at": "2026-08-17T00:00:00Z"},
            "result": {
                "status": "completed",
                "backend_session_id": "019f0000-0000-7000-8000-000000000799",
                "output": {"message": "唯一最终回答", "turn_id": "turn_799"},
            },
        }
        # Real wait callbacks and repositories run; only the remote provider is
        # substituted. Repeated terminal notifications must not end delivery.
        kwargs["on_snapshot"](snapshot)
        kwargs["on_snapshot"](snapshot)
        assert repository.current_projection(resident_id)["state"]["work_state"] == "running"
        return snapshot

    monkeypatch.setattr(
        execution_port, "service", lambda: SimpleNamespace(wait=wait_for_result),
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "_build_resident_execution_request",
        lambda *_args: request,
    )
    monkeypatch.setattr(
        resident_execution_engine_domain,
        "durable_resident_execution_request",
        lambda _record, _job, factory: factory(),
    )
    monkeypatch.setattr(
        resident_execution_engine_domain,
        "bind_or_resume_resident_execution",
        lambda *_args: execution_id,
    )
    before_cursor = repository.current_projection(resident_id)["cursor"]
    first_outcome = _deliver_session(
        resident_record,
        job,
    )
    second_outcome = _deliver_session(
        resident_record,
        job,
    )
    first = dict(first_outcome.assistant_message or {})
    second = dict(second_outcome.assistant_message or {})

    assert first["message_id"] == second["message_id"]
    appended = [
        event
        for event in repository.events_after(resident_id, before_cursor)["events"]
        if event["event_type"] == "conversation.message.appended"
        and event["payload"]["message"]["message_id"] == first["message_id"]
    ]
    assert len(appended) == 1
    stored = repository.read_messages(resident_id)
    assert [item["message_id"] for item in stored].count(first["message_id"]) == 1

    resident_execution_domain._mark_completion_applied(job, resident_domain_common.now_iso())
    resident_execution_work.write(resident_record, job)
    events = repository.events_after(resident_id, before_cursor)["events"]
    completed = [
        event for event in events
        if event["event_type"] == "resident.input.state.changed"
        and event["payload"]["input_state"]["state"] == "completed"
    ]
    idle = [
        event for event in events
        if event["event_type"] == "resident.state.changed"
        and event["payload"]["state"]["work_state"] == "idle"
    ]
    assert len(completed) == len(idle) == 1
    assert completed[0]["payload"]["input_state"]["reply_message_ids"] == [first["message_id"]]
    assert appended[0]["cursor"] < completed[0]["cursor"] < idle[0]["cursor"]


def test_worker_completion_notifies_exact_resident_identity(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工作完成清空任务后通知准确的 Resident 身份"""
    resident_id = str(resident_record["resident_id"])
    continuation_id = "resident-continuation:test-main-identity"
    user_message_id = "msg_dddddddddddddddddddddddddddddddd"
    job = {
        'execution_id': "job_main_identity",
        "user_message_id": user_message_id,
        "actor": "CASE-A",
        "status": "running",
        "continuation_id": continuation_id,
        "queue": [],
    }
    resident_execution_work.write(resident_record, job)
    resident_lifecycle_domain.append_conversation_message(resident_id, {
        "message_id": user_message_id,
        "role": "user",
        "author_id": "CASE-A",
        "content_parts": [{"type": "text", "text": "完成主会话"}],
        "created_at": "2026-08-12T13:55:59Z",
    })
    completion_resident_ids: list[str] = []

    def record_completion(completed_resident_id, *_args, **_kwargs):
        assert resident_execution_work.read(resident_record) == {}
        completion_resident_ids.append(str(completed_resident_id))

    def send_resident(record, _job):
        assistant = {
            "message_id": "msg_0123456789abcdef",
            "role": "agent",
            "content_parts": [{"type": "text", "text": "完成"}],
            "created_at": "2026-08-12T13:56:00Z",
        }
        resident_lifecycle_domain.append_conversation_message(resident_id, assistant)
        return assistant, {**record, "last_error": ""}

    monkeypatch.setattr(resident_execution_domain, "send_to_resident_backend", send_resident)
    monkeypatch.setattr(
        resident_execution_domain,
        "_notify_lineage_message_completed",
        record_completion,
    )

    resident_execution_domain.run_resident_execution(resident_id, continuation_id)

    assert completion_resident_ids == [resident_id]
    assert resident_execution_work.read(resident_record) == {}
    messages = resident_conversation_stream.get_repository().read_messages(
        resident_id
    )
    assert [item["role"] for item in messages] == ["user", "agent"]
    assert _message_text(messages[-1]) == "完成"


def test_resident_send_publishes_nothing_before_turn_persistence(
    resident_record: dict[str, object],
) -> None:
    """Resident 轮次持久化失败时不发布消息且可原键重试"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_0123456789abcdef0123456789abcdef"
    with (
        mock.patch.object(
            resident_session_backend_domain,
            "handle_resident_native_command",
            return_value={},
        ),
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_intake_domain, "resolve_context_refs", return_value={}),
        mock.patch.object(
            resident_execution_work,
            "write",
            side_effect=RuntimeError("process interrupted"),
        ),
        mock.patch.object(
            resident_intake_domain,
            "last_user_message_text",
            side_effect=AssertionError("ordinary messages must not read history"),
        ),
    ):
        with pytest.raises(RuntimeError, match="process interrupted"):
            _published_delivery(backend.deliver_resident_message_content(
                resident_id,
                "CASE-A",
                "survive restart",
                message_id=message_id,
            ))

    messages = resident_conversation_stream.get_repository().read_messages(
        resident_id
    )
    assert messages == []

    recovered = resident_execution_work.read(resident_record)
    assert recovered == {}

    with (
        mock.patch.object(
            resident_session_backend_domain,
            "handle_resident_native_command",
            return_value={},
        ),
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_intake_domain, "resolve_context_refs", return_value={}),
        mock.patch.object(
            resident_intake_domain,
            "last_user_message_text",
            side_effect=AssertionError("ordinary messages must not read history"),
        ),
    ):
        retried = _published_delivery(backend.deliver_resident_message_content(
            resident_id,
            "CASE-A",
            "survive restart",
            message_id=message_id,
        ))

    assert retried["message"]["message_id"] == message_id
    assert retried["input_state"]["state"] == "running"
    assert retried["state"]["work_phase"] == "starting_session"
    assert resident_execution_work.read(resident_record)["user_message_id"] == message_id


def test_similar_failed_message_accepts_a_new_retry_job_generation(
    resident_record: dict[str, object],
) -> None:
    """失败后收到相同内容时创建新执行并清除旧轮状态"""
    failed = {

        "user_message_id": "message_failed_previous_generation",
        "raw_message": "repeat this exact role prompt",
        "effective_message": "repeat this exact role prompt",
        "message": "repeat this exact role prompt",
        "actor": "CASE-A",
        "outcome": "failed",
        "error": "previous attempt failed",
        "finished_at": "2026-07-29T12:00:00Z",
        "updated_at": "2026-07-29T12:00:00Z",
        "execution_id": "execution-previous-attempt",
        'execution_id': "execution-previous-attempt",
        "execution_request": {"execution_id": "execution-previous-attempt"},
        "failure_message_id": "runtime-error-job_failed_previous_generation",
        "failure_message_persisted_at": "2026-07-29T12:00:00Z",
        "continuation_id": "resident-continuation:previous-attempt",
        "continuation_started_at": "2026-07-29T11:59:00Z",
        "completion_applied_at": "2026-07-29T12:00:00Z",
        "completion_applied_execution_id": "execution-previous-attempt",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, failed)
    submit = mock.Mock()

    with (
        mock.patch.object(
            resident_session_backend_domain,
            "handle_resident_native_command",
            return_value={},
        ),
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_intake_domain, "resolve_context_refs", return_value={}),
    ):
        result = _published_delivery(backend.deliver_resident_message_content(
            "resident_aaaaaaaaaaaa",
            "CASE-A",
            "repeat this exact role prompt",
        ))

    message_id = str(resident_execution_work.read(resident_record)['execution_id'])
    assert result["resident_id"] == "resident_aaaaaaaaaaaa"
    assert result["message"]["message_id"] == result["message_id"]
    assert result["input_state"]["state"] == "running"
    assert result["state"]["work_phase"] == "starting_session"
    assert message_id.startswith("execution-")
    assert message_id != failed['execution_id']
    stored = resident_execution_work.read(resident_record)
    assert stored['execution_id'] == message_id
    assert stored["execution_id"] == message_id
    assert not stored.get("submitted_at")
    assert stored['execution_id'] != "execution-previous-attempt"
    assert stored.get("execution_request", {}).get("execution_id") != (
        "execution-previous-attempt"
    )
    assert "failure_message_id" not in stored
    assert "failure_message_persisted_at" not in stored
    assert "continuation_id" not in stored
    assert "continuation_started_at" not in stored
    assert "completion_applied_at" not in stored
    assert "completion_applied_execution_id" not in stored
    submit.assert_not_called()
    submitted = stored
    assert submitted['execution_id'] == message_id
    assert stored["raw_message"] == "repeat this exact role prompt"


def test_job_projection_and_binding_run_after_the_resident_lock(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任务投影和绑定通知在 Resident 状态锁释放后执行"""
    from management_portal.society_service import invariant_locks

    resident_id = str(resident_record["resident_id"])
    observed: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        resident_execution_work,
        "publish_conversation_state",
        lambda *_args, **_kwargs: observed.append(
            ("projection", invariant_locks.lock_held())
        ),
    )
    monkeypatch.setattr(resident_execution_work, "notify_changed", lambda *_args: None)
    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        lambda _resident_id: observed.append(
            ("binding", invariant_locks.lock_held())
        ),
    )

    with resident_record_domain.state_lock(resident_id):
        resident_execution_work.write(resident_record, {

            "execution_id": "execution-" + "a" * 24,
            "actor": "CASE-A",
         "submitted_at": "2026-10-14T00:00:00Z"})
        assert observed == []

    assert observed == [("projection", False), ("binding", False)]


def test_execution_message_append_runs_outside_the_execution_work_lock(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行消息追加在执行工作锁外进行并清除投影意图"""
    from management_portal.society_service import invariant_locks

    resident_id = str(resident_record["resident_id"])
    job_id = "job_message_after_unlock"
    execution_id = "execution-" + "b" * 24
    resident_execution_work.write(resident_record, {

        "execution_id": execution_id,
        "actor": "CASE-A",
     "submitted_at": "2026-10-14T00:00:00Z"})
    append = resident_lifecycle_domain.append_conversation_message
    observed: list[bool] = []

    def append_after_unlock(*args, **kwargs):
        observed.append(invariant_locks.lock_held())
        return append(*args, **kwargs)

    monkeypatch.setattr(
        resident_lifecycle_domain,
        "append_conversation_message",
        append_after_unlock,
    )
    persisted = resident_execution_engine_domain.persist_execution_display_messages(
        resident_id,

        execution_id,
        [{
            "output_id": "msg_" + "e" * 32,
            "phase": "commentary",
            "text": "project after the Job commit",
        }],
    )

    assert len(persisted) == 1
    assert observed == [False]
    assert not resident_execution_work.read(resident_record).get(
        resident_execution_work.CONVERSATION_PROJECTION_INTENTS
    )


def test_failed_job_projection_does_not_suppress_change_or_binding(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任务投影失败仍发送变化与绑定通知"""
    resident_id = str(resident_record["resident_id"])
    observed: list[str] = []

    def projection_failure(*_args, **_kwargs):
        observed.append("projection")
        raise RuntimeError("projection unavailable")

    monkeypatch.setattr(
        resident_execution_work,
        "publish_conversation_state",
        projection_failure,
    )
    monkeypatch.setattr(
        resident_execution_work,
        "notify_changed",
        lambda *_args: observed.append("change"),
    )
    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        lambda *_args: observed.append("binding"),
    )

    with pytest.raises(RuntimeError, match="projection unavailable"):
        with resident_record_domain.state_lock(resident_id):
            resident_execution_work.write(resident_record, {

                "execution_id": "execution-" + "c" * 24,
                "actor": "CASE-A",
             "submitted_at": "2026-10-14T00:00:00Z"})

    assert observed == ["projection", "change", "binding"]


def test_general_job_writes_cannot_drop_or_resurrect_projection_intents(
    resident_record: dict[str, object],
) -> None:
    """普通任务写入不能丢弃或复活对话投影意图"""
    resident_id = str(resident_record["resident_id"])
    job_id = "job_projection_intent_owner"
    execution_id = "execution-" + "d" * 24
    resident_execution_work.write(resident_record, {

        "execution_id": execution_id,
        "actor": "CASE-A",
     "submitted_at": "2026-10-14T00:00:00Z"})
    projected_message = resident_input_queue_domain.build_message(
        "agent",
        "authorized projection",
        message_id="msg_" + "f" * 32,
    )
    with resident_record_domain.state_lock(resident_id):
        assert resident_execution_work.authorize_conversation_projection(
            resident_record,

            execution_id=execution_id,
            message=projected_message,
        )
        stale_without_intent = {

            "execution_id": execution_id,
            "actor": "CASE-A",
         "submitted_at": "2026-10-14T00:00:00Z"}
        resident_execution_work.write(resident_record, stale_without_intent)
    authorized = resident_execution_work.read(resident_record)
    assert projected_message["message_id"] in authorized[
        resident_execution_work.CONVERSATION_PROJECTION_INTENTS
    ]
    stale_with_intent = dict(authorized)
    with resident_record_domain.state_lock(resident_id):
        resident_execution_work.complete_conversation_projection(
            resident_record,

            execution_id=execution_id,
            message_id=projected_message["message_id"],
        )
        resident_execution_work.write(resident_record, stale_with_intent)
    assert not resident_execution_work.read(resident_record).get(
        resident_execution_work.CONVERSATION_PROJECTION_INTENTS
    )
