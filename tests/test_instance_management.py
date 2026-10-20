import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from scripts import tfi_instance_manager as manager


class InstanceManagementTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.platform = Path(directory.name)
        self.root = self.platform / "instance/one"
        self.root.mkdir(parents=True)
        self.code = self.platform / "code"
        self.code.mkdir()
        self.state = {"target": {"shared_code_root": str(self.code),
                                "install_root": str(self.root), "bind": "0.0.0.0",
                                "ports": {"web": 12345}, "service_prefix": "test-one"},
                      "completed": ["database"], "stages": [{"status": "installed"}]}
        for target, value in (("PLATFORM", self.platform), ("CODE", self.code)):
            p = patch.object(manager, target, value)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(manager.installer, "owned_state", return_value=self.state)
        p.start(); self.addCleanup(p.stop)

    def test_catalog_ignores_seed_and_symlink_and_rejects_traversal(self):
        (self.root / "installation.json").write_text("{}")
        (self.platform / "instance/example_instance/seed").mkdir(parents=True)
        (self.platform / "instance/alias").symlink_to(self.root, target_is_directory=True)
        with patch.object(manager, "inspect_instance", return_value={"name": "one"}) as inspect:
            self.assertEqual(manager.catalog()["instances"], [{"name": "one"}])
            inspect.assert_called_once_with("one", "")
        for name in ("../one", "alias", "/tmp", "one/../one"):
            with self.assertRaises(ValueError):
                manager.instance_root(name)

    def test_foreign_shared_code_is_rejected(self):
        self.state["target"]["shared_code_root"] = str(self.platform / "foreign")
        with self.assertRaisesRegex(ValueError, "different code"):
            manager.state_for("one")

    def test_migration_and_incomplete_install_are_not_started(self):
        self.state["migration"] = {"business_execution_allowed": False}
        with patch.object(manager, "launch") as launch:
            with self.assertRaisesRegex(ValueError, "Migration"):
                manager.enqueue("start", "one")
            launch.assert_not_called()
        self.state.pop("migration")
        self.state["stages"] = []
        self.assertTrue(manager.start_blocker(self.state))
        self.state["stages"] = [{"status": "installed"}]
        self.assertEqual(manager.start_blocker(self.state), "")

    def test_duplicate_request_and_failed_dispatch(self):
        with patch.object(manager, "launch"):
            job = manager.enqueue("stop", "one")
        with patch.object(manager, "job_alive", return_value=True), self.assertRaises(ValueError):
            manager.enqueue("start", "one")
        record = job["operation"]
        record["status"] = "completed"
        manager.installer.write_json(manager.control_path(self.root), record)
        with patch.object(manager, "launch", side_effect=OSError("dispatch failure")):
            with self.assertRaises(OSError):
                manager.enqueue("start", "one")
        self.assertEqual(manager.operation(self.root)["status"], "failed")

    def test_worker_uses_lifecycle_and_records_failures(self):
        with patch.object(manager, "launch"):
            job = manager.enqueue("stop", "one")["operation"]
        with patch.object(manager.time, "sleep"), patch.object(manager.installer, "lifecycle") as lifecycle:
            self.assertEqual(manager.execute("one", job["id"])["status"], "completed")
            lifecycle.assert_called_once_with("stop", self.root)
        with patch.object(manager, "launch"):
            job = manager.enqueue("start", "one")["operation"]
        with patch.object(manager.time, "sleep"), patch.object(manager.installer, "lifecycle", side_effect=ValueError("ownership changed")):
            self.assertEqual(manager.execute("one", job["id"])["error"], "ownership changed")

    def test_runtime_and_record_symlinks_are_rejected(self):
        (self.root / "runtime").symlink_to(self.code, target_is_directory=True)
        with self.assertRaises(ValueError):
            manager.enqueue("stop", "one")

    def test_linux_job_outlives_web_cgroup(self):
        record = {"id": "test", "unit": "tfi-control-test"}
        with patch.object(manager.sys, "platform", "linux"), patch.object(manager.subprocess, "run") as run:
            manager.launch(self.root, record)
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["systemd-run", "--user"])
        self.assertIn("--property=Type=exec", args)
        self.assertIn("_run", args)
        self.assertNotIn("shell", run.call_args.kwargs)

    @unittest.skipIf(os.name == "nt", "POSIX service-owner environment")
    def test_control_environment_does_not_inherit_instance_secrets(self):
        with patch.dict(os.environ, {"HOME": "/private/instance/home",
                                    "PORTAL_DATABASE_URL": "secret",
                                    "PGPASSWORD": "secret", "PYTHONPATH": "/other/code"}):
            env = manager.supervisor_environment()
        self.assertNotEqual(env["HOME"], "/private/instance/home")
        self.assertFalse({"PORTAL_DATABASE_URL", "PGPASSWORD", "PYTHONPATH"} & env.keys())

    def test_urls_use_bind_and_host_interfaces(self):
        target = self.state["target"]
        with patch.object(manager.socket, "getaddrinfo", return_value=[
                (2, 1, 6, "", ("10.138.30.36", 0))]):
            urls = manager.access_urls(target)
        self.assertIn("http://10.138.30.36:12345/", urls)
        self.assertFalse(any("0.0.0.0" in u for u in urls))
        target["bind"] = "127.0.0.1"
        self.assertEqual(manager.access_urls(target), ["http://127.0.0.1:12345/"])


class InstanceAuthorizationTests(unittest.TestCase):
    def setUp(self):
        from management_portal.http_admin import AdminHttp
        self.admin = False
        self.payload = {"name": "one", "action": "start"}
        self.http = AdminHttp(respond=lambda _, status, body: (status, body),
                              session_actor=lambda _: "member",
                              access_payload=Mock(), read_json=lambda _: self.payload,
                              is_admin=lambda _: self.admin)

    def test_admin_gate_applies_to_reads_and_mutations(self):
        with patch.object(manager, "command") as command:
            self.assertEqual(self.http.instances_get(None, None, None)[0], 403)
            self.assertEqual(self.http.instances_post(None, None, None)[0], 403)
            command.assert_not_called()
        self.admin = True
        with patch.object(manager, "command", return_value={"operation": {"status": "queued"}}) as command:
            self.assertEqual(self.http.instances_post(None, None, None)[0], 202)
            command.assert_called_once_with("start", name="one")

    def test_no_arbitrary_commands_or_paths(self):
        self.admin = True
        with patch.object(manager, "command") as command:
            for payload in ({**self.payload, "root": "/other"}, {"name": "one", "action": "cleanup"},
                            {"name": [], "action": "start"}, []):
                self.payload = payload
                self.assertEqual(self.http.instances_post(None, None, None)[0], 400)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
