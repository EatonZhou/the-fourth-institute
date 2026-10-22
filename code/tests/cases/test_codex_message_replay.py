""":design: decision_3e8c2a7f91d4"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from unittest import mock

import pytest

from management_portal.codex_app_server_client import CodexAppServerClient, _TurnWaiter
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.backends import BackendContext, BackendInvocation, CodexBackend
from management_portal.execution.codex_recovery import CodexRecoveryExhausted
from management_portal.execution.contracts import ExecutionRecoveryDeferred, ExecutionRequest, SessionOptions
from management_portal.execution.session_manager import RuntimeSession
from management_portal.execution.event_projection import execution_outputs_from_events


def _start(turn: str) -> dict:
    return {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn}}


def _message(identity: str, text: str = "同样的中间消息", phase: str = "commentary") -> dict:
    return {"type": "response_item", "payload": {
        "type": "message", "role": "assistant", "id": identity, "phase": phase,
        "content": [{"type": "output_text", "text": text}],
    }}


def _snapshot(turn: str = "turn-one", count: int = 1) -> dict:
    return {"id": turn, "status": "completed", "items": [
        {"type": "agentMessage", "id": f"item-{45 + index}",
         "text": "同样的中间消息", "phase": "commentary"}
        for index in range(count)
    ]}


def _backend(path: Path):
    registry = mock.Mock()
    registry.find_thread_rollout.return_value = path
    return CodexBackend(account_registry=registry)


def _write(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def _review_item(kind: str, identity: str, **fields) -> dict:
    return {"type": "event_msg", "payload": {
        "type": "item_completed", "thread_id": "thread-one", "turn_id": "review-parent",
        "item": {"type": kind, "id": identity, **fields},
    }}


def _review_records() -> list[dict]:
    # De-identified shape observed in the native inline-review rollout: the
    # parent has mode items and a final, while task_started belongs to a child.
    return [
        _start("previous"), _message("previous-final", "审查完成", "final_answer"),
        _review_item("EnteredReviewMode", "review-entered"),
        _start("review-child"), _message("child-final", "审查完成", "final_answer"),
        _review_item("ExitedReviewMode", "review-exited", review_output={"findings": []}),
        _review_item("AgentMessage", "msg_review_native", content=[{"type": "Text", "text": "审查完成"}]),
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "review-parent"}},
        _start("next"), _message("next-final", "审查完成", "final_answer"),
    ]


def test_inline_review_replays_only_the_native_outer_message_once(tmp_path):
    path = tmp_path / "rollout.jsonl"
    records = _review_records()
    # Repeated completed receipts retain one native identity, not two Finals.
    records.insert(7, deepcopy(records[6]))
    _write(path, records)
    backend = _backend(path)
    client = CodexAppServerClient(tmp_path / "unused.sock")
    events = []
    client.read_turn = mock.Mock(return_value=_snapshot("review-parent"))
    waiter = _TurnWaiter(thread_id="thread-one", turn_id="review-parent", event_callback=events.append,
                        turn_snapshot_transform=lambda turn: backend._canonical_replay_turn("zero", "thread-one", turn))
    try:
        for _ in range(2):
            assert client._read_terminal_turn(waiter)["status"] == "completed"
        assert [event["params"]["item"]["id"] for event in events] == ["msg_review_native"]
        outputs = execution_outputs_from_events([
            {"sequence": index, "execution_id": "execution-" + "1" * 24,
             "type": "provider_event", "details": event}
            for index, event in enumerate(events, 1)
        ])
        assert len(outputs) == 1
        assert outputs[0]["text"] == "审查完成"
        assert events[0]["params"]["item"]["phase"] == "final_answer"
    finally:
        client.close()


@pytest.mark.parametrize("fault", [
    "missing-enter", "missing-exit", "missing-terminal", "missing-id", "wrong-thread",
    "wrong-turn", "partial-message", "conflicting-id", "rollback", "bounded",
])
def test_inline_review_without_exact_durable_receipts_defers(tmp_path, monkeypatch, fault):
    path = tmp_path / "rollout.jsonl"
    records = _review_records()
    if fault.startswith("missing-"):
        index = {"missing-enter": 2, "missing-exit": 5, "missing-terminal": 7}.get(fault)
        if index is not None:
            records.pop(index)
        else:
            records[6]["payload"]["item"].pop("id")
    elif fault in {"wrong-thread", "wrong-turn"}:
        records[6]["payload"]["thread_id" if fault == "wrong-thread" else "turn_id"] = "unrelated"
    elif fault == "conflicting-id":
        changed = deepcopy(records[6]); changed["payload"]["item"]["content"][0]["text"] = "冲突内容"
        records.insert(7, changed)
    elif fault == "rollback":
        records.append({"type": "event_msg", "payload": {"type": "thread_rolled_back", "num_turns": 1}})
    elif fault == "bounded":
        monkeypatch.setattr("management_portal.execution.codex_rollout_history.MAX_ACTIVE_HISTORY_SCAN_BYTES", 16)
    if fault == "partial-message":
        # The last record has no durable newline; no replacement identity may
        # be inferred from the turn snapshot's display item.
        _write(path, records[:6])
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(records[6]))
    else:
        _write(path, records)
    with pytest.raises(ExecutionRecoveryDeferred):
        _backend(path)._canonical_replay_turn("zero", "thread-one", _snapshot("review-parent"))


def test_restarted_worker_replays_native_message_identity_without_text_dedup_contracts(tmp_path, subtests, isolated_portal_case):
    """新 Worker 按原生消息 ID 重放消息，文本相同的消息不被去重

    :design-point:"""
    with subtests.test(contract='test_restarted_worker_replays_native_message_identity_without_text_dedup'), isolated_portal_case() as case_path:
        tmp_path = case_path
        path = tmp_path / "rollout.jsonl"
        _write(path, [_start("previous"), _message("msg_old"), _start("turn-one"),
                      _message("msg_native_one"), _message("msg_native_two"),
                      _start("next"), _message("msg_next")])
        backend = _backend(path)
        client = CodexAppServerClient(tmp_path / "unused.sock")
        events: list[dict] = []
        transform = lambda turn: backend._canonical_replay_turn("zero", "thread-one", turn)
        client.read_turn = mock.Mock(return_value=_snapshot(count=2))

        # First worker saw the real notification. A replacement has no waiter cache.
        events.append({"method": "item/completed", "params": {"turnId": "turn-one", "item": {
            "id": "msg_native_one", "type": "agentMessage", "phase": "commentary",
            "text": "同样的中间消息",
        }}})
        for status in ("inProgress", "completed"):
            client.read_turn.return_value = {**_snapshot(count=2), "status": status}
            waiter = _TurnWaiter(thread_id="thread-one", turn_id="turn-one",
                                 event_callback=events.append, turn_snapshot_transform=transform)
            client._read_terminal_turn(waiter)
        outputs = execution_outputs_from_events([
            {"sequence": index, "execution_id": "execution-" + "1" * 24,
             "type": "provider_event", "details": event}
            for index, event in enumerate(events, 1)
        ])
        assert len(outputs) == 2
        assert outputs[0]["text"] == outputs[1]["text"]
        assert outputs[0]["output_id"] != outputs[1]["output_id"]
        assert {event["params"]["item"]["id"] for event in events} == {
            "msg_native_one", "msg_native_two",
        }
        backend.account_registry.find_thread_rollout.assert_called_with(
            "zero", "thread-one", include_archived=True,
        )
    with subtests.test(contract='test_replay_keeps_final_and_ignores_compaction_and_user_copies'), isolated_portal_case() as case_path:
        tmp_path = case_path
        path = tmp_path / "rollout.jsonl"
        user = _message("user")
        user["payload"]["role"] = "user"
        _write(path, [_start("turn-one"), user,
                      {"type": "compacted", "payload": {"replacement_history": [_message("old")["payload"]]}},
                      _message("msg_final", "完成", "final_answer")])
        turn = _backend(path)._canonical_replay_turn("zero", "thread-one", _snapshot())
        assert turn["items"] == [{"id": "msg_final", "type": "agentMessage", "phase": "final_answer", "text": "完成", "questions": []}]
    with subtests.test(contract='test_missing_replay_identity_uses_persisted_budget_without_resending'), isolated_portal_case() as case_path:
        tmp_path = case_path
        request, session = _request_session()
        saved = {}
        client = mock.Mock()

        def read_snapshot(*args, **kwargs):
            kwargs["turn_snapshot_transform"](_snapshot())
            pytest.fail("Unavailable identities must not be acknowledged as replayed")

        client.run_turn.side_effect = read_snapshot
        client.resume_turn.side_effect = read_snapshot
        for attempt in range(4):
            backend = _backend(tmp_path / "not-yet-durable.jsonl")
            backend._resolve_client = mock.Mock(return_value=(
                mock.Mock(account_id="zero"), client, "thread-one", False,
            ))
            invocation = BackendInvocation(
                request=request, session=session, user_prompt=request.prompt,
                system_context="", attach_system_context=False, is_new_backend_session=False,
                recovery_checkpoint=({"turn_id": "turn-one", "provider_recovery_state": deepcopy(saved)}
                                     if attempt else {}),
            )

            def save(state):
                saved.clear()
                saved.update(deepcopy(state))

            with pytest.raises(ExecutionRecoveryDeferred if attempt < 3 else CodexRecoveryExhausted):
                backend.execute(invocation, BackendContext(
                    emit_event=lambda *_args: None, cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None, set_provider_recovery_state=save,
                ))
            assert saved["recovery_budget"]["attempts"] == min(attempt + 1, 3)

        assert client.run_turn.call_count == 1
        assert client.resume_turn.call_count == 3
        assert all(call.args[:2] == ("thread-one", "turn-one") for call in client.resume_turn.call_args_list)
    with subtests.test(contract='test_replacement_workers_preserve_persisted_trace_and_equal_text_messages'), isolated_portal_case() as case_path:
        tmp_path = case_path
        path = tmp_path / "rollout.jsonl"
        _write(path, [_start("turn-one"), _message("msg_native_one"),
                      _message("msg_native_two"), _message("msg_final", "完成", "final_answer")])
        request, session = _request_session()
        store = AgentDataStore()
        client = CodexAppServerClient(tmp_path / "unused.sock")
        client.read_turn = mock.Mock(return_value=_snapshot(count=3))
        backend = _backend(path)
        first_ids = []
        for worker in range(3):
            # No in-memory projector or waiter survives the worker replacement.
            projector = AgentDataProjector(store)
            projector.begin(request, session, created_at="2026-09-07T04:19:52.738Z")
            def observe(event):
                projector.observe(
                    provider="codex", backend_route_id="zero", backend_session_id="thread-one",
                    execution_id=request.execution_id, event=event,
                )
                # The ordered Conversation consumer delivers captured messages.
                from management_portal.execution.agent_data_store import stable_segment_id
                from tests.cases.test_agent_data_projection import _deliver_captured_message
                item = event.get("params", {}).get("item", {})
                if item.get("type") == "agentMessage":
                    _deliver_captured_message(store, request.agent_id, stable_segment_id(
                        "provider-model", request.execution_id, item["id"], item["phase"],
                    ))
            if worker == 0:
                observe({"method": "item/completed", "params": {"turnId": "turn-one", "item": {
                    "id": "msg_native_one", "type": "agentMessage", "phase": "commentary",
                    "text": "同样的中间消息",
                }}})
                first_ids = store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))
            else:
                client.read_turn.return_value = {
                    **_snapshot(count=3), "status": "inProgress" if worker == 1 else "completed",
                }
                client._read_terminal_turn(_TurnWaiter(
                    thread_id="thread-one", turn_id="turn-one", event_callback=observe,
                    turn_snapshot_transform=lambda turn: backend._canonical_replay_turn("zero", "thread-one", turn),
                ))
        trace_ids = store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))
        assert trace_ids[:len(first_ids)] == first_ids
        messages = [store.segment(identity) for identity in trace_ids]
        assert [message["content"] for message in messages if message["segment_type"] == "model"] == [
            "同样的中间消息", "同样的中间消息", "完成",
        ]


def test_an_empty_final_answer_does_not_hold_a_completed_turn_replay(tmp_path):
    """交接后重放已完成的 Codex 轮次时，空的最终回答不阻塞重放

    rollout 不为没有文字和提问的助手消息保留身份；交接后重放已完成轮次时丢弃这类空消息，其余消息照常按 rollout 身份重放，不再因等不到身份而耗尽恢复次数。

    A steward told not to reply ends with an empty final answer (10-09).

    The rollout keeps no identity for it, so a handover replay must not wait
    for one: the turn's real messages replay and the empty one is dropped.

    :design-point:
    """
    path = tmp_path / "rollout.jsonl"
    _write(path, [_start("turn-one"), _message("msg_native_one", "我会整理指引"),
                  _message("msg_empty_final", "", "final_answer")])
    snapshot = {"id": "turn-one", "status": "completed", "items": [
        {"type": "agentMessage", "id": "item-1", "text": "我会整理指引", "phase": "commentary"},
        {"type": "commandExecution", "id": "tool-1", "status": "completed"},
        {"type": "agentMessage", "id": "item-2", "text": "", "phase": "final_answer"},
    ]}
    turn = _backend(path)._canonical_replay_turn("zero", "thread-one", snapshot)
    assert [item["id"] for item in turn["items"]] == ["msg_native_one", "tool-1"]
    only_empty = {"id": "turn-one", "status": "completed", "items": [snapshot["items"][2]]}
    assert _backend(path)._canonical_replay_turn("zero", "thread-one", only_empty)["items"] == []


def test_uncommitted_message_identity_defers_replay_without_guessing(tmp_path, monkeypatch, subtests, isolated_portal_case):
    """消息 ID 尚未完整写入 rollout 时推迟重放，不猜测对应关系

    :design-point:"""
    for failure in ["missing", "partial-line", "wrong-turn", "bounded", "missing-id"]:
        with subtests.test(failure=failure), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                tmp_path = case_path
                path = tmp_path / "rollout.jsonl"
                if failure != "missing":
                    record = _message("" if failure == "missing-id" else "msg_native")
                    _write(path, [_start("wrong" if failure == "wrong-turn" else "turn-one")])
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(record) + ("" if failure == "partial-line" else "\n"))
                if failure == "bounded":
                    monkeypatch.setattr("management_portal.execution.codex_rollout_history.MAX_ACTIVE_HISTORY_SCAN_BYTES", 16)
                with pytest.raises(ExecutionRecoveryDeferred):
                    _backend(path)._canonical_replay_turn("zero", "thread-one", _snapshot())


def test_active_replay_uses_only_durable_messages_and_waits_for_partial_output(tmp_path, subtests, isolated_portal_case):
    """进行中轮次只重放已写入 rollout 的消息，未写完的输出等写完再发出

    :design-point:"""
    for native_ready in [False, True]:
        with subtests.test(native_ready=native_ready), isolated_portal_case() as case_path:
            tmp_path = case_path
            path = tmp_path / "rollout.jsonl"
            if native_ready:
                _write(path, [_start("turn-one"), _message("msg_native_one")])
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(_message("msg_native_two")))
            turn = {**_snapshot(count=2), "status": "inProgress"}
            tool = {"id": "tool-running", "type": "commandExecution", "status": "inProgress"}
            turn["items"].append(tool)
            backend = _backend(path)
            client = CodexAppServerClient(tmp_path / "unused.sock")
            events = []
            waiter = _TurnWaiter(
                thread_id="thread-one", turn_id="turn-one", event_callback=events.append,
                turn_snapshot_transform=lambda snapshot: backend._canonical_replay_turn("zero", "thread-one", snapshot),
            )
            client.read_turn = mock.Mock(return_value=turn)
            for _attempt in range(2):
                assert client._read_terminal_turn(waiter) is None
            assert [event["params"]["item"]["id"] for event in events] == (["msg_native_one"] if native_ready else [])
            _write(path, [_start("turn-one"), _message("msg_native_one"), _message("msg_native_two")])
            tool["status"] = "completed"
            assert client._read_terminal_turn(waiter) is None
            assert [event["params"]["item"]["id"] for event in events] == ["msg_native_one", "msg_native_two", tool["id"]]
            client.read_turn.return_value = {**turn, "status": "completed"}
            assert client._read_terminal_turn(waiter)["status"] == "completed"
            assert len(events) == 3
            client.close()


def _request_session():
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="continue", service_type="agent_session", provider="codex",
        operation="turn", cwd="/tmp", agent_id="resident_111111111111",
        session=SessionOptions(mode="persistent", binding_id="resident:replay", backend_route_id="zero"),
    )
    session = RuntimeSession(mode="persistent", binding_id=request.session.binding_id,
                             backend_session_id="thread-one", backend_route_id="zero",
                             is_new_backend_session=False)
    return request, session
