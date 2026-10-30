"""Recovery of a Resident's work: delivery retries, restart recovery, waiters and resubmission after an
interrupted submission.

:module: resident.identity
:importance: P2
"""
from __future__ import annotations
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service.domain_ports import execution_port

import threading
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.society_service import (
    resident_hatg_credentials,
    resident_execution_domain,
    resident_intake_domain,
    resident_execution_work,
    resident_lifecycle_domain,
    resident_lineage,
    resident_record_domain,
    resident_runtime_domain,
    resident_session_backend_domain,
    resident_domain_common,
    resident_input_queue_domain,
    resident_execution_engine_domain,
)
from management_portal.society_service import lifecycle
from management_portal.society_service import resident_domain as backend
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import execution_client
from tests.cases.resident_execution_test_support import _message_text
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401 - fixture


@pytest.mark.parametrize("location", ["send_now", "group", "info"])
def test_delivery_retry_finds_every_durable_input_location(resident_record, location):
    """投递重试识别即时输入、合并输入和暂存信息

    :module: resident.conversation"""
    from management_portal.society_service import resident_held_infos
    rid = resident_record["resident_id"]
    message = {"message_id": "msg_retry_location", "role": "user", "author_id": "CASE-A",
               "content_parts": [{"type": "text", "text": "same input"}], "created_at": "2026-10-11T00:00:00Z"}
    repository = resident_conversation_stream.get_repository()
    repository.accept_user_message(rid, message)
    repository.set_input_state(rid, message["message_id"], "queued")
    item = {"user_message_id": message["message_id"], "input_message": message}
    job = {'execution_id': "job_current", "user_message_id": "msg_current"}
    if location == "info":
        resident_held_infos.hold(resident_record, message)
    elif location == "group":
        job["queue"] = [{"user_message_id": "msg_group", "mailbox_members": [item]}]
    else:
        job["send_now_inputs"] = [item]
    assert resident_intake_domain.existing_resident_delivery(rid, message, bound_job=job, require_execution_binding=True)
    if location != "info":
        with pytest.raises(ValueError, match="duplicate message_id"):
            resident_execution_work.normalized_queue(job, [*(job.get("queue") or []), item])


def test_recovered_request_consumes_the_info_it_already_carries(resident_record, monkeypatch):
    """恢复请求消费已携带的信息且不重新拼装请求"""
    from management_portal.society_service import resident_held_infos
    rid = resident_record["resident_id"]
    message = {"message_id": "msg_held_before_crash", "role": "user", "author_id": "CASE-A",
               "content_parts": [{"type": "text", "text": "background fact"}], "created_at": "2026-10-11T00:00:00Z"}
    repository = resident_conversation_stream.get_repository()
    repository.accept_user_message(rid, message)
    repository.set_input_state(rid, message["message_id"], "queued")
    resident_held_infos.hold(resident_record, message)
    job = {'execution_id': "execution-000000000000000000000000", "actor": "CASE-A", "user_message_id": "msg_request_after_info",
           "actual_sent_prompt": "do work", "message": "do work", "prompt_cwd": resident_record["default_cwd"], "queue": []}
    resident_execution_work.write(resident_record, job)
    consume = resident_held_infos.consume
    monkeypatch.setattr(resident_held_infos, "consume", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("crashed before consume")))
    with pytest.raises(RuntimeError, match="crashed before consume"):
        resident_execution_domain._send_resident_execution(resident_record, job)
    restored = resident_execution_work.read(resident_record)
    assert "background fact" in restored["execution_request"]["prompt"]
    monkeypatch.setattr(resident_held_infos, "attach", lambda *_: pytest.fail("request must be recovered unchanged"))
    def consume_and_stop(*args, **kwargs):
        consume(*args, **kwargs)
        raise RuntimeError("stop before model submission")
    monkeypatch.setattr(resident_held_infos, "consume", consume_and_stop)
    with pytest.raises(RuntimeError, match="stop before model submission"):
        resident_execution_domain._send_resident_execution(resident_record, restored)
    assert resident_held_infos.peek(resident_record) == []
    assert repository.input_state(rid, message["message_id"])["state"] == "completed"


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "completed"])
def test_preparation_terminal_job_publishes_input_before_leaving_active_storage(resident_record, outcome):
    """执行准备结束后发布输入终态

    创建 Execution 前失败、取消或结束，轮次归档后仍提交输入终态，使通知和投递清理使用同一结束事实。

    :module: execution.persistence
    """
    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    repository.set_input_state(resident_id, "msg_prepare_finished", "running")
    resident_execution_work.write(resident_record, {
        'execution_id': "job_prepare_finished", "user_message_id": "msg_prepare_finished",
        "actor": "CASE-A", "outcome": outcome, "error": "preparation failed",
        "finished_at": repository.input_state(resident_id, "msg_prepare_finished")["updated_at"],
    })
    assert not resident_execution_work.read(resident_record).get('execution_id')
    with repository.state_store.database.read() as connection:
        row = connection.execute(
            "SELECT state FROM conversation_input_states WHERE resident_id=%s AND message_id=%s",
            (resident_id, "msg_prepare_finished"),
        ).fetchone()
    assert row["state"] == outcome
    assert repository.input_state(resident_id, "msg_prepare_finished")["state"] == outcome


