"""Queue controls of a Resident: clearing and cancelling the queue, send now into the running turn and
pending turn input.

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
    resident_access,
    resident_conversation_projection_domain,
    resident_intake_domain,
    resident_execution_work,
    resident_protocol,
    resident_session_backend_domain,
    resident_domain_common,
    resident_prompt_domain,
    resident_input_queue_domain,
    resident_execution_engine_domain,
)
from management_portal.society_service import resident_domain as backend
from management_portal.society_service.resident_execution_work import bind_execution
from tests.cases.resident_execution_test_support import _message_text
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401 - fixture


@pytest.mark.parametrize("head_phase", ["queued", "running"])
def test_clear_resident_queue_preserves_admitted_execution(
    resident_record, monkeypatch, head_phase,
):
    """清空 Resident 等待队列保留已准入执行和接收状态"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    initial_ids = ["msg_bulk_head", "msg_bulk_local", "msg_bulk_remote"]
    for message_id in initial_ids:
        repository.append_message(resident_id, {
            "message_id": message_id, "role": "user", "author_id": "sample_admin",
            "content_parts": [{"type": "text", "text": message_id}],
        })
        repository.set_input_state(resident_id, message_id, "queued")
    resident_execution_work.write(resident_record, {
         "status": head_phase, "actor": "sample_admin",
        "user_message_id": initial_ids[0], "execution_id": "execution-bulk-head",
        "queue": [
            { "actor": "CASE-A", "user_message_id": initial_ids[1]},
            { "actor": "sample_admin", "user_message_id": initial_ids[2]},
        ],
     "submitted_at": "2026-10-14T00:00:00Z"})
    original_intake = resident_protocol.get_resident_state(resident_id, "sample_admin")["state"]["intake_state"]

    service = mock.Mock(states=lambda executions: {key: {"status": head_phase} for key in executions})
    service.cancel.side_effect = AssertionError("Waiting inputs have no submitted Execution")
    monkeypatch.setattr(execution_port, "service", lambda: service)
    result = resident_protocol.clear_resident_queue(resident_id, "sample_admin")
    assert result["cleared_count"] == 2
    assert result["errors"] == []
    current = resident_execution_work.read(resident_record)
    assert current["queue"] == []
    assert current["execution_id"] == "execution-bulk-head"
    assert not current.get("stop_requested_by")
    assert all(repository.input_state(resident_id, mid)["state"] == "cancelled" for mid in initial_ids[1:])
    assert resident_protocol.get_resident_state(resident_id, "sample_admin")["state"]["intake_state"] == original_intake
    service.cancel.assert_not_called()


def test_clear_resident_queue_uses_isa_authority_and_empty_queue_is_idempotent(
    resident_record, monkeypatch,
):
    """清空空队列保持幂等，只读取额度且不提交或取消执行"""
    resident_id = str(resident_record["resident_id"])
    quota = mock.Mock(side_effect=lambda bindings: {
        key: {"state": "open", "reason": ""} for key in bindings})
    service = SimpleNamespace(binding_quota_states=quota)
    monkeypatch.setattr(execution_port, "service", lambda: service)
    for _attempt in range(2):
        result = resident_protocol.clear_resident_queue(resident_id, "sample_admin")
        assert result["cleared_count"] == 0
        assert result["errors"] == []
    monkeypatch.setattr(resident_access, "can_manage", lambda record, actor: False)
    assert resident_protocol.clear_resident_queue(resident_id, "sample_admin")["cleared_count"] == 0
    assert quota.call_count == 3
    assert all(set(call.args[0]) == {f"resident:{resident_id}"} for call in quota.call_args_list)


