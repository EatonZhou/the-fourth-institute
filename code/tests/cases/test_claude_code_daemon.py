""":module: execution.providers"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid
import pytest
from unittest import mock


from tests.cases import capability_contracts
from tests.cases.claude_sdk_support import load_claude_module

daemon_module = load_claude_module("claude_code_daemon")
# The daemon's own runtime, which has the Claude Agent SDK (and no pytest).
CLAUDE_SDK_PYTHON = Path.home() / ".local/share/archive-portal/claude-agent-sdk-current/bin/python"


async def _prompt_uuid(prompt) -> str:
    """The uuid the daemon tags a prompt with; the CLI echoes it back."""
    async for message in prompt:
        return str(message.get("uuid") or "")
    return ""


def _echo(prompt_uuid: str):
    """The CLI's echo of a prompt when it starts working on it."""
    return daemon_module.UserMessage(content="prompt", uuid=prompt_uuid)


def _untimed(steps: list[dict]) -> list[dict]:
    """A card's steps without when they happened."""
    return [{key: value for key, value in step.items() if key not in {"started_at", "finished_at", "at"}}
            for step in steps]


class ClaudeCodeDaemonTest(unittest.IsolatedAsyncioTestCase):

    async def test_context_usage_reports_current_window_and_rejects_stale_turn(self):
        """上下文用量按当前窗口上报，轮次已过期或读取失败时不上报

        :module: platform.core"""
        client = mock.Mock(get_context_usage=mock.AsyncMock(return_value={"totalTokens": 80, "maxTokens": 100}))
        handle = daemon_module.SessionHandle(key="k", signature=(), client=client, session_id="s", active_turn_id="t")
        emit = mock.AsyncMock()
        await daemon_module._report_context_usage(handle, "t", emit)
        event = emit.call_args.args[0]
        self.assertEqual(event["params"]["tokenUsage"], {"context": {"totalTokens": 80}, "modelContextWindow": 100})
        handle.active_turn_id = "next"
        await daemon_module._report_context_usage(handle, "t", emit)
        emit.assert_awaited_once()
        client.get_context_usage.side_effect = RuntimeError("context unavailable")
        await daemon_module._report_context_usage(handle, "next", emit)
        emit.assert_awaited_once()


    async def test_handover_reattaches_events_and_tools_without_restarting_turn(self):
        """交接时事件和工具调用改接到新 Worker，轮次不重新开始"""
        daemon = daemon_module.ClaudeCodeDaemon()
        started = asyncio.Event()
        resume = asyncio.Event()
        first_events = []
        replacement_events = []
        first_tool = mock.AsyncMock(return_value={"owner": "old"})
        replacement_tool = mock.AsyncMock(return_value={"owner": "new"})

        class Client:
            calls = 0
            tool_result = None

            async def query(self, prompt):
                self.calls += 1
                self.prompt_uuid = await _prompt_uuid(prompt)
                started.set()

            async def receive_response(self):
                await resume.wait()
                self.tool_result = await handle.tool_bridge.dispatch("lookup", {"value": 1})
                yield _echo(self.prompt_uuid)
                yield daemon_module.ResultMessage(
                    subtype="success", duration_ms=1, duration_api_ms=1,
                    is_error=False, num_turns=1, session_id="native",
                    result="resumed", errors=None,
                )

        client = Client()
        handle = daemon_module.SessionHandle(
            key="resident:test", signature=(), client=client,
            session_id="native", tool_bridge=mock.Mock(dispatch=None),
        )

        async def disconnected_emit(event):
            first_events.append(event)
            raise BrokenPipeError("Worker socket closed")

        async def replacement_emit(event):
            replacement_events.append(event)

        params = {"session_key": "resident:test", "turn_id": "same-turn", "prompt": "test"}
        with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
            first = asyncio.create_task(daemon.run_turn(
                {**params, "_tool_dispatch": first_tool}, disconnected_emit,
            ))
            await asyncio.wait_for(started.wait(), timeout=5)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(daemon.run_turn(
                {**params, "_tool_dispatch": replacement_tool}, replacement_emit,
            ))
            await asyncio.sleep(0)
            resume.set()
            result = await second

        self.assertEqual(client.calls, 1)
        self.assertEqual([event["method"] for event in first_events], ["turn/started"])
        self.assertIn("turn/completed", [event["method"] for event in replacement_events])
        first_tool.assert_not_awaited()
        replacement_tool.assert_awaited_once_with("lookup", {"value": 1})
        self.assertEqual(client.tool_result, {"owner": "new"})
        self.assertEqual(result["response"], "resumed")

    async def test_a_turn_without_worker_keeps_events_and_tool_calls_for_the_next_one(self):
        """没有 Worker 时轮次暂存事件和工具调用，交给下一个接入的 Worker"""
        delivered = []

        async def closed(event):
            raise BrokenPipeError("Worker left")

        async def lost_call(name, arguments):
            raise daemon_module.WorkerConnectionLost("Worker left")

        async def next_emit(event):
            delivered.append(event)

        next_tool = mock.AsyncMock(return_value={"owner": "next"})
        connection = daemon_module.TurnConnection(closed, lost_call)
        await connection.send_event({"method": "item/started"})
        await connection.send_event({"method": "item/completed"})
        call = asyncio.create_task(connection.call_tool("lookup", {"value": 1}))
        await asyncio.sleep(0.05)
        self.assertFalse(call.done())
        await connection.attach(next_emit, next_tool)
        self.assertEqual(await asyncio.wait_for(call, 5), {"owner": "next"})
        next_tool.assert_awaited_once_with("lookup", {"value": 1})
        await connection.send_event({"method": "turn/completed"})
        self.assertEqual([event["method"] for event in delivered], ["item/started", "item/completed", "turn/completed"])
        self.assertFalse(connection.detached)

    async def test_a_tool_call_the_worker_never_took_goes_to_the_next_worker(self):
        """Worker 断开时尚未接走的工具调用转交下一个 Worker 执行"""
        async def lost_call(name, arguments):
            raise daemon_module.WorkerConnectionLost("Worker left")

        next_tool = mock.AsyncMock(return_value={"owner": "next"})
        connection = daemon_module.TurnConnection(mock.AsyncMock(), lost_call)
        call = asyncio.create_task(connection.call_tool("lookup", {}))
        await asyncio.sleep(0.05)
        self.assertTrue(connection.detached)
        await connection.attach(mock.AsyncMock(), next_tool)
        self.assertEqual(await asyncio.wait_for(call, 5), {"owner": "next"})

    async def test_authorized_resident_shell_receives_service_database_environment(self):
        """获授权的 Resident shell 继承服务的 PORTAL_DATABASE_URL 环境变量"""
        daemon = daemon_module.ClaudeCodeDaemon()
        fake_client = mock.Mock()
        fake_client.connect = mock.AsyncMock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"PORTAL_DATABASE_URL": "postgresql://resident-shell-test"}
        ), mock.patch.object(
            daemon_module, "_provider_environment", return_value=({}, {"id": "test-profile", "default_model": "test-model", "models": [{"id": "test-model", "enabled": True}]})
        ), mock.patch.object(
            daemon_module, "_validated_model", return_value="test-model"
        ), mock.patch.object(
            daemon_module, "ClaudeSDKClient", return_value=fake_client
        ) as client_factory:
            await daemon._create_handle({
                "session_key": "resident:test",
                "cwd": tmp,
                "config_dir": str(Path(tmp) / "config"),
                "provider_profile": "test-profile",
                "model": "test-model",
                "is_owner": True,
                "resident_environment": {
                    "PORTAL_RESIDENT_ID": "resident_123456789abc",
                    "PORTAL_LINEAGE_ID": "lineage_123456789abc",
                },
                "inherit_portal_database": True,
            })
            env = client_factory.call_args.args[0].env
            self.assertEqual(env["PORTAL_DATABASE_URL"], "postgresql://resident-shell-test")

    async def test_cli_without_database_authorization_masks_the_daemon_database(self):
        """未授权的 Claude 回合拿不到守护进程的数据库地址

        Claude 守护进程为读取模型接口登记持有 PORTAL_DATABASE_URL；没有授权继承数据库的回合（无论有无 Resident 身份）启动 CLI 时显式把它置空，CLI 及其 shell 无法继承。

        The daemon reads model API registrations from the Portal database; the
        CLI inherits the daemon's environment, so an unauthorized turn masks it.

        :module: security.identity-access
        :importance: P1
        """
        daemon = daemon_module.ClaudeCodeDaemon()
        fake_client = mock.Mock()
        fake_client.connect = mock.AsyncMock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"PORTAL_DATABASE_URL": "postgresql://daemon-only-test"}
        ), mock.patch.object(
            daemon_module, "_provider_environment", return_value=({}, {"id": "test-profile", "default_model": "test-model", "models": [{"id": "test-model", "enabled": True}]})
        ), mock.patch.object(
            daemon_module, "_validated_model", return_value="test-model"
        ), mock.patch.object(
            daemon_module, "ClaudeSDKClient", return_value=fake_client
        ) as client_factory:
            masked = []
            for identity in ({}, {"PORTAL_RESIDENT_ID": "resident_123456789abc",
                                  "PORTAL_LINEAGE_ID": "lineage_123456789abc"}):
                await daemon._create_handle({
                    "session_key": "resident:test" + str(len(identity)), "cwd": tmp,
                    "config_dir": str(Path(tmp) / "config"), "provider_profile": "test-profile",
                    "model": "test-model", "is_owner": True, "resident_environment": identity,
                })
                masked.append(client_factory.call_args.args[0].env.get("PORTAL_DATABASE_URL"))
        # The SDK starts the CLI with the daemon's environment updated by these values.
        self.assertEqual(masked, ["", ""])

    async def test_cli_messages_carrying_an_image_fit_the_sdk_buffer(self) -> None:
        """SDK 读取缓冲至少 32 MiB，能容纳携带 base64 图片的 CLI 消息

        A Read of an image returns it base64-encoded in one CLI message,
        well above the SDK's 1 MiB default."""
        daemon = daemon_module.ClaudeCodeDaemon()
        fake_client = mock.Mock()
        fake_client.connect = mock.AsyncMock()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            daemon_module, "_provider_environment", return_value=({}, {"id": "test-profile", "default_model": "test-model", "models": [{"id": "test-model", "enabled": True}]})
        ), mock.patch.object(
            daemon_module, "_validated_model", return_value="test-model"
        ), mock.patch.object(
            daemon_module, "ClaudeSDKClient", return_value=fake_client
        ) as client_factory:
            await daemon._create_handle({
                "session_key": "resident:test",
                "cwd": tmp,
                "config_dir": str(Path(tmp) / "config"),
                "provider_profile": "test-profile",
                "model": "test-model",
                "is_owner": True,
            })
        self.assertGreaterEqual(client_factory.call_args.args[0].max_buffer_size, 32 * 1024 * 1024)

    async def test_a_resident_without_subagents_has_no_agent_tool(self) -> None:
        """"允许开启 sub agent" off takes the Agent tool away; a client that does not say allows it."""
        daemon = daemon_module.ClaudeCodeDaemon()
        fake_client = mock.Mock()
        fake_client.connect = mock.AsyncMock()
        disallowed = {}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            daemon_module, "_provider_environment", return_value=({}, {"id": "test-profile", "default_model": "test-model", "models": [{"id": "test-model", "enabled": True}]})
        ), mock.patch.object(
            daemon_module, "_validated_model", return_value="test-model"
        ), mock.patch.object(
            daemon_module, "ClaudeSDKClient", return_value=fake_client
        ) as client_factory:
            for allow in (True, False, None):
                params = {
                    "session_key": "resident:test", "cwd": tmp, "config_dir": str(Path(tmp) / "config"),
                    "provider_profile": "test-profile", "model": "test-model", "is_owner": True,
                    **({} if allow is None else {"allow_subagents": allow}),
                }
                await daemon._create_handle(params)
                disallowed[allow] = client_factory.call_args.args[0].disallowed_tools
        self.assertEqual(disallowed[False], ["Agent", "Task"])
        self.assertEqual(disallowed[True], [])
        self.assertEqual(disallowed[None], [])
        base = {"cwd": "/w", "model": "m"}
        # Changing the setting starts the session's CLI again with the other tools.
        self.assertNotEqual(
            daemon._signature({**base, "allow_subagents": False}, "m"), daemon._signature(base, "m"),
        )

    async def test_handover_interrupt_can_retry_same_turn_without_replaying_cached_error(self):
        """交接中断后同一轮可以重试，不重放缓存的错误结果"""
        daemon = daemon_module.ClaudeCodeDaemon()
        started, interrupted = asyncio.Event(), asyncio.Event()

        class Client:
            calls = 0

            async def query(self, prompt):
                self.calls += 1
                self.prompt_uuid = await _prompt_uuid(prompt)
                started.set()

            async def interrupt(self):
                interrupted.set()

            async def receive_response(self):
                first = self.calls == 1
                if first:
                    await interrupted.wait()
                yield _echo(self.prompt_uuid)
                yield daemon_module.ResultMessage(
                    subtype="error_during_execution" if first else "success",
                    duration_ms=1, duration_api_ms=1, is_error=first, num_turns=1,
                    session_id="native", result=None if first else "resumed",
                    errors=["[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=null"] if first else None)

        client = Client()
        handle = daemon_module.SessionHandle(key="resident:test", signature=(), client=client, session_id="native")
        daemon._sessions[handle.key] = handle
        params = {"session_key": handle.key, "turn_id": "same-turn", "prompt": "test"}
        with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
            task = asyncio.create_task(daemon.run_turn(params, mock.AsyncMock()))
            await started.wait()
            await daemon.interrupt({"session_key": handle.key, "turn_id": "same-turn", "session_id": "native"})
            with self.assertRaises(daemon_module.ClaudeTurnInterrupted):
                await task
            self.assertNotIn((handle.key, "same-turn"), daemon._turn_tasks)
            result = await daemon.run_turn(params, mock.AsyncMock())
            self.assertEqual(result["response"], "resumed")
            self.assertEqual(await daemon.run_turn(params, mock.AsyncMock()), result)
            self.assertEqual(client.calls, 2)

    async def test_resume_rehomes_session_after_workspace_migration(self) -> None:
        """Workspace 迁移后恢复会话时先把 transcript 移到新目录再 resume"""
        daemon = daemon_module.ClaudeCodeDaemon()
        fake_client = mock.Mock()
        fake_client.connect = mock.AsyncMock()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_cwd = root / "old" / "workspace"
            new_cwd = root / "new" / "workspace"
            old_cwd.mkdir(parents=True)
            new_cwd.mkdir(parents=True)
            config_dir = root / "config"
            session_id = str(uuid.uuid4())
            source_path = (
                config_dir
                / "projects"
                / daemon_module.project_key_for_directory(old_cwd)
                / f"{session_id}.jsonl"
            )
            source_path.parent.mkdir(parents=True)
            source_path.write_text('{"type":"user"}\n', encoding="utf-8")
            target_path = (
                config_dir
                / "projects"
                / daemon_module.project_key_for_directory(new_cwd)
                / f"{session_id}.jsonl"
            )

            with (
                mock.patch.object(
                    daemon_module,
                    "_provider_environment",
                    return_value=({}, {"id": "test-profile", "default_model": "test-model", "models": [{"id": "test-model", "enabled": True}]}),
                ),
                mock.patch.object(
                    daemon_module,
                    "_validated_model",
                    return_value="test-model",
                ),
                mock.patch.object(
                    daemon_module,
                    "ClaudeSDKClient",
                    return_value=fake_client,
                ) as client_factory,
            ):
                handle = await daemon._create_handle({
                    "session_key": "resident:main",
                    "session_id": session_id,
                    "cwd": str(new_cwd),
                    "config_dir": str(config_dir),
                    "provider_profile": "deepseek_primary",
                    "model": "deepseek-v4-flash",
                    "is_owner": True,
                })

            options = client_factory.call_args.args[0]
            self.assertEqual(handle.session_id, session_id)
            self.assertEqual(options.resume, session_id)
            self.assertFalse(source_path.exists())
            self.assertEqual(
                target_path.read_text(encoding="utf-8"),
                '{"type":"user"}\n',
            )
            fake_client.connect.assert_awaited_once()

    async def test_resume_rejects_ambiguous_session_namespaces(self) -> None:
        """同一会话 ID 在多个项目目录都有 transcript 时拒绝恢复"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            session_id = str(uuid.uuid4())
            for name in ("old-one", "old-two"):
                transcript = (
                    config_dir
                    / "projects"
                    / name
                    / f"{session_id}.jsonl"
                )
                transcript.parent.mkdir(parents=True)
                transcript.write_text('{"type":"user"}\n', encoding="utf-8")

            with self.assertRaisesRegex(
                RuntimeError,
                "lookup returned 2 files",
            ):
                daemon_module.ClaudeCodeDaemon._relocate_resumed_session_file(
                    session_id,
                    root / "new" / "workspace",
                    config_dir,
                )




    async def test_a_turn_the_daemon_lost_is_continued_once_the_cli_had_its_input(self) -> None:
        """daemon 丢失的轮次在 CLI 已收到输入时发 continue 续跑，未收到时按原 UUID 首次投递"""
        session_id = "6a1f0d3e-2b7c-4c55-9e0a-2f4b8d9c1e77"
        turn_prompt_uuid = daemon_module._turn_prompt_uuid("resident:main", "turn-lost")
        for committed in (True, False):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory() as config_dir:
                transcript = Path(config_dir) / "projects" / "-work" / f"{session_id}.jsonl"
                transcript.parent.mkdir(parents=True)
                records = [{"type": "user", "uuid": str(uuid.uuid4()), "message": {"role": "user", "content": "earlier"}}]
                if committed:
                    records.append({"type": "user", "uuid": turn_prompt_uuid,
                                    "message": {"role": "user", "content": "the original prompt"}})
                transcript.write_text("".join(json.dumps(record) + "\n" for record in records))
                daemon = daemon_module.ClaudeCodeDaemon()

                class Client:
                    async def query(self, prompt):
                        async for message in prompt:
                            self.sent = message
                            self.prompt_uuid = message["uuid"]

                    async def receive_response(self):
                        yield _echo(self.prompt_uuid)
                        yield daemon_module.ResultMessage(
                            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                            num_turns=1, session_id=session_id, result="carried on", errors=None,
                        )

                client = Client()
                handle = daemon_module.SessionHandle(key="resident:main", signature=(), client=client, session_id=session_id)

                events = []

                async def emit(event):
                    events.append(event)

                with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
                    result = await daemon.run_turn({
                        "session_key": "resident:main", "turn_id": "turn-lost", "prompt": "the original prompt",
                        "continue_prompt": "continue", "config_dir": config_dir,
                    }, emit)

                self.assertEqual(result["response"], "carried on")
                recovered = [event["params"] for event in events if event["method"] == "turn/recovered"]
                self.assertEqual(
                    [(item["delivery"], item["prompt"]) for item in recovered],
                    [("continued", "continue") if committed else ("first_delivery", "the original prompt")],
                )
                if committed:
                    # The CLI had the input: the turn is continued, the input not sent again.
                    self.assertEqual(client.sent["message"]["content"], "continue")
                    self.assertNotEqual(client.sent["uuid"], turn_prompt_uuid)
                else:
                    # The CLI never had it: this is its first delivery, under the turn's UUID.
                    self.assertEqual(client.sent["message"]["content"], "the original prompt")
                    self.assertEqual(client.sent["uuid"], turn_prompt_uuid)

    async def test_session_discovered_from_assistant_message_emits_attachment(self) -> None:
        """从助手消息得知会话 ID 时发出 thread/attached 事件，思考块保留签名"""
        daemon = daemon_module.ClaudeCodeDaemon()
        session_id = "87b35417-8848-497f-91a9-10e58bfa74b0"

        class FakeClient:
            async def query(self, prompt):
                self.prompt_uuid = await _prompt_uuid(prompt)

            async def receive_response(self):
                yield _echo(self.prompt_uuid)
                yield daemon_module.AssistantMessage(
                    content=[
                        daemon_module.ThinkingBlock("private thought", "signed-block"),
                        daemon_module.TextBlock("done"),
                    ],
                    model="deepseek-v4-pro",
                    session_id=session_id,
                )
                yield daemon_module.ResultMessage(
                    subtype="success",
                    duration_ms=10,
                    duration_api_ms=8,
                    is_error=False,
                    num_turns=1,
                    session_id=session_id,
                    result="done",
                    usage={"input_tokens": 1, "output_tokens": 1},
                )

        handle = daemon_module.SessionHandle(
            key="resident:main",
            signature=(),
            client=FakeClient(),
        )
        events = []

        async def emit(event):
            events.append(event)

        with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
            result = await daemon.run_turn(
                {
                    "session_key": "resident:main",
                    "prompt": "hello",
                    "turn_id": "turn-test",
                },
                emit,
            )

        attached = [event for event in events if event.get("method") == "thread/attached"]
        self.assertEqual(result["session_id"], session_id)
        self.assertEqual([event["params"]["threadId"] for event in attached], [session_id])
        self.assertEqual(daemon._session_ids[session_id], handle.key)
        thoughts = [
            event["params"]["item"] for event in events
            if event.get("method") == "item/completed"
            and event["params"]["item"].get("type") == "reasoning"
        ]
        self.assertEqual(len(thoughts), 1)
        self.assertEqual(thoughts[0]["thinking"], "private thought")
        self.assertEqual(thoughts[0]["signature"], "signed-block")
        self.assertEqual(thoughts[0]["id"], "turn-test:1:thinking:0")
        self.assertEqual(result["response"], "done")

    async def test_a_subagents_own_steps_are_not_the_residents_reply(self) -> None:
        """子 agent 自己的文本和工具步骤只进子 agent 事件，不算作 Resident 的回复

        Only the Agent tool call and its result are the Resident's; the subagent's text is not."""

        daemon = daemon_module.ClaudeCodeDaemon()
        session_id = "6f0c43a3-6f42-4a65-9a0b-8d1c2b0b6e71"

        class FakeClient:
            async def query(self, prompt):
                self.prompt_uuid = await _prompt_uuid(prompt)

            async def receive_response(self):
                yield _echo(self.prompt_uuid)
                yield daemon_module.AssistantMessage(
                    content=[daemon_module.ToolUseBlock(
                        "toolu_agent", "Agent", {"subagent_type": "statusline-setup", "prompt": "noop"},
                    )],
                    model="claude-opus-5-5", session_id=session_id,
                )
                yield daemon_module.UserMessage(
                    content="noop", uuid="subagent-prompt", parent_tool_use_id="toolu_agent",
                )
                yield daemon_module.AssistantMessage(
                    content=[
                        daemon_module.TextBlock("subagent text"),
                        daemon_module.ToolUseBlock("toolu_sub", "Bash", {"command": "ls"}),
                    ],
                    model="claude-opus-5-5", session_id=session_id, parent_tool_use_id="toolu_agent",
                )
                yield daemon_module.UserMessage(
                    content=[daemon_module.ToolResultBlock("toolu_sub", "a.py")],
                    parent_tool_use_id="toolu_agent",
                )
                yield daemon_module.UserMessage(
                    content=[daemon_module.ToolResultBlock("toolu_agent", "agent finished")],
                )
                yield daemon_module.AssistantMessage(
                    content=[daemon_module.TextBlock("done")], model="claude-opus-5-5", session_id=session_id,
                )
                yield daemon_module.ResultMessage(
                    subtype="success", duration_ms=10, duration_api_ms=8, is_error=False, num_turns=1,
                    session_id=session_id, result="done", usage={"input_tokens": 1, "output_tokens": 1},
                )

        handle = daemon_module.SessionHandle(key="resident:main", signature=(), client=FakeClient())
        events = []

        async def emit(event):
            events.append(event)

        with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
            result = await daemon.run_turn(
                {"session_key": "resident:main", "prompt": "hello", "turn_id": "turn-sub"}, emit,
            )

        self.assertEqual(result["response"], "done")
        children = [event for event in events if event.get("method") == "subagent/event"]
        self.assertEqual(len(children), 4)
        self.assertTrue(all(event["params"]["parentToolUseId"] == "toolu_agent" for event in children))
        self.assertIn("subagent text", json.dumps(children))
        # The run's card (see test_a_subagent_runs_card_comes_when_the_model_has_its_result) shows its steps.
        recorded = json.dumps([
            event for event in events if event.get("method") != "subagent/event"
            and ((event.get("params") or {}).get("item") or {}).get("type") != "subagentRun"
        ], ensure_ascii=False)
        self.assertNotIn("subagent text", recorded)
        self.assertNotIn("toolu_sub", recorded)
        calls = [event["payload"]["call_id"] for event in events if event.get("type") == "response_item"]
        self.assertEqual(calls, ["toolu_agent", "toolu_agent"])

    async def test_a_subagent_runs_card_comes_when_the_model_has_its_result(self) -> None:
        """Claude 子 agent 卡片随结果出现，后台运行让本轮继续到它的通知

        前台运行在 Agent 调用结果时出卡片；后台运行（包括 CLI 默认转入后台的调用）让 Portal 轮次保持打开，CLI 取通知开始的下一轮仍属本轮：卡片、续写的回答和用量都记在本轮，先前的回答作为过程文字保留。

        A run in the foreground: with its Agent call's result; in the background: with its notification.

        While the model waits, each change of a run gives its running card.  A
        run in the background holds the turn open: the CLI's turn that takes
        its notification is still this one.

        :importance: P1
        """

        daemon = daemon_module.ClaudeCodeDaemon()
        session_id = "7a1c43a3-6f42-4a65-9a0b-8d1c2b0b6e71"
        sdk = daemon_module
        notice = (
            "<task-notification>\n<task-id>a2</task-id>\n<tool-use-id>toolu_bg</tool-use-id>\n"
            "<status>completed</status>\n<summary>Agent \"Read\" finished</summary>\n"
            "<result>first line</result>\n</task-notification>"
        )

        class FakeClient:
            responses = 0

            async def query(self, prompt):
                self.prompt_uuid = await _prompt_uuid(prompt)

            async def receive_response(self):
                FakeClient.responses += 1
                if FakeClient.responses == 1:
                    yield _echo(self.prompt_uuid)
                    yield sdk.AssistantMessage(content=[sdk.ToolUseBlock("toolu_fg", "Agent", {
                        "subagent_type": "Explore", "description": "Count", "prompt": "count files"})],
                        model="claude-opus-5-5", session_id=session_id)
                    yield sdk.AssistantMessage(content=[sdk.ToolUseBlock("toolu_ls", "Bash", {"command": "ls"})],
                                               model="claude-sonnet-5-5", parent_tool_use_id="toolu_fg")
                    yield sdk.UserMessage(content=[sdk.ToolResultBlock("toolu_ls", "a b c")], parent_tool_use_id="toolu_fg")
                    yield sdk.AssistantMessage(content=[sdk.TextBlock("3 files")],
                                               model="claude-sonnet-5-5", parent_tool_use_id="toolu_fg")
                    yield sdk.UserMessage(
                        content=[sdk.ToolResultBlock("toolu_fg", [{"type": "text", "text": "3 files"},
                                                                  {"type": "text", "text": "agentId: x"}])],
                        uuid="r-fg", tool_use_result={"status": "completed", "content": [{"type": "text", "text": "3 files"}]})
                    yield sdk.AssistantMessage(content=[sdk.ToolUseBlock("toolu_bg", "Agent", {
                        "subagent_type": "Explore", "description": "Read", "prompt": "read"})],
                        model="claude-opus-5-5", session_id=session_id)
                    yield sdk.UserMessage(content=[sdk.ToolResultBlock("toolu_bg", "Async agent launched")],
                                          uuid="r-bg", tool_use_result={"isAsync": True, "status": "async_launched"})
                    yield sdk.AssistantMessage(content=[sdk.TextBlock("first line")],
                                               model="claude-sonnet-5-5", parent_tool_use_id="toolu_bg")
                    answer = "waiting for Read"
                else:
                    # The CLI takes the run's notification in a turn of its own.
                    yield sdk.UserMessage(content=notice, uuid="n-bg", origin={"kind": "task-notification"})
                    answer = "done"
                yield sdk.AssistantMessage(content=[sdk.TextBlock(answer)], model="claude-opus-5-5", session_id=session_id)
                yield sdk.ResultMessage(
                    subtype="success", duration_ms=10, duration_api_ms=8, is_error=False, num_turns=1,
                    session_id=session_id, result=answer, usage={"input_tokens": 1, "output_tokens": 1},
                )

        handle = daemon_module.SessionHandle(key="resident:main", signature=(), client=FakeClient())

        async def turn(turn_id):
            events = []

            async def emit(event):
                events.append(event)

            with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
                result = await daemon.run_turn({"session_key": "resident:main", "prompt": "hello", "turn_id": turn_id}, emit)
            emitted.extend(events)
            progress.extend(event["params"]["item"]["run"] for event in events if event.get("method") == "subagent/progress")
            return result, [event["params"]["item"] for event in events
                            if event.get("method") == "item/completed"
                            and ((event.get("params") or {}).get("item") or {}).get("type") == "subagentRun"]

        progress, emitted = [], []
        result, (foreground, background) = await turn("turn-1")
        self.assertEqual(FakeClient.responses, 2)
        self.assertEqual((result["response"], result["usage"]), ("done", {"input_tokens": 2, "output_tokens": 2}))
        texts = [(event["params"]["item"].get("phase"), event["params"]["item"]["text"]) for event in emitted
                 if event.get("method") == "item/completed" and event["params"]["item"].get("type") == "agentMessage"]
        self.assertEqual(texts[-2:], [("commentary", "waiting for Read"), ("final_answer", "done")])
        self.assertEqual(
            [(run["tool_use_id"], run["status"], run["turn_id"], run["background"],
              [(step["kind"], step.get("running", False)) for step in run["steps"]])
             for run in progress],
            [("toolu_fg", "running", "turn-1", False, []),
             ("toolu_fg", "running", "turn-1", False, [("tool", True)]),
             ("toolu_fg", "running", "turn-1", False, [("tool", False)]),
             ("toolu_fg", "running", "turn-1", False, [("tool", False), ("message", False)]),
             # The call did not ask for the background; the CLI's launch result says it went there.
             ("toolu_bg", "running", "turn-1", False, []),
             ("toolu_bg", "running", "turn-1", True, []),
             ("toolu_bg", "running", "turn-1", True, [("message", False)])],
        )
        run = foreground["run"]
        self.assertEqual((run["tool_use_id"], run["turn_id"], run["status"], run["result"]), ("toolu_fg", "turn-1", "completed", "3 files"))
        self.assertEqual((run["agent_type"], run["description"], run["background"], run["model"]), ("Explore", "Count", False, "claude-sonnet-5-5"))
        self.assertEqual(_untimed(run["steps"]), [{"kind": "tool", "tool_name": "Bash", "text": "ls", "error": False},
                                                  {"kind": "message", "text": "3 files"}])
        self.assertEqual(run["prompt"], "count files")
        run = background["run"]
        self.assertEqual((run["tool_use_id"], run["turn_id"], run["background"], run["result"]), ("toolu_bg", "turn-1", True, "first line"))
        self.assertEqual(_untimed(run["steps"]), [{"kind": "message", "text": "first line"}])
        capability_contracts.check_subagent_events(emitted)

    def _background_client(self, session_id: str, later):
        """A CLI whose first turn leaves one subagent in the background; ``later`` scripts what follows."""

        sdk = daemon_module

        class FakeClient:
            responses = 0
            stopped_tasks: list[str] = []
            interrupted = False

            async def query(self, prompt):
                self.prompt_uuid = await _prompt_uuid(prompt)

            async def interrupt(self):
                FakeClient.interrupted = True

            async def stop_task(self, task_id):
                FakeClient.stopped_tasks.append(task_id)

            async def receive_response(self):
                FakeClient.responses += 1
                if FakeClient.responses > 1:
                    async for message in later(self):
                        yield message
                    return
                yield _echo(self.prompt_uuid)
                yield sdk.AssistantMessage(content=[sdk.ToolUseBlock("toolu_bg", "Agent", {
                    "subagent_type": "Explore", "description": "Read", "prompt": "read", "run_in_background": True})],
                    model="claude-opus-5-5", session_id=session_id)
                yield sdk.UserMessage(content=[sdk.ToolResultBlock("toolu_bg", "Async agent launched")],
                                      uuid="r-bg", tool_use_result={"isAsync": True, "status": "async_launched"})
                yield sdk.SystemMessage(subtype="task_started", data={
                    "session_id": session_id, "task_id": "t-bg", "tool_use_id": "toolu_bg", "description": "Read"})
                yield sdk.ResultMessage(
                    subtype="success", duration_ms=10, duration_api_ms=8, is_error=False, num_turns=1,
                    session_id=session_id, result="waiting for Read", usage={"input_tokens": 1, "output_tokens": 1},
                )

        return FakeClient()

    async def test_a_background_runs_card_comes_with_the_clis_task_notification_once(self) -> None:
        """Claude 后台子 agent 的结果卡片随 CLI 的任务通知出现，且只出一次

        CLI 为取通知自己开始一轮时不向流回放通知：守护进程收到后台运行的 task_notification 时给出结果卡片，结果取子 agent 最后一条消息、用量取通知；同一结束的回放通知随后到达也不再出第二张卡片。

        When the CLI starts a turn of its own for a run's notification, the stream has no
        replayed notification: the card comes with the CLI's task notification, its result
        the run's last message.  A replay that does come gives no second card.

        :importance: P1
        """

        sdk = daemon_module
        session_id = "3e8b6d2c-1f4a-4b7e-9c0d-2a5f6e7b8c91"
        notice = ("<task-notification>\n<task-id>t-bg</task-id>\n<tool-use-id>toolu_bg</tool-use-id>\n"
                  "<status>completed</status>\n<result>2 files</result>\n</task-notification>")
        for replayed in (False, True):
            with self.subTest(replayed=replayed):
                async def later(_client, replayed=replayed):
                    yield sdk.AssistantMessage(content=[sdk.TextBlock("2 files")], model="claude-sonnet-5-5",
                                               parent_tool_use_id="toolu_bg")
                    yield sdk.SystemMessage(subtype="task_notification", data={
                        "session_id": session_id, "task_id": "t-bg", "tool_use_id": "toolu_bg", "status": "completed",
                        "summary": 'Agent "Read" completed', "output_file": "/tmp/t-bg.output",
                        "usage": {"total_tokens": 900, "tool_uses": 2}})
                    if replayed:
                        yield sdk.UserMessage(content=notice, uuid="n-bg")
                    yield sdk.AssistantMessage(content=[sdk.TextBlock("Read found 2 files")], model="claude-opus-5-5",
                                               session_id=session_id)
                    yield sdk.ResultMessage(
                        subtype="success", duration_ms=10, duration_api_ms=8, is_error=False, num_turns=1,
                        session_id=session_id, result="Read found 2 files", usage={"input_tokens": 1, "output_tokens": 1},
                    )

                client = self._background_client(session_id, later)
                daemon = daemon_module.ClaudeCodeDaemon()
                handle = daemon_module.SessionHandle(key="resident:main", signature=(), client=client, session_id=session_id)
                daemon._sessions["resident:main"] = handle
                events = []

                async def emit(event):
                    events.append(event)

                with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)), \
                        mock.patch.object(daemon_module, "BACKGROUND_SETTLE_SECONDS", 0.05):
                    result = await asyncio.wait_for(daemon.run_turn(
                        {"session_key": "resident:main", "prompt": "hello", "turn_id": "turn-1"}, emit), 5)

                self.assertEqual(result["response"], "Read found 2 files")
                cards = [event["params"]["item"]["run"] for event in events if event.get("method") == "item/completed"
                         and event["params"]["item"].get("type") == "subagentRun"]
                self.assertEqual([(run["tool_use_id"], run["turn_id"], run["status"], run["background"], run["result"])
                                  for run in cards], [("toolu_bg", "turn-1", "completed", True, "2 files")])
                self.assertEqual(cards[0]["usage"], {"total_tokens": 900, "tool_uses": 2})

    async def test_runs_that_end_together_each_get_the_turn_the_cli_starts_for_their_notification(self) -> None:
        """同时结束的后台子 agent 各自的通知轮次都属于本轮

        两个后台运行几乎同时结束、CLI 为每条通知各开一轮时，第一轮结束后虽已没有运行，Portal 轮次仍等 CLI 取第二条通知的那一轮，回答取最后一轮，两张卡片都在本轮。

        Two runs in the background end at once: the CLI takes each notification in a turn
        of its own, and the turn waits for the second although no run goes on after the first.

        :importance: P1
        """

        sdk = daemon_module
        session_id = "6a2d9e4f-8b1c-4d3e-a5f7-0c9b8a7d6e52"

        def result(text):
            return sdk.ResultMessage(subtype="success", duration_ms=10, duration_api_ms=8, is_error=False, num_turns=1,
                                     session_id=session_id, result=text, usage={"input_tokens": 1, "output_tokens": 1})

        class FakeClient:
            responses = 0

            async def query(self, prompt):
                self.prompt_uuid = await _prompt_uuid(prompt)

            async def receive_response(self):
                FakeClient.responses += 1
                if FakeClient.responses == 1:
                    yield _echo(self.prompt_uuid)
                    for call in ("toolu_a", "toolu_b"):
                        yield sdk.AssistantMessage(content=[sdk.ToolUseBlock(call, "Agent", {
                            "description": call, "prompt": "work", "run_in_background": True})],
                            model="claude-opus-5-5", session_id=session_id)
                        yield sdk.UserMessage(content=[sdk.ToolResultBlock(call, "Async agent launched")],
                                              tool_use_result={"isAsync": True, "status": "async_launched"})
                        yield sdk.SystemMessage(subtype="task_started", data={
                            "session_id": session_id, "task_id": f"t-{call}", "tool_use_id": call})
                    yield result("started")
                elif FakeClient.responses == 2:
                    for call in ("toolu_a", "toolu_b"):
                        yield sdk.SystemMessage(subtype="task_notification", data={
                            "session_id": session_id, "task_id": f"t-{call}", "tool_use_id": call, "status": "completed",
                            "summary": "done", "output_file": "/tmp/out"})
                    yield sdk.AssistantMessage(content=[sdk.TextBlock("a done, waiting for b")], model="claude-opus-5-5")
                    yield result("a done, waiting for b")
                else:
                    yield sdk.AssistantMessage(content=[sdk.TextBlock("both done")], model="claude-opus-5-5")
                    yield result("both done")

        daemon = daemon_module.ClaudeCodeDaemon()
        handle = daemon_module.SessionHandle(key="resident:main", signature=(), client=FakeClient(), session_id=session_id)
        daemon._sessions["resident:main"] = handle
        events = []

        async def emit(event):
            events.append(event)

        with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)), \
                mock.patch.object(daemon_module, "BACKGROUND_SETTLE_SECONDS", 2.0):
            outcome = await asyncio.wait_for(daemon.run_turn(
                {"session_key": "resident:main", "prompt": "hello", "turn_id": "turn-1"}, emit), 5)

        self.assertEqual((FakeClient.responses, outcome["response"]), (3, "both done"))
        cards = [event["params"]["item"]["run"]["tool_use_id"] for event in events if event.get("method") == "item/completed"
                 and event["params"]["item"].get("type") == "subagentRun"]
        self.assertEqual(sorted(cards), ["toolu_a", "toolu_b"])

    async def test_a_run_stopped_in_the_background_without_a_notification_leaves_the_turn_its_last_answer(self) -> None:
        """Claude 后台子 agent 无通知结束时，等待的轮次以上一回答结束

        CLI 只报告后台运行的任务 killed、没有给模型通知时：已没有运行在进行，CLI 也没有在等待窗口内开始新一轮，Portal 轮次以最后一次回答结束，不一直挂起。

        The CLI may end a background run's task with no notification for the model:
        once no run goes on and the CLI starts no turn, the turn ends with its last answer.

        :importance: P1
        """

        session_id = "5c0e7f1e-2a7b-4f53-9a0e-3c1d2b4a6f70"

        async def later(_client):
            yield daemon_module.SystemMessage(subtype="task_updated", data={
                "session_id": session_id, "task_id": "t-bg", "patch": {"status": "killed"}})
            await asyncio.Event().wait()

        client = self._background_client(session_id, later)
        daemon = daemon_module.ClaudeCodeDaemon()
        handle = daemon_module.SessionHandle(key="resident:main", signature=(), client=client, session_id=session_id)
        daemon._sessions["resident:main"] = handle

        async def emit(_event):
            pass

        with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)), \
                mock.patch.object(daemon_module, "BACKGROUND_SETTLE_SECONDS", 0.05):
            result = await asyncio.wait_for(daemon.run_turn(
                {"session_key": "resident:main", "prompt": "hello", "turn_id": "turn-1"}, emit), 5)

        self.assertEqual((type(client).responses, result["response"]), (2, "waiting for Read"))
        self.assertFalse(handle.subagent_runs.running_in_background())
        self.assertFalse(handle.steering_open)

    async def test_stopping_a_turn_that_waits_on_its_background_subagents_stops_them(self) -> None:
        """停止等待后台子 agent 的 Claude 轮次会一并停止这些子 agent

        轮次等待后台子 agent 期间仍可插话；停止轮次时 daemon 对仍在运行的后台子 agent 调用 stop_task，卡片以已停止结束，轮次以中断结束，子 agent 不在没有轮次读取的地方继续工作。

        While the turn waits, steering stays open; stopping the turn stops the runs it waits on.

        :importance: P1
        """

        session_id = "9d4f2b7a-6c1e-4e2d-8b3a-1f0e5d7c9a21"
        waiting = asyncio.Event()

        async def later(client):
            waiting.set()
            while not type(client).stopped_tasks:
                await asyncio.sleep(0.01)
            yield daemon_module.SystemMessage(subtype="task_updated", data={
                "session_id": session_id, "task_id": "t-bg", "patch": {"status": "killed"}})
            await asyncio.Event().wait()

        client = self._background_client(session_id, later)
        daemon = daemon_module.ClaudeCodeDaemon()
        handle = daemon_module.SessionHandle(key="resident:main", signature=(), client=client, session_id=session_id)
        daemon._sessions["resident:main"] = handle
        events = []

        async def emit(event):
            events.append(event)

        with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
            turn = asyncio.create_task(daemon.run_turn(
                {"session_key": "resident:main", "prompt": "hello", "turn_id": "turn-1"}, emit))
            await asyncio.wait_for(waiting.wait(), 5)
            self.assertTrue(handle.steering_open)
            stopped = await daemon.interrupt({"session_key": "resident:main", "turn_id": "turn-1"})
            with self.assertRaises(daemon_module.ClaudeTurnInterrupted):
                await asyncio.wait_for(turn, 5)

        self.assertTrue(stopped["interrupted"] and type(client).interrupted)
        self.assertEqual(type(client).stopped_tasks, ["t-bg"])
        cards = [event["params"]["item"]["run"] for event in events if event.get("method") == "item/completed"
                 and event["params"]["item"].get("type") == "subagentRun"]
        self.assertEqual([(run["tool_use_id"], run["status"]) for run in cards], [("toolu_bg", "stopped")])

    async def test_one_subagent_stops_through_its_cli_task_while_the_turn_goes_on(self) -> None:
        """Claude 单独停止一个子 agent：经 CLI 任务停止，父轮次继续

        daemon 按 CLI task 事件记录子 agent 的 task_id 和用量；subagent/stop 调用 stop_task，CLI 只报告任务 killed 时卡片以已停止结束一次，后续通知不重复出卡；通知中的 subagent_tokens 计入卡片用量。

        The Chat stops one run through the CLI's task of it, and the turn goes on.

        The CLI may say only that the task was killed: the run's card ends then,
        and once.  A run's token use comes from the CLI's task reports and from
        its notification.

        :importance: P1
        """

        daemon = daemon_module.ClaudeCodeDaemon()
        sdk = daemon_module
        session_id = "0b9e1c55-3d1f-4c1e-8f43-5a0f0f7d2c10"
        progressed, stopped = asyncio.Event(), asyncio.Event()
        notices = (
            "<task-notification>\n<task-id>t-a</task-id>\n<tool-use-id>toolu_a</tool-use-id>\n"
            "<status>stopped</status>\n</task-notification>\n"
            "<task-notification>\n<task-id>t-b</task-id>\n<tool-use-id>toolu_b</tool-use-id>\n"
            "<status>completed</status>\n<result>2 files</result>\n<usage><subagent_tokens>900</subagent_tokens>"
            "<tool_uses>3</tool_uses><duration_ms>40</duration_ms></usage>\n</task-notification>"
        )

        def system(subtype, **data):
            return sdk.SystemMessage(subtype=subtype, data={"session_id": session_id, **data})

        class FakeClient:
            stopped_tasks: list[str] = []

            async def query(self, prompt):
                self.prompt_uuid = await _prompt_uuid(prompt)

            async def stop_task(self, task_id):
                self.stopped_tasks.append(task_id)
                stopped.set()

            async def receive_response(self):
                yield _echo(self.prompt_uuid)
                for call, description in (("toolu_a", "Search"), ("toolu_b", "Count")):
                    yield sdk.AssistantMessage(content=[sdk.ToolUseBlock(call, "Agent", {
                        "subagent_type": "Explore", "description": description,
                        "prompt": description.lower() + " the files", "run_in_background": True})],
                        model="claude-opus-5-5", session_id=session_id)
                    yield sdk.UserMessage(content=[sdk.ToolResultBlock(call, "Async agent launched")],
                                          uuid="r-" + call, tool_use_result={"isAsync": True, "status": "async_launched"})
                    yield system("task_started", task_id="t-" + call[-1], tool_use_id=call, description=description)
                yield system("task_progress", task_id="t-a", tool_use_id="toolu_a", description="Search",
                             usage={"total_tokens": 120, "tool_uses": 1, "duration_ms": 5})
                progressed.set()
                await stopped.wait()
                yield system("task_updated", task_id="t-a", patch={"status": "killed"})
                yield sdk.UserMessage(content=notices, uuid="n-1", origin={"kind": "task-notification"})
                yield sdk.AssistantMessage(content=[sdk.TextBlock("done")], model="claude-opus-5-5", session_id=session_id)
                yield sdk.ResultMessage(
                    subtype="success", duration_ms=10, duration_api_ms=8, is_error=False, num_turns=1,
                    session_id=session_id, result="done", usage={"input_tokens": 1, "output_tokens": 1},
                )

        client = FakeClient()
        handle = daemon_module.SessionHandle(key="resident:main", signature=(), client=client, session_id=session_id)
        daemon._sessions["resident:main"] = handle
        events = []

        async def emit(event):
            events.append(event)

        with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
            turn = asyncio.create_task(daemon.run_turn(
                {"session_key": "resident:main", "prompt": "hello", "turn_id": "turn-1"}, emit))
            await asyncio.wait_for(progressed.wait(), 5)
            unknown = await daemon.stop_subagent({"session_key": "resident:main", "tool_use_id": "toolu_x"})
            answer = await daemon.stop_subagent(
                {"session_key": "resident:main", "session_id": session_id, "tool_use_id": "toolu_a"})
            result = await asyncio.wait_for(turn, 5)

        self.assertEqual(unknown, {"stopped": False, "reason": "unknown_subagent"})
        self.assertTrue(answer["stopped"])
        self.assertEqual(client.stopped_tasks, ["t-a"])
        self.assertEqual(result["response"], "done")
        live = [event["params"]["item"]["run"] for event in events if event.get("method") == "subagent/progress"]
        self.assertEqual(live[-1]["usage"], {"total_tokens": 120, "tool_uses": 1})
        cards = [event["params"]["item"]["run"] for event in events
                 if event.get("method") == "item/completed"
                 and ((event.get("params") or {}).get("item") or {}).get("type") == "subagentRun"]
        self.assertEqual(
            [(card["tool_use_id"], card["status"], card["prompt"], card["usage"]) for card in cards],
            [("toolu_a", "stopped", "search the files", {"total_tokens": 120, "tool_uses": 1}),
             ("toolu_b", "completed", "count the files", {"total_tokens": 900, "tool_uses": 3})],
        )
        self.assertEqual(cards[1]["result"], "2 files")
        self.assertEqual((handle.subagent_tasks, handle.subagent_stops), ({}, set()))
        self.assertTrue(all(card["end_id"] and card["finished_at"] for card in cards))

    async def test_session_setup_does_not_block_health_checks(self) -> None:
        """建立 Claude 会话期间健康检查不被阻塞"""
        daemon = daemon_module.ClaudeCodeDaemon()
        create_started = asyncio.Event()
        allow_create = asyncio.Event()
        fake_client = mock.Mock()
        fake_client.disconnect = mock.AsyncMock()

        async def create_handle(params):
            create_started.set()
            await allow_create.wait()
            return daemon_module.SessionHandle(
                key="resident:main",
                signature=daemon._signature(params, "deepseek-v4-pro"),
                client=fake_client,
            )

        params = {
            "session_key": "resident:main",
            "cwd": "/tmp",
            "config_dir": "/tmp/config",
            "provider_profile": "deepseek_primary",
            "model": "deepseek-v4-pro",
            "is_owner": True,
            "add_dirs": [],
        }
        with (
            mock.patch.object(daemon_module, "_provider_profile", return_value={}),
            mock.patch.object(daemon_module, "_validated_model", return_value="deepseek-v4-pro"),
            mock.patch.object(daemon_module, "_provider_environment", return_value=({}, {"id": "test-profile", "default_model": "test-model", "models": [{"id": "test-model", "enabled": True}]})),
            mock.patch.object(daemon, "_create_handle", side_effect=create_handle),
        ):
            setup_task = asyncio.create_task(daemon._handle_for(params))
            await create_started.wait()
            health = await asyncio.wait_for(daemon.health(), timeout=0.1)
            self.assertEqual(health["status"], "ok")
            allow_create.set()
            handle = await setup_task

        self.assertEqual(handle.key, "resident:main")

    async def test_close_session_disconnects_and_removes_mappings(self) -> None:
        """关闭会话时断开客户端并清除会话映射和建立锁"""
        daemon = daemon_module.ClaudeCodeDaemon()
        fake_client = mock.Mock()
        fake_client.disconnect = mock.AsyncMock()
        handle = daemon_module.SessionHandle(
            key="resident:fork:test",
            signature=(),
            client=fake_client,
            session_id="session-test",
        )
        daemon._sessions[handle.key] = handle
        daemon._session_ids[handle.session_id] = handle.key
        daemon._session_setup_locks[handle.key] = asyncio.Lock()

        result = await daemon.close_session({"session_key": handle.key})

        self.assertTrue(result["closed"])
        self.assertNotIn(handle.key, daemon._sessions)
        self.assertNotIn(handle.session_id, daemon._session_ids)
        self.assertNotIn(handle.key, daemon._session_setup_locks)
        fake_client.disconnect.assert_awaited_once()

    async def test_authentication_failure_recreates_cached_session_for_next_turn(self) -> None:
        """认证失败后下一轮重建缓存的 Claude 会话，普通工具失败时保留原会话"""
        for detail, should_replace in (
            ("Failed to authenticate. API Error: 403 Request not allowed", True),
            ("Tool execution failed", False),
        ):
            with self.subTest(detail=detail):
                daemon = daemon_module.ClaudeCodeDaemon()
                params = {
                    "session_key": "resident:test",
                    "turn_id": "turn-test",
                    "prompt": "continue",
                    "provider_profile": "test-profile",
                    "model": "test-model",
                }

                class Client:
                    disconnect = mock.AsyncMock()

                    async def query(self, _prompt):
                        return None

                    async def receive_response(self):
                        yield daemon_module.ResultMessage(
                            subtype="error_during_execution",
                            duration_ms=1, duration_api_ms=1,
                            is_error=True, num_turns=1, session_id="old-session",
                            result=None, errors=[detail],
                        )

                handle = daemon_module.SessionHandle(
                    key=params["session_key"],
                    signature=daemon._signature(params, "test-model"),
                    client=Client(), session_id="old-session",
                )
                daemon._sessions[handle.key] = handle
                daemon._session_ids[handle.session_id] = handle.key
                fresh = daemon_module.SessionHandle(
                    key=handle.key, signature=handle.signature,
                    client=mock.Mock(), session_id="fresh-session",
                )
                with (
                    mock.patch.object(daemon_module, "_provider_profile", return_value={}),
                    mock.patch.object(daemon_module, "_validated_model", return_value="test-model"),
                    mock.patch.object(daemon, "_create_handle", mock.AsyncMock(return_value=fresh)),
                ):
                    with self.assertRaisesRegex(RuntimeError, detail.split(".")[0]):
                        await daemon._run_turn_once(params, mock.AsyncMock())
                    next_handle = await daemon._handle_for(params)

                self.assertIs(next_handle, fresh if should_replace else handle)
                self.assertEqual(handle.invalidated, should_replace)
                if should_replace:
                    handle.client.disconnect.assert_awaited_once()
                else:
                    handle.client.disconnect.assert_not_awaited()

    async def test_a_cli_transport_error_reconnects_the_session_for_the_next_turn(self) -> None:
        """CLI 传输出错后作废该 Claude 会话连接，下一轮重新建立连接

        The SDK stops reading the CLI after a transport error such as an
        oversized message; the next turn must not reuse that connection."""
        daemon = daemon_module.ClaudeCodeDaemon()
        params = {
            "session_key": "resident:test",
            "turn_id": "turn-test",
            "prompt": "continue",
            "provider_profile": "test-profile",
            "model": "test-model",
        }

        class Client:
            disconnect = mock.AsyncMock()

            async def query(self, _prompt):
                return None

            async def receive_response(self):
                raise daemon_module.ClaudeSDKError(
                    "Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes..."
                )
                yield  # pragma: no cover - makes this an async generator

        handle = daemon_module.SessionHandle(
            key=params["session_key"],
            signature=daemon._signature(params, "test-model"),
            client=Client(), session_id="old-session",
        )
        daemon._sessions[handle.key] = handle
        daemon._session_ids[handle.session_id] = handle.key
        fresh = daemon_module.SessionHandle(
            key=handle.key, signature=handle.signature,
            client=mock.Mock(), session_id="old-session",
        )
        with (
            mock.patch.object(daemon_module, "_provider_profile", return_value={}),
            mock.patch.object(daemon_module, "_validated_model", return_value="test-model"),
            mock.patch.object(daemon, "_create_handle", mock.AsyncMock(return_value=fresh)),
        ):
            with self.assertRaisesRegex(daemon_module.ClaudeSDKError, "maximum buffer size"):
                await daemon._run_turn_once(params, mock.AsyncMock())
            next_handle = await daemon._handle_for(params)

        self.assertTrue(handle.invalidated)
        self.assertIs(next_handle, fresh)
        handle.client.disconnect.assert_awaited_once()

    async def test_a_result_before_the_prompt_starts_does_not_end_the_turn(self) -> None:
        """恢复会话时新提示开始前的空结果不结束本轮，本轮以该提示自己的结果结束

        On resume the CLI reports a background task of the previous process as
        stopped and closes that with an empty result before it starts on the new
        prompt; the turn ends with the prompt's own result."""
        daemon = daemon_module.ClaudeCodeDaemon()
        params = {"session_key": "resident:test", "turn_id": "turn-test", "prompt": "status?",
                  "provider_profile": "test-profile", "model": "test-model"}

        def result(text, turns):
            return daemon_module.ResultMessage(
                subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                num_turns=turns, session_id="native", result=text,
                usage={"input_tokens": turns, "output_tokens": turns},
            )

        class Client:
            reads = 0

            async def query(self, prompt):
                self.prompt_uuid = await _prompt_uuid(prompt)

            async def receive_response(self):
                self.reads += 1
                if self.reads == 1:
                    yield daemon_module.SystemMessage(
                        subtype="task_notification", data={"task_id": "b1", "status": "stopped"},
                    )
                    yield result("", 0)
                    return
                yield _echo(self.prompt_uuid)
                yield daemon_module.AssistantMessage(
                    content=[daemon_module.TextBlock("all done")], model="test-model", session_id="native",
                )
                yield result("all done", 1)

        handle = daemon_module.SessionHandle(
            key=params["session_key"], signature=daemon._signature(params, "test-model"),
            client=Client(), session_id="native",
        )
        daemon._sessions[handle.key] = handle
        daemon._session_ids[handle.session_id] = handle.key
        events = []

        async def emit(event):
            events.append(event)

        with (
            mock.patch.object(daemon_module, "_provider_profile", return_value={}),
            mock.patch.object(daemon_module, "_validated_model", return_value="test-model"),
        ):
            outcome = await daemon._run_turn_once(params, emit)

        self.assertEqual(outcome["response"], "all done")
        self.assertEqual(handle.client.reads, 2)
        raw_results = [e for e in events if e.get("method") == "claude/message"
                       and e["params"]["messageType"] == "ResultMessage"]
        self.assertEqual([e["params"]["message"]["num_turns"] for e in raw_results], [1])

    @unittest.skipUnless(CLAUDE_SDK_PYTHON.is_file(), "runs the SDK's own fork_session; needs the Claude Agent SDK runtime")
    async def test_fork_session_finds_source_and_rehomes_target_transcript(self) -> None:
        """fork 会话找到源 transcript，并把新 transcript 放到目标 Workspace 的项目目录"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_cwd = root / "source-workspace"
            target_cwd = root / "target-workspace"
            source_cwd.mkdir()
            target_cwd.mkdir()
            config_dir = root / "config"
            source_session_id = str(uuid.uuid4())
            first_message_id = str(uuid.uuid4())
            second_message_id = str(uuid.uuid4())
            source_project_dir = (
                config_dir
                / "projects"
                / daemon_module.project_key_for_directory(source_cwd)
            )
            source_project_dir.mkdir(parents=True)
            source_path = source_project_dir / f"{source_session_id}.jsonl"
            source_path.write_text(
                "\n".join(
                    json.dumps(item)
                    for item in (
                        {
                            "type": "user",
                            "uuid": first_message_id,
                            "parentUuid": None,
                            "sessionId": source_session_id,
                            "cwd": str(source_cwd),
                            "message": {"role": "user", "content": "hello"},
                        },
                        {
                            "type": "assistant",
                            "uuid": second_message_id,
                            "parentUuid": first_message_id,
                            "sessionId": source_session_id,
                            "cwd": str(source_cwd),
                            "message": {
                                "role": "assistant",
                                "content": [{"type": "text", "text": "done"}],
                            },
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            # The fork runs in a child Python that has the real SDK.
            with mock.patch.object(daemon_module.sys, "executable", str(CLAUDE_SDK_PYTHON)):
                forked_session_id = await daemon_module.ClaudeCodeDaemon._fork_session_file(
                    source_session_id,
                    target_cwd,
                    config_dir,
                    "Fork title",
                )

            target_path = (
                config_dir
                / "projects"
                / daemon_module.project_key_for_directory(target_cwd)
                / f"{forked_session_id}.jsonl"
            )
            self.assertTrue(source_path.is_file())
            self.assertTrue(target_path.is_file())
            self.assertFalse((source_project_dir / target_path.name).exists())
            self.assertIn(forked_session_id, target_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
