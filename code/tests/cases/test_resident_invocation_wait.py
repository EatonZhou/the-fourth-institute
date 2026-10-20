from types import SimpleNamespace

from management_portal import isa_client, resident_handoff


def test_observation_waits_for_missing_reply_records_without_hiding_access_errors(monkeypatch, subtests):
    """完成事件先于消息或产物落库时继续观察；权限撤回和输入删除仍正常报错。

    完成事件先于消息或产物落库时继续观察；权限撤回和输入删除仍正常报错。

    :module: resident.conversation
    """
    import pytest

    for missing in ("message", "artifact"):
        for access in ("allowed", "denied", "deleted", "failed", "cancelled", "reply_denied"):
            with subtests.test(missing=missing, access=access), monkeypatch.context() as patch:
                visible = False
                state = {"state": "completed", "reply_message_ids": ["reply-1"]}
                handle = dict(actor="alice", resident_id="resident-1", message_id="input-1")

                def read_state(*_):
                    if access == "denied":
                        raise PermissionError("access revoked")
                    if access == "deleted":
                        raise KeyError("input-1")
                    if access in {"failed", "cancelled"}:
                        return {**state, "state": access}
                    return state

                def message(*_):
                    if access == "reply_denied":
                        raise PermissionError("reply access revoked")
                    if missing == "message" and not visible:
                        raise KeyError("reply-1")
                    return {"message_id": "reply-1", "content_parts": [
                        {"type": "text", "text": "ready"},
                        {"type": "artifact_reference", "artifact_id": "artifact-1"},
                    ]}

                def artifact(*_):
                    if missing == "artifact" and not visible:
                        raise KeyError("artifact-1")
                    return {"value": {"done": True}}

                patch.setattr(resident_handoff, "get_society_service", lambda: SimpleNamespace(
                    resident=SimpleNamespace(get_resident_input_state=read_state,
                        get_resident_message=message, get_resident_artifact=artifact)))
                if access != "allowed":
                    error = (resident_handoff.ResidentInputTerminated if access in {"failed", "cancelled"}
                             else KeyError if access == "deleted" else PermissionError)
                    with pytest.raises(error):
                        resident_handoff.observe_invocation(**handle, input_state=state)
                    continue
                assert resident_handoff.observe_invocation(**handle, input_state=state)["result"] is None
                patch.setattr(resident_handoff, "wait_for_input", lambda **_: state)
                patch.setattr(resident_handoff, "REPLY_VISIBILITY_RETRY_DELAYS", (0,))
                patch.setattr(resident_handoff.time, "sleep", lambda _: None)
                with pytest.raises(TimeoutError, match="reply projection observation"):
                    resident_handoff.resume_and_wait(**handle)
                visible = True
                result = resident_handoff.resume_and_wait(**handle)
                assert result["message_id"] == "input-1"
                assert result["message"] == "ready"
                assert result["response"] == {"done": True}


def test_cancel_resident_invocation_uses_matching_hatg_instruction(monkeypatch) -> None:
    """取消后台调用经 CancelInput 取消对应输入并返回其状态"""
    calls = []

    class ResidentService:
        @staticmethod
        def cancel_resident_service_input(*args):
            return None

    def call(actor, instruction, arguments, *, key=""):
        calls.append((instruction, arguments, actor))
        return {"input_state": {"message_id": arguments["message_id"], "state": "cancelled"}}

    monkeypatch.setattr(isa_client, "call", call)
    monkeypatch.setattr(
        resident_handoff,
        "get_society_service",
        lambda: SimpleNamespace(resident=ResidentService()),
    )

    result = resident_handoff.cancel_resident_invocation(
        "resident-1",
        "sample_admin",
        "message-1",
    )

    assert calls == [("CancelInput", {"resident_id": "resident-1", "message_id": "message-1"}, "sample_admin")]
    assert result == {"message_id": "message-1", "state": "cancelled"}