def test_clear_resident_queue_cancels_held_inputs_without_reopening_intake(resident_record):
    """清空队列取消暂存输入并保持接收关闭"""
    resident_id = str(resident_record["resident_id"])
    resident_protocol.change_resident_state(
        resident_id, "CASE-A", state_domain="intake", transition="close",
    )
    with (
        mock.patch.object(resident_session_backend_domain, "handle_resident_native_command", return_value={}),
        mock.patch.object(slash_commands, "handle_slash_command", return_value={"handled": False}),
        mock.patch.object(resident_intake_domain, "resolve_context_refs", return_value={}),
        mock.patch.object(resident_prompt_domain, "build_resident_prompt", return_value="held prompt"),
    ):
        backend.deliver_resident_message_content(resident_id, "CASE-A", "held first message")
        backend.deliver_resident_message_content(resident_id, "CASE-A", "held following message")
    held = resident_execution_work.read(resident_record)
    assert resident_execution_work.is_execution_held(held)
    message_ids = [held["user_message_id"], *[item["user_message_id"] for item in held["queue"]]]
    with mock.patch.object(execution_port, "service", side_effect=AssertionError("No execution was admitted")):
        result = resident_protocol.clear_resident_queue(resident_id, "CASE-A")
        repeated = resident_protocol.clear_resident_queue(resident_id, "CASE-A")
    assert result["cleared_count"] == 2
    assert result["errors"] == []
    assert repeated["cleared_count"] == 0
    assert not resident_execution_work.is_active(resident_execution_work.read(resident_record))
    repository = resident_conversation_stream.get_repository()
    assert all(repository.input_state(resident_id, message_id)["state"] == "cancelled" for message_id in message_ids)
    assert resident_protocol.get_resident_state(resident_id, "CASE-A")["state"]["intake_state"] == "closed"
    # CASE-A 10-06: withdrawn messages never reached the Resident; the Conversation says nothing about them.
    assert not [item for item in repository.agent_data.read_chat_messages(resident_id)
                if item.get("reply_to_message_id") in message_ids or item.get("role") == "system"]


