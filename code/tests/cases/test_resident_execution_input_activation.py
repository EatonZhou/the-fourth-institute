"""How a delivered input becomes the active message: delivery modes, direct injection, intake, Mailbox,
capacity waits and materialization.

:module: resident.identity
:importance: P2
"""
from __future__ import annotations
from tests.cases.resident_delivery_test_support import published_delivery as _published_delivery
from management_portal.society_service import resident_backend
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service.domain_ports import execution_port
from management_portal.execution import slash_commands

from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.society_service import (
    resident_execution_domain,
    resident_execution_provider,
    resident_intake_domain,
    resident_execution_work,
    resident_lifecycle_domain,
    resident_protocol,
    resident_record_domain,
    resident_session_backend_domain,
    resident_domain_common,
    resident_prompt_domain,
    resident_input_queue_domain,
    resident_execution_engine_domain,
)
from management_portal.society_service import resident_domain as backend
from management_portal.society_service import resident_state
from management_portal.society_service.resident_execution_work import bind_execution
from management_portal.execution.service import ExecutionService
from tests.cases.resident_execution_test_support import _message_text
from tests.cases.resident_execution_test_support import (  # noqa: F401 - fixture
    resident_record,
    input_delivery,
)


def test_capacity_queue_snapshot_projects_visible_queue_reason(
    resident_record: dict[str, object],
) -> None:
    """容量不足的执行快照保留可见排队原因"""
    execution_id = "execution-" + "2" * 24
    job = {
        'execution_id': execution_id,
        "actor": "CASE-A",
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

        execution_id,
        {
            "status": "queued",
            "scheduler": {"queue_reason": "capacity"},
        },
    )
    stored = resident_execution_work.read(resident_record)
    queued = resident_execution_work.public_execution_state(
        stored,
        snapshot={
            "status": "queued",
            "scheduler": {"queue_reason": "capacity"},
        },
    )
    assert queued["status"] == "queued"
    assert queued["queue_reason"] == "capacity"
    assert "status" not in stored
    assert "queue_reason" not in stored

    resident_execution_engine_domain._project_resident_execution_snapshot(
        "resident_aaaaaaaaaaaa",

        execution_id,
        {
            "status": "claimed",
            "scheduler": {"queue_reason": ""},
        },
    )
    claimed = resident_execution_work.public_execution_state(
        stored,
        snapshot={"status": "claimed"},
    )
    assert claimed["status"] == "claimed"
    assert claimed["queue_reason"] == ""


def test_session_binding_queue_snapshot_remains_visibly_queued(
    resident_record: dict[str, object],
) -> None:
    """等待会话绑定的执行快照保持可见排队状态"""
    execution_id = "execution-" + "3" * 24
    job = {
        'execution_id': execution_id,
        "actor": "CASE-A",
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id=execution_id,


        now="2026-08-10T07:48:33Z",
    )
    resident_execution_work.write(resident_record, job)

    resident_execution_engine_domain._project_resident_execution_snapshot(
        "resident_aaaaaaaaaaaa",

        execution_id,
        {
            "status": "queued",
            "scheduler": {"queue_reason": "session_binding"},
        },
    )

    stored = resident_execution_work.read(resident_record)
    queued = resident_execution_work.public_execution_state(
        stored,
        snapshot={
            "status": "queued",
            "scheduler": {"queue_reason": "session_binding"},
        },
    )
    assert queued["status"] == "queued"
    assert queued["queue_reason"] == "session_binding"
    assert "status" not in stored
    assert "queue_reason" not in stored


def test_queue_promotion_keeps_local_continuation(
    resident_record: dict[str, object],
) -> None:
    """队列提升保留本地续接身份"""
    job = {
        'execution_id': "job_dev",
        "continuation_id": "resident-continuation:dev",
        "continuation_started_at": "2026-07-27T00:00:00Z",
        "queue": [],
    }
    next_job = {
        'execution_id': "job_next_dev",
        "user_message_id": "message_next_dev",
        "message": "continue in dev",
        "actor": "CASE-A",
    }

    resident_execution_work.promote_queue_item(
        job,
        next_job,
        [],
        "2026-07-27T00:01:00Z",
        retain_continuation=True,
    )

    assert "status" not in job
    assert "queue_reason" not in job
    assert job["continuation_id"] == "resident-continuation:dev"