def test_wait_for_input_reconciles_terminal_state_after_quiet_heartbeat(
    monkeypatch,
) -> None:
    """等待输入时通知心跳没有事件也重新读取状态，发现已完成即返回"""
    states = iter(
        [
            {"message_id": "message-1", "state": "running"},
            {"message_id": "message-1", "state": "completed"},
        ]
    )

    class ResidentService:
        def get_resident_conversation_snapshot(self, *_args, **_kwargs):
            return {"cursor": 7}

        def get_resident_input_state(self, *_args, **_kwargs):
            return next(states)

    class NotificationHub:
        sequence = 11

        @staticmethod
        def cursor_has_gap(_cursor):
            return False

        @staticmethod
        def wait(cursor, **_kwargs):
            return [], cursor

    monkeypatch.setattr(resident_handoff, "ensure_notification_listener", lambda: None)
    monkeypatch.setattr(resident_handoff, "get_notification_hub", NotificationHub)
    monkeypatch.setattr(
        resident_handoff,
        "get_society_service",
        lambda: SimpleNamespace(resident=ResidentService()),
    )

    result = resident_handoff.wait_for_input(
        actor="portal",
        resident_id="resident-1",
        message_id="message-1",
    )

    assert result["state"] == "completed"


def test_resume_waits_for_completed_reply_projection(monkeypatch) -> None:
    """输入已完成但回复消息尚不可读时，resume_and_wait 重试直到读到回复"""
    reads = []
    state = {
        "message_id": "message-1",
        "state": "completed",
        "reply_message_ids": ["reply-1"],
    }

    class ResidentService:
        @staticmethod
        def get_resident_message(*_args):
            reads.append("message")
            if len(reads) == 1:
                return {}
            return {
                "message_id": "reply-1",
                "content_parts": [{"type": "text", "text": "ready"}],
            }

        @staticmethod
        def get_resident_input_state(*_args):
            return state

    monkeypatch.setattr(resident_handoff, "wait_for_input", lambda **_kwargs: state)
    monkeypatch.setattr(resident_handoff.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(
        resident_handoff,
        "get_society_service",
        lambda: SimpleNamespace(resident=ResidentService()),
    )

    result = resident_handoff.resume_and_wait(
        actor="portal",
        resident_id="resident-1",
        message_id="message-1",
    )

    assert result["message"] == "ready"
    assert reads == ["message", "message"]


def test_resume_keeps_polling_when_completed_state_has_no_reply_id_yet(
    monkeypatch,
) -> None:
    """已完成状态尚无回复 ID 或最终回复未落库时继续轮询，返回合并的回复文本"""
    state_reads = []
    sleeps = []
    initial_state = {
        "message_id": "message-1",
        "state": "completed",
        "reply_message_ids": [],
    }

    class ResidentService:
        @staticmethod
        def get_resident_message(*_args):
            return {
                "message_id": "reply-1",
                "content_parts": [{"type": "text", "text": "final reply"}],
            }

        @staticmethod
        def get_resident_input_state(*_args):
            state_reads.append("state")
            if len(state_reads) < 7:
                return initial_state
            return {
                **initial_state,
                "reply_message_ids": ["reply-1"],
            }

    monkeypatch.setattr(
        resident_handoff,
        "wait_for_input",
        lambda **_kwargs: initial_state,
    )
    monkeypatch.setattr(
        resident_handoff.time,
        "sleep",
        sleeps.append,
    )
    monkeypatch.setattr(
        resident_handoff,
        "get_society_service",
        lambda: SimpleNamespace(resident=ResidentService()),
    )

    result = resident_handoff.resume_and_wait(
        actor="portal",
        resident_id="resident-1",
        message_id="message-1",
    )

    assert result["message"] == "final reply"
    assert len(state_reads) == 7
    assert sleeps == list(resident_handoff.REPLY_VISIBILITY_RETRY_DELAYS[:7])

    final_reads = []
    partial_sleeps = []
    state = {
        "message_id": "message-1",
        "state": "completed",
        "reply_message_ids": ["reply-progress", "reply-final"],
    }

    class ResidentService:
        @staticmethod
        def get_resident_message(_resident_id, _actor, reply_id):
            if reply_id == "reply-progress":
                return {
                    "message_id": reply_id,
                    "content_parts": [{"type": "text", "text": "working"}],
                }
            final_reads.append("final")
            if len(final_reads) == 1:
                return {}
            return {
                "message_id": reply_id,
                "content_parts": [{"type": "text", "text": "final reply"}],
            }

        @staticmethod
        def get_resident_input_state(*_args):
            return state

    monkeypatch.setattr(resident_handoff, "wait_for_input", lambda **_kwargs: state)
    monkeypatch.setattr(resident_handoff.time, "sleep", partial_sleeps.append)
    monkeypatch.setattr(
        resident_handoff,
        "get_society_service",
        lambda: SimpleNamespace(resident=ResidentService()),
    )

    result = resident_handoff.resume_and_wait(
        actor="portal",
        resident_id="resident-1",
        message_id="message-1",
    )

    assert result["message"] == "working\nfinal reply"
    assert final_reads == ["final", "final"]
    assert partial_sleeps == [resident_handoff.REPLY_VISIBILITY_RETRY_DELAYS[0]]


def test_local_cancellation_checks_do_not_poll_snapshot_each_second(monkeypatch):
    """等待输入时每秒检查本地取消请求，但不每秒读取会话快照"""
    from unittest.mock import Mock
    now = [0.0]
    snapshot = Mock(return_value={'cursor': 0})
    states = iter([{'state': 'running'}, {'state': 'completed'}])
    service = SimpleNamespace(get_resident_conversation_snapshot=snapshot,
                              get_resident_input_state=lambda *a: next(states))
    class Hub:
        sequence = 0
        def cursor_has_gap(self, cursor):
            return False
        def wait(self, cursor, **kwargs):
            now[0] += kwargs['timeout']
            return [], cursor
    monkeypatch.setattr(resident_handoff.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(resident_handoff, 'ensure_notification_listener', lambda: None)
    monkeypatch.setattr(resident_handoff, 'get_notification_hub', Hub)
    monkeypatch.setattr(resident_handoff, 'get_society_service', lambda: SimpleNamespace(resident=service))
    cancelled = Mock(return_value=False)
    result = resident_handoff.wait_for_input(actor='alice', resident_id='resident_test', message_id='msg_test',
                                               cancellation_requested=cancelled)
    assert result['state'] == 'completed'
    assert cancelled.call_count == 15 and snapshot.call_count == 2


def test_observation_and_terminal_failures_have_shared_semantics(monkeypatch, subtests):
    """观察到失败或取消时抛出 ResidentInputTerminated，等回复超时不重新提交也不取消输入"""
    import pytest
    for state in ("failed", "cancelled"):
        with subtests.test(state=state):
            payload = {"state": state, "error": {"message": "authoritative failure"}}
            with pytest.raises(resident_handoff.ResidentInputTerminated) as error:
                resident_handoff.observe_invocation(actor="alice", resident_id="resident-1",
                    message_id="msg-1", input_state=payload)
            assert error.value.status == state
            assert error.value.input_state == payload

    monkeypatch.setattr(resident_handoff, "wait_for_input", lambda **_: {"state": "completed", "reply_message_ids": []})
    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: SimpleNamespace(resident=SimpleNamespace(
        get_resident_input_state=lambda *_: {"state": "completed", "reply_message_ids": []})))
    monkeypatch.setattr(resident_handoff.time, "sleep", lambda _: None)
    monkeypatch.setattr(resident_handoff, "submit_invocation", lambda **_: pytest.fail("observation submitted another input"))
    monkeypatch.setattr(resident_handoff, "cancel_resident_invocation", lambda *_: pytest.fail("observation cancelled input"))
    with pytest.raises(TimeoutError, match="reply projection observation"):
        resident_handoff.resume_and_wait(actor="alice", resident_id="resident-1", message_id="msg-1")