def test_clear_resident_queue_is_independent_of_execution_service(resident_record, monkeypatch):
    """清空等待队列独立于执行服务可用性"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    head_id, remote_id, local_id = "msg_clear_partial_head", "msg_clear_partial_remote", "msg_clear_partial_local"
    for message_id in (head_id, remote_id, local_id):
        repository.append_message(resident_id, {
            "message_id": message_id, "role": "user", "author_id": "sample_admin",
            "content_parts": [{"type": "text", "text": message_id}],
        })
        repository.set_input_state(resident_id, message_id, "queued")
    resident_execution_work.write(resident_record, {
        'execution_id': "job_clear_partial_head", "status": "running", "actor": "sample_admin", "user_message_id": head_id,
        "queue": [
            { "actor": "sample_admin", "user_message_id": remote_id},
            { "actor": "sample_admin", "user_message_id": local_id},
        ],
    })
    service = mock.Mock()
    service.cancel.side_effect = RuntimeError("Execution temporarily unavailable")
    monkeypatch.setattr(execution_port, "service", lambda: service)
    result = resident_protocol.clear_resident_queue(resident_id, "sample_admin")
    assert result["cleared_message_ids"] == [remote_id, local_id]
    assert result["errors"] == []
    service.cancel.assert_not_called()
    current = resident_execution_work.read(resident_record)
    assert [item["user_message_id"] for item in current["queue"]] == []
    assert not current.get("stop_requested_by")
    assert repository.input_state(resident_id, local_id)["state"] == "cancelled"
    assert repository.input_state(resident_id, remote_id)["state"] == "cancelled"


def test_cancel_queue_retry_rolls_back_interrupted_resident_commit(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消队列提交中断时回滚输入状态且重试可完成"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_cancel_interrupted_bbbbbbbbb"
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [{"type": "text", "text": "cancel me"}],
            "author_id": "sample_admin",
        },
    )
    repository.set_input_state(resident_id, message_id, "queued")
    resident_execution_work.write(
        resident_record,
        {
            'execution_id': "job_current",
            "actor": "CASE-A",
            "status": "running",
            "queue": [{

                "user_message_id": message_id,
                "actor": "sample_admin",
}],
        },
    )
    cancel_calls = []

    class Service:
        @staticmethod
        def binding_quota_states(bindings):
            return {key: {"state": "open", "reason": ""} for key in bindings}

        @staticmethod
        def cancel(execution_id, **_kwargs):
            cancel_calls.append(execution_id)
            return {"status": "cancelled", "cancel_requested": True}

    monkeypatch.setattr(execution_port, "service", lambda: Service())
    original_write = resident_execution_work.write
    interrupted = True

    def interrupt_first_commit(*args, **kwargs):
        nonlocal interrupted
        if interrupted:
            interrupted = False
            monkeypatch.setattr(resident_domain_common, "now_iso", lambda: "2026-09-07T00:00:00Z")
            raise OSError("simulated Resident state commit interruption")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(resident_execution_work, "write", interrupt_first_commit)

    with pytest.raises(OSError, match="commit interruption"):
        resident_protocol.cancel_resident_input(
            resident_id,
            "sample_admin",
            message_id,
        )

    assert repository.input_state(resident_id, message_id)["state"] == "queued"
    assert len(resident_execution_work.normalized_queue(resident_execution_work.read(resident_record))) == 1

    result = resident_protocol.cancel_resident_input(
        resident_id,
        "sample_admin",
        message_id,
    )

    assert result["input_state"]["state"] == "cancelled"
    assert resident_execution_work.normalized_queue(resident_execution_work.read(resident_record)) == []
    assert cancel_calls == []
    # CASE-A 10-06: the page tells the one who withdrew it; the Conversation gets no notice.
    assert [item["message_id"] for item in repository.agent_data.read_chat_messages(resident_id)] == [message_id]


def test_inject_queue_message_does_not_stop_current_execution(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """队列消息注入当前执行后沿用消息身份且仅投递一次"""
    resident_id = str(resident_record["resident_id"])
    queued_message_id = "msg_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    queued_input = {
        "message_id": queued_message_id,
        "role": "user",
        "content_parts": [{"type": "text", "text": "补充检查移动端"}],
        "created_at": "2026-08-23T00:00:00Z",
        "author_id": "CASE-A",
    }
    resident_execution_work.write(
        resident_record,
        {

            "execution_id": "execution-111111111111111111111111",
            "actor": "CASE-A",
            "status": "running",
            "queue": [{
                'execution_id': "job_queued_input",
                "user_message_id": queued_message_id,
                "input_message": queued_input,
                "actor": "CASE-A",
                "raw_message": "补充检查移动端",
                "actual_sent_prompt": "补充检查移动端",
            }],
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    calls: list[tuple[str, str, list[dict[str, object]]]] = []

    class Service:
        def status(self, *_args, **_kwargs):
            return {"status": "running"}

        def append_input(self, execution_id, *, actor, input, **_kwargs):
            from management_portal.society_service.invariant_locks import lock_held
            assert not lock_held(), "Execution RPC under Resident state lock"
            calls.append((execution_id, actor, input))
            return {
                "control_id": "control-222222222222222222222222",
                "status": "delivered",
                "expected_turn_id": "turn-active",
            }

        def input_control(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(execution_port, "service", lambda: Service())
    assert resident_conversation_stream.get_repository().read_messages(
        resident_id
    ) == []
    result = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
        resident_id,
        "CASE-A",
        queued_message_id,
    ))

    stored = resident_execution_work.read(resident_record)
    assert stored['execution_id'] == 'execution-111111111111111111111111'
    assert "status" not in stored
    assert "queue_reason" not in stored
    assert stored["execution_id"] == "execution-111111111111111111111111"
    assert stored["queue"] == []
    assert calls == [(
        "execution-111111111111111111111111",
        "CASE-A",
        [{"type": "text", "text": "<user-message>\n补充检查移动端\n</user-message>"}],
    )]
    conversation = resident_conversation_stream.get_repository().read_messages(
        resident_id
    )
    queued_message = next(
        item for item in conversation if item["message_id"] == queued_message_id
    )
    assert _message_text(queued_message) == "补充检查移动端"
    assert conversation == [queued_message]
    assert queued_message["created_at"] == queued_input["created_at"]
    assert queued_message["activated_at"] != queued_message["created_at"]
    assert result["resident_id"] == resident_id
    assert result["message_id"] == queued_message_id
    assert result["accepted"] is True
    assert result["message"]["message_id"] == queued_message_id
    assert result["input_state"]["state"] == "completed"
    assert result["queue"]["items"] == []
    assert isinstance(result["state"], dict)
    assert isinstance(result["cursor"], int)
    assert resident_conversation_projection_domain.conversation_family_summaries(
        [resident_id], "CASE-A",
    )[resident_id]["unread_signature"]["completion_id"] == ""

    retried = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
        resident_id,
        "CASE-A",
        queued_message_id,
    ))
    assert retried["already_injected"] is True
    assert calls == [(
        "execution-111111111111111111111111",
        "CASE-A",
        [{"type": "text", "text": "<user-message>\n补充检查移动端\n</user-message>"}],
    )]


def test_a_send_now_input_the_execution_refuses_waits_for_the_next_turn(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行层拒收的立即发送输入改在下一轮处理

    立即发送的输入被执行层拒收（没有活动轮次、不支持追加或输入不合法）时，消息转入下一轮等待，不会滞留在队列之外，也不会在每次轮次结束时反复重试而阻塞后续消息；早先被拒收而滞留的输入在轮次结算时同样转入下一轮。

    A refused input is neither stranded outside the queue nor retried forever.

    :module: resident.conversation
    :importance: P1
    :design: decision_15f7df737ed5
    """
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_dddddddddddddddddddddddddddddddd"
    item = {
        'execution_id': "job_queued_input",
        "user_message_id": message_id,
        "input_message": {
            "message_id": message_id, "role": "user", "author_id": "CASE-A",
            "content_parts": [{"type": "text", "text": "补充检查移动端"}],
            "created_at": "2026-10-10T00:00:00Z",
        },
        "actor": "CASE-A",
        "raw_message": "补充检查移动端",
        "actual_sent_prompt": "补充检查移动端",
    }
    running = { "execution_id": "execution-444444444444444444444444", "actor": "CASE-A", "submitted_at": "2026-10-14T00:00:00Z"}

    class Service:
        def status(self, *_args, **_kwargs):
            return {"status": "running"}

        def append_input(self, *_args, **_kwargs):
            raise ValueError("execution input control text is too long")

        def input_control(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(execution_port, "service", lambda: Service())
    resident_execution_work.write(resident_record, {**running, "queue": [dict(item)]})
    _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))

    job = resident_execution_work.read(resident_record)
    assert not resident_execution_work.send_now_inputs(job)
    assert [entry["user_message_id"] for entry in job["queue"]] == [message_id]

    # An input an earlier refusal left outside the queue is settled the same way.
    stranded = {**item, "active_turn_input": {"control_id": "control-stranded", "actor": "CASE-A"}}
    resident_execution_work.write(resident_record, {**running, "send_now_inputs": [stranded]})
    assert resident_input_queue_domain.reconcile_queued_turn_inputs(resident_id, 'execution-444444444444444444444444')
    job = resident_execution_work.read(resident_record)
    assert not resident_execution_work.send_now_inputs(job)
    assert [entry["user_message_id"] for entry in job["queue"]] == [message_id]


