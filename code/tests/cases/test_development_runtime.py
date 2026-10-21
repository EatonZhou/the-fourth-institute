"""Dev/Release Web share installed service identity, sockets and database.

:module: operations.deployment
:design: decision_08bb2d9141f9
:profiles: local.fixture
:risk: no-external-side-effects
"""
import unittest
from scripts.tfi_development_runtime import adapt_component, affected, restart_components


class PairedRuntimeTest(unittest.TestCase):
    def test_web_changes_code_and_port_without_changing_shared_state(self):
        env = {"PORTAL_DATABASE_URL": "same-database", "PORTAL_INSTANCE": "release",
               "PORTAL_RESOURCE_SERVICE_SOCKET": "same-resource.sock",
               "PORTAL_EXECUTION_SERVICE_SOCKET": "same-execution.sock",
               "PORTAL_SOCIETY_SERVICE_SOCKET": "same-society.sock"}
        config = {"dev_code": "/dev/code", "release_code": "/release/code",
                  "web_ports": {"dev": 19001, "release": 19002}}
        command, result = adapt_component({}, "web", ["python", "--port", "19000"], env, config)
        self.assertEqual(command[command.index("--port") + 1], "19002")
        self.assertEqual(command[command.index("--timeout-graceful-shutdown") + 1], "15")
        self.assertEqual(result["PYTHONPATH"], "/release/code")
        self.assertEqual(result["PORTAL_SHARED_SOURCE_ROOT"], "/dev/code")
        self.assertTrue(all(result[k] == v for k, v in env.items()))
        worker_command, worker = adapt_component({}, "worker", ["python"], env, config)
        self.assertNotIn("--timeout-graceful-shutdown", worker_command)
        self.assertEqual(worker["PYTHONPATH"], "/dev/code")
        self.assertEqual(worker["PORTAL_EXECUTION_WORKER_SOURCE_ROOT"], "/dev/code")
        self.assertEqual(worker["PORTAL_INSTANCE"], "release")

    def test_activation_targets_only_affected_application_components(self):
        self.assertEqual(affected(["code/core_skills/tfi-agent-rule/SKILL.md"]), [])
        self.assertEqual(affected(["code/management_portal/static/portal_nav.js"]), ["web", "dev-web"])
        self.assertEqual(affected(["code/management_portal/society_service/server.py"]),
                         ["society", "web", "dev-web"])
        all_components = affected(["code/management_common/protocol.py"])
        self.assertIn("worker", all_components)
        self.assertNotIn("postgres", all_components)
        self.assertNotIn("nats", all_components)

    def test_restart_uses_one_shared_pool_and_selected_web(self):
        self.assertEqual(restart_components("dev", ["web", "lineage"]), ["dev-web", "society"])
        self.assertEqual(restart_components("release", ["web", "lineage"]), ["web", "society"])
        with self.assertRaises(ValueError):
            restart_components("dev", ["postgres"])


if __name__ == "__main__":
    unittest.main()
