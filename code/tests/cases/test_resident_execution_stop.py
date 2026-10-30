"""Stopping a Resident's turn: a durable stop intent before, during and after execution binding, and
the confirmed cancellation.

:module: resident.identity
:importance: P2
"""
from __future__ import annotations
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service.domain_ports import execution_port

from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.society_service import (
    resident_execution_domain,
    resident_execution_work,
    resident_protocol,
    resident_record_domain,
    resident_input_queue_domain,
    resident_execution_engine_domain,
)
from management_portal.society_service import resident_domain as backend
from tests.cases.resident_execution_test_support import _activity, _TURN_510K, _ended_execution
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401 - fixture


def test_stop_job_is_accepted_before_execution_binding(
    resident_record: dict[str, object],
) -> None:
    """执行绑定前接受停止请求且阻止后续提交"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_55555555555555555555555555555555"
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [{"type": "text", "text": "stop before binding"}],
            "author_id": "CASE-A",
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    resident_execution_work.write(
        resident_record,
        {
            'execution_id': "execution-" + "2" * 24,
            "user_message_id": message_id,
            "actor": "CASE-A",
            "message": "stop before binding",
            "actual_sent_prompt": "stop before binding",
            "prompt_cwd": str(resident_record["default_cwd"]),
            "queue": [],
        },
    )
    pending = resident_execution_work.read(resident_record)
    pending_request = resident_execution_domain._build_resident_execution_request(
        resident_record,
        pending,
    )

    with (
        mock.patch.object(execution_port, "service") as service,
        mock.patch.object(resident_record_domain, "save_resident_record") as save_resident,
    ):
        result = resident_protocol.cancel_resident_execution(
            resident_id,
            "CASE-A",
            message_id,
        )

    assert result["cancelled"] is False
    assert result["cancellation_requested"] is True
    assert result["backend_cancelled"] is False
    service.assert_not_called()
    save_resident.assert_not_called()
    stored = resident_execution_work.read(resident_record)
    assert stored["stop_requested_by"] == "CASE-A"
    assert stored["stop_requested_at"]
    from management_portal.society_service import resident_delivery_work
    resident_delivery_work.drain(str(resident_record["resident_id"]))
    assert repository.current_projection(resident_id)["state"]["work_state"] == "stopping"
    with pytest.raises(backend.ResidentTurnCancelled):
        resident_execution_engine_domain.durable_resident_execution_request(
            resident_record,
            stored,
            lambda: pending_request,
        )
    with (
        mock.patch.object(resident_execution_domain, "_build_resident_execution_request") as build,
        pytest.raises(backend.ResidentTurnCancelled),
    ):
        resident_execution_domain._send_resident_execution(
            resident_record,
            stored,
        )
    build.assert_not_called()
    prepared = resident_execution_domain._prepare_resident_continuation(resident_record)
    assert prepared is not None
    _, continuation_id = prepared
    with (
        mock.patch.object(resident_execution_domain, "_build_resident_execution_request") as build,
        mock.patch.object(resident_execution_domain, "_notify_lineage_message_terminal"),
    ):
        resident_execution_domain.run_resident_execution(resident_id, continuation_id)
    build.assert_not_called()
    assert resident_execution_work.read(resident_record) == {}
    assert repository.input_state(resident_id, message_id)["state"] == "cancelled"
    assert repository.current_projection(resident_id)["state"]["work_state"] == "idle"


def test_stop_racing_with_execution_submission_cancels_bound_execution(
    resident_record: dict[str, object],
) -> None:
    """停止与执行提交竞态取消最终绑定的执行"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_56565656565656565656565656565656"
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [{"type": "text", "text": "stop during submit"}],
            "author_id": "CASE-A",
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    job = {
        'execution_id': "execution-" + "3" * 24,
        "user_message_id": message_id,
        "actor": "CASE-A",
        "message": "stop during submit",
        "actual_sent_prompt": "stop during submit",
        "prompt_cwd": str(resident_record["default_cwd"]),
        "queue": [],
    }
    resident_execution_work.write(resident_record, job)
    service = mock.Mock()

    def submit(request: object) -> dict[str, object]:
        current = resident_execution_work.read(resident_record)
        current.update({
            'execution_id': "execution-" + "3" * 24,
            "stop_requested_by": "CASE-A",
            "stop_requested_at": "2026-08-28T00:00:01Z",
        })
        resident_execution_work.write(resident_record, current)
        return {"execution_id": str(request.execution_id)}

    def wait(execution_id: str, **kwargs: object) -> dict[str, object]:
        assert kwargs["cancellation_requested"] is None
        return {
            "execution_id": execution_id,
            "status": "cancelled",
            "scheduler": {"cancel_requested": True},
            "result": {"status": "cancelled", "error": "cancelled"},
        }

    service.submit.side_effect = submit
    service.wait.side_effect = wait

    with (
        mock.patch.object(execution_port, "service", return_value=service),
        pytest.raises(backend.ResidentTurnCancelled),
    ):
        resident_execution_domain._send_resident_execution(
            resident_record,
            resident_execution_work.read(resident_record),
        )

    service.submit.assert_called_once()
    stored = resident_execution_work.read(resident_record)
    assert stored['execution_id'] == "execution-" + "3" * 24
    assert stored["execution_id"]
    service.cancel.assert_called_once_with(
        resident_execution_work.submitted_execution_id(stored),
        actor="CASE-A",
        reason="execution cancelled by caller",
    )


