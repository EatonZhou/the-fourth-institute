"""How execution state projects onto the Resident: idle, stopping and activity, Chat phases, execution
snapshots and session projections.

:module: resident.identity
:importance: P2
"""
from __future__ import annotations
from management_portal.execution import event_projection as execution_events
from management_portal.society_service import resident_conversation_stream
from management_portal.execution import resident_runtime_settings

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.society_service import (
    resident_conversation_projection_domain,
    resident_directory_domain,
    resident_execution_work,
    resident_protocol,
    resident_record_domain,
    resident_runtime_domain,
    resident_domain_common,
    resident_execution_engine_domain,
)
from management_portal.society_service import resident_domain as backend
from management_portal.society_service.resident_execution_work import bind_execution
from tests.cases.resident_execution_test_support import _activity
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401 - fixture


def test_running_snapshot_and_cancel_check_read_only_lightweight_job_controls(resident_record, monkeypatch) -> None:
    """运行快照与取消检查只读取轻量任务控制信息

    验证大任务正文存在时，当前任务检查与 running 快照投影均不读取完整执行记录；当前任务检查不额外读取 Resident，快照正确保持运行状态。

    :module: resident.conversation
    :importance: P1
    :aspects: functional, performance
    """
    resident_id = str(resident_record['resident_id'])
    job_id, execution_id = 'lightweight-job', 'execution-' + 'f' * 24
    resident_execution_work.write(resident_record, { 'execution_id': execution_id, 'actor': 'CASE-A',
                                         'user_message_id': 'message-current', 'message': 'opaque' * 200_000, "submitted_at": "2026-10-14T00:00:00Z"})
    repository = resident_conversation_stream.get_repository()
    repository.set_state(resident_id, {'work_state': 'running', 'active_message_id': 'message-current',
                                       'queued_count': 0, 'intake_state': 'open'})
    store = resident_execution_work.get_lineage_data_store()
    monkeypatch.setattr(store, 'resident_execution_record_with_revision',
                        lambda *_args: pytest.fail('runtime observation read the complete execution payload'))
    monkeypatch.setattr(execution_events, 'execution_projection_from_execution',
                        lambda *_args, **_kwargs: {'outputs': [], 'runtime_details': []})
    observed = []
    original = resident_execution_engine_domain.resident_record_for_execution

    def read_record(identifier):
        observed.append(identifier)
        return original(identifier)

    monkeypatch.setattr(resident_execution_engine_domain, 'resident_record_for_execution', read_record)
    assert resident_execution_engine_domain._resident_execution_still_current(resident_id, execution_id)
    assert observed == []
    resident_execution_engine_domain._project_resident_execution_snapshot(resident_id,  execution_id, {'status': 'running'})
    assert observed == [resident_id]
    assert repository.current_projection(resident_id)['state']['work_state'] == 'running'


