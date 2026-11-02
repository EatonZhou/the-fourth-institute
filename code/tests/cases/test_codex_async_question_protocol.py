""":module: execution.service
:design: decision_1e2eb869bc10

Codex 0.166's native async handler emits an AgentMessage with id=call_id,
phase=final_answer, delivery=async and questions, before accepted:true.
Source: openai/codex rust-v0.166.0 core/src/tools/handlers/request_user_input_async.rs.
The installed experimental ServerNotification schema confirms the camelCase wire.
"""
from __future__ import annotations

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from management_portal.codex_app_server_client import CodexAppServerClient
from management_portal.codex_app_server_protocol import TurnWaiter
from management_portal.execution.codex_backend import CodexBackend
from management_portal.execution.codex_rollout_history import read_codex_turn_async_message_ids
from management_portal.execution.contracts import ExecutionRecoveryDeferred
from management_portal.execution.event_projection import execution_outputs_from_events


QUESTIONS = [{"title": "选择检查范围？", "options": ["定向", "全部"]}]


def _item(identity="call-question", text="", **fields):
    return {"type": "agentMessage", "id": identity, "text": text,
            "delivery": "async", "questions": deepcopy(QUESTIONS), **fields}


def _start(identity="turn-one"):
    return {"type": "event_msg", "payload": {"type": "task_started", "turn_id": identity}}


def _async_records(identity="call-question", accepted=True):
    return [
        {"type": "response_item", "payload": {
            "type": "function_call", "name": "request_user_input_async", "call_id": identity,
            "arguments": json.dumps({"questions": QUESTIONS}, ensure_ascii=False)}},
        {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": identity,
            "output": json.dumps({"accepted": accepted})}},
    ]


def _ordinary(identity="msg-final"):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "assistant", "id": identity, "phase": "final_answer",
        "content": [{"type": "output_text", "text": "等待你的回答"}]}}


def _backend(tmp_path, records):
    path = tmp_path / "native.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    registry = Mock()
    registry.find_thread_rollout.return_value = path
    return CodexBackend(account_registry=registry), path


@pytest.mark.parametrize("text", ["", "选择检查范围？\n1. 定向\n2. 全部"])
@pytest.mark.parametrize("status", ["inProgress", "completed"])
def test_native_async_card_live_and_snapshot_keep_one_call_identity(tmp_path, text, status):
    backend, _ = _backend(tmp_path, [_start(), *_async_records(), _ordinary()])
    client = CodexAppServerClient(tmp_path / "unused.sock")
    events = []
    waiter = TurnWaiter("thread-one", turn_id="turn-one", event_callback=events.append,
                        turn_snapshot_transform=lambda turn: backend._canonical_replay_turn("zero", "thread-one", turn))
    client.work.turns["thread-one"] = waiter
    observed = _item(text=text)
    try:
        params = {"threadId": "thread-one", "turnId": "turn-one", "completedAtMs": 1, "item": observed}
        client.events.dispatch_notification("item/completed", params)
        client.events.dispatch_notification("item/completed", params)
        assert len(events) == 1
        assert not waiter.unphased_items
        assert events[0]["params"]["item"]["phase"] == "final_answer"
        assert not waiter.final_answer_seen
        assert "phase" not in observed
        snapshot = {"id": "turn-one", "status": status, "items": [
            _item("item-2", text=text),
            {"type": "agentMessage", "id": "item-3", "text": "等待你的回答", "phase": "final_answer"},
        ]}
        turn = client.follower.canonical_turn(waiter, snapshot)
        assert [item["id"] for item in turn["items"]] == ["call-question", "msg-final"]
        client.follower.replay_completed_items(waiter, turn)
        client.follower.replay_completed_items(waiter, turn)
        assert [event["params"]["item"]["id"] for event in events] == ["call-question", "msg-final"]
        # A fresh follower observes the same question identity after reconnect.
        reloaded = []
        fresh = TurnWaiter("thread-one", turn_id="turn-one", event_callback=reloaded.append)
        client.follower.replay_completed_items(fresh, turn)
        outputs = execution_outputs_from_events([
            {"sequence": index, "execution_id": "execution-" + "1" * 24,
             "type": "provider_event", "details": event}
            for index, event in enumerate(reloaded, 1)
        ])
        assert len(outputs) == 2
        assert outputs[0]["questions"][0]["title"] == QUESTIONS[0]["title"]
        assert outputs[0]['output_id'] != outputs[1]['output_id']
        from management_portal.codex_app_server_protocol import codex_final_agent_response
        assert codex_final_agent_response(turn, []) == '等待你的回答'
        assert reloaded[0]["params"]["item"]["id"] == "call-question"
        turn["items"][0]["questions"][0]["title"] = "changed"
        assert snapshot["items"][0]["questions"] == QUESTIONS
    finally:
        client.close()