@pytest.mark.parametrize("failed_commit", [1, 2], ids=["intent", "delivery"])
def test_inject_queue_message_recovers_commit_interruption(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    failed_commit: int,
) -> None:
    """队列注入提交中断后恢复且输入仅追加一次"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_dddddddddddddddddddddddddddddddd"
    resident_execution_work.write(
        resident_record,
        {

            "execution_id": "execution-444444444444444444444444",
            "actor": "CASE-A",
            "status": "running",
            "queue": [{
                'execution_id': "job_queued_input",
                "user_message_id": message_id,
                "input_message": {
                    "message_id": message_id,
                    "role": "user",
                    "created_at": "2026-09-08T00:00:00Z",
                    "content_parts": [{"type": "text", "text": "提交中断恢复"}],
                    "author_id": "CASE-A",
                },
                "actor": "CASE-A",
                "actual_sent_prompt": "提交中断恢复",
            }],
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    append_calls = []

    class Service:
        @staticmethod
        def append_input(execution_id, *, actor, input, control_id, source_segment_id=""):
            append_calls.append((execution_id, actor, input, control_id))
            return {"control_id": control_id, "status": "delivered"}

        @staticmethod
        def input_control(*_args, **_kwargs):
            return {"status": "delivered"} if append_calls else None

    monkeypatch.setattr(execution_port, "service", lambda: Service())
    original_write = resident_execution_work.write
    commit_count = 0

    def interrupt_first_commit(*args, **kwargs):
        nonlocal commit_count
        commit_count += 1
        if commit_count == failed_commit:
            raise OSError("simulated Resident state commit interruption")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(resident_execution_work, "write", interrupt_first_commit)

    with pytest.raises(OSError, match="commit interruption"):
        _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
            resident_id,
            "CASE-A",
            message_id,
        ))

    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(resident_id, message_id)["state"] == (
        "queued" if failed_commit == 1 else "completed"
    )
    current = resident_execution_work.read(resident_record)
    assert len(resident_execution_work.normalized_queue(current)) == (1 if failed_commit == 1 else 0)
    assert len(resident_execution_work.send_now_inputs(current)) == (0 if failed_commit == 1 else 1)

    retried = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
        resident_id,
        "CASE-A",
        message_id,
    ))

    assert retried.get("already_injected", False) is (failed_commit == 2)
    assert retried["accepted"] is True
    assert resident_execution_work.normalized_queue(resident_execution_work.read(resident_record)) == []
    assert len(append_calls) == 1


def test_cancel_resident_input_reconciles_a_concurrent_state_change(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消输入遇到并发终态时返回已终结状态"""
    states = iter([
        {"message_id": "message-1", "state": "queued"},
        {"message_id": "message-1", "state": "completed"},
        {"message_id": "message-1", "state": "completed"},
        {"message_id": "message-1", "state": "completed"},
    ])
    queue_cancel_calls = []

    monkeypatch.setattr(
        resident_protocol,
        "_stored_resident_input_state",
        lambda *_args, **_kwargs: next(states),
    )

    def queue_item_already_moved(*_args, **_kwargs):
        queue_cancel_calls.append(True)
        raise KeyError("queue item not found")

    monkeypatch.setattr(
        resident_protocol,
        "cancel_resident_queue_item",
        queue_item_already_moved,
    )
    monkeypatch.setattr(
        resident_protocol,
        "get_resident_state",
        lambda *_args, **_kwargs: {
            "state": {"work_state": "idle"},
            "cursor": 9,
        },
    )
    result = resident_protocol.cancel_resident_input(
        str(resident_record["resident_id"]),
        "CASE-A",
        "message-1",
    )

    assert queue_cancel_calls == [True]
    assert result["already_terminal"] is True
    assert result["input_state"]["state"] == "completed"