def test_message_queued_behind_active_job_stays_in_mailbox(
    resident_record: dict[str, object],
) -> None:
    """活跃任务后的排队消息保留在 Mailbox 且暂不进入聊天"""
    foreign_job = {

        "user_message_id": "message_dev",
        "status": "running",
        "execution_id": "execution-" + "4" * 24,
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, foreign_job)
    with (
        mock.patch.object(
            resident_execution_work,
            "execution_snapshot",
            return_value={
                "execution_id": foreign_job["execution_id"],
                "status": "running",
            },
        ),
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
            "queued in release",
        ))

    assert result["resident_id"] == "resident_aaaaaaaaaaaa"
    assert result["input_state"]["state"] == "queued"
    stored = resident_execution_work.read(resident_record)
    assert stored['execution_id'] == 'execution-' + '4' * 24
    assert len(stored["queue"]) == 1
    queued = stored["queue"][0]
    assert "execution_id" not in queued
    assert queued["input_message"]["content_parts"][0]["text"] == "queued in release"
    repository = resident_conversation_stream.get_repository()
    assert repository.read_messages("resident_aaaaaaaaaaaa") == []
    accepted = repository.message(
        "resident_aaaaaaaaaaaa",
        queued["input_message"]["message_id"],
    )
    assert accepted is not None
    assert _message_text(accepted) == "queued in release"
    assert "activated_at" not in accepted
    assert repository.current_projection("resident_aaaaaaaaaaaa")["queue"][
        "items"
    ][0]["content_preview"] == "queued in release"