def test_recovery_never_reopens_a_terminal_execution_work(
    resident_record: dict[str, object],
) -> None:
    """恢复过程不重新打开已终结的执行工作"""
    resident_id = str(resident_record["resident_id"])
    resident_execution_work.write(resident_record, {

        "execution_id": "execution-terminal-projection",
        "actor": "CASE-A",
        "outcome": "failed",
        "execution_terminal_status": "failed",
        "execution_event_cursor": 42,
        "finished_at": "2026-08-25T03:00:00Z",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})

    with (
        mock.patch.object(
            resident_execution_work,
            "execution_snapshot",
            return_value={"status": "failed"},
        ),
        mock.patch.object(
            resident_execution_engine_domain,
            "_project_resident_execution_snapshot",
        ) as project,
    ):
        backend.resume_resident_execution(resident_id)

    project.assert_not_called()
    assert resident_execution_work.read(resident_record)["execution_event_cursor"] == 42


def test_recovery_attaches_only_the_completion_waiter(
    resident_record: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复仅挂接完成等待者且不查询或投影远端执行"""
    resident_id = str(resident_record["resident_id"])
    attached = []
    def forbidden(*args, **kwargs):
        raise AssertionError("Recovery must not query or project remote execution")
    monkeypatch.setattr(resident_execution_work, "execution_snapshot", forbidden)
    monkeypatch.setattr(resident_execution_engine_domain, "_project_resident_execution_snapshot", forbidden)
    # An active Job with no attached waiter is what recovery attaches one to.
    resident_execution_work.write(resident_record, {"execution_id": "execution-" + "a" * 24, "actor": "CASE-A", "queue": []})
    monkeypatch.setattr(resident_execution_domain, "_prepare_resident_continuation", lambda _record, **_kwargs: (resident_id, "waiter"))
    monkeypatch.setattr(resident_execution_domain, "start_resident_continuation", lambda record, key: attached.append((record["resident_id"], key)))
    assert backend.resume_resident_execution(resident_id)["residents"] == 1
    assert attached == [(resident_id, "waiter")]


def _record_harness_execution(
    resident_id: str,
    execution_id: str,
    *,
    accepted: bool = True,
) -> None:
    store = get_agent_data_store()
    store.begin_execution(
        execution_id=execution_id,
        agent_id=resident_id,
        actor_ref="CASE-A",
        provider="codex",
        started_at="2026-09-07T07:00:00Z",
    )
    store.finish_execution(
        execution_id,
        status="failed",
        provider_turn_id=f"turn-{execution_id}" if accepted else "",
    )


@pytest.mark.parametrize('mode', ['auto', 'manual'])
@pytest.mark.parametrize('enabled,first_status,error,input_accepted', [pytest.param(True, 'failed', 'Selected model is at capacity', True, id='capacity'), pytest.param(True, 'failed', 'Codex context compaction did not finish within 300 seconds', True, id='compact-timeout'), pytest.param(True, 'failed', "name 'unknown_symbol' is not defined", True, id='unexpected-error'), pytest.param(True, 'failed', 'provider unavailable', True, id='retry-also-fails'), pytest.param(False, 'failed', 'provider unavailable', True, id='disabled'), pytest.param(True, 'cancelled', 'cancelled by owner', True, id='cancelled'), pytest.param(True, 'completed', '', True, id='success'), pytest.param(True, 'failed', 'Codex app-server turn/start timed out', False, id='start-not-accepted'), pytest.param(True, 'failed', 'ExecutionService unavailable: timed out', False, id='dispatch-not-accepted'), pytest.param(True, 'failed', 'invalid model configuration', False, id='configuration-not-accepted')])
def test_lineage_projects_terminal_execution_without_harness_recovery(resident_record: dict[str, object], monkeypatch: pytest.MonkeyPatch, mode: str, enabled: bool, first_status: str, error: str, input_accepted: bool) -> None:
    """Lineage 投影执行终态且不合成技术重试输入

    Lineage projects final Execution results without synthesizing technical retries."""
    resident_id = str(resident_record['resident_id'])
    repository = resident_conversation_stream.get_repository()
    requests = []
    snapshots = {}
    acknowledgements = []
    wakeups = []
    exit_wakeups = []
    outcomes = iter([first_status])

    class ExecutionBoundary:

        def submit(self, request):
            assert request.execution_id not in snapshots
            requests.append(request)
            snapshots[request.execution_id] = {'execution_id': request.execution_id, 'status': 'queued', 'events': [], 'result': {}}
            return snapshots[request.execution_id]

        def status(self, execution_id, **_kwargs):
            return snapshots[execution_id]

        def wait(self, execution_id, **kwargs):
            assert kwargs['cancellation_requested']() is False
            status = next(outcomes)
            _record_harness_execution(resident_id, execution_id, accepted=input_accepted)
            result = {'status': status, 'error': error if status != 'completed' else '', 'output': {'message': 'isolated work completed'} if status == 'completed' else {}}
            snapshot = {**snapshots[execution_id], 'status': status, 'result': result, 'scheduler': {'status': status, 'updated_at': resident_domain_common.now_iso()}}
            snapshots[execution_id] = snapshot
            kwargs['on_snapshot'](snapshot)
            kwargs['on_snapshot'](snapshot)
            return snapshot

        def acknowledge(self, execution_id, **_kwargs):
            acknowledgements.append(execution_id)
    service = ExecutionBoundary()
    monkeypatch.setattr(execution_port, 'service', lambda: service)
    monkeypatch.setattr(resident_hatg_credentials, 'ensure_resident_credential', mock.Mock())
    monkeypatch.setattr(resident_execution_domain, '_notify_lineage_message_terminal', mock.Mock())
    monkeypatch.setattr(resident_execution_domain, '_notify_lineage_message_completed', mock.Mock())
    monkeypatch.setattr(lifecycle, 'notify_execution_binding', wakeups.append)
    finish_continuation = resident_execution_domain.finish_resident_continuation

    def observe_finish(target_id):
        before = len(wakeups)
        finish_continuation(target_id)
        exit_wakeups.extend(wakeups[before:])
    monkeypatch.setattr(resident_execution_domain, 'finish_resident_continuation', observe_finish)
    backend.change_resident_runtime_settings(resident_id, 'CASE-A', {'agent_harness': {'mode': mode, 'features': {'provider_error_continue': {'enabled': enabled}}}})
    saved = resident_runtime_domain.read_resident_runtime_settings(resident_record)['agent_harness']
    assert saved['mode'] == mode
    assert saved['features']['provider_error_continue']['enabled'] is enabled
    message_id = 'msg_123456789abcdef0123456789abcdef0'
    user_item = resident_input_queue_domain.resident_user_message(resident_id, 'isolated original task', 'CASE-A', message_id=message_id)
    repository.commit_user_message(resident_id, user_item)
    job = resident_execution_work.new_item(user_item, 'isolated original task', 'isolated original task', resident_domain_common.now_iso(), prompt_cwd=str(resident_record['default_cwd']))
    job.update({'actor': 'CASE-A', 'queue': []})
    original_job_id = job['execution_id']
    resident_execution_work.write(resident_record, job)
    prepared = resident_execution_domain._prepare_resident_continuation(resident_record)
    assert prepared is not None
    current_harness = resident_runtime_domain.read_resident_runtime_settings(resident_record)['agent_harness']
    assert current_harness['mode'] == mode
    assert current_harness['features']['provider_error_continue']['enabled'] is enabled
    resident_execution_work.track_current_thread(resident_record)
    resident_execution_domain.run_resident_execution(*prepared)
    assert len(requests) == 1
    assert repository.input_state(resident_id, message_id)['state'] == first_status
    assert exit_wakeups == []
    messages = repository.read_messages(resident_id)
    assert not any((_message_text(m) == 'continue' for m in messages)), [(m['role'], _message_text(m)) for m in messages]
    record = resident_record_domain.find_resident(resident_id)
    assert record is not None
    final_job = resident_execution_work.read(record)
    assert not resident_execution_work.is_active(final_job)
    assert not resident_execution_work.normalized_queue(final_job)
    assert resident_execution_domain._prepare_resident_continuation(record) is None
    assert len(exit_wakeups) == 0
    assert set(acknowledgements) == set(snapshots), {'error': record.get('last_error'), 'job_outcome': final_job.get('outcome'), 'events': [(m['role'], _message_text(m)) for m in repository.read_messages(resident_id)]}
    assert all((not s.get('projection_errors') for s in snapshots.values()))
    assert repository.current_projection(resident_id)['state']['work_state'] == 'idle'
    replies = [m for m in repository.read_messages(resident_id) if m['role'] == 'agent']
    if first_status == 'completed':
        assert len(replies) == 1
        assert _message_text(replies[0]) == 'isolated work completed'
        assert not record.get('last_error')
        assert final_job == {}
    else:
        assert replies == []
    for request in requests:
        harness = request.runtime_options['agent_harness']
        assert harness['mode'] == mode
        assert harness['features']['provider_error_continue']['enabled'] is enabled


def test_restart_recovery_isolates_one_broken_resident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重启恢复隔离故障 Resident 并继续恢复健康任务"""
    monkeypatch.setattr(execution_client, "health", lambda: {"scheduler": {"capacity_partitions": {}}})
    broken_id = "resident_000000000091"
    healthy_id = "resident_000000000092"
    records = {
        broken_id: {
            "resident_id": broken_id,
            "registration_state": "ready",
        },
        healthy_id: {
            "resident_id": healthy_id,
            "registration_state": "ready",
        },
    }
    started: list[tuple[str, str]] = []
    monkeypatch.setattr(resident_execution_work, "pending_records", lambda: list(records.values()))
    monkeypatch.setattr(
        resident_lifecycle_domain,
        "resident_aggregate_lineage_id",
        lambda _resident_id: "lineage_000000000090",
    )

    monkeypatch.setattr(
        resident_lineage,
        "all",
        lambda: pytest.fail("Recovery must not enumerate idle Residents"),
    )
    monkeypatch.setattr(
        resident_lineage,
        "snapshot",
        lambda _record: {"residents": []},
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "_active_resident_continuations",
        lambda: 0,
    )

    read_many_calls: list[list[str]] = []

    def read_many(items):
        resident_ids = [str(item.get("resident_id") or "") for item in items]
        read_many_calls.append(resident_ids)
        return {
            resident_id: {
                'execution_id': f"job_{resident_id}",
                "status": "running",
                "started_at": "2026-08-16T00:00:00Z",
            }
            for resident_id in resident_ids
        }

    monkeypatch.setattr(resident_execution_work, "read_many", read_many)
    monkeypatch.setattr(
        resident_execution_work,
        "read",
        lambda *_args, **_kwargs: pytest.fail(
            "global recovery must not read Resident jobs one at a time"
        ),
    )

    def continuation_needed(record, job):
        if str(record.get("resident_id") or "") == broken_id:
            raise KeyError("execution-expired")
        return bool(job)

    monkeypatch.setattr(
        resident_execution_work,
        "continuation_needed",
        continuation_needed,
    )
    monkeypatch.setattr(
        resident_record_domain,
        "find_resident",
        lambda resident_id: records.get(resident_id),
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "_prepare_resident_continuation",
        lambda record, **_kwargs: (
            str(record.get("resident_id") or ""),
            "resident-continuation:healthy",
        ),
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "start_resident_continuation",
        lambda record, continuation_id: started.append((
            str(record.get("resident_id") or ""),
            continuation_id,
        )),
    )

    result = backend.resume_pending_resident_executions()

    assert result == {
        "residents": 1,
        "active": 0,
        "errors": 1,
        "released_executions": 0,
        "steward_reserve_waits": 0,
        "failed_work_recoveries": 0,
    }
    assert read_many_calls == [[broken_id, healthy_id]]
    assert started == [(healthy_id, "resident-continuation:healthy")]


def test_resident_submission_recovers_same_request_after_prebind_crash(
    resident_record: dict[str, object],
) -> None:
    """提交绑定前崩溃后复用同一持久请求恢复执行"""
    service = mock.Mock()
    service.submit.side_effect = lambda request: {
        "execution_id": request.execution_id,
    }
    job = {
        'execution_id': "execution-" + "1" * 24,
        "actor": "CASE-A",
        "message": "hello",
        "actual_sent_prompt": "hello",
        "prompt_cwd": str(resident_record["default_cwd"]),
        "queue": [],
    }
    resident_execution_work.write(resident_record, job)
    first = resident_execution_engine_domain.durable_resident_execution_request(
        resident_record,
        job,
        lambda: resident_execution_domain._build_resident_execution_request(resident_record, job),
    )

    # Scheduler accepted the request, then SocietyService crashed before it
    # could persist the execution binding.
    service.submit(first)
    recovered_job = resident_execution_work.read(resident_record)
    assert recovered_job["execution_id"] == first.execution_id
    assert not recovered_job.get("submitted_at")
    recovered = resident_execution_engine_domain.durable_resident_execution_request(
        resident_record,
        recovered_job,
        mock.Mock(side_effect=PermissionError("Authorization configuration is unavailable")),
    )
    assert recovered.to_payload() == first.to_payload()

    execution_id = resident_execution_engine_domain.bind_or_resume_resident_execution(
        service,
        recovered,
        resident_record,
        recovered_job,
    )

    assert execution_id == first.execution_id
    assert [call.args[0].execution_id for call in service.submit.call_args_list] == [
        first.execution_id,
        first.execution_id,
    ]
    stored = resident_execution_work.read(resident_record)
    assert stored["execution_id"] == execution_id
    assert "execution_layer" not in stored
    with (
        mock.patch.object(execution_port, "service", return_value=service),
        mock.patch.object(resident_execution_domain, "_build_resident_execution_request", side_effect=PermissionError("Authorization configuration is unavailable")) as build_turn,
        mock.patch.object(resident_session_backend_domain, "build_backend_session_operation_request", side_effect=PermissionError("Authorization configuration is unavailable")) as build_operation,
        mock.patch.object(resident_execution_engine_domain, "_flush_pending_active_turn_controls"),
        mock.patch.object(resident_execution_engine_domain, "wait_for_resident_execution", side_effect=RuntimeError("existing execution reached")) as wait,
    ):
        for operation in ("turn", "compact"):
            with pytest.raises(RuntimeError, match="^existing execution reached$"):
                if operation == "turn":
                    resident_execution_domain._send_resident_execution(resident_record, stored)
                else:
                    resident_execution_domain._run_resident_session_operation(resident_record, stored, operation)
        build_turn.assert_not_called()
        build_operation.assert_not_called()
        assert wait.call_count == 2
        assert service.resume.call_count == 2
        assert service.submit.call_count == 2
        assert all(call.kwargs["execution_id"] == first.execution_id for call in wait.call_args_list)


def test_resident_recovery_uses_canonical_message_already_accepted_at_intake(
    resident_record: dict[str, object],
) -> None:
    """Resident 恢复复用接收时已保存的规范消息"""
    resident_id = str(resident_record["resident_id"])
    resident_record["last_error"] = "previous terminal error"
    message = {
        "message_id": "msg_fedcba9876543210fedcba9876543210",
        "role": "user",
        "content_parts": [
            {"type": "text", "text": "recover this exact input"}
        ],
        "author_id": "CASE-A",
        "created_at": "2026-08-24T12:00:00Z",
    }
    job = {
        'execution_id': "job_durable_intake",
        "user_message_id": message["message_id"],
        "raw_message": _message_text(message),
        "message": _message_text(message),
        "actual_sent_prompt": _message_text(message),
        "prompt_cwd": str(resident_record["default_cwd"]),
        "actor": "CASE-A",
        "queue": [],
    }
    repository = resident_conversation_stream.get_repository()
    repository.commit_user_message(resident_id, message)
    repository.set_input_state(resident_id, str(message["message_id"]), "queued")
    resident_execution_work.write(resident_record, job)

    prepared = resident_execution_domain._prepare_resident_continuation(resident_record)

    assert prepared is not None
    assert resident_record["last_error"] == ""
    assert [item["message_id"] for item in repository.read_messages(resident_id)] == [
        message["message_id"]
    ]


def test_resident_recovery_preserves_delivered_input_while_finishing_job(
    resident_record: dict[str, object],
) -> None:
    """恢复收尾保留已完成输入状态直至结果处理结束"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    execution_id = "execution-" + "1" * 24
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [{"type": "text", "text": "completed input"}],
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    repository.set_input_state(resident_id, message_id, "completed")
    resident_execution_work.write(
        resident_record,
        {

            "user_message_id": message_id,
            "actor": "CASE-A",
            "execution_id": execution_id,
            "queue": [],
         "submitted_at": "2026-10-14T00:00:00Z"},
        snapshot={"status": "completed"},
    )

    with mock.patch.object(
        resident_execution_work,
        "execution_snapshot",
        return_value={"status": "completed"},
    ):
        prepared = resident_execution_domain._prepare_resident_continuation(resident_record)

    assert prepared is not None
    assert repository.input_state(resident_id, message_id)["state"] == "completed"
    # Input delivery is durable, but the Resident job still needs finalization.
    assert repository.current_projection(resident_id)["state"]["work_state"] == "running"


def test_resident_recovery_clears_stale_error_for_active_continuation(
    resident_record: dict[str, object],
) -> None:
    """活跃续接恢复清除旧错误信息"""
    resident_record["last_error"] = "old provider interruption"
    resident_execution_work.write(
        resident_record,
        {'execution_id': "job_running", "actor": "CASE-A"},
    )

    with mock.patch.object(
        resident_execution_work,
        "continuation_needed",
        return_value=False,
    ):
        prepared = resident_execution_domain._prepare_resident_continuation(resident_record)

    assert prepared is None
    assert resident_record["last_error"] == ""


def test_live_waiter_is_not_reprojected_or_queried_by_recovery(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """存在活跃等待者时恢复不重复查询或投影"""
    resident_id = str(resident_record["resident_id"])
    job = {"actor": "CASE-A",
         "execution_id": "execution-" + "7" * 24,
        **resident_execution_work.continuation_fields(resident_record),
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    monkeypatch.setattr(resident_execution_work, "thread", lambda _record: threading.current_thread())

    def forbidden_work(*args, **kwargs):
        raise AssertionError("A live waiter already owns the execution projection")

    monkeypatch.setattr(resident_execution_work, "publish_conversation_state", forbidden_work)
    monkeypatch.setattr(resident_execution_work, "execution_snapshot", forbidden_work)
    assert resident_input_queue_domain.active_resident_execution(resident_record)['execution_id'] == job['execution_id']
    assert backend.resume_resident_execution(resident_id)["residents"] == 0


def test_preparing_a_waiter_never_queries_execution_service(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """准备续接等待者只使用本地状态"""
    resident_execution_work.write(resident_record, {
         "execution_id": "execution-" + "8" * 24,
        "actor": "CASE-A",
     "submitted_at": "2026-10-14T00:00:00Z"})
    query = mock.Mock(side_effect=AssertionError("Preparation must be local"))
    monkeypatch.setattr(resident_execution_work, "execution_snapshot", query)
    assert resident_execution_domain._prepare_resident_continuation(resident_record) is not None
    query.assert_not_called()


def test_compact_persists_runnable_intent_before_execution_submission(
    resident_record: dict[str, object],
) -> None:
    """压缩操作先持久保存可运行意图再由续接提交执行"""
    execution_id = "execution-" + "7" * 24
    service = mock.Mock()
    service.submit.return_value = {
        "execution_id": execution_id,
        "status": "queued",
        "events": [],
        "result": {},
    }

    with mock.patch(
        "management_portal.execution.service.ExecutionService",
        return_value=service,
    ):
        resident_intake_domain._queue_resident_compaction(
            resident_record, "CASE-A", "msg_00000000000000000000000000000001"
        )

    stored = resident_execution_work.read(resident_record)
    assert stored["execution_id"].startswith("execution-")
    assert not stored.get("submitted_at")
    assert resident_execution_work.execution_id(stored)
    assert resident_execution_work.is_compact(stored)
    assert resident_execution_work.continuation_needed(resident_record, stored)
    service.submit.assert_not_called()


@pytest.mark.parametrize("terminal", [False, True])
def test_recovery_preparation_does_not_hydrate_resources(resident_record, monkeypatch, terminal) -> None:
    """执行恢复准备不在状态锁内读取资源权限

    验证恢复只准备本地 continuation，保留 actor 与 job 绑定校验及终态不重启，避免资源服务延迟阻塞 Resident。

    :importance: P1
    :aspects: functional, performance, architecture-conformance
    :design: decision_edc470e09f5a, decision_1b2bdf5c33d6
    """
    resident_id = str(resident_record["resident_id"])
    job = {
        'execution_id': "job_control_recovery", "user_message_id": "msg_control_recovery",
        "actor": "CASE-A", "message": "continue", "actual_sent_prompt": "continue",
        "prompt_cwd": str(resident_record["default_cwd"]), "queue": [],
    }
    if terminal:
        job.update(outcome="completed", finished_at="2026-09-06T12:00:00Z")
    resident_execution_work.write(resident_record, job)

    with mock.patch.object(resident_execution_domain, "start_resident_continuation") as start:
        result = backend.resume_resident_execution(resident_id)
        assert result["residents"] == (0 if terminal else 1)
        assert start.call_count == (0 if terminal else 1)
        if not terminal:
            assert start.call_args.args[0]["resident_id"] == resident_id
            assert start.call_args.args[1] == resident_execution_work.read(resident_record)["continuation_id"]


def test_recovery_preserves_existing_continuation_identity(
    resident_record: dict[str, object],
) -> None:
    """重复准备恢复保留已有续接身份"""
    job = {
        'execution_id': "job_existing_continuation",
        "user_message_id": "msg_existing_continuation",
        "actor": "CASE-A",
        "message": "continue the same durable job",
        "actual_sent_prompt": "continue the same durable job",
        "prompt_cwd": str(resident_record["default_cwd"]),
        "queue": [],
    }
    resident_execution_work.write(resident_record, job)

    first = resident_execution_domain._prepare_resident_continuation(resident_record)
    second = resident_execution_domain._prepare_resident_continuation(resident_record)

    assert first is not None
    assert second == first


def test_recovery_finishes_durable_terminal_projection_then_releases_turn(resident_record, monkeypatch):
    """恢复先完成持久终态消息投影再释放轮次

    :module: resident.conversation
    :aspects: functional, performance
    :design: decision_edc470e09f5a
    :design-point:
    """
    resident_id = str(resident_record['resident_id'])
    job_id, execution_id = 'job_projection_recovery', 'execution-' + 'a' * 24
    message = resident_input_queue_domain.build_message('agent', 'completed reply', message_id='msg_' + 'a' * 32)
    store = resident_execution_work.get_lineage_data_store()
    store.put_resident_execution_record(resident_id, {
         'execution_id': execution_id, 'actor': 'CASE-A',
        'outcome': 'completed', 'execution_acknowledged': True,
        'conversation_projection_intents': {message['message_id']: {
             'execution_id': execution_id, 'message': message,
         "submitted_at": "2026-10-14T00:00:00Z"}},
     "submitted_at": "2026-10-14T00:00:00Z"})
    observed = []
    monkeypatch.setattr(resident_lifecycle_domain, 'append_conversation_message',
                        lambda rid, value: observed.append((rid, value)))
    resident_execution_domain.resume_pending_resident_executions()
    assert observed == [(resident_id, message)]
    assert not resident_execution_work.read(resident_record)
    resident_execution_domain.resume_pending_resident_executions()
    assert len(observed) == 1