def test_cancel_resident_turn_rejects_queue_items_without_command_rerouting(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消当前轮拒绝排队输入且不转发队列取消命令"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_" + "7" * 32
    monkeypatch.setattr(
        resident_protocol,
        "_stored_resident_input_state",
        lambda *_args, **_kwargs: {"message_id": message_id, "state": "queued"},
    )
    monkeypatch.setattr(
        resident_protocol,
        "get_resident_state",
        lambda *_args, **_kwargs: {
            "state": {"work_state": "running", "active_message_id": "msg_" + "8" * 32},
        },
    )
    monkeypatch.setattr(
        resident_protocol,
        "cancel_resident_queue_item",
        lambda *_args, **_kwargs: pytest.fail("CancelResidentTurn rerouted to a queue command"),
    )

    with pytest.raises(ValueError, match="message is not the active Resident input"):
        resident_protocol.cancel_resident_execution(resident_id, "CASE-A", message_id)


def test_inject_queue_message_waits_durably_for_execution_binding(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """队列注入持久等待执行绑定后再追加消息"""
    resident_id = str(resident_record["resident_id"])
    queued_message_id = "msg_cccccccccccccccccccccccccccccccc"
    queued_input = {
        "message_id": queued_message_id,
        "role": "user",
        "content_parts": [
            {"type": "text", "text": "追加到尚未绑定的 turn"}
        ],
        "created_at": "2026-08-23T00:00:00Z",
        "author_id": "CASE-A",
    }
    resident_execution_work.write(
        resident_record,
        {
            'execution_id': "execution-333333333333333333333333",
            "user_message_id": "message-active-binding",
            "actor": "CASE-A",
            "status": "running",
            "queue": [{
                'execution_id': "job_queued_before_binding",
                "user_message_id": queued_message_id,
                "input_message": queued_input,
                "actor": "CASE-A",
                "raw_message": "追加到尚未绑定的 turn",
                "effective_message": "追加到尚未绑定的 turn",
                "actual_sent_prompt": "追加到尚未绑定的 turn",
            }],
        },
    )
    calls: list[tuple[str, str, list[dict[str, object]]]] = []

    class Service:
        def append_input(self, execution_id, *, actor, input, **_kwargs):
            calls.append((execution_id, actor, input))
            return {"control_id": "control-pending", "status": "pending"}

        def input_control(self, *_args, **_kwargs):
            return {"status": "delivered"} if calls else None

    service = Service()
    monkeypatch.setattr(execution_port, "service", lambda: service)
    repository = resident_conversation_stream.get_repository()
    assert repository.read_messages(resident_id) == []

    result = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
        resident_id,
        "CASE-A",
        queued_message_id,
    ))

    assert result["accepted"] is True
    assert result["queue"]["items"] == []
    assert calls == []
    pending_job = resident_execution_work.read(resident_record)
    assert pending_job["queue"] == []
    assert resident_execution_work.send_now_inputs(pending_job)[0]["user_message_id"] == queued_message_id
    assert resident_execution_work.send_now_inputs(pending_job)[0]["active_turn_input"]["control_id"]
    assert repository.read_messages(resident_id) == []

    bind_execution(
        pending_job,
        execution_id="execution-333333333333333333333333",


        now="2026-08-28T00:00:00Z",
    )
    resident_execution_work.write(resident_record, pending_job)
    resident_input_queue_domain.reconcile_queued_turn_inputs(
        resident_id,
        "execution-333333333333333333333333",
    )

    assert calls == [(
        "execution-333333333333333333333333",
        "CASE-A",
        [{"type": "text", "text": "<user-message>\n追加到尚未绑定的 turn\n</user-message>"}],
    )]
    assert "pending_active_turn_inputs" not in resident_execution_work.read(resident_record)
    assert resident_input_queue_domain.reconcile_queued_turn_inputs(resident_id, "execution-333333333333333333333333")
    assert resident_execution_work.read(resident_record)["queue"] == []
    assert [item["message_id"] for item in repository.read_messages(resident_id)] == [queued_message_id]


