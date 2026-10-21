"""Opt-in paid/subscription smoke; never part of unattended offline validation.

Run with the pinned SDK Python and PORTAL_TEST_CLAUDE_ACCOUNT_DIR set to a
dedicated, already authenticated account directory. This creates two real turns
and a native transcript. It registers its route only in the isolated test
database and never touches a live Resident; the daemon, SDK, CLI and provider
are real.

:module: execution.providers
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

from tests.cases.model_api_test_support import register_apis


LIVE_MODEL = os.getenv("PORTAL_TEST_CLAUDE_MODEL", "sonnet")


@unittest.skipUnless(os.getenv("PORTAL_TEST_CLAUDE_ACCOUNT_DIR"),
                     "explicit authenticated subscription smoke opt-in required")
class ClaudeSubscriptionLiveTest(unittest.IsolatedAsyncioTestCase):
    async def test_live_socket_tool_roundtrip_and_sanitized_login_status(self):
        """真实 Claude 订阅账户经 daemon socket 完成一次 Portal 工具调用往返"""
        from management_portal.claude_code_client import ClaudeCodeClient

        account = Path(os.environ["PORTAL_TEST_CLAUDE_ACCOUNT_DIR"])
        entry = {"id": "native_smoke", "enabled": True, "kind": "claude_code_subscription",
                 "provider": "anthropic", "config_dir": str(account), "default_model": LIVE_MODEL,
                 "models": [{"id": LIVE_MODEL, "enabled": True}]}
        # The child daemon inherits the isolated test database and reads the route there.
        register_apis(entry)
        with tempfile.TemporaryDirectory(prefix="claude-socket-smoke-") as directory:
            socket = Path(directory) / "daemon.sock"
            child = subprocess.Popen([sys.executable, "-c",
                "import asyncio,sys; from pathlib import Path; "
                "from management_portal import claude_code_daemon as d; "
                "asyncio.run(d._serve(Path(sys.argv[1])))",
                str(socket)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(200):
                    if socket.exists() or child.poll() is not None:
                        break
                    await asyncio.sleep(.05)
                self.assertIsNone(child.poll(), "test daemon exited before becoming ready")
                client = ClaudeCodeClient(socket)
                marker = "PORTAL_TOOL_" + uuid.uuid4().hex
                calls = []

                def dispatch(call):
                    calls.append(call)
                    return {"content": [{"type": "text", "text": marker}], "isError": False}

                result = await asyncio.wait_for(asyncio.to_thread(client.run_turn,
                    session_key="test:" + uuid.uuid4().hex, session_id="",
                    prompt="Call the portal_receipt tool once with {}. Reply with exactly its returned text. Do not use other tools.",
                    cwd=str(Path(__file__).resolve().parents[2]), config_dir=str(account),
                    provider_profile="native_smoke", model=LIVE_MODEL, is_owner=False,
                    turn_id="smoke-tool-" + uuid.uuid4().hex,
                    tools=[{"name": "portal_receipt", "description": "Get the verification receipt.",
                            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}],
                    tool_callback=dispatch,
                ), timeout=90)
                self.assertEqual(result.response, marker)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["tool"], "portal_receipt")
            finally:
                child.terminate()
                try:
                    await asyncio.to_thread(child.wait, timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    await asyncio.to_thread(child.wait)

    async def test_native_terminal_event_and_resume_after_daemon_recreation(self):
        """真实 Claude 订阅账户每轮只发一个结束事件，重建 daemon 后可恢复同一会话"""
        from management_portal import claude_code_daemon as module

        account = Path(os.environ["PORTAL_TEST_CLAUDE_ACCOUNT_DIR"])
        self.assertTrue(account.is_absolute() and account.is_dir())
        entry = {
            "id": "claude_subscription_smoke", "enabled": True,
            "kind": "claude_code_subscription", "provider": "anthropic",
            "config_dir": str(account), "default_model": LIVE_MODEL,
            "models": [{"id": LIVE_MODEL, "enabled": True}],
        }
        marker = "PORTAL_" + uuid.uuid4().hex[:12]
        params = {
            "session_key": "subscription-smoke:" + uuid.uuid4().hex,
            "cwd": str(Path(__file__).resolve().parents[2]),
            "config_dir": str(account), "provider_profile": entry["id"],
            "model": LIVE_MODEL, "is_owner": False,
        }
        session_id = ""
        prompts = [
            f"Remember this marker for the next message: {marker}. "
            "Reply with exactly that marker. Do not use tools.",
            "Reply with exactly the marker from my previous message. Do not use tools.",
        ]
        with mock.patch.object(module, "_provider_profile", return_value=entry):
            for index, prompt in enumerate(prompts):
                daemon = module.ClaudeCodeDaemon()
                events = []

                async def emit(event):
                    events.append(event)

                try:
                    result = await asyncio.wait_for(daemon.run_turn({
                        **params, "session_id": session_id,
                        "turn_id": f"smoke-{index}", "prompt": prompt,
                    }, emit), timeout=90)
                    self.assertEqual(result["response"], marker)
                    answers = [e["params"]["item"] for e in events
                               if e.get("method") == "item/completed"
                               and e["params"]["item"].get("type") == "agentMessage"]
                    self.assertEqual(len(answers), 1)
                    self.assertEqual(answers[0]["phase"], "final_answer")
                    self.assertEqual(answers[0]["text"], marker)
                    self.assertTrue(result["session_id"])
                    if session_id:
                        self.assertEqual(result["session_id"], session_id)
                    session_id = result["session_id"]
                    terminal = [e for e in events if e.get("method") == "turn/completed"]
                    self.assertEqual(len(terminal), 1)
                    self.assertEqual(terminal[0]["params"]["turn"]["id"], f"smoke-{index}")
                    self.assertFalse(any(e.get("payload", {}).get("type") == "function_call"
                                         or e.get("method") == "item/started" for e in events))
                finally:
                    await daemon.close()