def test_stop_after_request_construction_prevents_execution_submission(
    resident_record: dict[str, object],
) -> None:
    """构建请求后收到停止意图时阻止执行提交"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_57575757575757575757575757575757"
    job = {
        'execution_id': "job_stop_after_request_construction",
        "user_message_id": message_id,
        "actor": "CASE-A",
        "message": "stop before submit",
        "actual_sent_prompt": "stop before submit",
        "prompt_cwd": str(resident_record["default_cwd"]),
        "queue": [],
    }
    resident_execution_work.write(resident_record, job)
    stored = resident_execution_work.read(resident_record)
    request = resident_execution_domain._build_resident_execution_request(
        resident_record,
        stored,
    )
    service = mock.Mock()

    def finish_request_construction(*_args: object) -> object:
        current = resident_execution_work.read(resident_record)
        current.update({
            'execution_id': "job_admitted_after_request_construction",
            "stop_requested_by": "CASE-A",
            "stop_requested_at": "2026-08-28T00:00:01Z",
        })
        resident_execution_work.write(resident_record, current)
        return request

    with (
        mock.patch.object(
            resident_execution_engine_domain,
            "durable_resident_execution_request",
            side_effect=finish_request_construction,
        ),
        mock.patch.object(execution_port, "service", return_value=service),
        pytest.raises(backend.ResidentTurnCancelled),
    ):
        resident_execution_domain._send_resident_execution(
            resident_record,
            stored,
        )

    service.submit.assert_not_called()
    service.resume.assert_not_called()
    service.wait.assert_not_called()


def test_stopped_job_cannot_authorize_final_projection(
    resident_record: dict[str, object],
) -> None:
    """已请求停止的任务不能提交最终回答投影"""
    resident_id = str(resident_record["resident_id"])
    job_id = "job_stopped_final_projection"
    execution_id = "execution-" + "7" * 24
    resident_execution_work.write(resident_record, {

        "user_message_id": "msg_58585858585858585858585858585858",
        "execution_id": execution_id,
        "actor": "CASE-A",
        "stop_requested_by": "CASE-A",
        "stop_requested_at": "2026-08-28T00:00:01Z",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})
    final = resident_input_queue_domain.build_message(
        "agent",
        "must not be visible",
        message_id="msg_59595959595959595959595959595959",
    )

    assert resident_execution_domain._persist_execution_final_message(
        resident_id,

        execution_id,
        final,
    ) is None
    assert resident_conversation_stream.get_repository().read_messages(
        resident_id
    ) == []


def test_stopped_bound_execution_waits_for_scheduler_terminal_state(
    resident_record: dict[str, object],
) -> None:
    """停止已绑定执行后等待调度器终态"""
    execution_id = "execution-" + "8" * 24
    job = {

        "user_message_id": "msg_89898989898989898989898989898989",
        "execution_id": execution_id,
        "actor": "CASE-A",
        "message": "wait for physical cancellation",
        "actual_sent_prompt": "wait for physical cancellation",
        "prompt_cwd": str(resident_record["default_cwd"]),
        "stop_requested_by": "CASE-A",
        "stop_requested_at": "2026-08-28T00:00:01Z",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    service = mock.Mock()
    service.wait.return_value = {
        "execution_id": execution_id,
        "status": "cancelled",
        "scheduler": {"cancel_requested": True},
        "result": {"status": "cancelled", "error": "cancelled"},
    }

    with (
        mock.patch.object(execution_port, "service", return_value=service),
        pytest.raises(backend.ResidentTurnCancelled),
    ):
        resident_execution_domain._send_resident_execution(
            resident_record,
            resident_execution_work.read(resident_record),
        )

    service.submit.assert_not_called()
    service.resume.assert_called_once_with(
        execution_id,
        actor="CASE-A",
        correlation_id=job['execution_id'],
        service_type="agent_session",
        provider="codex",
    )
    service.wait.assert_called_once()


def test_stop_job_cancels_its_execution(
    resident_record: dict[str, object],
) -> None:
    """停止任务先持久化意图并保留原请求者及当前活动"""
    message_id = "msg_66666666666666666666666666666666"
    foreign_job = {

        "user_message_id": message_id,
        "status": "running",
        "execution_id": "execution-" + "5" * 24,
        "actor": "sample_admin",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        str(resident_record["resident_id"]),
        {
            "message_id": foreign_job["user_message_id"],
            "role": "user",
            "content_parts": [{"type": "text", "text": "stop this run"}],
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    resident_execution_work.write(resident_record, foreign_job)
    repository.set_activities(
        str(resident_record["resident_id"]),
        [_activity(str(resident_record["resident_id"]))],
    )
    service = mock.Mock()
    service.cancel.return_value = {
        "execution_id": foreign_job["execution_id"],
        "status": "running",
        "scheduler": {"cancel_requested": True},
    }

    with (
        mock.patch(
            "management_portal.execution.service.ExecutionService",
            return_value=service,
        ) as service_factory,
    ):
        result = resident_protocol.cancel_resident_execution(
            "resident_aaaaaaaaaaaa",
            "CASE-A",
            message_id,
        )

    assert result["cancelled"] is False
    assert result["cancellation_requested"] is True
    assert result["backend_cancelled"] is False
    assert "state" not in result and "cursor" not in result
    service_factory.assert_not_called()
    service.cancel.assert_not_called()
    assert resident_execution_work.read(resident_record)["actor"] == "sample_admin"
    from management_portal.society_service import resident_delivery_work
    resident_delivery_work.drain(str(resident_record["resident_id"]))
    projection = repository.current_projection(
        str(resident_record["resident_id"]),
    )
    assert projection["state"]["work_state"] == "stopping"
    assert projection["activities"] == [
        _activity(str(resident_record["resident_id"]))
    ]
    assert repository.input_state(
        str(resident_record["resident_id"]),
        foreign_job["user_message_id"],
    )["state"] == "running"


def test_stop_identity_is_rechecked_when_current_turn_changes(
    resident_record: dict[str, object],
) -> None:
    """停止时重新核对输入身份避免取消已切换的新轮

    :design: decision_c524c775e0be
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    original_message_id = "msg_" + "8" * 32
    next_message_id = "msg_" + "9" * 32
    repository = resident_conversation_stream.get_repository()
    repository.append_message(resident_id, {
        "message_id": original_message_id,
        "role": "user",
        "content_parts": [{"type": "text", "text": "stop only this input"}],
        "created_at": "2026-09-07T00:00:00Z",
    })
    resident_execution_work.write(resident_record, {

        "user_message_id": original_message_id,
        "execution_id": "execution-" + "8" * 24,
        "status": "running",
        "actor": "sample_admin",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}, snapshot={"status": "running"})
    cancel = backend.request_resident_execution_cancellation

    def advance_turn_before_cancellation(*args, **kwargs):
        repository.set_input_state(resident_id, original_message_id, "completed")
        resident_execution_work.write(resident_record, {

            "user_message_id": next_message_id,
            "execution_id": "execution-" + "9" * 24,
            "status": "running",
            "actor": "sample_admin",
            "queue": [],
         "submitted_at": "2026-10-14T00:00:00Z"}, snapshot={"status": "running"})
        return cancel(*args, **kwargs)

    with (
        mock.patch.object(resident_execution_domain, "request_resident_execution_cancellation", side_effect=advance_turn_before_cancellation),
        pytest.raises(ValueError, match="message is not the active Resident input"),
    ):
        resident_protocol.cancel_resident_execution(resident_id, "sample_admin", original_message_id)

    stored = resident_execution_work.read(resident_record)
    assert stored["user_message_id"] == next_message_id
    assert not stored.get("stop_requested_at")
    assert repository.current_projection(resident_id)["state"]["work_state"] == "running"