@pytest.mark.parametrize(
    "scheduler_state",
    ["held", "submitting", "ready", "queued", "claimed", "dispatching", "running"],
)
def test_api_session_state_never_exposes_scheduler_state(
    resident_record: dict[str, object],
    scheduler_state: str,
) -> None:
    """会话状态接口将调度器阶段归一为活跃状态"""
    resident_record["session_state"] = scheduler_state

    assert resident_record_domain.record_backend_session_state(resident_record) == "active"


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_terminal_job_projects_resident_idle(
    resident_record: dict[str, object],
    outcome: str,
) -> None:
    """执行任务终结后将 Resident 投影为空闲"""
    resident_execution_work.publish_conversation_state(
        resident_record,
        {

            "execution_id": "execution-" + "d" * 24,
            "outcome": outcome,
            "finished_at": "2026-08-27T00:00:00Z",
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    state = resident_conversation_stream.get_repository().current_projection(
        str(resident_record["resident_id"])
    )["state"]
    assert state == {
        "work_state": "idle",
        "active_message_id": "",
        "queued_count": 0,
        "intake_state": "open",
    }


def test_terminal_job_clears_resident_activity(
    resident_record: dict[str, object],
) -> None:
    """执行任务终结后清空 Resident 活动"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    repository.set_activities(resident_id, [_activity(resident_id)])

    resident_execution_work.publish_conversation_state(
        resident_record,
        {

            "execution_id": "execution-" + "c" * 24,
            "outcome": "cancelled",
            "finished_at": "2026-08-27T00:00:00Z",
         "submitted_at": "2026-10-14T00:00:00Z"},
    )

    assert repository.current_projection(resident_id)["activities"] == []


def test_execution_cancellation_request_projects_resident_stopping(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行取消请求将 Resident 投影为停止中"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    monkeypatch.setattr(
        repository,
        "current_projection",
        lambda *_args, **_kwargs: pytest.fail(
            "runtime state publication loaded the complete Conversation projection"
        ),
    )

    resident_execution_work.publish_conversation_state(
        resident_record,
        {

            "execution_id": "execution-" + "a" * 24,
            "user_message_id": "message_stopping",
         "submitted_at": "2026-10-14T00:00:00Z"},
        snapshot={
            "status": "running",
            "scheduler": {"cancel_requested": True},
        },
    )

    assert repository.published_state(resident_id) == {
        "work_state": "stopping",
        "active_message_id": "message_stopping",
        "queued_count": 0,
        "intake_state": "open",
    }


def test_next_queued_message_does_not_inherit_previous_turn_activity(
    resident_record: dict[str, object],
) -> None:
    """下一排队消息激活时清空上一轮活动"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    repository.set_state(resident_id, {
        "work_state": "running",
        "active_message_id": "message_previous",
        "queued_count": 1,
        "intake_state": "open",
    })
    repository.set_activities(resident_id, [_activity(resident_id)])

    resident_execution_work.publish_conversation_state(
        resident_record,
        {
            'execution_id': "job_next",
            "user_message_id": "message_next",
            "started_at": "2026-08-27T00:00:01Z",
        },
    )

    projection = repository.current_projection(resident_id)
    assert projection["state"]["work_state"] == "queued"
    assert projection["state"]["active_message_id"] == "message_next"
    assert projection["activities"] == []


@pytest.mark.parametrize(
    ("snapshot", "expected_state", "expected_phase"),
    [
        ({}, "queued", "starting_session"),
        ({"status": "claimed"}, "running", "starting_session"),
        (
            {"status": "queued", "scheduler": {"queue_reason": "capacity"}},
            "queued",
            "waiting_capacity",
        ),
        (
            {
                "status": "queued",
                "scheduler": {"queue_reason": "dispatch_interval"},
            },
            "queued",
            "waiting_dispatch",
        ),
        (
            {"status": "queued", "scheduler": {"queue_reason": "ready"}},
            "queued",
            "starting_session",
        ),
        (
            {"status": "queued", "scheduler": {"queue_reason": "session_binding"}},
            "queued",
            "",
        ),
    ],
)
def test_execution_lifecycle_projects_transient_chat_phase(
    resident_record: dict[str, object],
    snapshot: dict[str, object],
    expected_state: str,
    expected_phase: str,
) -> None:
    """执行生命周期将调度原因映射为聊天等待阶段"""
    job = {
        'execution_id': "job_phase",
        "user_message_id": "message_phase",
    }
    if snapshot:
        job["execution_id"] = "execution-" + "b" * 24
        job["submitted_at"] = "2026-10-14T00:00:00Z"
    state = resident_execution_work.conversation_state(
        job,
        intake_state="open",
        snapshot=snapshot,
    )

    assert state["work_state"] == expected_state
    assert state.get("work_phase", "") == expected_phase