def test_starting_session_materializes_message_at_the_execution_tail(
    resident_record: dict[str, object],
) -> None:
    """会话启动时在执行尾部激活消息并保留原创建时间"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    prior_reply = resident_input_queue_domain.build_message(
        "agent",
        "previous turn completed",
        message_id="msg_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        created_at="2026-08-31T13:49:00Z",
    )
    repository.append_message(resident_id, prior_reply)
    queued_message = resident_input_queue_domain.resident_user_message(
        resident_id,
        "queued while the previous turn was running",
        "CASE-A",
        message_id="msg_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    queued_message["created_at"] = "2026-08-31T13:46:00Z"
    resident_lifecycle_domain.mark_resident_input_queued(resident_id, queued_message)

    assert [
        item["message_id"] for item in repository.read_messages(resident_id)
    ] == [prior_reply["message_id"]]
    before_cursor = repository.current_projection(resident_id)["cursor"]

    resident_execution_work.publish_conversation_state(
        resident_record,
        {
            'execution_id': "job_starting_session",
            "user_message_id": queued_message["message_id"],
            "input_message": queued_message,
            "started_at": "2026-08-31T13:50:00Z",
        },
    )

    projection = repository.current_projection(resident_id)
    assert projection["state"]["work_phase"] == "starting_session"
    assert [
        item["message_id"] for item in repository.read_messages(resident_id)
    ] == [prior_reply["message_id"], queued_message["message_id"]]
    dispatched = repository.message(resident_id, queued_message["message_id"])
    assert dispatched is not None
    assert dispatched["created_at"] == "2026-08-31T13:46:00Z"
    assert dispatched["activated_at"] == "2026-08-31T13:50:00Z"
    assert repository.input_state(
        resident_id,
        queued_message["message_id"],
    )["state"] == "running"
    event_types = [
        event["event_type"]
        for event in repository.events_after(resident_id, before_cursor)["events"]
    ]
    assert event_types[0] == "conversation.message.appended"
    assert event_types.index("conversation.message.appended") < event_types.index(
        "resident.state.changed"
    )


@pytest.mark.parametrize("initial_snapshot", [None, {"status": "running"}])
@pytest.mark.parametrize("transition", ["metadata", "running", "stopping"])
def test_observed_active_input_is_not_rematerialized_for_state_updates(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    initial_snapshot: dict[str, object] | None,
    transition: str,
) -> None:
    """活跃输入状态更新不重复物化已观察的消息"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    message = resident_input_queue_domain.resident_user_message(
        resident_id, "one accepted input", "CASE-A",
        message_id="msg_abababababababababababababababab",
    )
    job = {

        "user_message_id": message["message_id"],
        "input_message": message,
        "execution_id": "execution-" + "a" * 24,
        "started_at": "2026-09-08T15:00:00Z",
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.publish_conversation_state(
        resident_record, job, snapshot=initial_snapshot,
    )
    original = repository.message(resident_id, message["message_id"])
    assert original is not None

    def forbidden(*_args, **_kwargs):
        raise AssertionError("State-only updates must not rematerialize an active input")

    monkeypatch.setattr(resident_execution_work, "materialize_conversation_input", forbidden)
    snapshot = {"status": "running"} if transition == "running" else None
    if transition == "stopping":
        job["stop_requested_at"] = "2026-09-08T15:00:01Z"
    resident_execution_work.publish_conversation_state(resident_record, job, snapshot=snapshot)

    assert repository.message(resident_id, message["message_id"]) == original
    assert len(repository.read_messages(resident_id)) == 1
    state = repository.current_projection(resident_id)["state"]
    assert state["active_message_id"] == message["message_id"]
    if transition == "stopping":
        assert state["work_state"] == "stopping"


def test_input_activation_failure_does_not_advance_public_checkpoint(
    resident_record: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """输入激活失败时不推进公开状态且重试可完成"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    message = resident_input_queue_domain.resident_user_message(
        resident_id, "retry activation", "CASE-A",
        message_id="msg_acacacacacacacacacacacacacacacac",
    )
    job = {
        'execution_id': "job_activation_retry", "user_message_id": message["message_id"],
        "input_message": message, "started_at": "2026-09-08T15:00:00Z",
    }
    before = repository.current_projection(resident_id)["state"]
    with monkeypatch.context() as patch:
        def fail(*_args, **_kwargs):
            raise RuntimeError("input commit failed")
        patch.setattr(resident_execution_work, "materialize_conversation_input", fail)
        with pytest.raises(RuntimeError, match="input commit failed"):
            resident_execution_work.publish_conversation_state(resident_record, job)
    assert repository.current_projection(resident_id)["state"] == before
    resident_execution_work.publish_conversation_state(resident_record, job)
    assert repository.message(resident_id, message["message_id"]) is not None
    assert repository.current_projection(resident_id)["state"]["active_message_id"] == message["message_id"]


def test_new_active_input_has_its_own_materialization_checkpoint(
    resident_record: dict[str, object],
) -> None:
    """新活跃输入各自拥有独立的物化检查点"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    expected_ids = []
    for marker in ("d", "e"):
        message = resident_input_queue_domain.resident_user_message(
            resident_id, "next input " + marker, "CASE-A", message_id="msg_" + marker * 32,
        )
        job = {
            'execution_id': "job_new_input_" + marker,
            "user_message_id": message["message_id"], "input_message": message,
            "started_at": "2026-09-08T15:00:00Z",
        }
        resident_execution_work.publish_conversation_state(resident_record, job)
        expected_ids.append(message["message_id"])
    assert [message["message_id"] for message in repository.read_messages(resident_id)] == expected_ids
    assert repository.current_projection(resident_id)["state"]["active_message_id"] == expected_ids[-1]


def test_waiting_capacity_keeps_message_out_of_chat_until_starting_session(
    resident_record: dict[str, object],
) -> None:
    """等待容量的消息在会话启动前不进入聊天记录"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    queued_message = resident_input_queue_domain.resident_user_message(
        resident_id,
        "wait for scheduler capacity",
        "CASE-A",
        message_id="msg_cccccccccccccccccccccccccccccccc",
    )
    job = {

        "user_message_id": queued_message["message_id"],
        "input_message": queued_message,
        "execution_id": "execution-" + "7" * 24,
        "started_at": "2026-08-31T14:00:00Z",
     "submitted_at": "2026-10-14T00:00:00Z"}

    resident_execution_work.publish_conversation_state(
        resident_record,
        job,
        snapshot={
            "status": "queued",
            "scheduler": {"queue_reason": "capacity"},
        },
    )

    assert repository.read_messages(resident_id) == []
    assert repository.current_projection(resident_id)["state"][
        "work_phase"
    ] == "waiting_capacity"

    resident_execution_work.publish_conversation_state(
        resident_record,
        job,
        snapshot={
            "status": "queued",
            "scheduler": {"queue_reason": "ready"},
        },
    )

    assert [
        item["message_id"] for item in repository.read_messages(resident_id)
    ] == [queued_message["message_id"]]
    assert repository.current_projection(resident_id)["state"][
        "work_phase"
    ] == "starting_session"


def test_bound_execution_without_snapshot_does_not_invent_running(
    resident_record: dict[str, object],
) -> None:
    """已绑定执行缺少快照时保持排队及会话启动阶段"""
    repository = resident_conversation_stream.get_repository()
    job = {

        "user_message_id": "msg_12341234123412341234123412341234",
        "execution_id": "execution-" + "9" * 24,
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.publish_conversation_state(resident_record, job)
    state = repository.current_projection(str(resident_record["resident_id"]))["state"]
    assert state["work_state"] == "queued"
    assert state["work_phase"] == "starting_session"


@pytest.mark.parametrize("snapshot", [
    {"status": "claimed"},
    {"status": "running"},
    {"status": "queued", "scheduler": {"queue_reason": "ready"}},
    {"status": "queued", "scheduler": {"queue_reason": "session_binding"}},
    {"status": "queued", "scheduler": {"queue_reason": "dispatch_interval"}},
    {"status": "queued", "scheduler": {"queue_reason": "capacity"}},
])
def test_snapshotless_update_preserves_observed_dispatch_state(
    resident_record: dict[str, object], snapshot: dict[str, object],
) -> None:
    """无快照更新保留已观察的调度状态并允许停止"""
    repository = resident_conversation_stream.get_repository()
    resident_id = str(resident_record["resident_id"])
    job = {

        "user_message_id": "msg_56785678567856785678567856785678",
        "execution_id": "execution-" + "9" * 24,
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.publish_conversation_state(resident_record, job, snapshot=snapshot)
    expected = dict(repository.current_projection(resident_id)["state"])
    resident_execution_work.publish_conversation_state(resident_record, job)
    assert repository.current_projection(resident_id)["state"] == expected
    job["stop_requested_at"] = "2026-09-07T08:00:00Z"
    resident_execution_work.publish_conversation_state(resident_record, job)
    assert repository.current_projection(resident_id)["state"]["work_state"] == "stopping"


def test_dispatch_interval_has_its_own_wait_phase(
    resident_record: dict[str, object],
) -> None:
    """调度间隔等待投影为独立等待阶段"""
    repository = resident_conversation_stream.get_repository()
    resident_execution_work.publish_conversation_state(resident_record, {

        "user_message_id": "msg_98769876987698769876987698769876",
        "execution_id": "execution-" + "9" * 24,
     "submitted_at": "2026-10-14T00:00:00Z"}, snapshot={"status": "queued", "scheduler": {"queue_reason": "dispatch_interval"}})
    state = repository.current_projection(str(resident_record["resident_id"]))["state"]
    assert state["work_state"] == "queued"
    assert state["work_phase"] == "waiting_dispatch"


def test_recovery_snapshot_can_requeue_the_same_running_input(
    resident_record: dict[str, object],
) -> None:
    """恢复快照可将同一运行输入重新置为容量等待"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    message = resident_input_queue_domain.resident_user_message(
        resident_id, "recover the accepted input", "CASE-A",
        message_id="msg_76547654765476547654765476547654",
    )
    job = {
         "user_message_id": message["message_id"],
        "input_message": message, "execution_id": "execution-" + "9" * 24,
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.publish_conversation_state(resident_record, job, snapshot={"status": "running"})
    resident_execution_work.publish_conversation_state(resident_record, job, snapshot={
        "status": "queued", "scheduler": {"queue_reason": "capacity"},
    })
    state = repository.current_projection(resident_id)["state"]
    assert state["work_state"] == "queued"
    assert state["work_phase"] == "waiting_capacity"
    assert state["active_message_id"] == message["message_id"]
    assert [item["message_id"] for item in repository.read_messages(resident_id)] == [message["message_id"]]


def test_waiting_capacity_is_not_overwritten_by_snapshotless_job_write(
    resident_record: dict[str, object],
) -> None:
    """无快照任务写入保留容量等待且不提前激活消息"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    queued_message = resident_input_queue_domain.resident_user_message(
        resident_id,
        "wait for scheduler capacity",
        "CASE-A",
        message_id="msg_dddddddddddddddddddddddddddddddd",
    )
    job = {

        "user_message_id": queued_message["message_id"],
        "input_message": queued_message,
        "execution_id": "execution-" + "8" * 24,
        "started_at": "2026-08-31T14:00:00Z",
        "queue": [
            {
                'execution_id': "job_next",
                "user_message_id": "msg_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            }
        ],
     "submitted_at": "2026-10-14T00:00:00Z"}

    resident_execution_work.publish_conversation_state(
        resident_record,
        job,
        snapshot={
            "status": "queued",
            "scheduler": {"queue_reason": "capacity"},
        },
    )
    resident_execution_work.publish_conversation_state(resident_record, job)

    projection = repository.current_projection(resident_id)
    assert projection["state"] == {
        "work_state": "queued",
        "active_message_id": queued_message["message_id"],
        "queued_count": 1,
        "intake_state": "open",
        "work_phase": "waiting_capacity",
    }
    assert repository.read_messages(resident_id) == []
    assert repository.input_state(
        resident_id,
        queued_message["message_id"],
    )["state"] == "queued"


def test_lazy_resident_first_message_creates_session_job(
    resident_record: dict[str, object],
) -> None:
    """延迟创建的 Resident 首条消息建立会话任务且延后组装提示"""
    resident_backend.delete_session_identity(resident_record)
    resident_record.update({
        "session_state": "pending_create",
    })
    resident_record_domain.save_resident_record(resident_record)
    with (
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_intake_domain, "resolve_context_refs", return_value={}),
        mock.patch.object(resident_prompt_domain, "build_resident_prompt", side_effect=AssertionError("prompt built during admission")),
    ):
        result = _published_delivery(backend.deliver_resident_message_content(
            "resident_aaaaaaaaaaaa",
            "CASE-A",
            "开始第一轮。",
            message_id="msg_0123456789abcdef",
    ))

    assert result["resident_id"] == "resident_aaaaaaaaaaaa"
    assert result["message"]["message_id"] == "msg_0123456789abcdef"
    assert result["input_state"]["state"] == "running"
    assert result["state"]["work_phase"] == "starting_session"
    submitted_job = resident_execution_work.read(resident_record)
    assert submitted_job["bootstrap_session"] is True
    assert submitted_job["raw_message"] == "开始第一轮。"
    assert "prompt_builder_version" not in submitted_job
    assert "actual_sent_prompt" not in submitted_job
    assert resident_conversation_stream.get_repository().message(
        "resident_aaaaaaaaaaaa", "msg_0123456789abcdef"
    ) is not None
    stored = resident_execution_work.read(resident_record)
    assert "status" not in stored
    assert "queue_reason" not in stored
    assert stored["user_message_id"] == "msg_0123456789abcdef"


@pytest.mark.parametrize("policy", ["notify_only", "enqueue_without_wake", "unknown"])
def test_removed_delivery_modes_are_rejected_without_persistence(input_delivery, policy):
    """已移除的投递模式在持久化前被拒绝"""
    with pytest.raises(ValueError, match="activation_policy must be"):
        backend.deliver_resident_message_content(
            input_delivery["resident_id"], "CASE-A", "Do not silently start this", activation_policy=policy,
        )
    assert resident_execution_work.read(input_delivery) == {}
    assert resident_conversation_stream.get_repository().read_messages(input_delivery["resident_id"]) == []


def test_delivery_instructions_share_the_two_activation_modes():
    """投递与转发指令共用两种激活模式"""
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.society_service import resident_message_contract

    declared = {
        name: tuple(parameter.enum)
        for name, declaration in instruction_catalog().items()
        for parameter in declaration.parameters
        if parameter.name == "activation_policy"
    }
    assert set(declared) == {"DeliverMessage", "ForwardMessages"}
    assert set(declared.values()) == {resident_message_contract.ACTIVATION_POLICIES}


def test_normal_delivery_starts_idle_and_advances_busy_queue(input_delivery, monkeypatch):
    """普通投递启动空闲任务且忙碌时按序排队提升"""
    resident_id = input_delivery["resident_id"]
    prompt_builder = resident_prompt_domain.build_resident_prompt
    monkeypatch.setattr(resident_prompt_domain, "build_resident_prompt", lambda *_: (_ for _ in ()).throw(AssertionError("prompt built during admission")))
    first = _published_delivery(backend.deliver_resident_message_content(resident_id, "CASE-A", "First"))
    job = resident_execution_work.read(input_delivery)
    assert "prompt_builder_version" not in job
    assert "actual_sent_prompt" not in job
    assert resident_conversation_stream.get_repository().message(resident_id, first["message_id"]) is not None
    monkeypatch.setattr(resident_prompt_domain, "build_resident_prompt", prompt_builder)
    prepared = resident_execution_domain._portable_context_execution(input_delivery, job)
    assert prepared["prompt_builder_version"] == resident_execution_work.PROMPT_BUILDER_VERSION
    assert "<user-message>\nFirst\n</user-message>" in prepared["actual_sent_prompt"]
    assert resident_execution_work.execution_id(job)
    assert resident_execution_work.continuation_needed(input_delivery, job)
    assert not resident_execution_work.is_execution_held(job)
    second = _published_delivery(backend.deliver_resident_message_content(resident_id, "CASE-A", "Second"))
    third = _published_delivery(backend.deliver_resident_message_content(resident_id, "CASE-A", "Third"))
    job = resident_execution_work.read(input_delivery)
    assert job["user_message_id"] == first["message_id"]
    queue = resident_execution_work.normalized_queue(job)
    assert [item["user_message_id"] for item in queue] == [second["message_id"], third["message_id"]]
    assert [message["message_id"] for message in resident_conversation_stream.get_repository().read_messages(resident_id)] == [first["message_id"]]
    assert resident_conversation_stream.get_repository().input_state(resident_id, second["message_id"])["state"] == "queued"
    assert resident_conversation_stream.get_repository().input_state(resident_id, third["message_id"])["state"] == "queued"
    promoted = resident_execution_work.promote_queue_item(job, queue[0], queue[1:], resident_domain_common.now_iso())
    assert promoted["user_message_id"] == second["message_id"]
    assert "actual_sent_prompt" not in promoted
    assert "prompt_built_at" not in promoted
    assert not resident_execution_work.is_execution_held(promoted)
    assert resident_execution_work.continuation_needed(input_delivery, promoted)


@pytest.mark.parametrize("start", ["idle", "reopened", "promoted"])
def test_closing_intake_during_session_start_keeps_active_input_out_of_queue(input_delivery, start):
    """关闭接单不将正在启动 Session 的输入退回队列

    验证首次启动、重新开接单和队列提升后，关闭接单仍保留当前输入，后续输入继续等待，完整与轻量状态读取保持一致。

    :module: resident.conversation
    :importance: P1
    """
    resident_id = input_delivery["resident_id"]
    repository = resident_conversation_stream.get_repository()

    def intake(transition):
        resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition=transition)

    if start == "reopened":
        intake("close")
    first = _published_delivery(backend.deliver_resident_message_content(resident_id, "CASE-A", "Start this session"))
    if start == "reopened":
        intake("open")
        assert resident_execution_domain._prepare_resident_continuation(
            resident_record_domain.find_resident(resident_id),
        )
    elif start == "promoted":
        first = _published_delivery(backend.deliver_resident_message_content(resident_id, "CASE-A", "Next session"))
        job = resident_execution_work.read(input_delivery)
        queue = resident_execution_work.normalized_queue(job)
        resident_execution_work.promote_queue_item(job, queue[0], [], resident_domain_common.now_iso())
        resident_execution_work.write(input_delivery, job)
    assert repository.published_state(resident_id)["work_phase"] == "starting_session"
    assert not resident_execution_work.submitted_execution_id(resident_execution_work.read(input_delivery))

    intake("close")
    snapshot = resident_protocol.get_resident_conversation_snapshot(resident_id, "CASE-A")
    assert snapshot["queue"]["items"] == []
    assert snapshot["state"]["active_message_id"] == first["message_id"]
    assert snapshot["state"]["queued_count"] == 0
    for job in (resident_execution_work.read(input_delivery),
                resident_execution_work.read_control(resident_id)["state"]):
        assert job["intake_state"] == "closed"
        assert not resident_execution_work.is_execution_held(job)
        assert resident_execution_work.continuation_needed(input_delivery, job)

    second = _published_delivery(backend.deliver_resident_message_content(resident_id, "CASE-A", "Wait until intake reopens"))
    snapshot = resident_protocol.get_resident_conversation_snapshot(resident_id, "CASE-A")
    assert [item["message_id"] for item in snapshot["queue"]["items"]] == [second["message_id"]]
    assert snapshot["state"]["queued_count"] == 1
    assert snapshot["state"]["work_phase"] == "starting_session"
    assert repository.input_state(resident_id, first["message_id"])["state"] == "running"
    assert repository.input_state(resident_id, second["message_id"])["state"] == "queued"
    assert sum(message["message_id"] == first["message_id"] for message in repository.read_messages(resident_id)) == 1

    # A new head must still obey the closed gate after the current turn ends.
    job = resident_execution_work.read(input_delivery)
    resident_execution_work.promote_queue_item(job, job["queue"][0], [], resident_domain_common.now_iso())
    resident_execution_work.write(input_delivery, job)
    assert resident_execution_work.is_execution_held(resident_execution_work.read(input_delivery))
    assert second["message_id"] not in [message["message_id"] for message in repository.read_messages(resident_id)]
    assert repository.published_state(resident_id)["active_message_id"] == ""


@pytest.mark.parametrize("policy", ["notify_only", "enqueue_without_wake", "unsupported_future_policy"])
def test_unknown_persisted_policy_cannot_replay_work(input_delivery, policy):
    """未知持久化激活策略阻止工作重放并显示原因"""
    message_id = "msg_ffffffffffffffffffffffffffffffff"
    job = {
        'execution_id': "job_old", "actor": "CASE-A", "activation_policy": policy,
        "user_message_id": message_id,
        "input_message": {"message_id": message_id, "role": "user", "author_id": "CASE-A",
                          "content_parts": [{"type": "text", "text": "Legacy pending input"}]},
    }
    resident_execution_work.write(input_delivery, job)
    persisted = resident_execution_work.read(input_delivery)
    assert resident_execution_work.is_execution_held(persisted)
    assert not resident_execution_work.continuation_needed(input_delivery, persisted)
    assert resident_execution_work.public_execution_state(persisted)["queue_reason"] == resident_execution_work.INVALID_ACTIVATION_REASON


@pytest.mark.parametrize("status", ["delivered", "pending", "next_turn"])
def test_direct_injection_reuses_queue_delivery_and_identity(input_delivery, monkeypatch, status):
    """直接注入复用队列投递与消息身份且区分投递状态"""
    resident_id = input_delivery["resident_id"]
    resident_execution_work.write(input_delivery, {
         "actor": "CASE-A",
        "execution_id": "execution-111111111111111111111111", "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})
    service = mock.Mock()
    service.status.return_value = {"status": "running"}
    service.input_control.return_value = None
    if status == "next_turn":
        service.append_input.side_effect = ValueError("execution has no active turn")
    else:
        service.append_input.return_value = {"status": status, "control_id": "control-test"}
    monkeypatch.setattr(execution_port, "service", lambda: service)
    message_id = "msg_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    payload = {
        "resident_id": resident_id, "activation_policy": "inject_active_turn",
        "message": {"message_id": message_id, "role": "user",
                    "content_parts": [{"type": "text", "text": "Additional input"}]},
    }
    result = _published_delivery(resident_protocol.deliver_resident_input(payload, "CASE-A"))
    assert result["message_id"] == message_id
    service.append_input.assert_called_once()
    job = resident_execution_work.read(input_delivery)
    assert job['execution_id'] == 'execution-111111111111111111111111'
    assert job["execution_id"] == "execution-111111111111111111111111"
    service.submit.assert_not_called()
    service.cancel.assert_not_called()
    repository = resident_conversation_stream.get_repository()
    if status == "delivered":
        assert result["accepted"]
        assert job["queue"] == []
        assert repository.input_state(resident_id, message_id)["state"] == "completed"
        assert len(repository.read_messages(resident_id)) == 1
        _published_delivery(resident_protocol.deliver_resident_input(payload, "CASE-A"))
        service.append_input.assert_called_once()
    else:
        assert result["accepted"]
        assert [item["user_message_id"] for item in job["queue"]] == ([message_id] if status == "next_turn" else [])
        assert repository.input_state(resident_id, message_id)["state"] == "queued"
        if status == "pending":
            assert resident_execution_work.send_now_inputs(job)[0]["user_message_id"] == message_id
            assert not job.get("send_now_next_turn")
        else:
            assert job["queue"][0]["user_message_id"] == message_id
            assert not resident_execution_work.send_now_inputs(job)


def test_direct_injection_requires_active_work_before_acceptance(input_delivery):
    """无活跃任务时直接注入在接收输入前被拒绝"""
    with pytest.raises(ValueError, match="没有可注入"):
        _published_delivery(backend.deliver_resident_message_content(
            input_delivery["resident_id"], "CASE-A", "Extra", activation_policy="inject_active_turn",
        ))
    assert resident_execution_work.read(input_delivery) == {}
    assert resident_conversation_stream.get_repository().user_message_counts(
        [input_delivery["resident_id"]],
    ).get(input_delivery["resident_id"], 0) == 0


def test_direct_injection_rejects_unsupported_provider(input_delivery, monkeypatch):
    """Provider 不支持当前轮输入时直接注入被拒绝"""
    from dataclasses import replace

    provider = resident_execution_provider.provider_for(input_delivery)
    monkeypatch.setattr(provider, "capabilities", replace(provider.capabilities, supports_active_turn_input=False))
    monkeypatch.setattr(resident_execution_provider, "provider_for", lambda record: provider)
    with pytest.raises(ValueError, match="不支持注入"):
        _published_delivery(backend.deliver_resident_message_content(
            input_delivery["resident_id"], "CASE-A", "Extra", activation_policy="inject_active_turn",
        ))
    assert resident_execution_work.read(input_delivery) == {}


def test_direct_injection_turn_completion_race_keeps_one_queued_input(input_delivery, monkeypatch):
    """直接注入与轮次完成竞态只保留一个待运行输入"""
    resident_execution_work.write(input_delivery, {
         "actor": "CASE-A",
        "execution_id": "execution-111111111111111111111111", "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})
    def finish_while_preparing(*args, **kwargs):
        job = resident_execution_work.read(input_delivery)
        job["outcome"] = "completed"
        resident_execution_work.write(input_delivery, job)
        return {}
    monkeypatch.setattr(resident_intake_domain, "resolve_context_refs", finish_while_preparing)
    result = _published_delivery(backend.deliver_resident_message_content(
        input_delivery["resident_id"], "CASE-A", "Extra", activation_policy="inject_active_turn",
    ))
    assert result["injected"] is False
    assert result["delivery_status"] == "next_turn"
    job = resident_execution_work.read(input_delivery)
    assert job["user_message_id"] == result["message_id"]
    assert job["queue"] == []
    assert not resident_execution_work.is_execution_held(job)
    assert resident_execution_work.continuation_needed(input_delivery, job)


@pytest.mark.parametrize("generic", [False, True])
@pytest.mark.parametrize("with_queue", [False, True])
def test_cancel_closed_intake_first_input_needs_no_execution(resident_record, generic, with_queue):
    """接收关闭时取消首条输入无需执行服务且保留后续队列

    :importance: P1"""
    resident_id = str(resident_record["resident_id"])
    resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition="close")
    with (
        mock.patch.object(resident_session_backend_domain, "handle_resident_native_command", return_value={}),
        mock.patch.object(slash_commands, "handle_slash_command", return_value={"handled": False}),
        mock.patch.object(resident_intake_domain, "resolve_context_refs", return_value={}),
        mock.patch.object(resident_prompt_domain, "build_resident_prompt", return_value="held prompt"),
    ):
        delivery = backend.deliver_resident_message_content(resident_id, "CASE-A", "hold this message")
        following = backend.deliver_resident_message_content(resident_id, "CASE-A", "keep this message") if with_queue else None
    assert resident_execution_work.is_execution_held(resident_execution_work.read(resident_record))
    cancel = resident_protocol.cancel_resident_input if generic else resident_protocol.cancel_resident_execution
    with mock.patch.object(execution_port, "service", side_effect=AssertionError("No execution was admitted")):
        cancel(resident_id, "CASE-A", delivery["message_id"])
    assert resident_protocol.get_resident_input_state(resident_id, "CASE-A", delivery["message_id"])["state"] == "cancelled"
    assert resident_execution_work.is_active(resident_execution_work.read(resident_record)) is with_queue
    if following:
        assert resident_protocol.get_resident_input_state(resident_id, "CASE-A", following["message_id"])["state"] == "queued"
        assert resident_execution_work.read(resident_record)["user_message_id"] == following["message_id"]
    assert resident_protocol.get_resident_state(resident_id, "CASE-A")["state"]["intake_state"] == "closed"


def test_closed_resident_intake_holds_message_until_reopened(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 接收关闭时暂存消息直至重新打开"""
    monkeypatch.setattr(ExecutionService, "binding_quota_states",
                        lambda _self, bindings: {key: {"state": "open", "reason": ""} for key in bindings})
    resident_protocol.change_resident_state(
        "resident_aaaaaaaaaaaa",
        "CASE-A",
        state_domain="intake",
        transition="close",
    )
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
        mock.patch.object(resident_prompt_domain, "build_resident_prompt", return_value="held prompt"),
    ):
        result = _published_delivery(resident_protocol.deliver_resident_input({
            "resident_id": "resident_aaaaaaaaaaaa",
            "message": {
                "message_id": "msg_22222222222222222222222222222222",
                "role": "user",
                "content_parts": [{"type": "text", "text": "hold this message"}],
            },
        }, "CASE-A"))

    assert result["resident_id"] == "resident_aaaaaaaaaaaa"
    assert result["input_state"]["state"] == "queued"

    held = resident_execution_work.read(resident_record)
    state_view = resident_protocol.get_resident_state(
        "resident_aaaaaaaaaaaa",
        "CASE-A",
    )
    assert state_view["resident_id"] == "resident_aaaaaaaaaaaa"
    assert state_view["controls"]["can_send"] is True
    assert state_view["controls"]["send_hint"]
    assert isinstance(state_view["cursor"], int)
    assert state_view["state"]["intake_state"] == "closed"
    assert state_view["controls"]["can_send"] is True
    assert resident_execution_work.public_execution_state(held) == {
        "status": "held",
        "queue_reason": resident_state.INTAKE_CLOSED_QUEUE_REASON,
    }
    assert held["intake_state"] == "closed"
    assert held.get("execution_id")
    assert not resident_execution_work.submitted_execution_id(held)
    assert resident_execution_work.continuation_needed(
        resident_record,
        held,
    ) is False

    response = resident_protocol.change_resident_state(
        "resident_aaaaaaaaaaaa",
        "CASE-A",
        state_domain="intake",
        transition="open",
    )
    reopened = resident_execution_work.read(resident_record)
    assert response["intake_state"] == "open"
    assert response["state_domain"] == "intake"
    assert response["field"] == "intake_state"
    assert response["previous_state"] == "closed"
    assert response["state"] == "open"
    assert response["changed"] is True
    assert "status" not in reopened
    assert "queue_reason" not in reopened
    assert reopened["intake_state"] == "open"
    assert resident_execution_work.continuation_needed(resident_record, reopened)
    # Opening changes the manual gate; the execution owner reserves the
    # request identity when it prepares the resumed work.
    current_record = resident_record_domain.find_resident("resident_aaaaaaaaaaaa")
    assert resident_execution_domain._prepare_resident_continuation(current_record)
    assert resident_execution_work.execution_id(resident_execution_work.read(current_record))
    reopened_view = resident_protocol.get_resident_state("resident_aaaaaaaaaaaa", "CASE-A")
    assert reopened_view["state"]["intake_state"] == "open"
    assert reopened_view["controls"]["can_send"] is True
    assert reopened_view["controls"]["send_hint"] == ""