def test_stop_notifies_exact_execution_after_outer_state_unlock(resident_record):
    """停止通知在外层状态锁释放后发送到准确执行"""
    from management_portal.society_service import invariant_locks, lifecycle

    resident_id = str(resident_record["resident_id"])
    message_id = "msg_99999999999999999999999999999999"
    execution_id = "execution-" + "7" * 24
    resident_execution_work.write(resident_record, {
         "status": "running",
        "user_message_id": message_id, "execution_id": execution_id,
        "actor": "sample_admin", "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})
    notifications = []

    def notified(target_execution_id, actor):
        assert not invariant_locks.lock_held()
        assert resident_execution_work.read(resident_record)["stop_requested_at"]
        notifications.append((target_execution_id, actor))

    lifecycle.set_execution_cancellation_handler(notified)
    try:
        with resident_record_domain.state_lock(resident_id):
            backend.request_resident_execution_cancellation(resident_id, "CASE-A", message_id)
            assert notifications == []
        assert notifications == [(execution_id, "sample_admin")]
    finally:
        lifecycle.set_execution_cancellation_handler(None)


@pytest.mark.parametrize("fail_commit", [False, True])
def test_stop_notification_waits_for_execution_binding_and_successful_commit(resident_record, fail_commit):
    """停止通知等待执行绑定且仅在事务提交成功后发送"""
    from management_portal.society_service import lifecycle

    resident_id = str(resident_record["resident_id"])
    job = {
        'execution_id': "execution-" + "8" * 24,
        "user_message_id": "msg_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "actor": "sample_admin", "queue": [], "stop_requested_at": "2026-09-07T00:00:00Z",
    }
    notifications = []
    lifecycle.set_execution_cancellation_handler(lambda *target: notifications.append(target))
    try:
        resident_execution_work.write(resident_record, job)
        assert notifications == []
        try:
            with resident_record_domain.state_lock(resident_id):
                job["submitted_at"] = "2026-10-14T00:00:00Z"
                resident_execution_work.write(resident_record, job)
                assert notifications == []
                if fail_commit:
                    raise RuntimeError("state transition failed")
        except RuntimeError:
            if not fail_commit:
                raise
        expected = [] if fail_commit else [(job["execution_id"], "sample_admin")]
        assert notifications == expected
    finally:
        lifecycle.set_execution_cancellation_handler(None)


def test_stale_execution_binding_cannot_erase_committed_stop_intent(
    resident_record,
):
    """过期执行绑定写入保留已提交的停止意图"""
    from management_portal.society_service import lifecycle

    resident_id = str(resident_record["resident_id"])
    message_id = "msg_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    execution_id = "execution-" + "9" * 24
    initial = {
        'execution_id': execution_id,
        "user_message_id": message_id,
        "actor": "sample_admin",
        "queue": [],
    }
    resident_execution_work.write(resident_record, initial)
    stale_submission = dict(initial)
    notifications = []
    lifecycle.set_execution_cancellation_handler(
        lambda *target: notifications.append(target)
    )
    try:
        backend.request_resident_execution_cancellation(
            resident_id,
            "CASE-A",
            message_id,
        )
        assert notifications == []

        stale_submission["submitted_at"] = "2026-10-14T00:00:00Z"
        resident_execution_work.write(resident_record, stale_submission)

        stored = resident_execution_work.read(resident_record)
        assert stored["stop_requested_at"]
        assert stored["stop_requested_by"] == "CASE-A"
        assert stored["execution_id"] == execution_id
        assert notifications == [(execution_id, "sample_admin")]
        assert resident_conversation_stream.get_repository().current_projection(
            resident_id
        )["state"]["work_state"] == "stopping"
    finally:
        lifecycle.set_execution_cancellation_handler(None)


def test_execution_admission_replacement_preserves_committed_input_stop(
    resident_record,
):
    """执行准入替换临时身份时保留同一输入的停止意图"""
    from management_portal.society_service import lifecycle

    resident_id = str(resident_record["resident_id"])
    message_id = "msg_cccccccccccccccccccccccccccccccc"
    execution_id = "execution-" + "a" * 24
    provisional = {
        'execution_id': "job_provisional_immediate_stop",
        "user_message_id": message_id,
        "actor": "sample_admin",
        "queue": [],
    }
    resident_execution_work.write(resident_record, provisional)
    notifications = []
    lifecycle.set_execution_cancellation_handler(
        lambda *target: notifications.append(target)
    )
    try:
        backend.request_resident_execution_cancellation(
            resident_id,
            "CASE-A",
            message_id,
        )

        admitted = {
            **provisional,

            "execution_id": execution_id,
         "submitted_at": "2026-10-14T00:00:00Z"}
        resident_execution_work.write(resident_record, admitted)

        stored = resident_execution_work.read(resident_record)
        assert stored['execution_id'] == admitted['execution_id']
        assert stored["user_message_id"] == message_id
        assert stored["stop_requested_at"]
        assert stored["stop_requested_by"] == "CASE-A"
        assert notifications == [(execution_id, "sample_admin")]
        assert resident_conversation_stream.get_repository().current_projection(
            resident_id
        )["state"]["work_state"] == "stopping"
    finally:
        lifecycle.set_execution_cancellation_handler(None)


def test_execution_store_preserves_stop_for_stale_admission_write(
    resident_record,
):
    """执行存储层保留过期准入写入之前已提交的停止标记"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_dddddddddddddddddddddddddddddddd"
    store = resident_execution_work.get_lineage_data_store()
    provisional = {
        'execution_id': "job_storage_provisional",
        "user_message_id": message_id,
        "actor": "sample_admin",
        "queue": [],
    }
    store.put_resident_execution_record(resident_id, provisional)
    store.put_resident_execution_record(resident_id, {
        **provisional,
        "stop_requested_at": "2026-09-28T00:00:00Z",
        "stop_requested_by": "CASE-A",
    })

    revision, committed = store.put_resident_execution_record(
        resident_id,
        {
            **provisional,

            "execution_id": "execution-" + "d" * 24,
         "submitted_at": "2026-10-14T00:00:00Z"},
        return_state=True,
    )

    assert revision >= 3
    assert committed['execution_id'] == 'execution-' + 'd' * 24
    assert committed["stop_requested_at"] == "2026-09-28T00:00:00Z"
    assert committed["stop_requested_by"] == "CASE-A"
    assert all(resident_execution_work.read(resident_record).get(key) == value for key,value in committed.items())


def test_stop_intent_is_durable_without_waiting_for_execution_service(
    resident_record: dict[str, object],
) -> None:
    """停止意图持久保存且无需等待执行服务"""
    message_id = "msg_77777777777777777777777777777777"
    job = {

        "user_message_id": message_id,
        "execution_id": "execution-" + "6" * 24,
        "actor": "sample_admin",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        str(resident_record["resident_id"]),
        {
            "message_id": job["user_message_id"],
            "role": "user",
            "content_parts": [
                {"type": "text", "text": "keep running if cancellation fails"}
            ],
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    resident_execution_work.write(resident_record, job)
    service = mock.Mock()
    service.cancel.side_effect = RuntimeError("control plane unavailable")

    with (
        mock.patch(
            "management_portal.execution.service.ExecutionService",
            return_value=service,
        ),
    ):
        result = resident_protocol.cancel_resident_execution(
            str(resident_record["resident_id"]),
            "sample_admin",
            str(job["user_message_id"]),
        )

    stored = resident_execution_work.read(resident_record)
    assert resident_execution_work.is_active(stored)
    assert stored["stop_requested_at"]
    assert result["cancellation_requested"] is True
    service.cancel.assert_not_called()
    from management_portal.society_service import resident_delivery_work
    resident_delivery_work.drain(str(resident_record["resident_id"]))
    assert repository.current_projection(
        str(resident_record["resident_id"])
    )["state"]["work_state"] == "stopping"


def test_confirmed_cancellation_terminalizes_message_and_clears_job(
    resident_record: dict[str, object],
) -> None:
    """确认取消后终结输入、保存用量并清空任务"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_88888888888888888888888888888888"
    continuation_id = "continuation_cancelled_terminal"
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [
                {"type": "text", "text": "cancel after backend confirmation"}
            ],
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    job = {

        "user_message_id": message_id,
        "execution_id": "execution-" + "7" * 24,
        "actor": "sample_admin",
        "continuation_id": continuation_id,
        "continuation_started_at": "2026-08-28T00:00:01Z",
        "stop_requested_by": "CASE-A",
        "stop_requested_at": "2026-08-28T00:00:02Z",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    _ended_execution(resident_id, job["execution_id"], "cancelled", _TURN_510K)

    with (
        mock.patch.object(
            resident_input_queue_domain,
            "acknowledge_execution",
            return_value=True,
        ),
        mock.patch.object(
            resident_execution_domain,
            "_notify_lineage_message_terminal",
        ) as notify,
    ):
        advanced = resident_execution_domain.apply_cancelled_continuation(
            resident_id,
            continuation_id,
            usage=_TURN_510K,
        )

    assert advanced is False
    assert resident_execution_work.read(resident_record) == {}
    input_state = repository.input_state(resident_id, message_id)
    assert input_state["state"] == "cancelled"
    assert len(input_state["reply_message_ids"]) == 1
    # The stop notice shows what the stopped turn used.
    assert repository.public_message(resident_id, input_state["reply_message_ids"][0])["usage"] == _TURN_510K
    assert resident_record_domain.find_resident(resident_id)["last_turn_usage"] == _TURN_510K
    assert repository.current_projection(resident_id)["state"]["work_state"] == "idle"
    notify.assert_called_once()