def test_async_call_identity_does_not_inject_unobserved_question_content(tmp_path):
    backend, _ = _backend(tmp_path, [_start(), *_async_records(), _ordinary()])
    turn = backend._canonical_replay_turn("zero", "thread-one", {
        "id": "turn-one", "status": "completed", "items": [
            {"type": "agentMessage", "id": "item-1", "text": "等待你的回答", "phase": "final_answer"}]})
    assert [item["id"] for item in turn["items"]] == ["msg-final"]
    assert not turn["items"][0]["questions"]


def test_async_snapshot_subset_preserves_exact_ids_without_matching_content(tmp_path):
    backend, _ = _backend(tmp_path, [_start(), *_async_records("call-unobserved"), *_async_records()])
    turn = backend._canonical_replay_turn("zero", "thread-one", {
        "id": "turn-one", "status": "completed", "items": [_item()]})
    assert [item["id"] for item in turn["items"]] == ["call-question"]


def test_mixed_native_and_display_ids_do_not_reassign_known_async_identity(tmp_path):
    backend, _ = _backend(tmp_path, [_start(), *_async_records("call-first"), *_async_records("call-second")])
    turn = backend._canonical_replay_turn("zero", "thread-one", {
        "id": "turn-one", "status": "completed", "items": [_item("call-second"), _item("item-9")]})
    assert [item["id"] for item in turn["items"]] == ["call-second", "call-first"]


def test_unobserved_async_receipt_cannot_disambiguate_a_display_only_subset(tmp_path):
    backend, _ = _backend(tmp_path, [_start(), *_async_records("call-first"), *_async_records("call-second")])
    with pytest.raises(ExecutionRecoveryDeferred):
        backend._canonical_replay_turn("zero", "thread-one", {
            "id": "turn-one", "status": "completed", "items": [_item("item-9")]})


@pytest.mark.parametrize("records", [
    [_start(), *_async_records(accepted=False)],
    [_start("previous"), *_async_records(), _start()],
    [_start(), _async_records()[0]],
    [_start(), *_async_records(), {"type": "event_msg", "payload": {"type": "thread_rolled_back", "num_turns": 1}}],
])
def test_async_unverified_identity_defers_instead_of_inventing_card(tmp_path, records):
    backend, _ = _backend(tmp_path, records)
    with pytest.raises(ExecutionRecoveryDeferred):
        backend._canonical_replay_turn("zero", "thread-one", {
            "id": "turn-one", "status": "completed", "items": [_item("item-7")]})


def test_identity_reader_only_returns_accepted_async_ids_in_exact_turn(tmp_path):
    backend, path = _backend(tmp_path, [
        _start("previous"), *_async_records("call-old"), _start(),
        *_async_records("call-first"), *_async_records("call-second"),
        _start("next"), *_async_records("call-next"),
    ])
    assert read_codex_turn_async_message_ids(path, "turn-one") == ("call-first", "call-second")
    assert read_codex_turn_async_message_ids(path, "missing") is None


def test_unphased_plain_message_keeps_existing_reconciliation(tmp_path):
    client = CodexAppServerClient(tmp_path / "unused.sock")
    events = []
    waiter = TurnWaiter("thread-one", turn_id="turn-one", event_callback=events.append,
                        turn_snapshot_transform=lambda turn: turn)
    client.work.turns["thread-one"] = waiter
    try:
        client.events.dispatch_notification("item/completed", {
            "threadId": "thread-one", "turnId": "turn-one",
            "item": {"type": "agentMessage", "id": "msg-one", "text": "普通消息"}})
        assert not events
        assert set(waiter.unphased_items) == {"msg-one"}
    finally:
        client.close()
