""":module: execution.providers"""
from __future__ import annotations

import json
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest import mock

from management_portal.kimi_code_client import (
    DEFAULT_MODEL,
    KimiCodeClient,
    KimiCodeTurnFailed,
    KimiCodeUnavailable,
)
from management_portal.execution import event_projection
from management_portal.portal_chat_workspace_projection import runtime_detail


def live_answer(text):
    return [
        {"type": "turn.started", "payload": {"agentId": "main", "turnId": 1}},
        {"type": "assistant.delta", "payload": {"agentId": "main", "turnId": 1, "delta": text}},
        {"type": "turn.ended", "payload": {"agentId": "main", "turnId": 1, "reason": "completed"}},
    ]


def _snapshot(
    *,
    seq: int,
    busy: bool,
    reason: str = "",
    messages: list[dict] | None = None,
    approvals: list[dict] | None = None,
    assistant_text: str = "working",
    thinking_text: str = "",
    running_tools: list[dict] | None = None,
    subagents: list[dict] | None = None,
) -> dict:
    return {
        "as_of_seq": seq,
        "session": {
            "busy": busy,
            "last_turn_reason": reason,
            "usage": {},
        },
        "messages": {"items": messages or [], "has_more": False},
        "in_flight_turn": {
            "assistant_text": assistant_text,
            "thinking_text": thinking_text,
            "running_tools": running_tools or [],
        }
        if busy
        else None,
        "subagents": subagents or [],
        "pending_approvals": approvals or [],
        "pending_questions": [],
    }


class ScriptedKimiCodeClient(KimiCodeClient):
    def __init__(
        self,
        snapshots: list[object],
        events_root: Path,
        persisted_messages: list[dict] | None = None,
    ) -> None:
        super().__init__("http://127.0.0.1:1/api/v1", poll_seconds=0.2, events_root=events_root)
        self.snapshots = deque(snapshots)
        self.persisted_messages = persisted_messages or []
        self.requests: list[tuple[str, str, dict | None]] = []

    def _open_event_stream(self, session_id):
        self.requests.append(("SUBSCRIBE", session_id, None))
        frames = list(getattr(self, "live_frames", []))
        class Stream:
            def drain(self, observe):
                while frames:
                    observe({"session_id": session_id, "epoch": "test", **frames.pop(0)})
            def close(self):
                pass
        return Stream()

    def _request(self, method, path, payload=None, **_kwargs):
        self.requests.append((method, path, payload))
        if path.endswith("/profile"):
            return {"usage": {}}
        if path.endswith("/snapshot"):
            value = self.snapshots.popleft()
            if isinstance(value, Exception):
                raise value
            return value
        if path.endswith("/prompts"):
            return {"prompt_id": "prompt-1", "user_message_id": "user-1"}
        if path.endswith("/messages"):
            return {"items": self.persisted_messages, "has_more": False}
        if "/approvals/" in path:
            return {"resolved": True}
        if "/questions/" in path:
            return {"dismissed": True}
        raise AssertionError(f"unexpected request: {method} {path}")


