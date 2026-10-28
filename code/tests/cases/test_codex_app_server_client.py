""":module: execution.providers"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import tempfile
import threading
import time
import unittest
import pytest
from pathlib import Path
from unittest import mock


from management_portal.codex_app_server_client import CodexAppServerClient
from management_portal.codex_app_server_protocol import (
    CodexAppServerError,
    CodexThreadNotFound,
    CodexTurnAuthenticationError,
    CodexCompactionInProgress,
    CodexThreadWriterBusy,
    CodexTurnContinuationRequired,
    CodexTurnConnectionLost,
    CodexTurnInterrupted,
    CodexTurnRetryableFailure,
    CodexTurnSafetyBlocked,
    CompactWaiter,
    GoalWaiter,
    PendingRequest,
    TurnWaiter,
    codex_final_agent_response,
    terminal_turn_error,
)
from management_portal.codex_app_server_threads import codex_thread_execution_lock
from management_portal.codex_app_server_turn_follow import CodexTurnFollower


def test_running_turn_normalizes_message_identity_before_replay():
    """进行中轮次重放快照前先规范消息 ID，未完成的条目不发出

    :module: platform.core"""
    client = CodexAppServerClient(Path("/unused.sock"))
    events = []
    completed = {"id": "done", "type": "commandExecution", "status": "completed"}
    pending = {"id": "pending", "type": "commandExecution", "status": "inProgress"}
    message = {"id": "item-4", "type": "agentMessage", "text": "Progress", "phase": "commentary"}
    snapshot = {"id": "turn", "status": "inProgress", "items": [message, completed, pending]}
    transform = mock.Mock(return_value={**snapshot, "items": [
        {**message, "id": "msg_native", "status": "completed"}, completed, pending,
    ]})
    waiter = TurnWaiter("thread", turn_id="turn", turn_snapshot_transform=transform,
                         event_callback=events.append)
    client.reads.read_turn = mock.Mock(return_value=snapshot)
    assert client.follower.read_terminal_turn(waiter) is None
    assert client.follower.read_terminal_turn(waiter) is None
    assert transform.call_args_list == [mock.call(snapshot), mock.call(snapshot)]
    assert [event["params"]["item"]["id"] for event in events] == ["msg_native", "done"]


@pytest.mark.parametrize("rejected", [{"image_detail": "bad"}, {"collaboration_mode": "plan"}])
def test_rejected_turn_input_leaves_no_turn_registered(rejected):
    """被拒绝的轮次输入不会在线程上留下已登记的轮次和工具处理器

    run_turn validates the turn's input before it registers the turn's waiter
    and dynamic tool handler, so a rejected input leaves neither behind."""
    client = CodexAppServerClient(Path("/unused.sock"))
    client.rpc.request = mock.Mock(return_value={"thread": {"id": "thread"}, "data": []})
    with pytest.raises(ValueError):
        client.run_turn("thread", "hello", cwd="/tmp", dynamic_tool_handler=lambda params: {}, **rejected)
    assert not client.work.turns
    assert not client.work.dynamic_tool_handlers
    assert client.snapshot()["busy_threads"] == 0


def test_reconciliation_is_throttled_but_terminal_notification_wakes_it():
    """轮次对账按间隔节流，收到结束通知时立即对账

    :module: platform.core"""
    waiter = TurnWaiter("thread")
    with mock.patch("management_portal.codex_app_server_turn_follow.time.monotonic", return_value=100):
        assert CodexTurnFollower.turn_reconcile_due(waiter)
        assert not CodexTurnFollower.turn_reconcile_due(waiter)
        waiter.event.set()
        assert CodexTurnFollower.turn_reconcile_due(waiter)


class FakeAppServer:
    def __init__(
        self,
        socket_path: Path,
        *,
        initialize_delay_seconds: float = 0.0,
        disconnect_on_turn_start: bool = False,
        omit_turn_completed: bool = False,
        omit_idle_notification: bool = False,
        idle_notification_delay_seconds: float = 0.0,
        call_dynamic_tool: bool = False,
        omit_compact_completed: bool = False,
        resume_delay_seconds: float = 0.0,
        thread_start_delay_seconds: float = 0.0,
        fork_delay_seconds: float = 0.0,
        compact_start_delay_seconds: float = 0.0,
        compact_terminal_delay_seconds: float = 0.0,
        turn_start_delay_seconds: float = 0.0,
        wait_for_turn_steer: bool = False,
        terminal_goal_status: str = "",
        turn_status: str = "completed",
        turn_error: object = None,
        active_turn_id: str = "",
        complete_active_turn_on_read: bool = False,
        complete_active_turn_on_steer: bool = False,
        keep_active_after_final: bool = False,
        fork_requires_direct_turn: bool = False,
        compact_conflict_on_first_turn_start: bool = False,
        resume_reports_active_writer: bool = False,
        error_notifications: list[dict] | None = None,
        omit_final_answer: bool = False,
    ) -> None:
        self.socket_path = socket_path
        self.initialize_delay_seconds = initialize_delay_seconds
        self.disconnect_on_turn_start = disconnect_on_turn_start
        self.omit_turn_completed = omit_turn_completed
        self.omit_idle_notification = omit_idle_notification
        self.idle_notification_delay_seconds = idle_notification_delay_seconds
        self.call_dynamic_tool = call_dynamic_tool
        self.omit_compact_completed = omit_compact_completed
        self.resume_delay_seconds = resume_delay_seconds
        self.thread_start_delay_seconds = thread_start_delay_seconds
        self.fork_delay_seconds = fork_delay_seconds
        self.compact_start_delay_seconds = compact_start_delay_seconds
        self.compact_terminal_delay_seconds = compact_terminal_delay_seconds
        self.turn_start_delay_seconds = turn_start_delay_seconds
        self.wait_for_turn_steer = wait_for_turn_steer
        self.terminal_goal_status = terminal_goal_status
        self.turn_status = turn_status
        self.turn_error = turn_error
        self.active_turn_id = str(active_turn_id or "")
        self.complete_active_turn_on_read = complete_active_turn_on_read
        self.complete_active_turn_on_steer = complete_active_turn_on_steer
        self.keep_active_after_final = keep_active_after_final
        self.fork_requires_direct_turn = fork_requires_direct_turn
        self.compact_conflict_on_first_turn_start = (
            compact_conflict_on_first_turn_start
        )
        self.resume_reports_active_writer = resume_reports_active_writer
        self.error_notifications = error_notifications or []
        self.omit_final_answer = omit_final_answer
        self.turn_start_attempts = 0
        self.unmaterialized_thread_ids: set[str] = set()
        self.methods: list[str] = []
        self.requests: list[dict] = []
        self.last_turn: dict | None = None
        self.goal: dict | None = None
        self.dynamic_tool_response: dict | None = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _complete_active_turn(
        self,
        connection: socket.socket,
        thread_id: str,
    ) -> None:
        turn_id = self.active_turn_id
        if not turn_id:
            return
        final_item = {
            "id": "item-recovered-final",
            "type": "agentMessage",
            "phase": "final_answer",
            "text": "recovered finished",
        }
        self.last_turn = {
            "id": turn_id,
            "items": [final_item],
            "itemsView": "full",
            "status": "completed",
            "error": None,
        }
        self.active_turn_id = ""
        self._write(connection, {
            "method": "item/completed",
            "params": {
                "threadId": thread_id,
                "turnId": turn_id,
                "completedAtMs": 1,
                "item": final_item,
            },
        })
        self._write(connection, {
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": self.last_turn,
            },
        })

    def start(self) -> None:
        self.thread.start()
        self.assert_ready()

    def assert_ready(self) -> None:
        if not self.ready.wait(2):
            raise RuntimeError("fake app-server did not start")

    def _read_exact(self, connection: socket.socket, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = connection.recv(size - len(data))
            if not chunk:
                raise EOFError
            data.extend(chunk)
        return bytes(data)

    def _read_message(self, connection: socket.socket) -> dict:
        first, second = self._read_exact(connection, 2)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(connection, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(connection, 8))[0]
        mask = self._read_exact(connection, 4) if second & 0x80 else b""
        payload = self._read_exact(connection, length)
        if mask:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        if first & 0x0F == 0x8:
            raise EOFError
        return json.loads(payload.decode("utf-8"))

    def _write(self, connection: socket.socket, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        if len(data) < 126:
            header = bytes((0x81, len(data)))
        elif len(data) <= 0xFFFF:
            header = bytes((0x81, 126)) + struct.pack("!H", len(data))
        else:
            header = bytes((0x81, 127)) + struct.pack("!Q", len(data))
        connection.sendall(header + data)

    def _run(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        server.listen(1)
        self.ready.set()
        connection, _ = server.accept()
        try:
            request = bytearray()
            while b"\r\n\r\n" not in request:
                request.extend(connection.recv(4096))
            header_text = request.decode("iso-8859-1")
            key = next(
                line.split(":", 1)[1].strip()
                for line in header_text.split("\r\n")
                if line.lower().startswith("sec-websocket-key:")
            )
            accept = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
            ).decode("ascii")
            connection.sendall((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode("ascii"))
            while True:
                message = self._read_message(connection)
                method = str(message.get("method") or "")
                self.methods.append(method)
                self.requests.append(message)
                request_id = message.get("id")
                if method == "initialize":
                    time.sleep(self.initialize_delay_seconds)
                    self._write(connection, {"id": request_id, "result": {"userAgent": "fake"}})
                elif method == 'config/read':
                    self._write(connection, {'id': request_id, 'result': {'config': {}}})
                elif method == 'account/read':
                    self._write(connection, {'id': request_id, 'result': {'account': {'type': 'chatgpt'}}})
                elif method == "thread/resume":
                    time.sleep(self.resume_delay_seconds)
                    thread_id = message["params"]["threadId"]
                    if self.resume_reports_active_writer:
                        self._write(connection, {
                            "id": request_id,
                            "error": {
                                "message": (
                                    f"thread {thread_id} already has an active writer"
                                )
                            },
                        })
                    elif thread_id in self.unmaterialized_thread_ids:
                        self._write(connection, {
                            "id": request_id,
                            "error": {
                                "message": (
                                    "no rollout found for thread id "
                                    f"{thread_id}"
                                )
                            },
                        })
                    else:
                        self._write(connection, {"id": request_id, "result": {"thread": {"id": thread_id}}})
                elif method == "thread/inject_items":
                    self._write(connection, {"id": request_id, "result": {}})
                elif method == "thread/start":
                    time.sleep(self.thread_start_delay_seconds)
                    self._write(connection, {"id": request_id, "result": {"thread": {"id": "thread-new"}}})
                elif method == "thread/fork":
                    time.sleep(self.fork_delay_seconds)
                    if self.fork_requires_direct_turn:
                        self.unmaterialized_thread_ids.add("thread-fork")
                    self._write(connection, {"id": request_id, "result": {"thread": {"id": "thread-fork"}}})
                elif method == "thread/compact/start":
                    time.sleep(self.compact_start_delay_seconds)
                    thread_id = message["params"]["threadId"]
                    compact_turn_id = (
                        "019f0000-0000-7000-8000-000000000250"
                    )
                    if self.compact_terminal_delay_seconds:
                        self.active_turn_id = compact_turn_id
                        self._write(connection, {
                            "method": "turn/started",
                            "params": {
                                "threadId": thread_id,
                                "turn": {
                                    "id": compact_turn_id,
                                    "status": "inProgress",
                                },
                            },
                        })
                    self._write(connection, {"id": request_id, "result": {}})
                    self._write(connection, {
                        "method": "thread/tokenUsage/updated",
                        "params": {
                            "threadId": thread_id,
                            "tokenUsage": {
                                "last": {
                                    "inputTokens": 0,
                                    "cachedInputTokens": 0,
                                    "outputTokens": 0,
                                    "reasoningOutputTokens": 0,
                                    "totalTokens": 14834,
                                },
                                "total": {
                                    "inputTokens": 126563804,
                                    "cachedInputTokens": 121226496,
                                    "outputTokens": 522984,
                                    "reasoningOutputTokens": 188607,
                                    "totalTokens": 127086788,
                                },
                                "modelContextWindow": 258400,
                            },
                        },
                    })
                    if not self.omit_compact_completed:
                        self._write(connection, {"method": "thread/compacted", "params": {"threadId": thread_id}})
                    if self.compact_terminal_delay_seconds:
                        time.sleep(self.compact_terminal_delay_seconds)
                        self.active_turn_id = ""
                        self.last_turn = {
                            "id": compact_turn_id,
                            "items": [{"type": "contextCompaction"}],
                            "itemsView": "full",
                            "status": "completed",
                            "error": None,
                        }
                        self._write(connection, {
                            "method": "turn/completed",
                            "params": {
                                "threadId": thread_id,
                                "turn": self.last_turn,
                            },
                        })
                elif method == "thread/goal/get":
                    self._write(connection, {
                        "id": request_id,
                        "result": {"goal": self.goal},
                    })
                elif method == "thread/goal/set":
                    params = message["params"]
                    thread_id = params["threadId"]
                    objective = str(
                        params.get("objective")
                        or (self.goal or {}).get("objective")
                        or ""
                    )
                    status = str(
                        params.get("status")
                        or (self.goal or {}).get("status")
                        or "active"
                    )
                    self.goal = {
                        **(self.goal or {}),
                        "objective": objective,
                        "status": status,
                        "tokensUsed": int((self.goal or {}).get("tokensUsed") or 0),
                        "timeUsedSeconds": int((self.goal or {}).get("timeUsedSeconds") or 0),
                    }
                    if params.get("tokenBudget") is not None:
                        self.goal["tokenBudget"] = params["tokenBudget"]
                    goal_response = dict(self.goal)
                    if status == "active":
                        turn_id = "019f0000-0000-7000-8000-000000000299"
                        self._write(connection, {
                            "method": "turn/started",
                            "params": {
                                "threadId": thread_id,
                                "turn": {"id": turn_id, "status": "inProgress"},
                            },
                        })
                        self._write(connection, {
                            "method": "thread/tokenUsage/updated",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "tokenUsage": {
                                    "last": {
                                        "inputTokens": 12,
                                        "cachedInputTokens": 2,
                                        "outputTokens": 9,
                                        "reasoningOutputTokens": 1,
                                        "totalTokens": 21,
                                    },
                                    "total": {
                                        "inputTokens": 12,
                                        "cachedInputTokens": 2,
                                        "outputTokens": 9,
                                        "reasoningOutputTokens": 1,
                                        "totalTokens": 21,
                                    },
                                    "modelContextWindow": 200000,
                                },
                            },
                        })
                        final_item = {
                            "id": "item-goal-final",
                            "type": "agentMessage",
                            "phase": "final_answer",
                            "text": "goal finished",
                        }
                        self._write(connection, {
                            "method": "item/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "completedAtMs": 1,
                                "item": final_item,
                            },
                        })
                        self._write(connection, {
                            "method": "turn/completed",
                            "params": {
                                "threadId": thread_id,
                                "turn": {
                                    "id": turn_id,
                                    "items": [final_item],
                                    "status": "completed",
                                },
                            },
                        })
                        self.goal = {
                            **self.goal,
                            "status": "complete",
                            "tokensUsed": 21,
                            "timeUsedSeconds": 1,
                        }
                        self._write(connection, {
                            "method": "thread/goal/updated",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "goal": self.goal,
                            },
                        })
                    self._write(connection, {
                        "id": request_id,
                        "result": {"goal": goal_response},
                    })
                elif method == "thread/goal/clear":
                    thread_id = message["params"]["threadId"]
                    self.goal = None
                    self._write(connection, {"id": request_id, "result": {}})
                    self._write(connection, {
                        "method": "thread/goal/cleared",
                        "params": {"threadId": thread_id},
                    })
                elif method in {"turn/start", "review/start"}:
                    self.turn_start_attempts += 1
                    if (
                        self.compact_conflict_on_first_turn_start
                        and self.turn_start_attempts == 1
                    ):
                        self.active_turn_id = (
                            "019f0000-0000-7000-8000-000000000250"
                        )
                        self._write(connection, {
                            "id": request_id,
                            "error": {
                                "code": -32600,
                                "message": (
                                    "failed to submit turn input: "
                                    "ActiveTurnNotSteerable "
                                    "{ turn_kind: Compact }"
                                ),
                            },
                        })
                        continue
                    time.sleep(self.turn_start_delay_seconds)
                    if self.disconnect_on_turn_start:
                        connection.shutdown(socket.SHUT_RDWR)
                        connection.close()
                        return
                    thread_id = message["params"]["threadId"]
                    self.unmaterialized_thread_ids.discard(thread_id)
                    turn_id = "019f0000-0000-7000-8000-000000000201"
                    self._write(connection, {"id": request_id, "result": {"turn": {"id": turn_id, "items": [], "status": "inProgress"}}})
                    if self.wait_for_turn_steer:
                        steer_request = self._read_message(connection)
                        self.methods.append(
                            str(steer_request.get("method") or "")
                        )
                        self.requests.append(steer_request)
                        self._write(connection, {
                            "id": steer_request.get("id"),
                            "result": {"turnId": turn_id},
                        })
                    if self.call_dynamic_tool:
                        self._write(connection, {
                            "id": 9001,
                            "method": "item/tool/call",
                            "params": {
                                "arguments": {"path": "TASK.md"},
                                "callId": "call-1",
                                "threadId": thread_id,
                                "tool": "read_workspace_file",
                                "turnId": turn_id,
                            },
                        })
                        command_request = self._read_message(connection)
                        self.methods.append(
                            str(command_request.get("method") or "")
                        )
                        self.requests.append(command_request)
                        command_id = command_request.get("id")
                        self._write(connection, {
                            "id": command_id,
                            "result": {
                                "exitCode": 0,
                                "stderr": "",
                                "stdout": "{\"ok\":true}",
                            },
                        })
                        self.dynamic_tool_response = self._read_message(
                            connection
                        )
                    self._write(connection, {
                        "method": "thread/tokenUsage/updated",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "tokenUsage": {
                                "last": {"inputTokens": 10, "cachedInputTokens": 2, "outputTokens": 5, "reasoningOutputTokens": 1, "totalTokens": 15},
                                "total": {"inputTokens": 100, "cachedInputTokens": 20, "outputTokens": 50, "reasoningOutputTokens": 10, "totalTokens": 150},
                                "modelContextWindow": 200000,
                            },
                        },
                    })
                    final_item = {"id": "item-final", "type": "agentMessage", "phase": "final_answer", "text": "finished"}
                    for notification in self.error_notifications:
                        self._write(connection, {
                            "method": "error",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                **notification,
                            },
                        })
                    completed_turn_id = (
                        "019f0000-0000-7000-8000-000000000298"
                        if self.terminal_goal_status
                        else turn_id
                    )
                    self.last_turn = {
                        "id": completed_turn_id,
                        "items": [] if self.omit_final_answer else [final_item],
                        "itemsView": "full",
                        "status": self.turn_status,
                        "error": self.turn_error,
                    }
                    if not self.omit_final_answer:
                        self._write(connection, {
                            "method": "item/completed",
                            "params": {"threadId": thread_id, "turnId": completed_turn_id, "completedAtMs": 1, "item": final_item},
                        })
                    if self.keep_active_after_final:
                        self.active_turn_id = completed_turn_id
                    if self.terminal_goal_status:
                        self.goal = {
                            "objective": "test goal",
                            "status": self.terminal_goal_status,
                        }
                        self._write(connection, {
                            "method": "thread/goal/updated",
                            "params": {"threadId": thread_id, "goal": self.goal},
                        })
                    elif self.omit_turn_completed:
                        time.sleep(self.idle_notification_delay_seconds)
                        if not self.omit_idle_notification:
                            self._write(connection, {
                                "method": "thread/status/changed",
                                "params": {"threadId": thread_id, "status": {"type": "idle"}},
                            })
                    else:
                        self._write(connection, {
                            "method": "turn/completed",
                            "params": {"threadId": thread_id, "turn": self.last_turn},
                        })
                elif method == "thread/turns/list":
                    active_turn = (
                        {
                            "id": self.active_turn_id,
                            "items": [{"type": "userMessage", "clientId": "accepted-input"}],
                            "itemsView": "summary",
                            "status": "inProgress",
                            "error": None,
                        }
                        if self.active_turn_id
                        else None
                    )
                    self._write(connection, {
                        "id": request_id,
                        "result": {
                            "data": [active_turn or self.last_turn]
                            if active_turn or self.last_turn
                            else [],
                            "nextCursor": None,
                        },
                    })
                    if active_turn and self.complete_active_turn_on_read:
                        self._complete_active_turn(connection, message["params"]["threadId"])
                elif method == "turn/steer":
                    turn_id = str(message["params"].get("expectedTurnId") or "")
                    self._write(connection, {
                        "id": request_id,
                        "result": {"turnId": turn_id},
                    })
                    if self.complete_active_turn_on_steer:
                        self._complete_active_turn(
                            connection,
                            message["params"]["threadId"],
                        )
                elif method == "thread/unsubscribe":
                    self._write(connection, {"id": request_id, "result": {"status": "unsubscribed"}})
                elif method == "turn/interrupt":
                    self._write(connection, {"id": request_id, "result": {}})
        except EOFError:
            pass
        finally:
            try:
                connection.close()
            except Exception:
                pass
            server.close()


class CodexAppServerClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temp_dir.name) / "app-server.sock"

    def test_database_history_import_runs_after_resume_before_new_input(self) -> None:
        """数据库历史在 thread/resume 之后、新输入的 turn/start 之前导入"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)
        history = [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "historical question"},
            {"type": "input_image", "image_url": "data:image/png;base64," + "A" * 1_176_028},
        ]}]
        try:
            client.run_turn(
                "thread-history", "new question", cwd=self.temp_dir.name,
                history_initializer=lambda thread: client.request(
                    "thread/inject_items", {"threadId": thread, "items": history},
                ),
            )
            requests = server.requests
            methods = [item["method"] for item in requests]
            self.assertLess(methods.index("thread/resume"), methods.index("thread/inject_items"))
            self.assertLess(methods.index("thread/inject_items"), methods.index("turn/start"))
            imported = next(item for item in requests if item["method"] == "thread/inject_items")
            self.assertEqual(imported["params"]["items"], history)
            started = next(item for item in requests if item["method"] == "turn/start")
            self.assertEqual(started["params"]["input"], [{"type": "text", "text": "new question"}])
        finally:
            client.close()

    def test_failed_history_import_does_not_start_an_empty_context_turn(self) -> None:
        """历史导入失败时不在空上下文中开始新轮次"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)
        try:
            with self.assertRaisesRegex(RuntimeError, "history import uncertain"):
                client.run_turn(
                    "thread-history", "new question", cwd=self.temp_dir.name,
                    history_initializer=mock.Mock(side_effect=RuntimeError("history import uncertain")),
                )
            self.assertFalse(any(item["method"] == "turn/start" for item in server.requests))
        finally:
            client.close()

    def test_first_final_answer_is_canonical_when_provider_emits_two(self) -> None:
        """Provider 发出两个 final_answer 时以第一个作为最终回答"""
        turn = {
            "items": [
                {
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "complete answer",
                },
                {
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "checkpoint acknowledgement",
                },
            ]
        }

        self.assertEqual(
            codex_final_agent_response(turn, []),
            "complete answer",
        )


    def test_turn_control_updates_running_turn_speed_without_steering(self) -> None:
        """轮次控制可更新进行中轮次的 service_tier，不发 turn/steer"""
        client = CodexAppServerClient(self.socket_path)
        waiter = TurnWaiter(
            thread_id="thread-speed",
            turn_id="turn-speed",
        )
        control = {
            "action": "update_settings",
            "settings": {"service_tier": "priority"},
        }
        results: list[tuple[dict, bool, str]] = []

        with mock.patch.object(
            client.controls,
            "update_turn_settings",
            return_value={"status": "applied"},
        ) as update_turn_settings, mock.patch.object(
            client.controls,
            "steer_turn",
        ) as steer_turn:
            transition = client.controls.deliver_turn_control(
                thread_id=waiter.thread_id,
                waiter=waiter,
                turn_control_provider=lambda: control,
                turn_control_result_callback=(
                    lambda item, applied, error: results.append(
                        (item, applied, error)
                    )
                ),
                cancellation_requested=None,
            )

        self.assertIsNone(transition)
        update_turn_settings.assert_called_once_with(
            "thread-speed",
            "turn-speed",
            service_tier="priority",
            cancellation_requested=None,
        )
        steer_turn.assert_not_called()
        self.assertEqual(results, [(control, True, "")])

    def test_turn_settings_update_clears_fast_tier_with_json_null(self) -> None:
        """清除快速档位时 turn/settings/update 以 JSON null 提交 serviceTier"""
        client = CodexAppServerClient(self.socket_path)
        with mock.patch.object(
            client.rpc,
            "request",
            return_value={"status": "applied"},
        ) as request:
            response = client.update_turn_settings(
                "thread-speed",
                "turn-speed",
                service_tier="",
            )

        self.assertEqual(response, {"status": "applied"})
        request.assert_called_once_with(
            "turn/settings/update",
            {
                "threadId": "thread-speed",
                "turnId": "turn-speed",
                "serviceTier": None,
            },
            timeout=client.timeouts.lifecycle,
            cancellation_requested=None,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_turn_waiter_starts_non_idle_before_status_notification(self) -> None:
        """轮次等待器在收到状态通知前默认线程不空闲"""
        self.assertFalse(TurnWaiter(thread_id="thread-test").thread_idle)

    def test_final_answer_closes_turn_control_channel(self) -> None:
        """已出现 final_answer 后关闭轮次控制通道，不再取后续控制输入"""
        client = CodexAppServerClient(self.socket_path)
        waiter = TurnWaiter(
            thread_id="thread-test",
            turn_id="turn-test",
            final_answer_seen=True,
        )
        provider_calls: list[bool] = []

        result = client.controls.deliver_turn_control(
            thread_id=waiter.thread_id,
            waiter=waiter,
            turn_control_provider=lambda: provider_calls.append(True),
            turn_control_result_callback=None,
            cancellation_requested=None,
        )

        self.assertIsNone(result)
        self.assertEqual(provider_calls, [])
        client.close()

    def test_final_answer_start_closes_input_before_event_projection(self) -> None:
        """final_answer 条目开始时先停止接收输入，再投影事件"""
        client = CodexAppServerClient(self.socket_path)
        waiter = TurnWaiter(thread_id="thread-test", turn_id="turn-test")
        client.work.turns[waiter.thread_id] = waiter
        control_provider = mock.Mock(return_value=None)
        waiter.event_callback = lambda event: client.controls.deliver_turn_control(
            thread_id=waiter.thread_id, waiter=waiter,
            turn_control_provider=control_provider,
            turn_control_result_callback=None, cancellation_requested=None,
        )
        try:
            client.events.dispatch_notification("item/started", {
                "threadId": waiter.thread_id, "turnId": waiter.turn_id,
                "item": {"id": "final", "type": "agentMessage", "phase": "final_answer", "text": ""},
            })
            control_provider.assert_not_called()
            self.assertTrue(waiter.final_answer_seen)
            self.assertIsNone(waiter.completed)
            self.assertEqual(waiter.agent_messages, [])
            self.assertFalse(waiter.event.is_set())
        finally:
            client.close()

    def test_commentary_and_other_turn_final_do_not_close_input(self) -> None:
        """commentary 或其他轮次的 final_answer 开始时，本轮仍继续接收输入"""
        client = CodexAppServerClient(self.socket_path)
        waiter = TurnWaiter(thread_id="thread-test", turn_id="turn-test")
        client.work.turns[waiter.thread_id] = waiter
        try:
            for turn_id, phase in [(waiter.turn_id, "commentary"), ("other-turn", "final_answer")]:
                client.events.dispatch_notification("item/started", {
                    "threadId": waiter.thread_id, "turnId": turn_id,
                    "item": {"id": "agent-item", "type": "agentMessage", "phase": phase, "text": ""},
                })
            self.assertFalse(waiter.final_answer_seen)
        finally:
            client.close()

    def test_active_final_snapshot_closes_input_without_completing_turn(self) -> None:
        """进行中快照出现 final_answer 时停止接收输入，但不结束本轮也不发出事件"""
        client = CodexAppServerClient(self.socket_path)
        try:
            for callback in (None, mock.Mock()):
                with self.subTest(has_callback=callback is not None):
                    waiter = TurnWaiter(thread_id="thread-test", turn_id="turn-test", event_callback=callback)
                    client.follower.replay_completed_items(waiter, {
                        "id": waiter.turn_id, "status": "inProgress", "items": [{
                            "id": "final", "type": "agentMessage", "phase": "final_answer",
                            "status": "inProgress", "text": "partial final",
                        }],
                    })
                    self.assertTrue(waiter.final_answer_seen)
                    self.assertEqual(waiter.agent_messages, [])
                    self.assertFalse(waiter.event.is_set())
                    if callback is not None:
                        callback.assert_not_called()
        finally:
            client.close()

    def test_completed_turn_closes_turn_control_channel_without_final_answer(
        self,
    ) -> None:
        """轮次已完成时即使没有 final_answer 也关闭轮次控制通道"""
        client = CodexAppServerClient(self.socket_path)
        waiter = TurnWaiter(
            thread_id="thread-test",
            turn_id="turn-test",
            completed={"turn": {"status": "completed"}},
        )
        provider_calls: list[bool] = []

        result = client.controls.deliver_turn_control(
            thread_id=waiter.thread_id,
            waiter=waiter,
            turn_control_provider=lambda: provider_calls.append(True),
            turn_control_result_callback=None,
            cancellation_requested=None,
        )

        self.assertIsNone(result)
        self.assertEqual(provider_calls, [])
        client.close()

    def test_run_turn_reuses_protocol_and_collects_result(self) -> None:
        """同一线程的连续轮次复用连接只 resume 一次，并收集回答和 token 用量"""
        server = FakeAppServer(self.socket_path)
        server.start()
        events: list[dict] = []
        client = CodexAppServerClient(self.socket_path)
        result = client.run_turn(
            "019f0000-0000-7000-8000-000000000200",
            "hello",
            cwd="/tmp",
            sandbox="danger-full-access",
            event_callback=events.append,
        )
        second = client.run_turn(
            "019f0000-0000-7000-8000-000000000200",
            "again",
            cwd="/tmp",
            sandbox="danger-full-access",
        )

        self.assertEqual(result.response, "finished")
        self.assertEqual(second.response, "finished")
        self.assertEqual(result.token_usage["last"]["totalTokens"], 15)
        self.assertEqual(result.token_usage["context"]["totalTokens"], 15)
        self.assertIn("thread/resume", server.methods)
        self.assertEqual(server.methods.count("thread/resume"), 1)
        self.assertEqual(server.methods.count("turn/start"), 2)
        self.assertEqual(events[-1]["method"], "turn/completed")
        client.close()

    def test_native_review_uses_review_start_and_shared_turn_lifecycle(self) -> None:
        """原生代码评审用 review/start 启动，并沿用普通轮次的结束流程"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)
        events = []
        try:
            result = client.run_turn("019f0000-0000-7000-8000-000000000200", "", cwd="/tmp",
                                     review_target={"type": "custom", "instructions": "Review captured diff only"}, event_callback=events.append)
            self.assertEqual(result.response, "finished")
            self.assertIn("review/start", server.methods)
            self.assertNotIn("turn/start", server.methods)
            receipts = [event for event in events if event["method"] == "portal/provider_request_accepted"]
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["params"], {
                "threadId": "019f0000-0000-7000-8000-000000000200",
                "turnId": result.turn_id, "rpcMethod": "review/start",
            })
            self.assertNotIn("Review captured diff", json.dumps(receipts))
            self.assertEqual(events[-1]["method"], "turn/completed")
        finally:
            client.close()

    def test_review_reconciliation_keeps_the_accepted_parent_turn(self) -> None:
        """评审对账以已接受的父轮次为准，忽略被中断的子轮次"""
        # Inline review exposes its worker turn in the parent's turn list.
        # That child can be interrupted while the actual review has succeeded.
        for resume in (False, True):
            with self.subTest(resume=resume):
                client = CodexAppServerClient(self.socket_path)
                parent, child = "review-parent", "review-child"
                result_item = {"id": "review-result", "type": "agentMessage",
                               "text": "The captured patch is correct."}
                complete = {"id": parent, "status": "completed", "items": [result_item]}
                child_running = {"id": child, "status": "inProgress", "items": []}
                client.rpc.request = mock.Mock(return_value={"turn": {"id": parent, "status": "inProgress"}})
                client.reads.read_active_turn = mock.Mock(side_effect=(
                    [child_running, None] if resume else [None, child_running, None]
                ))
                client.reads.read_turn = mock.Mock(side_effect=lambda thread, turn: (
                    complete if turn == parent else {"id": child, "status": "interrupted", "items": []}
                ))
                events, starts = [], []
                try:
                    with mock.patch.multiple(
                        "management_portal.codex_app_server_turn_follow",
                        TURN_ACTIVE_RECONCILE_INTERVAL_SECONDS=0.001,
                        TURN_COMPLETION_RECONCILE_INTERVAL_SECONDS=0.001,
                    ):
                        if resume:
                            result = client.resume_turn("review-thread", parent, cwd="/tmp",
                                event_callback=events.append, turn_started_callback=starts.append,
                                timeout_seconds=1)
                        else:
                            result = client.run_turn("review-thread", "", cwd="/tmp",
                                review_target={"type": "custom", "instructions": "Review captured patch"},
                                event_callback=events.append, turn_started_callback=starts.append)
                    self.assertEqual(result.turn_id, parent)
                    self.assertEqual(result.response, result_item["text"])
                    self.assertEqual(starts, [parent])
                    receipts = [e for e in events if e["method"] == "portal/provider_request_accepted"]
                    self.assertEqual(len(receipts), 0 if resume else 1)
                    self.assertTrue(all(e["params"].get("turnId", parent) == parent for e in events))
                finally:
                    client.close()

    def test_exact_turn_lookup_searches_older_pages_without_rebinding(self) -> None:
        """按 ID 查找轮次时向更早的分页翻找，分页不前进时报错"""
        client = CodexAppServerClient(self.socket_path)
        expected = {"id": "review-parent", "status": "completed", "items": []}
        client.rpc.request = mock.Mock(side_effect=[
            {"data": [{"id": "review-child", "status": "interrupted"}], "nextCursor": "older"},
            {"data": [expected], "nextCursor": "unneeded"},
        ])
        self.assertEqual(client.read_turn("thread", "review-parent"), expected)
        self.assertEqual(client.rpc.request.call_args_list[1].args[1]["cursor"], "older")
        self.assertEqual(client.rpc.request.call_count, 2)
        client.rpc.request = mock.Mock(return_value={"data": [], "nextCursor": "loop"})
        with self.assertRaisesRegex(CodexAppServerError, "pagination did not advance"):
            client.read_turn("thread", "absent")
        client.close()

    def test_run_turn_reports_thread_writer_owned_by_another_process(self) -> None:
        """线程写入方被其他进程占用时报 CodexThreadWriterBusy，不开始新轮次"""
        server = FakeAppServer(
            self.socket_path,
            resume_reports_active_writer=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)

        with self.assertRaises(CodexThreadWriterBusy):
            client.run_turn(
                "019f0000-0000-7000-8000-000000000201",
                "continue after worker replacement",
                cwd="/tmp",
            )
        self.assertEqual(server.methods.count("thread/resume"), 1)
        self.assertEqual(server.methods.count("turn/start"), 0)
        client.close()

    def test_run_turn_forwards_local_and_remote_images(self) -> None:
        """run_turn 把本地图片和远程图片 URL 随文本一起提交"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)

        client.run_turn(
            "019f0000-0000-7000-8000-000000000211",
            "inspect",
            cwd="/tmp",
            images=["/tmp/probe.png"],
            image_urls=["https://example.test/probe.jpg"],
        )

        turn_request = next(
            request
            for request in server.requests
            if request.get("method") == "turn/start"
        )
        self.assertEqual(turn_request["params"]["input"], [
            {"type": "text", "text": "inspect"},
            {"type": "localImage", "path": "/tmp/probe.png"},
            {"type": "image", "url": "https://example.test/probe.jpg"},
        ])
        client.close()

    def test_run_turn_forwards_native_plan_collaboration_mode(self) -> None:
        """run_turn 以原生 collaborationMode 提交 plan 模式和模型设置"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)

        client.run_turn(
            "019f0000-0000-7000-8000-000000000215",
            "make a plan",
            cwd="/tmp",
            model="gpt-5.6-sol",
            effort="high",
            collaboration_mode="plan",
        )

        turn_request = next(
            request
            for request in server.requests
            if request.get("method") == "turn/start"
        )
        self.assertEqual(
            turn_request["params"]["collaborationMode"],
            {
                "mode": "plan",
                "settings": {
                    "model": "gpt-5.6-sol",
                    "developer_instructions": None,
                    "reasoning_effort": "high",
                },
            },
        )
        client.close()

    def test_run_goal_uses_native_protocol_and_follows_until_terminal(self) -> None:
        """运行 goal 使用原生 thread/goal 协议并跟踪到 goal 结束"""
        server = FakeAppServer(self.socket_path)
        server.start()
        events: list[dict] = []
        client = CodexAppServerClient(self.socket_path)

        result = client.run_goal(
            "019f0000-0000-7000-8000-000000000216",
            action="set",
            objective="finish the portal goal integration",
            token_budget=1000,
            event_callback=events.append,
        )

        self.assertEqual(result.response, "goal finished")
        self.assertEqual(result.goal["status"], "complete")
        self.assertEqual(result.goal["tokensUsed"], 21)
        self.assertEqual(result.token_usage["last"]["totalTokens"], 21)
        self.assertIn("thread/goal/get", server.methods)
        self.assertIn("thread/goal/set", server.methods)
        self.assertNotIn("turn/start", server.methods)
        goal_request = next(
            request
            for request in server.requests
            if request.get("method") == "thread/goal/set"
        )
        self.assertEqual(goal_request["params"]["tokenBudget"], 1000)
        self.assertEqual(events[-1]["method"], "thread/goal/updated")
        client.close()

    def test_run_turn_sends_soft_control_from_turn_thread(self) -> None:
        """轮次进行中用 turn/steer 投递软控制输入，并回报应用结果"""
        server = FakeAppServer(
            self.socket_path,
            wait_for_turn_steer=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)
        controls = [{
            "control_id": "checkpoint-1",
            "reason": "duplicate_read_pressure",
            "input": [{"type": "text", "text": "consolidate evidence"}],
        }]
        results: list[tuple[dict, bool, str]] = []

        result = client.run_turn(
            "019f0000-0000-7000-8000-000000000212",
            "inspect",
            cwd="/tmp",
            turn_control_provider=(
                lambda: controls.pop(0) if controls else None
            ),
            turn_control_result_callback=(
                lambda control, applied, error: results.append(
                    (control, applied, error)
                )
            ),
        )

        steer = next(
            request
            for request in server.requests
            if request.get("method") == "turn/steer"
        )
        self.assertEqual(result.response, "finished")
        self.assertEqual(steer["params"]["expectedTurnId"], result.turn_id)
        self.assertEqual(steer["params"]["input"], [
            {"type": "text", "text": "consolidate evidence"},
        ])
        self.assertEqual(results, [(
            {
                "control_id": "checkpoint-1",
                "reason": "duplicate_read_pressure",
                "input": [
                    {"type": "text", "text": "consolidate evidence"},
                ],
            },
            True,
            "",
        )])
        client.close()

    def test_run_turn_adopts_existing_active_turn_and_steers_it(self) -> None:
        """已有进行中轮次时 run_turn 接管该轮并投递插话，不重复提交提示"""
        active_turn_id = "019f0000-0000-7000-8000-000000000777"
        server = FakeAppServer(
            self.socket_path,
            active_turn_id=active_turn_id,
            complete_active_turn_on_steer=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)
        controls = [{
            "control_id": "control-recovered",
            "expected_turn_id": active_turn_id,
            "input": [{"type": "text", "text": "handle this now"}],
        }]
        started_turns: list[str] = []
        control_results: list[tuple[dict, bool, str]] = []

        with mock.patch(
            "management_portal.codex_app_server_turn_follow.TURN_CONTROL_POLL_INTERVAL_SECONDS",
            0.01,
        ):
            result = client.run_turn(
                "019f0000-0000-7000-8000-000000000200",
                "do not duplicate this prompt",
                cwd="/tmp",
                client_user_message_id="accepted-input",
                turn_started_callback=started_turns.append,
                turn_control_provider=(
                    lambda: controls.pop(0) if controls else None
                ),
                turn_control_result_callback=(
                    lambda control, applied, error: control_results.append(
                        (control, applied, error)
                    )
                ),
            )

        steer = next(
            request
            for request in server.requests
            if request.get("method") == "turn/steer"
        )
        self.assertEqual(result.turn_id, active_turn_id)
        self.assertEqual(result.response, "recovered finished")
        self.assertEqual(started_turns, [active_turn_id])
        self.assertNotIn("turn/start", server.methods)
        self.assertEqual(
            steer["params"]["expectedTurnId"],
            active_turn_id,
        )
        self.assertEqual(control_results[0][1:], (True, ""))
        client.close()

    def test_run_turn_waits_for_compaction_conflict_then_retries_once(self) -> None:
        """turn/start 与进行中的压缩冲突时，等压缩结束后重试一次"""
        server = FakeAppServer(
            self.socket_path,
            compact_conflict_on_first_turn_start=True,
            complete_active_turn_on_read=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)

        with mock.patch(
            "management_portal.codex_app_server_turn_follow.TURN_CONTROL_POLL_INTERVAL_SECONDS",
            0.01,
        ):
            result = client.run_turn(
                "019f0000-0000-7000-8000-000000000200",
                "continue after compact",
                cwd="/tmp",
            )

        self.assertEqual(result.response, "finished")
        self.assertEqual(server.methods.count("turn/start"), 2)
        self.assertGreaterEqual(server.methods.count("thread/turns/list"), 3)
        client.close()

    def test_run_turn_defers_when_provider_compaction_remains_active(self) -> None:
        """Provider 压缩一直未结束时 run_turn 报 CodexCompactionInProgress 推迟本轮"""
        server = FakeAppServer(
            self.socket_path,
            compact_conflict_on_first_turn_start=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)

        with (
            mock.patch(
                "management_portal.codex_app_server_turns."
                "TURN_COMPACTION_CONFLICT_WAIT_SECONDS",
                0.03,
            ),
            mock.patch(
                "management_portal.codex_app_server_turns."
                "COMPACTION_RELEASE_POLL_INTERVAL_SECONDS",
                0.005,
            ),
            self.assertRaises(CodexCompactionInProgress),
        ):
            client.run_turn(
                "019f0000-0000-7000-8000-000000000200",
                "continue after long compact",
                cwd="/tmp",
            )

        self.assertEqual(server.methods.count("turn/start"), 1)
        self.assertGreaterEqual(server.methods.count("thread/turns/list"), 2)
        client.close()

    def test_resume_turn_realigns_stale_checkpoint_to_active_turn(self) -> None:
        """检查点中的轮次已过期时，恢复改接当前进行中的轮次并投递插话"""
        stale_turn_id = "019f0000-0000-7000-8000-000000000201"
        active_turn_id = "019f0000-0000-7000-8000-000000000888"
        server = FakeAppServer(
            self.socket_path,
            active_turn_id=active_turn_id,
            complete_active_turn_on_steer=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)
        started_turns: list[str] = []
        controls = [{
            "control_id": "control-resumed",
            "expected_turn_id": active_turn_id,
            "input": [{"type": "text", "text": "resume steering"}],
        }]
        control_results: list[tuple[dict, bool, str]] = []

        with mock.patch(
            "management_portal.codex_app_server_turn_follow.TURN_CONTROL_POLL_INTERVAL_SECONDS",
            0.01,
        ):
            result = client.resume_turn(
                "019f0000-0000-7000-8000-000000000200",
                stale_turn_id,
                cwd="/tmp",
                turn_started_callback=started_turns.append,
                turn_control_provider=(
                    lambda: controls.pop(0) if controls else None
                ),
                turn_control_result_callback=(
                    lambda control, applied, error: control_results.append(
                        (control, applied, error)
                    )
                ),
            )

        self.assertEqual(result.turn_id, active_turn_id)
        self.assertEqual(result.response, "recovered finished")
        self.assertEqual(started_turns, [active_turn_id])
        self.assertEqual(control_results[0][1:], (True, ""))
        client.close()

    def test_resume_turn_has_an_optional_reconciliation_deadline(self) -> None:
        """恢复轮次的对账可设截止时间，超时后清理等待状态"""
        thread_id = "019f0000-0000-7000-8000-000000000209"
        turn_id = "019f0000-0000-7000-8000-000000000210"
        client = CodexAppServerClient(self.socket_path)
        client.rpc.request = mock.Mock(return_value={})
        client.reads.read_active_turn = mock.Mock(
            return_value={"id": turn_id, "status": "inProgress"}
        )
        client.reads.read_task_complete_goal = mock.Mock(return_value=None)
        client.follower.read_terminal_turn = mock.Mock(return_value=None)

        with mock.patch(
            "management_portal.codex_app_server_turn_follow."
            "TURN_COMPLETION_RECONCILE_INTERVAL_SECONDS",
            0.01,
        ):
            with self.assertRaises(TimeoutError):
                client.resume_turn(
                    thread_id,
                    turn_id,
                    cwd="/tmp",
                    timeout_seconds=0.03,
                )

        self.assertNotIn(thread_id, client.work.turns)
        client.close()

    def test_resume_turn_replays_items_completed_while_disconnected(self) -> None:
        """恢复轮次时补发断线期间已完成的条目"""
        thread_id = "019f0000-0000-7000-8000-000000000209"
        turn_id = "019f0000-0000-7000-8000-000000000210"
        missed_item = {
            "id": "item-missed-during-handover",
            "type": "agentMessage",
            "phase": "commentary",
            "text": "work continued while the worker was disconnected",
        }
        final_item = {
            "id": "item-final-after-handover",
            "type": "agentMessage",
            "phase": "final_answer",
            "text": "finished after reconnect",
        }
        snapshots = iter([
            {
                "data": [{
                    "id": turn_id,
                    "status": "inProgress",
                    "items": [missed_item],
                }],
            },
            {
                "data": [{
                    "id": turn_id,
                    "status": "completed",
                    "items": [missed_item, final_item],
                }],
            },
        ])
        client = CodexAppServerClient(self.socket_path)
        client.rpc.request = mock.Mock(
            side_effect=lambda method, *_args, **_kwargs: (
                next(snapshots)
                if method == "thread/turns/list"
                else {}
            )
        )
        client.reads.read_active_turn = mock.Mock(
            return_value={"id": turn_id, "status": "inProgress"}
        )
        client.reads.read_task_complete_goal = mock.Mock(return_value=None)
        events: list[dict] = []

        with mock.patch(
            "management_portal.codex_app_server_turn_follow."
            "TURN_COMPLETION_RECONCILE_INTERVAL_SECONDS",
            0.01,
        ):
            result = client.resume_turn(
                thread_id,
                turn_id,
                cwd="/tmp",
                event_callback=events.append,
            )

        completed_item_ids = [
            event["params"]["item"]["id"]
            for event in events
            if event.get("method") == "item/completed"
        ]
        self.assertEqual(
            completed_item_ids,
            [missed_item["id"], final_item["id"]],
        )
        self.assertTrue(all(
            event.get("origin") == "turn_snapshot"
            for event in events if event.get("method") == "item/completed"
        ))
        self.assertEqual(result.response, final_item["text"])
        client.close()

    def test_resume_turn_follows_a_compaction_successor(self) -> None:
        """恢复轮次遇到压缩轮次时跟到其后继轮次取最终回答"""
        thread_id = "019f0000-0000-7000-8000-000000000205"
        compact_turn_id = "019f0000-0000-7000-8000-000000000206"
        successor_turn_id = "019f0000-0000-7000-8000-000000000207"
        client = CodexAppServerClient(self.socket_path)
        client.rpc.request = mock.Mock(return_value={})
        client.reads.read_task_complete_goal = mock.Mock(return_value=None)
        client.reads.read_active_turn = mock.Mock(side_effect=[
            {"id": compact_turn_id, "status": "inProgress"},
            {"id": successor_turn_id, "status": "inProgress"},
        ])
        client.follower.read_terminal_turn = mock.Mock(side_effect=[
            {
                "id": compact_turn_id,
                "status": "completed",
                "items": [{"type": "contextCompaction"}],
            },
            {
                "id": successor_turn_id,
                "status": "completed",
                "items": [{
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "continued after compaction",
                }],
            },
        ])
        started_turns: list[str] = []

        result = client.resume_turn(
            thread_id,
            "stale-checkpoint-turn",
            cwd="/tmp",
            turn_started_callback=started_turns.append,
        )

        self.assertEqual(result.turn_id, successor_turn_id)
        self.assertEqual(result.response, "continued after compaction")
        self.assertEqual(started_turns, [compact_turn_id, successor_turn_id])
        client.close()

    def test_resume_turn_reports_compaction_without_successor(self) -> None:
        """恢复时压缩轮次之后没有后继轮次，报缺少最终回答并要求续跑"""
        thread_id = "019f0000-0000-7000-8000-000000000208"
        compact_turn_id = "019f0000-0000-7000-8000-000000000209"
        client = CodexAppServerClient(self.socket_path)
        client.rpc.request = mock.Mock(return_value={})
        client.reads.read_task_complete_goal = mock.Mock(return_value=None)
        active_reads = iter([
            {"id": compact_turn_id, "status": "inProgress"},
            None,
        ])
        client.reads.read_active_turn = mock.Mock(
            side_effect=lambda _thread_id: next(active_reads, None)
        )
        client.follower.read_terminal_turn = mock.Mock(return_value={
            "id": compact_turn_id,
            "status": "completed",
            "items": [{"type": "contextCompaction"}],
        })

        with mock.patch(
            "management_portal.codex_app_server_turn_follow."
            "TURN_SUCCESSOR_RECONCILE_GRACE_SECONDS",
            0.01,
        ), self.assertRaises(CodexTurnContinuationRequired) as raised:
            client.resume_turn(
                thread_id,
                "stale-checkpoint-turn",
                cwd="/tmp",
            )
        self.assertEqual(raised.exception.reason, "missing_final_response")
        client.close()

    def test_resume_turn_continues_completed_turn_without_final_response(self) -> None:
        """恢复的轮次已完成但没有最终回答时要求续跑"""
        thread_id = "019f0000-0000-7000-8000-000000000210"
        turn_id = "019f0000-0000-7000-8000-000000000211"
        client = CodexAppServerClient(self.socket_path)
        client.rpc.request = mock.Mock(return_value={})
        client.reads.read_task_complete_goal = mock.Mock(return_value=None)
        client.reads.read_active_turn = mock.Mock(return_value=None)
        client.follower.read_terminal_turn = mock.Mock(return_value={
            "id": turn_id,
            "status": "completed",
            "items": [{"type": "reasoning"}],
        })

        with mock.patch(
            "management_portal.codex_app_server_turn_follow."
            "TURN_SUCCESSOR_RECONCILE_GRACE_SECONDS",
            0.01,
        ), self.assertRaises(CodexTurnContinuationRequired) as raised:
            client.resume_turn(thread_id, turn_id, cwd="/tmp")

        self.assertEqual(raised.exception.reason, "missing_final_response")
        client.close()

    def test_run_turn_control_requests_ignore_generic_timeout(self) -> None:
        """thread/resume 和 turn/start 请求不受通用请求超时限制"""
        server = FakeAppServer(
            self.socket_path,
            resume_delay_seconds=0.06,
            turn_start_delay_seconds=0.06,
        )
        server.start()
        client = CodexAppServerClient(
            self.socket_path,
            request_timeout_seconds=1,
        )
        client.timeouts.request = 0.02

        result = client.run_turn(
            "019f0000-0000-7000-8000-000000000210",
            "hello",
            cwd="/tmp",
        )

        self.assertEqual(result.response, "finished")
        self.assertIn("thread/resume", server.methods)
        self.assertIn("turn/start", server.methods)
        client.close()

    def test_turn_start_has_separate_extended_timeout(self) -> None:
        """turn/start 使用单独的 360 秒请求时限"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)
        try:
            self.assertEqual(client.timeouts.turn_start, 360.0)
            self.assertEqual(client.timeouts.lifecycle, 180.0)
            with mock.patch.object(client.rpc, "request", wraps=client.rpc.request) as requests:
                result = client.run_turn(
                    "019f0000-0000-7000-8000-000000000219", "hello", cwd="/tmp",
                )
            self.assertEqual(result.response, "finished")
            starts = [call for call in requests.call_args_list if call.args[0] == "turn/start"]
            self.assertEqual([call.kwargs["timeout"] for call in starts], [360.0])
        finally:
            client.close()

    def test_turn_start_timeout_can_be_overridden_independently(self) -> None:
        """turn/start 请求时限可单独覆盖，不影响生命周期请求时限"""
        client = CodexAppServerClient(
            self.socket_path, turn_start_request_timeout_seconds=0.25,
        )
        self.assertEqual(client.timeouts.turn_start, 0.25)
        self.assertEqual(client.timeouts.lifecycle, 180.0)
        client.close()

    def test_run_turn_resume_request_uses_lifecycle_timeout(self) -> None:
        """run_turn 的 thread/resume 请求按生命周期请求时限超时"""
        server = FakeAppServer(
            self.socket_path,
            resume_delay_seconds=0.08,
        )
        server.start()
        client = CodexAppServerClient(
            self.socket_path,
            request_timeout_seconds=1,
            lifecycle_request_timeout_seconds=0.02,
        )

        with self.assertRaisesRegex(TimeoutError, "thread/resume timed out"):
            client.run_turn(
                "019f0000-0000-7000-8000-000000000211",
                "hello",
                cwd="/tmp",
            )

        # Let the fake server finish its delayed response before closing the
        # socket so this timeout assertion cannot leak a BrokenPipe warning
        # into a later test.
        time.sleep(0.12)
        client.close()

    def test_local_thread_lock_wait_can_be_cancelled(self) -> None:
        """等待进程内的线程锁时可以被取消"""
        client = CodexAppServerClient(self.socket_path)
        thread_id = "019f0000-0000-7000-8000-000000000214"
        held_lock = client.threads.lock_for(thread_id)
        held_lock.acquire()
        cancellation = threading.Event()
        waiting = threading.Event()
        errors: list[BaseException] = []

        def wait_for_lock() -> None:
            waiting.set()
            try:
                with client.threads.locked(
                    thread_id,
                    cancellation_requested=cancellation.is_set,
                ):
                    pass
            except BaseException as exc:
                errors.append(exc)

        waiter = threading.Thread(target=wait_for_lock, daemon=True)
        try:
            with mock.patch(
                "management_portal.codex_app_server_threads."
                "THREAD_LOCK_POLL_INTERVAL_SECONDS",
                0.01,
            ):
                waiter.start()
                self.assertTrue(waiting.wait(timeout=1))
                cancellation.set()
                waiter.join(timeout=1)
        finally:
            held_lock.release()
            client.close()

        self.assertFalse(waiter.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], InterruptedError)

    def test_run_turn_rejects_accepted_turn_missing_from_provider_state(self) -> None:
        """已接受的轮次在 Provider 状态中查不到时报连接丢失，并带上线程和轮次 ID"""
        thread_id = "019f0000-0000-7000-8000-000000000212"
        turn_id = "019f0000-0000-7000-8000-000000000213"
        client = CodexAppServerClient(self.socket_path)

        def request(method, _params, **_kwargs):
            if method == "turn/start":
                return {"turn": {"id": turn_id, "status": "inProgress"}}
            return {}

        client.rpc.request = mock.Mock(side_effect=request)
        client.reads.read_active_turn = mock.Mock(return_value=None)
        client.follower.read_terminal_turn = mock.Mock(return_value=None)

        with (
            mock.patch(
                "management_portal.codex_app_server_turn_follow."
                "TURN_COMPLETION_RECONCILE_INTERVAL_SECONDS",
                0.001,
            ),
            mock.patch(
                "management_portal.codex_app_server_turn_follow."
                "TURN_ACTIVE_RECONCILE_INTERVAL_SECONDS",
                0.001,
            ),
            mock.patch(
                "management_portal.codex_app_server_turn_follow."
                "TURN_ACCEPTED_MISSING_GRACE_SECONDS",
                0.01,
            ),
            self.assertRaises(CodexTurnConnectionLost) as raised,
        ):
            client.run_turn(thread_id, "continue", cwd="/tmp")

        self.assertEqual(raised.exception.thread_id, thread_id)
        self.assertEqual(raised.exception.turn_id, turn_id)
        client.close()

    def test_dynamic_tool_request_is_dispatched_without_reader_deadlock(
        self,
    ) -> None:
        """动态工具处理中再向 app-server 发请求时不造成读取线程死锁"""
        server = FakeAppServer(
            self.socket_path,
            call_dynamic_tool=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)

        def dispatch(params: dict) -> dict:
            completed = client.execute_command({
                "command": [
                    "/usr/bin/python3",
                    "runtime/c1_tool_dispatch.py",
                    params["tool"],
                    json.dumps(params["arguments"]),
                ],
                "cwd": "/tmp",
                "permissionProfile": "portal-closed-cwd",
            })
            return {
                "success": completed["exitCode"] == 0,
                "contentItems": [
                    {
                        "type": "inputText",
                        "text": completed["stdout"],
                    }
                ],
            }

        result = client.run_turn(
            "019f0000-0000-7000-8000-000000000209",
            "hello",
            cwd="/tmp",
            permissions="portal-closed-cwd",
            dynamic_tool_handler=dispatch,
        )

        self.assertEqual(result.response, "finished")
        self.assertIn("command/exec", server.methods)
        self.assertEqual(
            server.dynamic_tool_response,
            {
                "id": 9001,
                "result": {
                    "success": True,
                    "contentItems": [
                        {
                            "type": "inputText",
                            "text": "{\"ok\":true}",
                        }
                    ],
                },
            },
        )
        client.close()

    def test_run_turn_forwards_bounded_input_image_detail(self) -> None:
        """run_turn 把图片 detail 等级传给本地和远程图片输入"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)

        result = client.run_turn(
            "019f0000-0000-7000-8000-000000000219",
            "inspect",
            cwd="/tmp",
            images=["/tmp/large.png"],
            image_urls=["https://example.test/large.png"],
            image_detail="high",
        )

        self.assertEqual(result.response, "finished")
        turn_start = next(
            request
            for request in server.requests
            if request.get("method") == "turn/start"
        )
        self.assertEqual(
            turn_start["params"]["input"],
            [
                {"type": "text", "text": "inspect"},
                {
                    "type": "localImage",
                    "path": "/tmp/large.png",
                    "detail": "high",
                },
                {
                    "type": "image",
                    "url": "https://example.test/large.png",
                    "detail": "high",
                },
            ],
        )
        client.close()

    def test_run_turn_forwards_structured_sandbox_policy_and_approval(self) -> None:
        """每轮的沙箱策略和审批策略传给 thread/resume 和 turn/start，切换后按新策略提交"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)
        sandbox_policy = {
            "type": "workspaceWrite",
            "writableRoots": ["/tmp/workspace"],
            "networkAccess": False,
        }

        client.run_turn(
            "019f0000-0000-7000-8000-000000000206",
            "hello",
            cwd="/tmp/workspace",
            sandbox_policy=sandbox_policy,
            approval_policy="never",
        )

        resume_request = next(
            request
            for request in server.requests
            if request.get("method") == "thread/resume"
        )
        turn_request = next(
            request
            for request in server.requests
            if request.get("method") == "turn/start"
        )
        self.assertEqual(
            resume_request["params"]["sandbox"],
            "workspace-write",
        )
        self.assertEqual(
            resume_request["params"]["approvalPolicy"],
            "never",
        )
        self.assertEqual(
            turn_request["params"]["sandboxPolicy"],
            sandbox_policy,
        )
        self.assertEqual(
            turn_request["params"]["approvalPolicy"],
            "never",
        )
        client.run_turn(
            "019f0000-0000-7000-8000-000000000206",
            "continue without filesystem sandbox",
            cwd="/tmp/workspace",
            sandbox_policy={"type": "dangerFullAccess"},
            approval_policy="never",
        )
        continued = next(request for request in reversed(server.requests) if request.get("method") == "turn/start")
        self.assertEqual(continued["params"]["sandboxPolicy"], {"type": "dangerFullAccess"})
        self.assertEqual(continued["params"]["approvalPolicy"], "never")
        client.run_turn(
            "019f0000-0000-7000-8000-000000000207",
            "resume another session without filesystem sandbox",
            cwd="/tmp/workspace",
            sandbox_policy={"type": "dangerFullAccess"},
            approval_policy="never",
        )
        resumed = next(request for request in reversed(server.requests) if request.get("method") == "thread/resume")
        self.assertEqual(resumed["params"]["sandbox"], "danger-full-access")
        self.assertEqual(resumed["params"]["approvalPolicy"], "never")
        client.run_turn(
            "019f0000-0000-7000-8000-000000000207",
            "continue with authorized workspace sandbox",
            cwd="/tmp/workspace",
            sandbox_policy=sandbox_policy,
            approval_policy="never",
        )
        restricted = next(request for request in reversed(server.requests) if request.get("method") == "turn/start")
        self.assertEqual(restricted["params"]["sandboxPolicy"], sandbox_policy)
        self.assertEqual(restricted["params"]["approvalPolicy"], "never")
        client.close()

    def test_run_turn_reloads_thread_when_project_instruction_config_changes(self) -> None:
        """项目说明配置变化时 run_turn 退订线程并用新配置重新 resume"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)
        thread_id = "019f0000-0000-7000-8000-000000000205"

        client.run_turn(
            thread_id,
            "without project instructions",
            cwd="/tmp",
            config={"project_doc_max_bytes": 0},
        )
        client.run_turn(
            thread_id,
            "with project instructions",
            cwd="/tmp",
            config={},
        )

        resume_requests = [
            request
            for request in server.requests
            if request.get("method") == "thread/resume"
        ]
        self.assertEqual(
            [request["params"].get("config", {}) for request in resume_requests],
            [{"project_doc_max_bytes": 0}, {}],
        )
        self.assertNotIn('config/read', server.methods)
        self.assertNotIn('account/read', server.methods)
        self.assertEqual(server.methods.count("thread/unsubscribe"), 1)
        client.close()

    def test_new_zero_turn_thread_defers_config_reload_until_materialized(self) -> None:
        """新线程在第一轮落盘前不重载配置，第二轮才退订并 resume"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)
        started = client.start_thread({
            "cwd": "/tmp",
            "config": {"model_auto_compact_token_limit": 200_000},
        })
        thread_id = started["thread"]["id"]

        first = client.run_turn(
            thread_id,
            "materialize before reloading config",
            cwd="/tmp",
            config={"model_auto_compact_token_limit": 180_880},
        )

        self.assertEqual(first.response, "finished")
        self.assertNotIn("thread/unsubscribe", server.methods)
        self.assertNotIn("thread/resume", server.methods)

        second = client.run_turn(
            thread_id,
            "reload after rollout exists",
            cwd="/tmp",
            config={"model_auto_compact_token_limit": 180_880},
        )

        self.assertEqual(second.response, "finished")
        self.assertEqual(server.methods.count("thread/unsubscribe"), 1)
        self.assertEqual(server.methods.count("thread/resume"), 1)
        client.close()

    def test_disconnect_after_turn_start_is_ambiguous_and_never_falls_back(self) -> None:
        """turn/start 后连接断开时报 CodexTurnConnectionLost，不重新提交"""
        server = FakeAppServer(self.socket_path, disconnect_on_turn_start=True)
        server.start()
        client = CodexAppServerClient(self.socket_path)
        with self.assertRaises(CodexTurnConnectionLost):
            client.run_turn(
                "019f0000-0000-7000-8000-000000000202",
                "hello",
                cwd="/tmp",
            )
        client.close()

    def test_interrupted_turn_without_provider_error_has_structured_reason(self) -> None:
        """没有 Provider 错误的中断轮次报 provider_turn_interrupted 结构化原因"""
        server = FakeAppServer(
            self.socket_path,
            turn_status="interrupted",
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)

        with self.assertRaises(CodexTurnInterrupted) as raised:
            client.run_turn(
                "019f0000-0000-7000-8000-000000000202",
                "hello",
                cwd="/tmp",
            )

        self.assertEqual(
            str(raised.exception),
            "Codex turn interrupted without a provider reason",
        )
        self.assertEqual(
            raised.exception.error_code,
            "provider_turn_interrupted",
        )
        self.assertEqual(
            raised.exception.error_details["provider_status"],
            "interrupted",
        )
        self.assertNotIn("unknown error", str(raised.exception))
        client.close()

    def test_capacity_failure_is_typed_as_retryable(self) -> None:
        """模型容量不足的失败归类为可重试的 capacity 失败"""
        error = terminal_turn_error(
            "thread-capacity",
            {
                "id": "turn-capacity",
                "status": "failed",
                "error": {
                    "message": (
                        "Selected model is at capacity. "
                        "Please try a different model."
                    ),
                },
            },
        )

        self.assertIsInstance(error, CodexTurnRetryableFailure)
        self.assertEqual(error.failure_kind, "capacity")
        self.assertEqual(
            error.error_details["provider_failure_kind"],
            "capacity",
        )

    def test_safety_block_failure_is_terminal_not_retryable(self) -> None:
        """安全系统拦截的失败归类为不可重试的 provider_safety_blocked"""
        error = terminal_turn_error(
            "thread-safety",
            {
                "id": "turn-safety",
                "status": "failed",
                "error": {
                    "message": (
                        "This request was blocked by our safety systems. "
                        "Reason: Potentially unintended activity."
                    ),
                },
            },
        )

        self.assertEqual(error.error_code, "provider_safety_blocked")
        self.assertNotIsInstance(error, CodexTurnRetryableFailure)
        self.assertIs(error.error_details["retryable"], False)

    def test_authentication_error_overrides_provider_retry_hint(self) -> None:
        """令牌失效的认证错误即使 Provider 标记会重试，也按不可重试的认证失败处理"""
        message = (
            "Your access token could not be refreshed because your refresh token was revoked. "
            "Please log out and sign in again."
        )
        server = FakeAppServer(
            self.socket_path,
            error_notifications=[{"error": {"message": message}, "willRetry": True}],
            omit_final_answer=True,
            omit_turn_completed=True,
            omit_idle_notification=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)
        try:
            with self.assertRaises(CodexTurnAuthenticationError) as raised:
                client.run_turn("019f0000-0000-7000-8000-000000000200", "hello", cwd="/tmp")
            self.assertIn(message, str(raised.exception))
            self.assertEqual(raised.exception.error_code, "provider_authentication_failed")
            self.assertIs(raised.exception.error_details["retryable"], False)
            self.assertEqual(server.turn_start_attempts, 1)
        finally:
            client.close()

    def test_structured_authentication_errors_are_terminal(self) -> None:
        """unauthorized 结构化错误码按不可重试的认证失败处理"""
        for error in (
            {"codexErrorInfo": "unauthorized"},
        ):
            with self.subTest(error=error):
                failure = terminal_turn_error("thread-auth", {
                    "id": "turn-auth", "status": "completed", "error": error,
                })
                self.assertIsInstance(failure, CodexTurnAuthenticationError)
                self.assertIs(failure.error_details["retryable"], False)

    def test_provider_terminal_error_is_not_replaced_by_missing_final(self) -> None:
        """Provider 用量上限错误不被缺少最终回答的续跑要求取代

        :design: decision_6ad0384e020d
        :design-point:
        """
        message = "You've hit your usage limit. Try again later."
        server = FakeAppServer(
            self.socket_path,
            error_notifications=[{"error": {"message": message}, "willRetry": False}],
            omit_final_answer=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)
        try:
            with self.assertRaises(CodexAppServerError) as raised:
                client.run_turn(
                    "019f0000-0000-7000-8000-000000000200", "hello", cwd="/tmp",
                )
            self.assertNotIsInstance(raised.exception, CodexTurnContinuationRequired)
            self.assertIn(message, str(raised.exception))
            self.assertEqual(raised.exception.error_code, "provider_usage_limit")
            self.assertIs(raised.exception.error_details["retryable"], False)
        finally:
            client.close()

    def test_safety_rejection_overrides_turn_status_and_structured_code(self) -> None:
        """带安全策略错误码的轮次无论状态如何都按安全拦截处理"""
        for status in ("failed", "interrupted", "completed"):
            with self.subTest(status=status):
                error = terminal_turn_error("thread-safety", {
                    "id": "turn-safety", "status": status,
                    "error": {"codexErrorInfo": "misalignmentPolicyViolation"},
                })
                self.assertIsInstance(error, CodexTurnSafetyBlocked)
                self.assertIs(error.error_details["retryable"], False)

    def test_safety_notification_does_not_require_will_retry_false(self) -> None:
        """安全拦截通知无论 willRetry 取值都结束本轮且不重新提交"""
        message = (
            "This request was blocked by our safety systems. "
            "Reason: Potentially unintended activity."
        )
        for will_retry in (False, True, None):
            with self.subTest(will_retry=will_retry):
                notification = {"error": {"message": message}}
                if will_retry is not None:
                    notification["willRetry"] = will_retry
                socket_path = self.socket_path.with_name(f"safety-{will_retry}.sock")
                server = FakeAppServer(
                    socket_path, error_notifications=[notification],
                    omit_final_answer=True,
                )
                server.start()
                client = CodexAppServerClient(socket_path)
                try:
                    with self.assertRaises(CodexTurnSafetyBlocked) as raised:
                        client.run_turn(
                            "019f0000-0000-7000-8000-000000000200", "hello", cwd="/tmp",
                        )
                    self.assertEqual(raised.exception.error_details["provider_reason"], message)
                    self.assertEqual(server.methods.count("turn/start"), 1)
                finally:
                    client.close()

    def test_safety_error_survives_later_notifications_and_disconnect(self) -> None:
        """安全拦截错误不被之后的错误通知、systemError、完成通知或断线覆盖"""
        client = CodexAppServerClient(self.socket_path)
        waiter = TurnWaiter(thread_id="thread-safety", turn_id="turn-safety")
        client.work.turns[waiter.thread_id] = waiter
        client.events.dispatch_notification("error", {
            "threadId": waiter.thread_id, "turnId": waiter.turn_id,
            "willRetry": False, "error": {"codexErrorInfo": "cyberPolicy"},
        })
        original_error = waiter.error
        self.assertIsInstance(original_error, CodexTurnSafetyBlocked)
        client.events.dispatch_notification("error", {
            "threadId": waiter.thread_id, "turnId": waiter.turn_id,
            "willRetry": False, "error": {"message": "stream disconnected"},
        })
        client.events.dispatch_notification("thread/status/changed", {
            "threadId": waiter.thread_id, "status": {"type": "systemError"},
        })
        client.events.dispatch_notification("turn/completed", {
            "threadId": waiter.thread_id,
            "turn": {"id": waiter.turn_id, "status": "completed", "items": []},
        })
        client.disconnect(ConnectionError("transport closed"))
        self.assertIs(waiter.error, original_error)
        client.close()

    def test_rpc_safety_rejection_preserves_terminal_details_on_disconnect(self) -> None:
        """RPC 被安全拦截后断线，请求仍保留不可重试的安全拦截错误"""
        client = CodexAppServerClient(self.socket_path)
        pending = PendingRequest()
        client.rpc._pending[17] = pending
        client.rpc.dispatch_message({"id": 17, "error": {
            "message": "This request was blocked by our safety systems.",
        }})
        client.disconnect(ConnectionError("transport closed"))
        self.assertIsInstance(pending.error, CodexTurnSafetyBlocked)
        self.assertIs(pending.error.error_details["retryable"], False)
        client.close()

    def test_goal_and_compact_preserve_provider_safety_rejection(self) -> None:
        """goal 和压缩收到安全拦截错误后保留该错误，断线也不覆盖"""
        client = CodexAppServerClient(self.socket_path)
        goal = GoalWaiter(thread_id="thread-goal")
        compact = CompactWaiter(thread_id="thread-compact")
        client.work.goals[goal.thread_id] = goal
        client.work.compactions[compact.thread_id] = compact
        for waiter in (goal, compact):
            client.events.dispatch_notification("error", {
                "threadId": waiter.thread_id,
                "error": {"message": "This request was blocked by our safety systems."},
                "willRetry": True,
            })
            self.assertIsInstance(waiter.error, CodexTurnSafetyBlocked)
            self.assertTrue(waiter.event.is_set())
        client.disconnect(ConnectionError("closed"))
        for waiter in (goal, compact):
            self.assertIsInstance(waiter.error, CodexTurnSafetyBlocked)
        client.close()

    def test_completed_turn_cannot_hide_safety_error(self) -> None:
        """轮次显示完成但带安全策略错误时仍报 CodexTurnSafetyBlocked"""
        server = FakeAppServer(
            self.socket_path, omit_final_answer=True,
            turn_error={"codexErrorInfo": "misalignmentPolicyViolation"},
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)
        try:
            with self.assertRaises(CodexTurnSafetyBlocked):
                client.run_turn(
                    "019f0000-0000-7000-8000-000000000200", "hello", cwd="/tmp",
                )
        finally:
            client.close()

    def test_resume_turn_does_not_replace_safety_notification_with_empty_completion(self) -> None:
        """恢复轮次时安全拦截通知不被空的完成结果覆盖"""
        thread_id, turn_id = "thread-safety", "turn-safety"
        client = CodexAppServerClient(self.socket_path)
        client.rpc.request = mock.Mock(return_value={})
        client.reads.read_task_complete_goal = mock.Mock(return_value=None)
        client.reads.read_active_turn = mock.Mock(return_value=None)

        def terminal_turn(_waiter):
            client.events.dispatch_notification("error", {
                "threadId": thread_id, "turnId": turn_id,
                "error": {"codexErrorInfo": "misalignmentPolicyViolation"},
            })
            return {"id": turn_id, "status": "completed", "items": []}

        client.follower.read_terminal_turn = mock.Mock(side_effect=terminal_turn)
        try:
            with self.assertRaises(CodexTurnSafetyBlocked):
                client.resume_turn(thread_id, turn_id, cwd="/tmp")
        finally:
            client.close()

    def test_provider_retry_notification_allows_successful_final(self) -> None:
        """Provider 发出会重试的错误通知后，轮次仍可正常完成

        :design: decision_6ad0384e020d
        :design-point:
        """
        server = FakeAppServer(
            self.socket_path,
            error_notifications=[{"error": {"message": "temporary failure"}, "willRetry": True}],
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)
        try:
            result = client.run_turn(
                "019f0000-0000-7000-8000-000000000200", "hello", cwd="/tmp",
            )
            self.assertEqual(result.response, "finished")
        finally:
            client.close()

    def test_provider_terminal_error_ignores_another_turn_and_survives_system_error(self) -> None:
        """Provider 结束错误只作用于当前轮次，之后的 systemError 状态不覆盖它

        :design: decision_6ad0384e020d
        :design-point:
        """
        client = CodexAppServerClient(self.socket_path)
        waiter = TurnWaiter(thread_id="thread-error", turn_id="turn-current")
        client.work.turns[waiter.thread_id] = waiter
        params = {
            "threadId": waiter.thread_id, "turnId": "turn-old", "willRetry": False,
            "error": {"message": "You've hit your usage limit."},
        }
        client.events.dispatch_notification("error", params)
        self.assertIsNone(waiter.error)
        self.assertFalse(waiter.event.is_set())
        client.events.dispatch_notification("error", {**params, "turnId": waiter.turn_id})
        original_error = waiter.error
        self.assertIsNotNone(original_error)
        client.events.dispatch_notification("thread/status/changed", {
            "threadId": waiter.thread_id, "status": {"type": "systemError"},
        })
        self.assertIs(waiter.error, original_error)
        self.assertTrue(waiter.event.is_set())
        client.close()

        for historical_status in ("failed", "completed"):
            with self.subTest(historical_status=historical_status):
                client = CodexAppServerClient(self.socket_path)
                events = []
                submitted = []
                accepted = []
                old_turn = {
                    "id": "turn-old", "status": historical_status, "items": [],
                    "error": {"message": "You've hit your usage limit."} if historical_status == "failed" else None,
                }
                new_turn = {
                    "id": "turn-new", "status": "completed", "error": None,
                    "items": [{"type": "agentMessage", "text": "new reply", "phase": "final_answer"}],
                }

                def request(method, params, **_kwargs):
                    if method == "thread/resume":
                        return {"thread": {"id": "thread-error"}}
                    if method == "thread/turns/list":
                        client.events.dispatch_notification("thread/tokenUsage/updated", {
                            "threadId": "thread-error", "turnId": "turn-old",
                            "tokenUsage": {"last": {"totalTokens": 900}},
                        })
                        return {"data": [old_turn], "nextCursor": None}
                    if method == "turn/start":
                        submitted.append(params["input"])
                        client.events.dispatch_notification("turn/completed", {"threadId": "thread-error", "turn": old_turn})
                        client.events.dispatch_notification("turn/started", {
                            "threadId": "thread-error", "turn": {"id": "turn-new", "status": "inProgress"},
                        })
                        client.events.dispatch_notification("turn/completed", {"threadId": "thread-error", "turn": new_turn})
                        return {"turn": {"id": "turn-new", "status": "inProgress"}}
                    self.fail(f"unexpected Provider request: {method}")

                client.rpc.request = request
                try:
                    with mock.patch("management_portal.codex_app_server_turn_follow.TURN_COMPLETION_RECONCILE_INTERVAL_SECONDS", 0.01):
                        result = client.run_turn(
                            "thread-error", "new user input", cwd="/tmp",
                            event_callback=events.append, turn_started_callback=accepted.append,
                        )
                    self.assertEqual(result.response, "new reply")
                    self.assertEqual(result.turn_id, "turn-new")
                    self.assertEqual(accepted, ["turn-new"])
                    self.assertEqual(submitted, [[{"type": "text", "text": "new user input"}]])
                    self.assertEqual([event["method"] for event in events], [
                        "turn/started", "turn/completed", "portal/provider_request_accepted",
                    ])
                    self.assertEqual(events[-1]["params"], {
                        "threadId": "thread-error", "turnId": "turn-new", "rpcMethod": "turn/start",
                    })
                    self.assertEqual(result.token_usage, {})
                finally:
                    client.close()

    def test_permission_failure_is_not_typed_as_retryable(self) -> None:
        """执行权限被拒的失败不归类为可重试"""
        error = terminal_turn_error(
            "thread-permission",
            {
                "id": "turn-permission",
                "status": "failed",
                "error": {"message": "execution permission denied"},
            },
        )

        self.assertNotIsInstance(error, CodexTurnRetryableFailure)

    def test_idle_thread_reconciles_terminal_turn_when_completion_notification_is_missing(self) -> None:
        """缺少 turn/completed 通知时，线程转为空闲后对账得到本轮结果"""
        server = FakeAppServer(
            self.socket_path,
            omit_turn_completed=True,
            idle_notification_delay_seconds=0.03,
        )
        server.start()
        events: list[dict] = []
        client = CodexAppServerClient(self.socket_path)

        with mock.patch(
            "management_portal.codex_app_server_turn_follow.TURN_COMPLETION_RECONCILE_INTERVAL_SECONDS",
            0.01,
        ):
            result = client.run_turn(
                "019f0000-0000-7000-8000-000000000204",
                "hello",
                cwd="/tmp",
                event_callback=events.append,
            )

        self.assertEqual(result.response, "finished")
        self.assertIn("thread/turns/list", server.methods)
        self.assertEqual(events[-2]["method"], "thread/status/changed")
        self.assertEqual(events[-1]["method"], "turn/completed")
        self.assertEqual(events[-1]["params"]["turn"]["id"], result.turn_id)
        client.close()

    def test_active_turn_recovers_progress_before_completion_without_notifications(self) -> None:
        """缺少通知时进行中的轮次从快照补发已完成的进度消息，轮次完成前不提前结束"""
        thread_id = "019f0000-0000-7000-8000-000000000209"
        turn_id = "019f0000-0000-7000-8000-000000000210"
        progress = {
            "id": "item-missing-progress", "type": "agentMessage",
            "phase": "commentary", "text": "download is progressing",
        }
        partial = {
            "id": "item-still-streaming", "type": "agentMessage",
            "phase": "commentary", "text": "unfinished", "status": "inProgress",
        }
        active = {"id": turn_id, "status": "inProgress", "items": [progress, partial]}
        final = {"id": "item-final", "type": "agentMessage", "phase": "final_answer", "text": "done"}
        completed = {"id": turn_id, "status": "completed", "items": [progress, final]}
        observed, finish, cancel = threading.Event(), threading.Event(), threading.Event()
        events, results, failures = [], [], []
        client = CodexAppServerClient(self.socket_path)
        client.rpc.request = mock.Mock(return_value={"turn": {"id": turn_id, "status": "inProgress"}})
        def active_snapshot(*_args, **_kwargs):
            waiter = client.work.turns.get(thread_id)
            # The real active-turn query uses itemsView=summary by default.
            return {"id": turn_id, "status": "inProgress"} if waiter and waiter.turn_id and not finish.is_set() else None

        client.reads.read_active_turn = mock.Mock(side_effect=active_snapshot)
        client.reads.read_turn = mock.Mock(side_effect=lambda *_args: completed if finish.is_set() else active)

        def receive(event):
            events.append(event)
            if event.get("params", {}).get("item", {}).get("id") == progress["id"]:
                observed.set()

        def run():
            try:
                results.append(client.run_turn(
                    thread_id, "one input", cwd="/tmp", event_callback=receive,
                    cancellation_requested=cancel.is_set,
                ))
            except BaseException as error:
                failures.append(error)

        with mock.patch.multiple(
            "management_portal.codex_app_server_turn_follow",
            TURN_ACTIVE_RECONCILE_INTERVAL_SECONDS=0.01,
            TURN_COMPLETION_RECONCILE_INTERVAL_SECONDS=0.01,
        ):
            runner = threading.Thread(target=run, daemon=True)
            runner.start()
            try:
                self.assertTrue(observed.wait(1), failures)
                self.assertFalse(results)
                self.assertTrue(runner.is_alive())
                finish.set()
                runner.join(timeout=2)
                self.assertFalse(runner.is_alive())
                self.assertFalse(failures)
                self.assertEqual(results[0].response, "done")
                item_ids = [event.get("params", {}).get("item", {}).get("id") for event in events]
                self.assertEqual(item_ids.count(progress["id"]), 1)
                self.assertNotIn(partial["id"], item_ids)
                self.assertEqual([call.args[0] for call in client.rpc.request.call_args_list].count("turn/start"), 1)
            finally:
                cancel.set()
                runner.join(timeout=2)
                client.close()

    def test_failed_event_projection_can_replay_once_without_losing_the_item(self) -> None:
        """事件投影失败的条目在下次快照重放时补发一次，不丢失也不重复"""
        client = CodexAppServerClient(self.socket_path)
        item = {"id": "retry-item", "type": "agentMessage", "text": "saved", "phase": "commentary"}
        for initial_delivery in ("notification", "snapshot"):
            with self.subTest(initial_delivery=initial_delivery):
                callback = mock.Mock(side_effect=[RuntimeError("unavailable"), None])
                waiter = TurnWaiter(thread_id="thread-progress", turn_id="turn-progress", event_callback=callback)
                client.work.turns[waiter.thread_id] = waiter
                turn = {"id": waiter.turn_id, "status": "inProgress", "items": [item]}
                if initial_delivery == "notification":
                    client.events.dispatch_notification("item/completed", {
                        "threadId": waiter.thread_id, "turnId": waiter.turn_id, "item": item,
                    })
                else:
                    client.follower.replay_completed_items(waiter, turn)
                self.assertNotIn(item["id"], waiter.completed_item_ids)
                client.follower.replay_completed_items(waiter, turn)
                client.follower.replay_completed_items(waiter, turn)
                self.assertEqual(callback.call_count, 2)
                self.assertEqual(waiter.agent_messages, [item["text"]])
        client.close()

    def test_active_replay_defers_unfinished_items_until_their_terminal_snapshot(self) -> None:
        """重放进行中轮次的快照时，未完成的条目等到完成后才发出且只发一次"""
        client = CodexAppServerClient(self.socket_path)
        events = []
        waiter = TurnWaiter(thread_id="thread-progress", turn_id="turn-progress", event_callback=events.append)
        pending = {"id": "pending-command", "type": "commandExecution", "status": "inProgress"}
        unverified = {"id": "unverified-text", "type": "agentMessage", "text": ""}
        turn = {"id": waiter.turn_id, "status": "inProgress", "items": [pending, unverified]}
        client.follower.replay_completed_items(waiter, turn)
        self.assertFalse(events)
        self.assertFalse(waiter.completed_item_ids)
        pending["status"] = "completed"
        client.follower.replay_completed_items(waiter, turn)
        client.follower.replay_completed_items(waiter, turn)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["params"]["item"]["id"], pending["id"])
        client.close()

    def test_active_snapshot_replays_native_text_once_without_status(self) -> None:
        """无 status 字段的 agentMessage 在快照填入文本后只重放一次，之后的通知不重复

        :module: platform.core"""
        client = CodexAppServerClient(self.socket_path)
        events = []
        waiter = TurnWaiter(thread_id="thread-progress", turn_id="turn-progress", event_callback=events.append)
        client.work.turns[waiter.thread_id] = waiter
        for phase in ("commentary", "final_answer", None):
            with self.subTest(phase=phase):
                events.clear()
                item = {"id": f"message-{phase}", "type": "agentMessage", "phase": phase, "text": ""}
                turn = {"id": waiter.turn_id, "status": "inProgress", "items": [item]}
                client.reads.read_turn = mock.Mock(return_value=turn)
                self.assertIsNone(client.follower.read_terminal_turn(waiter))
                self.assertFalse(events)
                self.assertNotIn(item["id"], waiter.completed_item_ids)

                # Native snapshots fill the text when the message completes;
                # agentMessage has no status field, including while its turn runs.
                item["text"] = "A complete progress update"
                self.assertIsNone(client.follower.read_terminal_turn(waiter))
                self.assertIsNone(client.follower.read_terminal_turn(waiter))
                self.assertEqual(len(events), 1)
                client.events.dispatch_notification("item/completed", {
                    "threadId": waiter.thread_id, "turnId": waiter.turn_id, "item": item,
                })
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["params"]["item"]["text"], item["text"])
                self.assertFalse(waiter.event.is_set())
        client.close()

    def test_active_thread_reconciles_when_all_terminal_notifications_are_missing(self) -> None:
        """所有结束通知都缺失时，进行中的线程通过 thread/turns/list 对账完成本轮"""
        server = FakeAppServer(
            self.socket_path,
            omit_turn_completed=True,
            omit_idle_notification=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)

        with mock.patch(
            "management_portal.codex_app_server_turn_follow."
            "TURN_ACTIVE_RECONCILE_INTERVAL_SECONDS",
            0.01,
        ):
            result = client.run_turn(
                "019f0000-0000-7000-8000-000000000204",
                "hello",
                cwd="/tmp",
            )

        self.assertEqual(result.response, "finished")
        self.assertIn("thread/turns/list", server.methods)
        client.close()


    def test_terminal_reconciliation_does_not_adopt_an_unrelated_completed_turn(self) -> None:
        """结束对账不把其他已完成轮次的回答当作本轮结果"""
        client = CodexAppServerClient(self.socket_path)
        client.rpc.request = mock.Mock(return_value={
            "data": [{
                "id": "019f0000-0000-7000-8000-000000000311",
                "status": "completed",
                "items": [{
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "previous answer",
                }],
            }],
        })
        waiter = TurnWaiter(
            thread_id="019f0000-0000-7000-8000-000000000310",
            turn_id="019f0000-0000-7000-8000-000000000312",
            terminal_goal={"status": "completed"},
        )

        self.assertIsNone(client.follower.read_terminal_turn(waiter))
        self.assertIsNone(client.follower.read_terminal_goal_successor(waiter))
        request = client.rpc.request.call_args.args
        self.assertEqual(request[0], "thread/turns/list")
        self.assertEqual(request[1]["limit"], 5)
        client.close()

    def test_thread_system_error_requires_a_logical_continuation(self) -> None:
        """线程进入 systemError 时本轮结束并要求续跑"""
        client = CodexAppServerClient(self.socket_path)
        waiter = TurnWaiter(
            thread_id="thread-system-error",
            turn_id="turn-system-error",
        )
        with client.state_lock:
            client.work.turns[waiter.thread_id] = waiter

        client.events.dispatch_notification(
            "thread/status/changed",
            {
                "threadId": waiter.thread_id,
                "status": {"type": "systemError"},
            },
        )

        self.assertTrue(waiter.event.is_set())
        self.assertIsInstance(
            waiter.error,
            CodexTurnContinuationRequired,
        )
        self.assertEqual(waiter.error.reason, "provider_system_error")
        client.close()

    def test_resume_turn_rejects_missing_turn_on_system_error_thread(self) -> None:
        """线程处于 systemError 且找不到轮次时，恢复以 provider_system_error 要求续跑"""
        thread_id = "thread-system-error-reconcile"
        turn_id = "turn-system-error-reconcile"
        client = CodexAppServerClient(self.socket_path)
        client.rpc.request = mock.Mock(return_value={})
        client.reads.read_task_complete_goal = mock.Mock(return_value=None)
        client.reads.read_active_turn = mock.Mock(return_value=None)
        client.follower.read_terminal_turn = mock.Mock(return_value=None)
        client.reads.read_thread_status = mock.Mock(
            return_value={"type": "systemError"}
        )

        with self.assertRaises(CodexTurnContinuationRequired) as raised:
            client.resume_turn(thread_id, turn_id, cwd="/tmp")

        self.assertEqual(raised.exception.reason, "provider_system_error")
        client.close()

    def test_blocked_goal_completes_ordinary_turn_with_latest_goal_turn(self) -> None:
        """goal 转为 blocked 时普通轮次以最新的 goal 轮次结果完成"""
        server = FakeAppServer(
            self.socket_path,
            terminal_goal_status="blocked",
        )
        server.start()
        events: list[dict] = []
        client = CodexAppServerClient(self.socket_path)

        with mock.patch(
            "management_portal.codex_app_server_turn_follow.TURN_COMPLETION_RECONCILE_INTERVAL_SECONDS",
            0.01,
        ):
            result = client.run_turn(
                "019f0000-0000-7000-8000-000000000204",
                "hello",
                cwd="/tmp",
                event_callback=events.append,
            )

        self.assertEqual(result.response, "finished")
        self.assertEqual(
            result.turn_id,
            "019f0000-0000-7000-8000-000000000298",
        )
        self.assertIn("thread/turns/list", server.methods)
        self.assertEqual(events[-2]["method"], "thread/goal/updated")
        self.assertEqual(events[-1]["method"], "turn/completed")
        client.close()

    def test_resume_turn_recovers_completed_turn_from_already_blocked_goal(self) -> None:
        """恢复轮次时 goal 已是 blocked，从最近的 goal 轮次取回结果"""
        server = FakeAppServer(self.socket_path)
        server.goal = {"objective": "test goal", "status": "blocked"}
        server.last_turn = {
            "id": "019f0000-0000-7000-8000-000000000298",
            "items": [
                {
                    "id": "item-goal-blocked",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "goal blocked",
                }
            ],
            "itemsView": "full",
            "status": "completed",
            "error": None,
        }
        server.start()
        client = CodexAppServerClient(self.socket_path)

        result = client.resume_turn(
            "019f0000-0000-7000-8000-000000000204",
            "019f0000-0000-7000-8000-000000000201",
            cwd="/tmp",
        )

        self.assertEqual(result.response, "goal blocked")
        self.assertEqual(
            result.turn_id,
            "019f0000-0000-7000-8000-000000000298",
        )
        self.assertIn("thread/goal/get", server.methods)
        self.assertIn("thread/turns/list", server.methods)
        client.close()

    def test_interrupt_turn_uses_native_app_server_request(self) -> None:
        """中断轮次使用 app-server 原生的 turn/interrupt 请求"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)

        client.interrupt_turn(
            "019f0000-0000-7000-8000-000000000202",
            "019f0000-0000-7000-8000-000000000203",
        )

        self.assertIn("turn/interrupt", server.methods)
        client.close()

    def test_interrupt_turn_does_not_resubmit_an_observed_terminal_turn(self) -> None:
        """已观察到结束的轮次不再发送中断请求"""
        client = CodexAppServerClient(self.socket_path)
        waiter = TurnWaiter(thread_id="interrupt-thread", turn_id="interrupt-turn")
        client.work.turns[waiter.thread_id] = waiter
        for status in ("completed", "interrupted", "failed"):
            with self.subTest(status=status):
                waiter.completed = {
                    "threadId": waiter.thread_id,
                    "turn": {"id": waiter.turn_id, "status": status},
                }
                with mock.patch.object(client.rpc, "request", return_value={}) as request:
                    self.assertEqual(client.interrupt_turn(waiter.thread_id, waiter.turn_id), {})
                request.assert_not_called()
        client.close()

    def test_interrupt_turn_shares_one_inflight_control_and_waits_for_its_return(self) -> None:
        """并发中断共用一个进行中的 turn/interrupt 请求，并等它返回"""
        client = CodexAppServerClient(self.socket_path)
        waiter = TurnWaiter(thread_id="interrupt-thread", turn_id="interrupt-turn")
        client.work.turns[waiter.thread_id] = waiter
        request_entered = threading.Event()
        release_request = threading.Event()
        started = [threading.Event(), threading.Event()]
        finished = [threading.Event(), threading.Event()]
        results = []

        def native_interrupt(*_args, **_kwargs):
            request_entered.set()
            if not release_request.wait(2):
                raise TimeoutError("test must release the native control")
            return {"accepted": True}

        def interrupt(index):
            started[index].set()
            try:
                results.append(client.interrupt_turn(waiter.thread_id, waiter.turn_id))
            finally:
                finished[index].set()

        threads = [threading.Thread(target=interrupt, args=(index,)) for index in range(2)]
        with mock.patch.object(client.rpc, "request", side_effect=native_interrupt) as request:
            try:
                threads[0].start()
                self.assertTrue(request_entered.wait(1))
                threads[1].start()
                self.assertTrue(started[1].wait(1))
                client.events.dispatch_notification("turn/completed", {
                    "threadId": waiter.thread_id,
                    "turn": {"id": waiter.turn_id, "status": "interrupted"},
                })
                # A terminal notification must not release a still-pending
                # native control into the next Worker generation.
                self.assertFalse(finished[0].wait(0.05))
                self.assertFalse(finished[1].wait(0.05))
            finally:
                release_request.set()
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(timeout=2)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(results, [{"accepted": True}, {"accepted": True}])
            request.assert_called_once_with("turn/interrupt", {
                "threadId": waiter.thread_id, "turnId": waiter.turn_id,
            })
        client.close()

    def test_interrupt_turn_does_not_retry_an_ambiguous_control_in_same_waiter(self) -> None:
        """中断请求结果不明时，同一轮次不再重发中断"""
        client = CodexAppServerClient(self.socket_path)
        waiter = TurnWaiter(thread_id="interrupt-thread", turn_id="interrupt-turn")
        client.work.turns[waiter.thread_id] = waiter
        with mock.patch.object(client.rpc, "request", side_effect=TimeoutError("missing interrupt receipt")) as request:
            for _attempt in range(2):
                with self.assertRaisesRegex(TimeoutError, "missing interrupt receipt"):
                    client.interrupt_turn(waiter.thread_id, waiter.turn_id)
            self.assertEqual(request.call_count, 1)
        client.close()

    def test_interrupt_turn_requires_exact_terminal_identity_not_idle_or_progress(self) -> None:
        """只有同一线程同一轮次的结束状态才免发中断，空闲或进行中仍会发送"""
        client = CodexAppServerClient(self.socket_path)
        for completed in (None, {"turn": None}, {"turn": "malformed"},
                          {"threadId": "another-thread", "turn": {"id": "interrupt-turn", "status": "completed"}},
                          {"turn": {"id": "another-turn", "status": "completed"}},
                          {"turn": {"id": "interrupt-turn", "status": "inProgress"}},
                          {"turn": {"id": "interrupt-turn", "status": "unknown"}}):
            with self.subTest(completed=completed):
                waiter = TurnWaiter(thread_id="interrupt-thread", turn_id="interrupt-turn")
                waiter.thread_idle = True
                waiter.final_answer_seen = True
                waiter.completed = completed
                client.work.turns[waiter.thread_id] = waiter
                with mock.patch.object(client.rpc, "request", return_value={}) as request:
                    client.interrupt_turn(waiter.thread_id, waiter.turn_id)
                request.assert_called_once()
        client.close()

    def test_interrupt_turn_control_is_scoped_to_thread_and_successor_turn(self) -> None:
        """中断控制按线程和轮次隔离，一个线程的中断等待不阻塞其他线程或后继轮次"""
        client = CodexAppServerClient(self.socket_path)
        first = TurnWaiter(thread_id="first-thread", turn_id="old-turn")
        second = TurnWaiter(thread_id="second-thread", turn_id="old-turn")
        client.work.turns.update({first.thread_id: first, second.thread_id: second})
        entered = threading.Event()
        release = threading.Event()
        first_finished = threading.Event()

        def native_interrupt(_method, params):
            if params["threadId"] == first.thread_id and params["turnId"] == "old-turn":
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("first thread's control was not released")
            return dict(params)

        def interrupt_first():
            try:
                client.interrupt_turn(first.thread_id, "old-turn")
            finally:
                first_finished.set()

        thread = threading.Thread(target=interrupt_first)
        with mock.patch.object(client.rpc, "request", side_effect=native_interrupt) as request:
            try:
                thread.start()
                self.assertTrue(entered.wait(1))
                self.assertEqual(client.interrupt_turn(second.thread_id, second.turn_id), {
                    "threadId": second.thread_id, "turnId": second.turn_id,
                })
                self.assertFalse(first_finished.is_set())
            finally:
                release.set()
                thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            first.turn_id = "successor-turn"
            self.assertEqual(client.interrupt_turn(first.thread_id, first.turn_id), {
                "threadId": first.thread_id, "turnId": first.turn_id,
            })
            self.assertEqual(request.call_count, 3)
        client.close()

    def test_initialize_standard_forms_preserves_thread_approval_policy(self) -> None:
        """声明标准表单输入能力，同时原样传递线程的 Full Access 策略。"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)
        try:
            client.start_thread({
                "cwd": "/tmp", "ephemeral": False,
                "threadSource": "user", "approvalPolicy": "never",
            })
            initialized = next(item for item in server.requests if item["method"] == "initialize")
            self.assertEqual(initialized["params"]["capabilities"], {
                "experimentalApi": True,
                "extensions": {"openai/standard-form-input": {}},
            })
            started = next(item for item in server.requests if item["method"] == "thread/start")
            self.assertEqual(started["params"]["approvalPolicy"], "never")
            self.assertEqual(started["params"]["threadSource"], "user")
        finally:
            client.close()

    def test_thread_lifecycle_requests_share_the_persistent_connection(self) -> None:
        """新建、fork 和压缩线程共用同一持久连接，压缩后报告新的上下文大小"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)

        started = client.start_thread({"cwd": "/tmp", "ephemeral": False})
        forked = client.fork_thread({"threadId": "thread-new", "ephemeral": False})
        compact_usage = client.compact_thread("thread-fork")

        self.assertEqual(started["thread"]["id"], "thread-new")
        self.assertEqual(forked["thread"]["id"], "thread-fork")
        # After compacting, Codex reports the size of the compacted history:
        # the new context, not tokens the compaction used.
        self.assertEqual(compact_usage["context"]["totalTokens"], 14834)
        self.assertNotIn("last", compact_usage)
        self.assertEqual(compact_usage["modelContextWindow"], 258400)
        self.assertEqual(server.methods.count("initialize"), 1)
        self.assertIn("thread/compact/start", server.methods)
        client.close()

    def test_first_turn_does_not_read_an_unmaterialized_thread(self) -> None:
        """新建线程的第一轮直接开始，不 resume 也不读取尚未落盘的线程"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)

        started = client.start_thread({"cwd": "/tmp", "ephemeral": False})
        result = client.run_turn(
            started["thread"]["id"],
            "hello",
            cwd="/tmp",
        )

        self.assertEqual(result.response, "finished")
        self.assertNotIn("thread/resume", server.methods)
        self.assertNotIn("thread/turns/list", server.methods)
        self.assertNotIn(started["thread"]["id"], client.threads._unmaterialized)
        client.close()

    def test_first_fork_turn_can_be_claimed_by_another_worker(self) -> None:
        """fork 出的线程的第一轮可由另一个 Worker 恢复线程后执行"""
        server = FakeAppServer(
            self.socket_path,
            fork_requires_direct_turn=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)

        forked = client.fork_thread({
            "threadId": "thread-parent",
            "ephemeral": False,
        })
        thread_id = forked["thread"]["id"]
        with client.state_lock:
            # A different worker does not inherit the creator's local cache.
            client.threads._activity.clear()
            client.threads._configs.clear()
            client.threads._unmaterialized.clear()

        result = client.run_turn(thread_id, "hello", cwd="/tmp")

        self.assertEqual(result.response, "finished")
        self.assertIn("thread/resume", server.methods)
        self.assertIn("turn/start", server.methods)
        self.assertNotIn(thread_id, client.threads._unmaterialized)
        client.close()

    def test_loaded_thread_starts_next_turn_without_active_turn_preflight(self) -> None:
        """已加载的线程开始下一轮时不预先查询进行中的轮次"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)
        thread_id = "019f0000-0000-7000-8000-000000000206"

        first = client.run_turn(thread_id, "first", cwd="/tmp")
        preflight_reads = server.methods.count("thread/turns/list")
        second = client.run_turn(thread_id, "second", cwd="/tmp")

        self.assertEqual(first.response, "finished")
        self.assertEqual(second.response, "finished")
        self.assertEqual(
            server.methods.count("thread/turns/list"),
            preflight_reads,
        )
        self.assertEqual(server.methods.count("turn/start"), 2)
        client.close()

    def test_thread_lifecycle_control_requests_do_not_use_default_timeout(self) -> None:
        """新建、fork 和压缩线程的请求不受默认请求超时限制"""
        server = FakeAppServer(
            self.socket_path,
            initialize_delay_seconds=0.04,
            thread_start_delay_seconds=0.04,
            fork_delay_seconds=0.04,
            compact_start_delay_seconds=0.04,
        )
        server.start()
        client = CodexAppServerClient(
            self.socket_path,
            request_timeout_seconds=1,
        )
        client.timeouts.request = 0.01

        started = client.start_thread({"cwd": "/tmp", "ephemeral": False})
        forked = client.fork_thread({"threadId": "thread-new", "ephemeral": False})
        compact_usage = client.compact_thread("thread-fork")

        self.assertEqual(started["thread"]["id"], "thread-new")
        self.assertEqual(forked["thread"]["id"], "thread-fork")
        # After compacting, Codex reports the size of the compacted history:
        # the new context, not tokens the compaction used.
        self.assertEqual(compact_usage["context"]["totalTokens"], 14834)
        self.assertNotIn("last", compact_usage)
        client.close()

    def test_compact_completion_wait_can_be_cancelled(self) -> None:
        """等待压缩完成时可以被取消"""
        server = FakeAppServer(
            self.socket_path,
            omit_compact_completed=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)
        cancellation_requested = threading.Event()
        timer = threading.Timer(0.05, cancellation_requested.set)
        timer.start()
        try:
            with self.assertRaises(InterruptedError):
                client.compact_thread(
                    "thread-compact-cancel",
                    cancellation_requested=cancellation_requested.is_set,
                )
        finally:
            timer.cancel()
            client.close()

    def test_compact_request_has_optional_bounded_timeout(self) -> None:
        """压缩请求可以设置总超时时间"""
        server = FakeAppServer(
            self.socket_path,
            omit_compact_completed=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)
        try:
            with self.assertRaises(TimeoutError):
                client.compact_thread(
                    "thread-compact-timeout",
                    timeout_seconds=0.05,
                )
        finally:
            client.close()

    def test_compact_has_independent_no_progress_timeout(self) -> None:
        """压缩长时间无进展时按独立的无进展时限超时"""
        server = FakeAppServer(
            self.socket_path,
            omit_compact_completed=True,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)
        try:
            with self.assertRaisesRegex(TimeoutError, "made no progress"):
                client.compact_thread(
                    "thread-compact-stalled",
                    timeout_seconds=1.0,
                    no_progress_timeout_seconds=0.05,
                    status_poll_interval_seconds=0.01,
                )
        finally:
            client.close()

    def test_compact_waits_for_provider_turn_before_next_turn(self) -> None:
        """压缩等 Provider 的压缩轮次结束后才开始下一轮"""
        server = FakeAppServer(
            self.socket_path,
            compact_terminal_delay_seconds=0.05,
        )
        server.start()
        client = CodexAppServerClient(self.socket_path)

        client.compact_thread("thread-compact-then-continue")
        result = client.run_turn(
            "thread-compact-then-continue",
            "continue after compact",
            cwd="/tmp",
        )

        self.assertEqual(result.response, "finished")
        self.assertEqual(
            result.turn_id,
            "019f0000-0000-7000-8000-000000000201",
        )
        client.close()

    def test_idle_and_lru_threads_are_unsubscribed_without_touching_active_turns(self) -> None:
        """空闲过久和超出数量上限的线程被退订，有进行中轮次的线程保留"""
        client = CodexAppServerClient(
            self.socket_path,
            idle_unsubscribe_seconds=5 * 60 * 60,
            max_loaded_threads=2,
        )
        now = time.monotonic()
        client.threads._activity = {
            "old": now - 6 * 60 * 60,
            "lru": now - 60,
            "recent": now - 30,
            "active": now - 7 * 60 * 60,
        }
        client.work.turns["active"] = mock.Mock()
        calls: list[str] = []

        def request(_method, params, **_kwargs):
            calls.append(params["threadId"])
            return {"status": "unsubscribed"}

        with mock.patch.object(client.rpc, "request", side_effect=request):
            client.threads.unsubscribe_idle()

        self.assertEqual(set(calls), {"old", "lru"})
        self.assertIn("active", client.threads._activity)
        self.assertIn("recent", client.threads._activity)

    def test_unloaded_notifications_require_resume_before_the_next_turn(self) -> None:
        """线程卸载或关闭通知清除本地缓存，下一轮需要重新 resume"""
        for method, extra in [
            ("thread/status/changed", {"status": {"type": "notLoaded"}}),
            ("thread/closed", {}),
        ]:
            with self.subTest(method=method):
                client = CodexAppServerClient(self.socket_path)
                client.threads._activity["persisted"] = time.monotonic()
                client.threads._configs["persisted"] = "previous-config"
                client.threads._unmaterialized.add("persisted")
                lock = client.threads.lock_for("persisted")
                client.events.dispatch_notification(method, {"threadId": "persisted", **extra})
                self.assertNotIn("persisted", client.threads._activity)
                self.assertNotIn("persisted", client.threads._configs)
                self.assertNotIn("persisted", client.threads._unmaterialized)
                self.assertIs(client.threads.lock_for("persisted"), lock)

    def test_explicit_missing_thread_response_invalidates_the_cached_thread(self) -> None:
        """app-server 明确回复线程不存在时清除该线程的本地缓存"""
        for error, missing in [
            ("thread not found: persisted", True),
            ("no rollout found for thread id persisted", True),
            ("invalid paginated history lineage for persisted: missing source rollout", True),
            ("invalid paginated history lineage for other: missing source rollout", False),
            ("invalid paginated history lineage for persisted: invalid cursor", False),
        ]:
            with self.subTest(error=error):
                client = CodexAppServerClient(self.socket_path)
                client.threads._activity["persisted"] = time.monotonic()
                def reject(message):
                    client.rpc.dispatch_message({
                        "id": message["id"],
                        "error": {"code": -32600, "message": error},
                    })
                with mock.patch.object(client.rpc, "connect"), mock.patch.object(client.rpc, "send", side_effect=reject):
                    with self.assertRaises(CodexAppServerError) as caught:
                        client.request("thread/fork", {"threadId": "persisted"})
                self.assertEqual(isinstance(caught.exception, CodexThreadNotFound), missing)
                self.assertEqual("persisted" not in client.threads._activity, missing)

    def test_failed_turn_start_does_not_restore_unloaded_cache(self) -> None:
        """线程已卸载导致 turn/start 失败时，不恢复已清除的线程缓存"""
        for notification, extra in [
            ("thread/closed", {}),
            ("thread/status/changed", {"status": {"type": "notLoaded"}}),
        ]:
            with self.subTest(notification=notification):
                client = CodexAppServerClient(self.socket_path)
                client.threads._activity["persisted"] = time.monotonic()
                client.threads._configs["persisted"] = "previous-config"
                lock = client.threads.lock_for("persisted")

                def reject(method, params, **kwargs):
                    self.assertEqual(method, "turn/start")
                    client.events.dispatch_notification(notification, {"threadId": "persisted", **extra})
                    self.assertNotIn("persisted", client.threads._activity)
                    self.assertNotIn("persisted", client.threads._configs)
                    raise CodexAppServerError("thread not found: persisted")

                with mock.patch.object(client.rpc, "request", side_effect=reject) as request:
                    with self.assertRaisesRegex(CodexAppServerError, "thread not found"):
                        client.run_turn("persisted", "one request", cwd=self.temp_dir.name)
                self.assertEqual(request.call_count, 1)
                self.assertNotIn("persisted", client.threads._activity)
                self.assertNotIn("persisted", client.threads._configs)
                self.assertNotIn("persisted", client.work.turns)
                self.assertIs(client.threads.lock_for("persisted"), lock)

    def test_idle_cleanup_skips_a_thread_acquired_after_candidate_selection(self) -> None:
        """空闲清理跳过选中后又被占用的线程"""
        client = CodexAppServerClient(self.socket_path, max_loaded_threads=1)
        client.threads._activity["persisted"] = time.monotonic() - 1_000_000
        lock = client.threads.lock_for("persisted")
        lock.acquire()
        try:
            with mock.patch.object(client.rpc, "request") as request:
                client.threads.unsubscribe_idle()
                request.assert_not_called()
        finally:
            lock.release()
        self.assertIn("persisted", client.threads._activity)

    def test_idle_cleanup_preserves_goal_and_compaction_threads(self) -> None:
        """空闲清理不退订正在执行 goal 或压缩的线程"""
        client = CodexAppServerClient(self.socket_path, max_loaded_threads=1)
        client.threads._activity = {name: time.monotonic() - 1_000_000 for name in ("goal", "compact")}
        client.work.goals["goal"] = mock.Mock()
        client.work.compactions["compact"] = mock.Mock()
        with mock.patch.object(client.rpc, "request") as request:
            client.threads.unsubscribe_idle()
            request.assert_not_called()

    def test_next_turn_resumes_the_same_persisted_thread_after_unload(self) -> None:
        """线程被卸载后，下一轮先 resume 同一已持久化线程再开始"""
        server = FakeAppServer(self.socket_path)
        server.start()
        client = CodexAppServerClient(self.socket_path)
        try:
            client.threads._activity["persisted"] = time.monotonic()
            client.events.dispatch_notification("thread/status/changed", {
                "threadId": "persisted", "status": {"type": "notLoaded"},
            })
            result = client.run_turn("persisted", "one request", cwd=self.temp_dir.name)
            methods = [item["method"] for item in server.requests]
            self.assertLess(methods.index("thread/resume"), methods.index("turn/start"))
            self.assertNotIn("thread/start", methods)
            self.assertEqual(methods.count("turn/start"), 1)
            self.assertEqual(result.thread_id, "persisted")
        finally:
            client.close()

    def test_idle_maintenance_preserves_unmaterialized_threads(self) -> None:
        """空闲清理保留尚未落盘的新线程"""
        client = CodexAppServerClient(
            self.socket_path,
            idle_unsubscribe_seconds=1,
            max_loaded_threads=1,
        )
        now = time.monotonic()
        client.threads._activity = {
            "empty": now - 60,
            "materialized": now - 30,
        }
        client.threads._unmaterialized.add("empty")
        calls: list[str] = []

        def request(_method, params, **_kwargs):
            calls.append(params["threadId"])
            return {"status": "unsubscribed"}

        with mock.patch.object(client.rpc, "request", side_effect=request):
            client.threads.unsubscribe_idle()

        self.assertEqual(calls, ["materialized"])
        self.assertIn("empty", client.threads._activity)
        self.assertIn("empty", client.threads._unmaterialized)

    def test_thread_execution_lock_serializes_independent_callers(self) -> None:
        """跨进程线程执行锁让独立调用方对同一线程依次执行"""
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()

        def first() -> None:
            with codex_thread_execution_lock("thread-shared"):
                first_entered.set()
                release_first.wait(2)

        def second() -> None:
            first_entered.wait(2)
            with codex_thread_execution_lock("thread-shared"):
                second_entered.set()

        with mock.patch.dict("os.environ", {"PORTAL_CODEX_APP_SERVER_THREAD_LOCK_ROOT": self.temp_dir.name}):
            first_thread = threading.Thread(target=first)
            second_thread = threading.Thread(target=second)
            first_thread.start()
            second_thread.start()
            self.assertTrue(first_entered.wait(2))
            self.assertFalse(second_entered.wait(0.05))
            release_first.set()
            self.assertTrue(second_entered.wait(2))
            first_thread.join(2)
            second_thread.join(2)


    def test_thread_execution_lock_wait_can_be_cancelled(self) -> None:
        """等待跨进程线程执行锁时可以被取消"""
        first_entered = threading.Event()
        release_first = threading.Event()
        cancellation_requested = threading.Event()
        second_finished = threading.Event()
        second_errors: list[BaseException] = []

        def first() -> None:
            with codex_thread_execution_lock("thread-cancel"):
                first_entered.set()
                release_first.wait(2)

        def second() -> None:
            first_entered.wait(2)
            try:
                with codex_thread_execution_lock(
                    "thread-cancel",
                    cancellation_requested=cancellation_requested.is_set,
                    poll_interval_seconds=0.01,
                ):
                    pass
            except BaseException as exc:
                second_errors.append(exc)
            finally:
                second_finished.set()

        with mock.patch.dict(
            "os.environ",
            {
                "PORTAL_CODEX_APP_SERVER_THREAD_LOCK_ROOT": (
                    self.temp_dir.name
                )
            },
        ):
            first_thread = threading.Thread(target=first)
            second_thread = threading.Thread(target=second)
            first_thread.start()
            second_thread.start()
            self.assertTrue(first_entered.wait(2))
            cancellation_requested.set()
            self.assertTrue(second_finished.wait(2))
            self.assertEqual(len(second_errors), 1)
            self.assertIsInstance(second_errors[0], InterruptedError)
            release_first.set()
            first_thread.join(2)
            second_thread.join(2)


    def test_pending_request_wait_can_be_cancelled(self) -> None:
        """等待 app-server 请求响应时可以被取消"""
        client = CodexAppServerClient(
            self.socket_path,
            request_timeout_seconds=0.01,
        )
        client.rpc._transport = mock.Mock()
        cancellation_requested = threading.Event()
        timer = threading.Timer(0.05, cancellation_requested.set)
        timer.start()
        try:
            with mock.patch.object(client.rpc, "send"):
                with self.assertRaises(InterruptedError):
                    client.request(
                        "thread/resume",
                        {"threadId": "thread-cancel"},
                        timeout=None,
                        cancellation_requested=(
                            cancellation_requested.is_set
                        ),
                    )
        finally:
            timer.cancel()
            client.close()


if __name__ == "__main__":
    unittest.main()
