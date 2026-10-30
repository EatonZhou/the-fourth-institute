"""Explicit PostgreSQL integration tests for Claude's shared session boundary.

Uses the same PostgreSQL factory as production under pytest's isolated schema.
The storage case uses synthetic results. Set PORTAL_TEST_CLAUDE_ROUTE as well
to opt into the deployed daemon/provider recovery case; it consumes subscription
usage, creates native resume material, and never changes an existing Resident.
The deployed daemon resolves the route in the Portal database, which the test's
isolated schema does not hold, so PORTAL_TEST_CLAUDE_ACCOUNT_DIR and
PORTAL_TEST_CLAUDE_MODEL name that route's account directory and model.

:module: execution.providers
"""
from __future__ import annotations
from tests.cases.agent_data_test_support import session_transcript_ids

import os
from datetime import datetime, timezone
import tempfile
import unittest
from unittest import mock
import uuid

from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.session_manager import SessionManager


class ClaudePostgresTest(unittest.TestCase):
    @unittest.skipUnless(
        all(os.getenv(name) for name in (
            "PORTAL_TEST_CLAUDE_ROUTE", "PORTAL_TEST_CLAUDE_ACCOUNT_DIR", "PORTAL_TEST_CLAUDE_MODEL")),
        "explicit live Claude route, account directory and model required",
    )
    def test_live_daemon_result_recovers_without_repeating_provider_call(self):
        """真实 Claude 调用完成后持久化中断，恢复时取回已有结果而不重复调用 Provider

        Real provider + deployed socket + isolated PostgreSQL, including a crash boundary."""
        from management_portal.claude_code_client import shared_client
        from management_portal.execution.agent_data_projection import AgentDataProjector
        from management_portal.execution.claude_backend import ClaudeBackend
        from management_portal.execution.harness import AgentHarness
        from management_portal.execution.runtime import ExecutionRuntime, RuntimeContext

        route = os.environ["PORTAL_TEST_CLAUDE_ROUTE"]
        profile = {"config_dir": os.environ["PORTAL_TEST_CLAUDE_ACCOUNT_DIR"],
                   "default_model": os.environ["PORTAL_TEST_CLAUDE_MODEL"]}
        from tests.cases.model_api_test_support import register_apis
        register_apis({"id": route, "kind": "claude_code_subscription", "enabled": True,
                       "provider": "anthropic", **profile,
                       "models": [{"id": profile["default_model"], "enabled": True}]})
        checkpoints = []
        binding_id = "claude-live-acceptance:" + uuid.uuid4().hex
        marker = "PORTAL_RUNTIME_" + uuid.uuid4().hex
        backend = ClaudeBackend()
        accepted_at = datetime.now(timezone.utc).isoformat()

        try:
            with tempfile.TemporaryDirectory(prefix="claude-runtime-acceptance-") as directory:
                def runtime():
                    store = AgentDataStore()
                    return ExecutionRuntime(
                        agent_providers={"claude": backend}, agent_harness=AgentHarness(),
                        session_manager=SessionManager(agent_data=store),
                        agent_data_projector=AgentDataProjector(store), state_root=directory,
                    ), store

                engine, store = runtime()
                request = ExecutionRequest.new(
                    service_type="agent_session", provider="claude", operation="turn",
                    actor="test", agent_id=binding_id, cwd=directory,
                    prompt=f"Remember {marker}. Reply with exactly this marker. Do not use tools.",
                    session=SessionOptions(mode="persistent", binding_id=binding_id, backend_route_id=route),
                    runtime_options={"provider_profile": route, "config_dir": profile["config_dir"],
                                     "model": profile["default_model"], "is_owner": False},
                ).validate()
                with mock.patch.object(engine.sessions, "complete", side_effect=RuntimeError("simulated persistence interruption")):
                    with self.assertRaisesRegex(RuntimeError, "simulated persistence interruption"):
                        engine.execute(request, RuntimeContext(accepted_at=accepted_at, checkpoint_provider=checkpoints.append))
                checkpoint = next(c for c in reversed(checkpoints) if c.get("provider_completed"))
                self.assertEqual(checkpoint["output"]["message"], marker)
                store.database.close()

                # Reopen the SQL store and prove recovery uses the durable result.
                recovered, store = runtime()
                with mock.patch.object(backend, "execute", side_effect=AssertionError("provider replay")):
                    result = recovered.execute(request, RuntimeContext(accepted_at=accepted_at, recovery_checkpoint=checkpoint))
                self.assertEqual(result.output["message"], marker)
                self.assertTrue(result.usage)
                binding = recovered.sessions.provider_binding(binding_id)
                self.assertEqual(binding["backend_route_id"], route)
                self.assertEqual(binding["backend_session_id"], result.backend_session_id)
                before = session_transcript_ids(store, binding["provider_session_ref"])
                followup = ExecutionRequest.new(
                    service_type="agent_session", provider="claude", operation="turn",
                    actor="test", agent_id=binding_id, cwd=directory,
                    prompt="Reply with exactly the marker I asked you to remember. Do not use tools.",
                    session=request.session, runtime_options=request.runtime_options,
                ).validate()
                answer = recovered.execute(followup)
                self.assertEqual(answer.output["message"], marker)
                self.assertEqual(answer.backend_session_id, result.backend_session_id)
                after = session_transcript_ids(store, binding["provider_session_ref"])
                self.assertEqual(after[:len(before)], before)
                self.assertEqual(len(after), len(set(after)))
                self.assertGreater(len(after), len(before))
        finally:
            shared_client().close_session(binding_id)

    def test_routes_and_history_survive_reopening_the_shared_store(self):
        """重新打开共享 PostgreSQL 存储后，各路由的会话绑定和历史仍可恢复"""
        store = AgentDataStore()
        self.assertEqual(store.database.backend, "postgresql")
        manager = SessionManager(agent_data=store)
        requests = []
        for route in ("claude_one", "claude_two", "deepseek_primary"):
            agent = "test_agent_" + route
            request = ExecutionRequest.new(
                service_type="agent_session", provider="claude", operation="turn",
                actor="test", agent_id=agent, cwd="/workspace", prompt="synthetic storage test",
                session=SessionOptions(mode="persistent", binding_id="resident:" + agent,
                                       backend_route_id=route),
            ).validate()
            session = manager.prepare(request)
            manager.complete(request, session, "thread_" + route, backend_route_id=route)
            binding = manager.provider_binding(request.session.binding_id)
            segment = store.put_segment(segment_type="model", content="reply_" + route,
                                        metadata={"role": "assistant"})
            requests.append((request, binding, segment))
        store.database.close()

        reopened = AgentDataStore()
        recovered = SessionManager(agent_data=reopened)
        for request, binding, segment in requests:
            session = recovered.prepare(request)
            self.assertEqual(session.backend_route_id, request.session.backend_route_id)
            self.assertEqual(session.backend_session_id, binding["backend_session_id"])
            self.assertEqual(session.provider_session_ref, binding["provider_session_ref"])
            self.assertFalse(session.is_new_backend_session)
            self.assertEqual(reopened.segment(segment)["content"], "reply_" + session.backend_route_id)
