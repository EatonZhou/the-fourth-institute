"""超时默认值按实例当前合同设置，显式配置和轮询节奏保持原语义。

:module: execution.service
"""
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from management_common.postgres_database import PostgresDatabase
from management_common.service_socket import retry_delays
from management_portal.codex_app_server_client import CodexAppServerClient
from management_portal.execution.scheduler import SchedulerClient
from management_portal.execution.resident_configuration import normalize_backend_options
from management_portal.execution.transport import ExecutionServiceClient
from management_portal.society_service.script_runs import store


class TimeoutDefaultsTests(unittest.TestCase):
    def test_database_connection_configures_query_and_lock_budgets(self):
        connection = MagicMock()
        PostgresDatabase("postgresql:///test", schema="agent")._configure(connection)
        statements = [call.args[0] for call in connection.execute.call_args_list]
        self.assertIn("SET statement_timeout = '360s'", statements)
        self.assertIn("SET lock_timeout = '30s'", statements)
        connection.commit.assert_called_once()

    def test_codex_defaults_and_explicit_overrides_are_not_scaled_twice(self):
        default = CodexAppServerClient("/tmp/not-connected.sock")
        self.assertEqual(
            (default.timeouts.request, default.timeouts.lifecycle,
             default.timeouts.turn_start),
            (90, 180, 360),
        )
        explicit = CodexAppServerClient(
            "/tmp/not-connected.sock", request_timeout_seconds=7,
            lifecycle_request_timeout_seconds=11, turn_start_request_timeout_seconds=13,
        )
        self.assertEqual(
            (explicit.timeouts.request, explicit.timeouts.lifecycle,
             explicit.timeouts.turn_start),
            (7, 11, 13),
        )

    def test_worker_claim_extends_lease_but_keeps_long_poll_cadence(self):
        service = MagicMock()
        service.request.return_value = {"claimed": False}
        client = SchedulerClient(worker_id="test", build_id="test", capabilities=[],
                                 service_client=service)
        client.claim()
        call = service.request.call_args
        self.assertEqual(call.args[1]["lease_seconds"], 270)
        self.assertEqual(call.args[1]["timeout_seconds"], 15)
        self.assertEqual(call.kwargs["timeout"], 21)  # 15 second poll + 6 second RPC margin.
        client.heartbeat("execution-test", "claim-test")
        self.assertEqual(service.request.call_args.args[1]["lease_seconds"], 270)

    def test_rpc_override_and_restart_backoff_preserve_units(self):
        client = ExecutionServiceClient("/tmp/not-connected.sock")
        self.assertEqual((client.timeout, client.restart_wait), (90, 90))
        explicit = ExecutionServiceClient("/tmp/not-connected.sock", timeout=12, restart_wait=0)
        self.assertEqual((explicit.timeout, explicit.restart_wait), (12, 0))
        delays = retry_delays()
        self.assertEqual([next(delays) for _ in range(5)], [0.25, 0.5, 1, 2, 2])

    def test_api_default_and_explicit_timeout_have_same_contract(self):
        self.assertEqual(normalize_backend_options({}, "provider_api")["timeout_seconds"], 360)
        self.assertEqual(
            normalize_backend_options({"timeout_seconds": 23}, "provider_api")["timeout_seconds"], 23,
        )

    def test_script_lease_claim_and_renewal_share_duration(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {"holder": "test"}
        with patch.object(store, "domain") as domain:
            domain.return_value.transaction.return_value.__enter__.return_value = connection
            self.assertTrue(store.acquire_lease("test"))
            self.assertEqual(connection.execute.call_args.args[1], ("test", 90))
            self.assertTrue(store.renew_lease("test"))
            self.assertEqual(connection.execute.call_args.args[1], (90, "test"))


def test_browser_timeout_defaults_and_explicit_cancellation():
    """HTTP 默认等待360秒，显式毫秒时限、无时限与立即取消保持原语义。"""
    script = Path(__file__).with_name("javascript") / "browser_timeout_defaults.mjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