def test_applied_completion_projects_completed_input_and_idle_resident(
    resident_record: dict[str, object],
) -> None:
    """完成结果应用后将输入置为完成并将 Resident 置为空闲"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    message_id = "msg_11111111111111111111111111111111"
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [{"type": "text", "text": "question"}],
            "created_at": "2026-08-27T00:00:00Z",
        },
    )
    execution_id = "execution-" + "e" * 24
    job = {

        "execution_id": execution_id,
        "user_message_id": message_id,
        "completion_applied_at": "2026-08-27T00:00:01Z",
        "completion_applied_execution_id": execution_id,
     "submitted_at": "2026-10-14T00:00:00Z"}

    resident_execution_work.publish_conversation_state(resident_record, job)

    projection = repository.current_projection(resident_id)
    assert projection["state"]["work_state"] == "idle"
    assert repository.input_state(resident_id, message_id)["state"] == "completed"


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
def test_terminal_execution_snapshot_waits_for_resident_delivery(
    resident_record: dict[str, object],
    terminal_status: str,
) -> None:
    """执行快照已终结时仍等待 Resident 完成结果投递"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    message_id = "msg_22222222222222222222222222222222"
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [{"type": "text", "text": "question"}],
            "created_at": "2026-08-27T00:00:00Z",
        },
    )
    job = {

        "execution_id": "execution-" + "f" * 24,
        "user_message_id": message_id,
     "submitted_at": "2026-10-14T00:00:00Z"}

    resident_execution_work.publish_conversation_state(
        resident_record,
        job,
        snapshot={"status": terminal_status},
    )
    cursor = repository.snapshot(resident_id)["cursor"]
    resident_execution_work.publish_conversation_state(
        resident_record,
        job,
        snapshot={"status": terminal_status},
    )

    projection = repository.current_projection(resident_id)
    assert projection["state"]["work_state"] == "running"
    assert repository.input_state(
        resident_id,
        message_id,
    )["state"] == "running"
    assert repository.snapshot(resident_id)["cursor"] == cursor


def test_started_event_projects_only_the_bound_execution_work(
    resident_record: dict[str, object],
) -> None:
    """执行启动快照只投影当前绑定的执行工作"""
    execution_id = "execution-" + "1" * 24
    job = {
        'execution_id': execution_id,
        "actor": "CASE-A",
        "conversation_projection_error": {"message": "old projection error"},
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id=execution_id,


        now="2026-07-27T00:00:00Z",
    )
    resident_execution_work.write(resident_record, job)

    resident_execution_engine_domain._project_resident_execution_snapshot(
        "resident_aaaaaaaaaaaa",

        "execution-stale",
        {"status": "running"},
    )
    stale = resident_execution_work.read(resident_record)
    assert "status" not in stale
    assert "conversation_projection_error" in stale

    resident_execution_engine_domain._project_resident_execution_snapshot(
        "resident_aaaaaaaaaaaa",

        execution_id,
        {"status": "running"},
    )
    stored = resident_execution_work.read(resident_record)
    assert "status" not in stored
    assert "queue_reason" not in stored
    assert "worker_heartbeat_at" not in stored
    assert "conversation_projection_error" not in stored
    assert resident_execution_work.public_execution_state(
        stored,
        snapshot={"status": "running"},
    ) == {"status": "running", "queue_reason": ""}