def test_pending_active_turn_flush_retries_one_idempotent_append_after_commit_failure(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """待注入控制提交失败后使用稳定控制标识重试"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-555555555555555555555555"
    message_id = "msg_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    resident_execution_work.write(
        resident_record,
        {

            "execution_id": execution_id,
            "actor": "CASE-A",
            "pending_active_turn_inputs": [{
                "message_id": message_id,
                "actor": "CASE-A",
                "input": [{"type": "text", "text": "只注入一次"}],
                "control_id": "resident-input:stable-control",
            }],
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    append_attempts: list[str] = []
    delivered_controls: set[str] = set()

    class Service:
        @staticmethod
        def append_input(_execution_id, *, control_id, **_kwargs):
            from management_portal.society_service.invariant_locks import lock_held
            assert not lock_held(), "Execution RPC under Resident state lock"
            append_attempts.append(control_id)
            delivered_controls.add(control_id)
            return {"control_id": control_id, "status": "delivered"}

    original_write = resident_execution_work.write
    interrupted = True

    def interrupt_first_commit(*args, **kwargs):
        nonlocal interrupted
        if interrupted:
            interrupted = False
            raise OSError("simulated pending-input commit interruption")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(resident_execution_work, "write", interrupt_first_commit)

    with pytest.raises(OSError, match="commit interruption"):
        resident_execution_engine_domain._flush_pending_active_turn_controls(
            Service(),
            resident_id,
            execution_id,
        )

    assert resident_execution_work.read(resident_record)["pending_active_turn_inputs"]

    resident_execution_engine_domain._flush_pending_active_turn_controls(
        Service(),
        resident_id,
        execution_id,
    )

    assert append_attempts == [
        "resident-input:stable-control",
        "resident-input:stable-control",
    ]
    assert delivered_controls == {"resident-input:stable-control"}
    assert "pending_active_turn_inputs" not in resident_execution_work.read(resident_record)


def test_pending_active_turn_flush_does_not_append_after_stop_requested(
    resident_record: dict[str, object],
) -> None:
    """停止请求发出后不再追加待注入输入"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-666666666666666666666666"
    resident_execution_work.write(
        resident_record,
        {

            "execution_id": execution_id,
            "actor": "CASE-A",
            "stop_requested_at": "2026-08-31T08:00:00Z",
            "pending_active_turn_inputs": [{
                "message_id": "msg_ffffffffffffffffffffffffffffffff",
                "actor": "CASE-A",
                "input": [{"type": "text", "text": "不要注入旧执行"}],
            }],
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    service = mock.Mock()

    resident_execution_engine_domain._flush_pending_active_turn_controls(
        service,
        resident_id,
        execution_id,
    )

    service.append_input.assert_not_called()
    assert resident_execution_work.read(resident_record)["pending_active_turn_inputs"]