class KimiCodeClientTest(unittest.TestCase):
    def test_resumed_history_appends_the_missed_answer_for_its_prompt(self):
        """kimi 恢复历史仅补充当前提示词遗漏的答案

        :module: platform.core
        :design: decision_6b9e42f1c8a7
        :design-point:
        """
        client = ScriptedKimiCodeClient(
            [_snapshot(seq=9, busy=False, reason="completed")], self.events_root,
            persisted_messages=[
                {"id": "answer-1", "role": "assistant", "prompt_id": "prompt-1", "content": [{"type": "text", "text": "complete answer"}]},
                {"id": "answer-2", "role": "assistant", "prompt_id": "other-prompt", "content": [{"type": "text", "text": "unrelated answer"}]},
            ],
        )
        events = []
        with mock.patch("management_portal.kimi_code_client.time.sleep"):
            result = client.resume_turn(session_id="s", prompt_id="prompt-1", event_callback=events.append)
        self.assertEqual(result.response, "complete answer")
        items = [e["params"]["item"] for e in events if e["method"] == "item/completed"]
        self.assertEqual(items, [{"id": "kimi-recovered:s:prompt-1:answer-1", "type": "agentMessage",
                                 "phase": "final_answer", "text": "complete answer"}])

    def test_resumed_answer_appends_without_rewriting_live_items(self):
        """kimi 恢复答案以独立条目追加且不改写实时条目

        :module: platform.core"""
        client = ScriptedKimiCodeClient(
            [_snapshot(seq=9, busy=False, reason="completed")], self.events_root,
            persisted_messages=[{"id": "answer-1", "role": "assistant", "prompt_id": "prompt-1",
                                 "content": [{"type": "thinking", "thinking": "private reasoning"},
                                             {"type": "text", "text": "complete answer"}]}],
        )
        client.live_frames = live_answer("answer suffix")
        events = []
        result = client.resume_turn(session_id="s", prompt_id="prompt-1", event_callback=events.append)
        items = [e["params"]["item"] for e in events if e["method"] == "item/completed"]
        self.assertEqual([item["text"] for item in items], ["answer suffix", "complete answer"])
        self.assertNotEqual(items[0]["id"], items[1]["id"])
        self.assertEqual(result.response, "complete answer")

    def test_resumed_complete_live_answer_is_not_appended_again(self):
        """kimi 恢复时已有完整实时答案不再重复追加

        :module: platform.core"""
        client = ScriptedKimiCodeClient(
            [_snapshot(seq=9, busy=False, reason="completed")], self.events_root,
            persisted_messages=[{"id": "answer-1", "role": "assistant", "prompt_id": "prompt-1",
                                 "content": [{"type": "text", "text": "complete answer"}]}],
        )
        client.live_frames = live_answer("complete answer")
        events = []
        result = client.resume_turn(session_id="s", prompt_id="prompt-1", event_callback=events.append)
        items = [e["params"]["item"] for e in events if e["method"] == "item/completed"]
        self.assertEqual([item["text"] for item in items], ["complete answer"])
        self.assertEqual(result.response, "complete answer")

    def test_completed_turn_without_live_response_fails_without_history_recheck(self) -> None:
        """kimi 轮次完成却无实时回复时失败且不回查历史"""
        user_message = {"id": "user-1", "role": "user", "prompt_id": "prompt-1"}
        client = ScriptedKimiCodeClient([
            _snapshot(seq=0, busy=False),
            _snapshot(seq=4, busy=False, reason="completed", messages=[user_message]),
        ], self.events_root)
        events = []

        with mock.patch("management_portal.kimi_code_client.time.sleep"):
            with self.assertRaisesRegex(KimiCodeTurnFailed, "without a final response"):
                client.run_turn(
                    session_id="session-empty", prompt="hello", tools=[],
                    event_callback=events.append,
                )

        self.assertEqual(sum(path.endswith("/messages") for _, path, _ in client.requests), 0)
        self.assertFalse(any(event["method"] == "turn/completed" for event in events))

    def test_current_turn_streaming_response_is_retained_when_messages_lag(self) -> None:
        """kimi 消息列表滞后时保留当前轮流式回复"""
        user_message = {"id": "user-1", "role": "user", "prompt_id": "prompt-1"}
        client = ScriptedKimiCodeClient([
            _snapshot(seq=0, busy=False),
            _snapshot(seq=2, busy=True, messages=[user_message], assistant_text="current streamed answer"),
            _snapshot(seq=4, busy=False, reason="completed", messages=[user_message]),
        ], self.events_root)

        client.live_frames = [
            {"type": "turn.started", "payload": {"agentId": "main", "turnId": 1}},
            {"type": "assistant.delta", "payload": {"agentId": "main", "turnId": 1, "delta": "current streamed answer"}},
            {"type": "turn.ended", "payload": {"agentId": "main", "turnId": 1, "reason": "completed"}},
        ]

        with mock.patch("management_portal.kimi_code_client.time.sleep"):
            result = client.run_turn(session_id="session-streamed", prompt="hello", tools=[])

        self.assertEqual(result.response, "current streamed answer")
        self.assertEqual(client.requests[0][0], "SUBSCRIBE")

    def test_thinking_snapshots_are_not_an_ingestion_source(self) -> None:
        """kimi 思考快照不作为推理内容采集来源"""
        message = {
            "id": "assistant-1", "role": "assistant", "prompt_id": "prompt-1",
            "content": [
                {"type": "thinking", "thinking": "complete thought"},
                {"type": "text", "text": "done"},
            ],
        }
        client = ScriptedKimiCodeClient([
            _snapshot(seq=0, busy=False),
            _snapshot(seq=1, busy=True, thinking_text="complete"),
            _snapshot(seq=2, busy=True, thinking_text="complete thought", messages=[message]),
            _snapshot(seq=3, busy=False, reason="completed", messages=[message]),
        ], self.events_root)
        events = []
        client.live_frames = live_answer('done')
        with mock.patch("management_portal.kimi_code_client.time.sleep"):
            result = client.run_turn(
                session_id="session-one", prompt="hello", tools=[],
                event_callback=events.append,
            )
        thoughts = [
            event["params"]["item"] for event in events
            if event.get("method") == "item/completed"
            and event["params"]["item"].get("type") == "reasoning"
        ]
        self.assertEqual(thoughts, [])
        self.assertEqual(result.response, "done")


    def test_resumed_thinking_only_history_does_not_emit_reasoning(self):
        """kimi 恢复仅含思考的历史时不发出推理条目

        :module: platform.core"""
        client = ScriptedKimiCodeClient(
            [_snapshot(seq=3, busy=False, reason="completed")], self.events_root,
            persisted_messages=[{"role": "assistant", "content": [{"type": "thinking", "thinking": "history"}]}],
        )
        client.live_frames = live_answer("live answer")
        events = []
        result = client.resume_turn(session_id="s", prompt_id="prompt-1", event_callback=events.append)
        items = [event["params"]["item"] for event in events if event.get("method") == "item/completed"]
        self.assertEqual([item["type"] for item in items], ["agentMessage"])
        self.assertEqual(result.response, "live answer")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.events_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_journal(self, session_id: str, events: list[tuple[int, dict]]) -> None:
        rows = [
            {
                "kind": "event",
                "seq": seq,
                "envelope": {
                    "seq": seq,
                    "type": str(payload.get("type") or ""),
                    "payload": payload,
                },
            }
            for seq, payload in events
        ]
        (self.events_root / f"{session_id}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

    def test_create_session_uses_requested_model(self) -> None:
        """kimi 创建会话采用指定模型"""
        client = KimiCodeClient(events_root=self.events_root)
        with mock.patch.object(client, "_request", return_value={"id": "session-created"}) as request:
            result = client.create_session(cwd="/tmp", title="test", tools=[], model="kimi-k2.7-code")

        self.assertEqual(result["id"], "session-created")
        payload = request.call_args.args[2]
        self.assertEqual(payload["agent_config"]["model"], "kimi-k2.7-code")

    def test_resume_turn_keeps_the_checkpointed_prompt_identity(self):
        """kimi 恢复轮次保留检查点中的提示词标识"""
        snapshot = _snapshot(seq=9, busy=False, reason="completed")
        snapshot["session"]["agent_config"] = {"model": "kimi-k3"}
        client = ScriptedKimiCodeClient([snapshot], self.events_root)
        client.live_frames = live_answer("current answer")
        client.live_frames.insert(2, {"type": "turn.step.completed", "payload": {"agentId": "main", "turnId": 1, "usage": {"inputOther": 70}}})
        events = []
        result = client.resume_turn(session_id="s", prompt_id="prompt-current", event_callback=events.append)
        self.assertEqual(result.response, "current answer")
        samples = [event["params"]["tokenUsage"] for event in events if event["method"] == "thread/tokenUsage/updated"]
        self.assertEqual(samples, [{"context": {"inputTokens": 70}, "modelContextWindow": 1048576}])
        self.assertTrue(all(event["params"].get("turnId", "prompt-current") == "prompt-current" for event in events))

    def test_run_turn_resumes_after_transient_loss_and_reads_native_usage(self) -> None:
        """kimi 短暂断连后恢复同轮执行并读取原生用量"""
        session_id = "session_test-usage"
        messages = [
            {"id": "user-1", "role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"id": "assistant-1", "role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ]
        client = ScriptedKimiCodeClient([
            _snapshot(seq=5, busy=False),
            KimiCodeUnavailable("restart"),
            _snapshot(seq=6, busy=True, messages=messages[:1]),
            _snapshot(seq=9, busy=False, reason="completed", messages=messages),
        ], self.events_root)
        self._write_journal(session_id, [
            (4, {"type": "turn.step.completed", "usage": {"inputOther": 999}}),
            (7, {
                "type": "turn.step.completed",
                "usage": {
                    "inputOther": 10,
                    "inputCacheRead": 5,
                    "inputCacheCreation": 2,
                    "output": 3,
                },
            }),
        ])

        client.live_frames = live_answer('done')
        with mock.patch("management_portal.kimi_code_client.time.sleep"):
            result = client.run_turn(
                session_id=session_id,
                prompt="hello",
                tools=["Read"],
                permission_mode="manual",
                model=DEFAULT_MODEL,
            )

        self.assertEqual(result.response, "done")
        self.assertEqual(result.usage, {
            "input_tokens": 10,
            "cached_input_tokens": 7,
            "cache_creation_tokens": 2,
            "output_tokens": 3,
            "total_tokens": 20,
        })
        prompt_requests = [item for item in client.requests if item[1].endswith("/prompts")]
        self.assertEqual(len(prompt_requests), 1)
        self.assertEqual(prompt_requests[0][2]["model"], "kimi-k3")
        profile_request = next(item for item in client.requests if item[1].endswith("/profile"))
        self.assertEqual(profile_request[2]["agent_config"]["model"], "kimi-k3")


    def test_manual_mode_approves_only_allowed_tools(self) -> None:
        """kimi 手工模式仅批准允许的工具"""
        messages = [
            {"id": "user-1", "role": "user", "content": [{"type": "text", "text": "inspect"}]},
            {"id": "assistant-1", "role": "assistant", "content": [{"type": "text", "text": "finished"}]},
        ]
        approvals = [
            {"approval_id": "approval-read", "tool_name": "Read"},
            {"approval_id": "approval-write", "tool_name": "Write"},
        ]
        client = ScriptedKimiCodeClient([
            _snapshot(seq=0, busy=False),
            _snapshot(seq=3, busy=True, messages=messages[:1], approvals=approvals),
            _snapshot(seq=5, busy=False, reason="completed", messages=messages),
        ], self.events_root)

        client.live_frames = live_answer('finished')
        with mock.patch("management_portal.kimi_code_client.time.sleep"):
            result = client.run_turn(
                session_id="session_test-policy",
                prompt="inspect",
                tools=["Read", "Grep"],
                permission_mode="manual",
            )

        decisions = {
            path.rsplit("/", 1)[-1]: payload["decision"]
            for method, path, payload in client.requests
            if method == "POST" and "/approvals/" in path
        }
        self.assertEqual(result.response, "finished")
        self.assertEqual(decisions, {
            "approval-read": "approved",
            "approval-write": "rejected",
        })


    def test_history_answer_is_never_a_fallback_for_missing_live_events(self):
        """kimi 缺少实时事件时不以历史答案兜底"""
        message = {"id": "user-1", "role": "user"}
        answer = {"role": "assistant", "content": [{"type": "text", "text": "history only"}]}
        client = ScriptedKimiCodeClient([
            _snapshot(seq=0, busy=False),
            _snapshot(seq=4, busy=False, reason="completed", messages=[message, answer]),
        ], self.events_root, [message, answer])
        with mock.patch("management_portal.kimi_code_client.time.sleep"):
            with self.assertRaisesRegex(KimiCodeTurnFailed, "live event stream"):
                client.run_turn(session_id="s", prompt="hello", tools=[])
        self.assertFalse(any(path.endswith("/messages") for _, path, _ in client.requests))

    def test_failed_turn_finishes_without_a_busy_sample_and_reports_native_error(self) -> None:
        """kimi 未采样到忙碌状态也可识别失败并报告原生错误"""
        session_id = "session_test-failure"
        user_message = {
            "id": "user-1",
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
        }
        client = ScriptedKimiCodeClient([
            _snapshot(seq=0, busy=False),
            _snapshot(seq=4, busy=False, reason="failed", messages=[user_message]),
        ], self.events_root)
        self._write_journal(session_id, [
            (2, {"type": "turn.step.interrupted", "message": "Model not set"}),
        ])

        with mock.patch("management_portal.kimi_code_client.time.sleep"):
            with self.assertRaisesRegex(KimiCodeTurnFailed, "Model not set"):
                client.run_turn(
                    session_id=session_id,
                    prompt="hello",
                    tools=[],
                    permission_mode="yolo",
                )

    def test_snapshots_do_not_ingest_answers_tools_or_subagents(self) -> None:
        """kimi 快照不导入答案、工具或子 agent 活动"""
        session_id = "session_test-live"
        user_message = {
            "id": "user-1",
            "role": "user",
            "content": [{"type": "text", "text": "inspect"}],
        }
        assistant_message = {
            "id": "assistant-1",
            "role": "assistant",
            "content": [{"type": "text", "text": "Inspection complete"}],
        }
        tool = {
            "tool_call_id": "Bash_1",
            "name": "Bash",
            "args": {"command": "rg -n TODO src"},
            "description": "Searching source files",
            "display": {"kind": "command", "command": "rg -n TODO src"},
            "last_progress": {"kind": "stdout", "text": "src/app.py:12", "percent": 50},
        }
        subagent_running = {
            "id": "agent-1",
            "kind": "subagent",
            "description": "Inspect tests",
            "status": "running",
        }
        subagent_done = {**subagent_running, "status": "completed", "output_preview": "Tests inspected"}
        client = ScriptedKimiCodeClient([
            _snapshot(seq=0, busy=False),
            _snapshot(
                seq=2,
                busy=True,
                messages=[user_message],
                assistant_text="Inspecting",
                thinking_text="Need to inspect the repository",
                running_tools=[tool],
                subagents=[subagent_running],
            ),
            _snapshot(
                seq=3,
                busy=True,
                messages=[user_message],
                assistant_text="Inspecting the repository",
                thinking_text="Need to inspect the repository and tests",
                running_tools=[{**tool, "last_progress": {"kind": "stdout", "text": "2 matches", "percent": 100}}],
                subagents=[subagent_done],
            ),
            _snapshot(
                seq=5,
                busy=False,
                reason="completed",
                messages=[user_message, assistant_message],
                subagents=[subagent_done],
            ),
        ], self.events_root)
        events: list[dict] = []

        client.live_frames = live_answer('live only')
        with mock.patch("management_portal.kimi_code_client.time.sleep"):
            result = client.run_turn(
                session_id=session_id,
                prompt="inspect",
                tools=["Bash"],
                event_callback=events.append,
            )

        updates = [
            item["payload"] for item in events
            if item.get("type") == "event_msg" and item.get("payload", {}).get("type") == "agent_message"
        ]
        self.assertEqual(updates, [])
        self.assertFalse(any(event.get("method") in {"item/started", "subagent/event"} for event in events))
        self.assertEqual(result.response, "live only")
        self.assertEqual([e["params"]["item"]["text"] for e in events if e["method"] == "item/completed"], ["live only"])


    def test_kimi_runtime_details_show_step_text_and_tool_facts(self) -> None:
        """Kimi 步骤文字与工具调用进入运行明细

        Kimi Code 在步骤结束时一次报告完整的步骤文字，运行明细把它显示为一条更新；工具调用显示为工具行，进度显示为“工具进度”行并带百分比。
        """
        # Kimi Code reports a step's text once, complete, when the step ends.
        objects = [
            (2, {
                "timestamp": "2026-07-20T10:00:01Z",
                "method": "item/completed",
                "params": {
                    "turnId": "kimi-turn",
                    "item": {"id": "kimi:turn:assistant:0", "type": "agentMessage",
                             "text": "Inspecting the repository", "phase": "commentary"},
                },
            }),
            (3, {
                "timestamp": "2026-07-20T10:00:02Z",
                "method": "item/started",
                "params": {
                    "item": {
                        "id": "Grep_1",
                        "type": "toolCall",
                        "name": "Grep",
                        "description": "Searching for TODO in src",
                        "status": "inProgress",
                    },
                },
            }),
            (4, {
                "timestamp": "2026-07-20T10:00:03Z",
                "method": "item/progress",
                "params": {
                    "item": {
                        "id": "Grep_1",
                        "type": "toolCall",
                        "name": "Grep",
                        "progress": {"kind": "progress", "text": "2 matches", "percent": 100},
                        "status": "inProgress",
                    },
                },
            }),
        ]

        details = [runtime_detail(item) for item in event_projection.runtime_details_from_objects(
            objects,
            include_updates=True,
        )]

        updates = [item for item in details if item["kind"] == "update"]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["content"], "Inspecting the repository")
        self.assertIn("Tool: Grep", {item["title"] for item in details})
        progress = next(item for item in details if item["title"] == "Tool progress: Grep")
        self.assertEqual(progress["content"], "2 matches · 100%")
        self.assertEqual(progress["status"], "running")

    def test_execution_backend_events_render_codex_activity(self) -> None:
        """执行后端事件渲染 codex 命令活动及退出状态"""
        objects = [
            (1, {
                "created_at": "2026-07-29T02:53:00Z",
                "type": "runtime_started",
                "details": {"service_type": "agent_session"},
            }),
            (2, {
                "created_at": "2026-07-29T02:53:01Z",
                "type": "provider_event",
                "details": {
                    "method": "item/started",
                    "params": {
                        "startedAtMs": 1785293581000,
                        "item": {
                            "id": "command-1",
                            "type": "commandExecution",
                            "command": "pytest -q tests/test_execution_layer.py",
                            "status": "inProgress",
                        },
                    },
                },
            }),
            (3, {
                "created_at": "2026-07-29T02:53:02Z",
                "type": "provider_event",
                "details": {
                    "method": "item/completed",
                    "params": {
                        "completedAtMs": 1785293582000,
                        "item": {
                            "id": "command-1",
                            "type": "commandExecution",
                            "command": "pytest -q tests/test_execution_layer.py",
                            "status": "completed",
                            "exitCode": 0,
                        },
                    },
                },
            }),
        ]

        details = [runtime_detail(item) for item in event_projection.runtime_details_from_objects(
            objects,
            include_updates=True,
        )]

        self.assertEqual(
            [item["title"] for item in details],
            ["Runtime started", "Command started", "Command finished"],
        )
        self.assertEqual(details[1]["status"], "running")
        self.assertIn("pytest -q", details[1]["content"])
        self.assertEqual(details[2]["status"], "completed")
        self.assertIn("exit 0", details[2]["content"])

    def test_provider_event_stream_keeps_live_reasoning_updates_plan_and_errors(self) -> None:
        """Provider 事件流保留实时推理、更新、计划和重连错误"""
        objects = [
            (1, {
                "created_at": "2026-07-29T03:00:00Z",
                "type": "provider_event",
                "details": {
                    "method": "item/started",
                    "params": {
                        "item": {
                            "id": "reasoning-1",
                            "type": "reasoning",
                        },
                    },
                },
            }),
            (2, {
                "created_at": "2026-07-29T03:00:01Z",
                "type": "provider_event",
                "details": {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "itemId": "message-1",
                        "delta": "正在检查",
                    },
                },
            }),
            (3, {
                "created_at": "2026-07-29T03:00:02Z",
                "type": "provider_event",
                "details": {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "itemId": "message-1",
                        "delta": "执行状态",
                    },
                },
            }),
            (4, {
                "created_at": "2026-07-29T03:00:03Z",
                "type": "provider_event",
                "details": {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "id": "message-1",
                            "type": "agentMessage",
                            "text": "正在检查执行状态。",
                        },
                    },
                },
            }),
            (5, {
                "created_at": "2026-07-29T03:00:04Z",
                "type": "provider_event",
                "details": {
                    "method": "turn/plan/updated",
                    "params": {
                        "turnId": "turn-1",
                        "explanation": "验证实时投影",
                        "plan": [{
                            "step": "检查 transcript",
                            "status": "inProgress",
                        }],
                    },
                },
            }),
            (6, {
                "created_at": "2026-07-29T03:00:05Z",
                "type": "provider_event",
                "details": {
                    "method": "error",
                    "params": {
                        "error": {"message": "Reconnecting... 1/5"},
                        "willRetry": True,
                    },
                },
            }),
        ]

        details = [runtime_detail(item) for item in event_projection.runtime_details_from_objects(
            objects,
            include_updates=True,
        )]

        reasoning = [item for item in details if item["kind"] == "reasoning"]
        updates = [item for item in details if item["kind"] == "update"]
        self.assertEqual(len(reasoning), 1)
        self.assertEqual(reasoning[0]["status"], "running")
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["content"], "正在检查执行状态。")
        self.assertEqual(updates[0]["status"], "completed")
        self.assertIn("Plan updated", {item["title"] for item in details})
        reconnecting = next(item for item in details if item["title"] == "Reconnecting")
        self.assertEqual(reconnecting["status"], "running")
        self.assertIn("1/5", reconnecting["content"])

    def test_current_app_server_activity_item_types_all_render(self) -> None:
        """当前应用服务各类活动条目均渲染为运行状态"""
        items = [
            {
                "id": "file-1",
                "type": "fileChange",
                "changes": [{
                    "kind": {"type": "update"},
                    "path": "/tmp/example.py",
                }],
            },
            {
                "id": "search-1",
                "type": "webSearch",
                "query": "Portal ExecutionService",
            },
            {
                "id": "image-1",
                "type": "imageView",
                "path": "/tmp/example.png",
            },
            {
                "id": "generation-1",
                "type": "imageGeneration",
                "status": "in_progress",
            },
            {
                "id": "compact-1",
                "type": "contextCompaction",
            },
            {
                "id": "mcp-1",
                "type": "mcpToolCall",
                "server": "openaiDeveloperDocs",
                "tool": "search_openai_docs",
                "status": "inProgress",
            },
        ]

        for line_number, item in enumerate(items, start=1):
            with self.subTest(item_type=item["type"]):
                details = [runtime_detail(item) for item in event_projection.runtime_details_from_objects(
                    [(
                        line_number,
                        {
                            "created_at": "2026-07-29T03:00:00Z",
                            "type": "provider_event",
                            "details": {
                                "method": "item/started",
                                "params": {"item": item},
                            },
                        },
                    )],
                    include_updates=True,
                )]
                self.assertEqual(len(details), 1)
                self.assertEqual(details[0]["status"], "running")

        mcp_details = [runtime_detail(item) for item in event_projection.runtime_details_from_objects(
            [(
                1,
                {
                    "type": "provider_event",
                    "details": {
                        "method": "item/started",
                        "params": {"item": items[-1]},
                    },
                },
            )],
            include_updates=True,
        )]
        self.assertEqual(
            mcp_details[0]["title"],
            "Tool: openaiDeveloperDocs.search_openai_docs",
        )


if __name__ == "__main__":
    unittest.main()
