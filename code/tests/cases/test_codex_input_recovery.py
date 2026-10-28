"""Interrupted native turns must not replace an undelivered request with continue.

:module: execution.harness
:importance: P1
:design: decision_3e8c2a7f91d4
"""
import json
from dataclasses import replace
from unittest import mock

import pytest

from management_portal.codex_app_server_protocol import CodexTurnInterrupted, CodexTurnResult
from management_portal.execution.backends import BackendContext, CodexBackend
from management_portal.execution.contracts import ExecutionRecoveryDeferred, InputMedia
from management_portal.execution.codex_rollout_history import read_codex_turn_input_recorded
from tests.cases.test_codex_recovery_budget import _invocation


def _event(kind, turn="original"):
    return {"type": "event_msg", "payload": {"type": kind, "turn_id": turn}}


def _input(text):
    return {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": text}]}}


def _write(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_delivery_evidence_requires_exact_terminal_turn(tmp_path, subtests):
    """Codex 恢复按完整原生轮次核验输入是否已记录"""
    start, end = _event("task_started"), _event("turn_aborted")
    for name, rows, expected in [
        ("before_input", [start, end], False),
        ("after_input", [start, _input("original task"), end], True),
        ("other_context", [start, _input("environment context"), end], None),
        ("active", [start, _input("original task")], None),
        ("missing_start", [_input("original task"), end], None),
        ("adjacent", [start, end, _event("task_started", "next"), _input("original task"), _event("task_complete", "next")], False),
        ("compaction", [start, {"type": "compacted", "payload": {"replacement_history": [_input("original task")]}}, end], False),
        ("rollback", [start, end, _event("thread_rolled_back")], None),
    ]:
        with subtests.test(name=name):
            path = tmp_path / "native.jsonl"
            _write(path, rows)
            assert read_codex_turn_input_recorded(path, "original", expected_prompt="original task") is expected
    assert read_codex_turn_input_recorded(tmp_path / "missing", "original") is None
    path.write_text(json.dumps(start) + "\n" + json.dumps(end))
    assert read_codex_turn_input_recorded(path, "original") is None


def test_recovery_resends_unrecorded_input_with_attachments_and_persists_choice(tmp_path, subtests):
    """Codex 未记录输入的中断重发原请求和附件"""
    for recorded in [False, True, None]:
        for durable in [False, True]:
            with subtests.test(recorded=recorded, durable=durable):
                invocation = _invocation()
                request = replace(invocation.request, input_media=(InputMedia("image", "local_path", "/tmp/original.png"),))
                state = {"original_turn_id": "original", "phase": "ready_to_continue", "continuation_prompt": "continue"} if durable else {}
                invocation = replace(invocation, request=request, recovery_checkpoint={"turn_id": "original", "provider_recovery_state": state})
                path = tmp_path / "native.jsonl"
                rows = [_event("task_started")]
                if recorded:
                    rows.append(_input(invocation.user_prompt))
                if recorded is not None:
                    rows.append(_event("turn_aborted"))
                _write(path, rows)
                registry, client = mock.Mock(), mock.Mock()
                registry.find_thread_rollout.return_value = path
                thread = invocation.session.backend_session_id
                client.resume_turn.side_effect = CodexTurnInterrupted(thread, "original", "interrupted")
                client.run_turn.return_value = CodexTurnResult(thread_id=thread, turn_id="recovered", response="done",
                                                             turn={"id": "recovered", "status": "completed"}, token_usage={})
                backend = CodexBackend(account_registry=registry)
                backend._resolve_client = mock.Mock(return_value=(mock.Mock(account_id="one"), client, thread, False))
                saved, events = [], []
                context = BackendContext(emit_event=lambda k, v: events.append((k, v)), cancellation_requested=lambda: False,
                                         set_interrupt_target=lambda *_a: None, set_provider_recovery_state=lambda s: saved.append(dict(s)))
                with mock.patch("management_portal.execution.backends.wait_before_continuation", return_value=True):
                    if recorded is None:
                        with pytest.raises(ExecutionRecoveryDeferred, match="input delivery"):
                            backend.execute(invocation, context)
                        client.run_turn.assert_not_called()
                        continue
                    assert backend.execute(invocation, context).output["message"] == "done"
                assert client.run_turn.call_args.args[1] == ("continue" if recorded else invocation.user_prompt)
                assert client.run_turn.call_args.kwargs["images"] == ([] if recorded else ["/tmp/original.png"])
                assert saved[-1]["original_input_recorded"] is recorded
                sent = [v for k, v in events if k == "recovery_input_sent"]
                assert sent[-1]["input_delivery"] == ("continued" if recorded else "resent")