def test_late_execution_snapshot_cannot_restore_terminal_activity(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """迟到的运行快照不能恢复已终结任务的活动"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "4" * 24
    terminal_job = {

        "actor": "CASE-A",
        "execution_id": execution_id,
        "outcome": "cancelled",
        "finished_at": "2026-08-27T00:00:00Z",
        **resident_execution_work.continuation_fields(resident_record),
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, terminal_job)
    repository = resident_conversation_stream.get_repository()
    repository.set_activities(resident_id, [_activity(resident_id, "stale")])
    monkeypatch.setattr(
        resident_execution_engine_domain,
        "runtime_activity_for_execution",
        lambda _resident_id, _job: {"details": [_activity(resident_id, "late")]},
    )

    resident_execution_engine_domain._project_resident_execution_snapshot(
        resident_id,

        execution_id,
        {"status": "running"},
    )

    assert repository.current_projection(resident_id)["activities"] == []


def test_terminal_execution_snapshot_latches_provider_status_until_delivery(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行终态状态锁存至结果投递且迟到快照不恢复活动"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "5" * 24
    job = {
        'execution_id': execution_id,
        "actor": "CASE-A",
        "user_message_id": "message_terminal_snapshot",
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id=execution_id,


        now="2026-08-30T09:00:00Z",
    )
    resident_execution_work.write(resident_record, job)
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        resident_id,
        {
            "message_id": "message_terminal_snapshot",
            "resident_id": resident_id,
            "role": "user",
            "author_id": "CASE-A",
            "content_parts": [{"type": "text", "text": "Run"}],
            "created_at": "2026-08-30T09:00:00Z",
        },
    )
    monkeypatch.setattr(
        resident_execution_engine_domain,
        "runtime_activity_for_execution",
        lambda _resident_id, _job: {
            "details": [_activity(resident_id, "stale")],
        },
    )

    resident_execution_engine_domain._project_resident_execution_snapshot(
        resident_id,

        execution_id,
        {"status": "completed"},
    )
    resident_execution_engine_domain._project_resident_execution_snapshot(
        resident_id,

        execution_id,
        {"status": "running"},
    )

    projection = repository.current_projection(resident_id)
    assert projection["state"]["work_state"] == "running"
    assert projection["activities"] == []
    assert repository.input_state(
        resident_id,
        "message_terminal_snapshot",
    )["state"] == "running"
    assert resident_execution_work.read(resident_record)["execution_terminal_status"] == "completed"


@pytest.mark.parametrize("transition", ["completed", "next_job"])
def test_snapshot_cannot_overwrite_a_concurrent_resident_transition(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    """执行快照不能覆盖并发完成或新任务切换"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "6" * 24
    job = {"actor": "CASE-A", "execution_id": execution_id, "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    repository = resident_conversation_stream.get_repository()
    expected = {}

    def concurrent_transition(*args, **kwargs):
        replacement = (
            {**job, "outcome": "completed", "finished_at": resident_domain_common.now_iso()}
            if transition == "completed" else {'actor': 'CASE-A', 'execution_id': "job_next"}
        )
        resident_execution_work.write(resident_record, replacement)
        expected.update(repository.current_projection(resident_id))
        return {"outputs": [], "runtime_details": []}

    monkeypatch.setattr(
        execution_events,
        "execution_projection_from_execution",
        concurrent_transition,
    )
    resident_execution_engine_domain._project_resident_execution_snapshot(
        resident_id,  execution_id, {"status": "running"},
    )
    assert repository.current_projection(resident_id) == expected


def test_conversation_snapshot_reads_canonical_running_state(
    resident_record: dict[str, object],
) -> None:
    """对话快照读取规范运行状态和活跃消息标识"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    repository.set_state(resident_id, {
        "work_state": "running",
        "active_message_id": "message_running",
        "queued_count": 0,
        "intake_state": "open",
    })
    snapshot = resident_protocol.get_resident_conversation_snapshot(
        resident_id,
        "CASE-A",
    )
    assert snapshot["resident"]["resident_id"] == resident_id
    assert "resident_id" not in snapshot
    assert snapshot["state"]["work_state"] == "running"
    assert snapshot["state"]["active_message_id"] == "message_running"


def test_conversation_snapshot_defaults_to_idle_without_runtime_projection(
    resident_record: dict[str, object],
) -> None:
    """缺少运行投影时对话快照默认空闲且队列为空"""
    snapshot = resident_protocol.get_resident_conversation_snapshot(
        str(resident_record["resident_id"]), "CASE-A"
    )
    assert snapshot["state"]["work_state"] == "idle"
    assert snapshot["activities"] == []
    assert snapshot["queue"] == {
        "resident_id": str(resident_record["resident_id"]),
        "items": [],
    }


def test_conversation_snapshot_reads_nonempty_canonical_activity_and_queue(
    resident_record: dict[str, object],
) -> None:
    """对话快照读取已有规范活动和队列内容"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    activity = _activity(resident_id, "command")
    queue = {
        "resident_id": resident_id,
        "items": [
            {
                "message_id": "message_queued",
                "author_id": "sample_admin",
                "content_preview": "queued follow-up",
                "enqueued_at": "2026-08-29T00:00:00Z",
            }
        ],
    }
    repository.set_activities(resident_id, [activity])
    # The queue is read from the Mailbox: nothing is set on the Conversation.
    repository.state_store.put_resident_execution_record(resident_id, {"queue": [{
        "user_message_id": "message_queued",
        "enqueued_at": "2026-08-29T00:00:00Z",
        "input_message": {
            "message_id": "message_queued", "role": "user", "author_id": "sample_admin",
            "created_at": "2026-08-29T00:00:00Z",
            "content_parts": [{"type": "text", "text": "queued follow-up"}],
        },
    }]})
    queue["items"][0]["content_parts"] = [{"type": "text", "text": "queued follow-up"}]

    snapshot = resident_protocol.get_resident_conversation_snapshot(
        resident_id,
        "CASE-A",
    )

    assert snapshot["activities"] == [activity]
    assert snapshot["queue"] == queue


def test_conversation_snapshot_includes_persisted_runtime_settings(
    resident_record: dict[str, object],
) -> None:
    """对话快照包含持久化运行设置"""
    resident_runtime_domain.write_resident_runtime_settings(
        resident_record,
        {
            "model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
        },
    )

    snapshot = resident_protocol.get_resident_conversation_snapshot(
        str(resident_record["resident_id"]),
        "CASE-A",
    )

    assert snapshot["runtime_settings"]["reasoning_effort"] == "xhigh"


def test_conversation_snapshot_is_a_side_effect_free_read(
    resident_record: dict[str, object],
) -> None:
    """读取对话快照不推进游标或产生副作用"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    resident_runtime_domain.write_resident_runtime_settings(
        resident_record,
        {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
    )
    cursor = repository.current_projection(resident_id)["cursor"]

    snapshot = resident_protocol.get_resident_conversation_snapshot(
        resident_id,
        "CASE-A",
    )

    assert snapshot["cursor"] == cursor
    assert repository.current_projection(resident_id)["cursor"] == cursor


def test_resident_session_projection_skips_lineage_lock_and_family_scan(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 会话投影跳过 Lineage 锁与 Family 对话扫描"""
    monkeypatch.setattr(
        resident_record_domain,
        "state_lock",
        lambda *_args, **_kwargs: pytest.fail("projection entered lineage lock"),
    )
    monkeypatch.setattr(
        resident_conversation_projection_domain,
        "conversation_family_summaries",
        lambda *_args, **_kwargs: pytest.fail("projection scanned family messages"),
    )
    monkeypatch.setattr(
        resident_conversation_projection_domain,
        "resident_conversation_messages",
        lambda *_args, **_kwargs: pytest.fail("projection read the conversation"),
    )

    detail = backend.get_resident_session_projection(
        str(resident_record["resident_id"]),
        "CASE-A",
    )

    assert detail["schema"] == "tfi.resident-session-projection.v1"
    assert detail["resident_id"] == resident_record["resident_id"]
    assert "family_residents" not in detail
    assert "messages" not in detail
    assert detail["provider_session"] == {
        "provider": "codex",
        "id": "019f0000-0000-7000-8000-000000000001",
    }
    assert "chat_workspace" not in detail
    assert detail["account_selection"]["source_account_id"] == "codex_zero"
    assert "codex_account_selection" not in detail
    assert detail["agent_harness_defaults"]["native_tool_loop_governor"] == (
        resident_runtime_settings.normalize_settings({})["agent_harness"]["features"]["native_tool_loop_governor"]
    )


def test_lineage_node_metadata_does_not_build_chat_or_workspace_projection(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lineage 节点元数据读取不构建会话或 Workspace 投影"""
    monkeypatch.setattr(
        resident_directory_domain,
        "_resident_session_projection_from_record",
        lambda *_args, **_kwargs: pytest.fail("node metadata built session projection"),
    )
    monkeypatch.setattr(
        resident_runtime_domain,
        "read_resident_runtime_settings",
        lambda *_args, **_kwargs: pytest.fail("node metadata read runtime settings"),
    )

    detail = backend.get_lineage_node_metadata(
        str(resident_record["resident_id"]),
        "CASE-A",
    )

    assert detail["schema"] == "tfi.lineage-node-metadata.v1"
    assert set(detail) == {
        "schema",
        "revision",
        "resident_id",
        "resident",
        "account_selection",
    }


def test_resident_session_projection_excludes_execution_storage_fields(
    resident_record: dict[str, object],
) -> None:
    """Resident 会话投影排除执行存储内部字段"""
    stored = dict(resident_record)
    execution = {
        "execution_id": "execution_internal",

        "runtime_error": {"message": "internal"},
        "actor": "CASE-A",
     "submitted_at": "2026-10-14T00:00:00Z"}
    stored["session_state"] = "running"
    resident_record_domain.save_resident_record(stored)
    resident_execution_work.write(stored, execution)
    assert resident_execution_work.read(stored)["execution_id"] == execution["execution_id"]

    detail = backend.get_resident_session_projection(
        str(resident_record["resident_id"]),
        "CASE-A",
    )

    public = detail["resident"]
    for field in (
        "execution_id",
        'execution_id',
        "runner_id",
        "session_state",
        "runtime_error",
        "backend_binding",
    ):
        assert field not in public
    assert "backend_binding" not in detail
    assert public["resident_id"] == resident_record["resident_id"]
    assert public["backend"] == "codex"
