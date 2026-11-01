"""「Portal 实例」从本机实例登记查找实例，按清单服务显示真实状态。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from management_common import host_instances
from management_common.instance_manifest import load
from scripts import instance_services
from scripts import tfi_instance_manager as manager
from tests.cases.instance_test_support import write_instance


class InstanceManagementTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="h")).resolve()
        self.addCleanup(shutil.rmtree, self.home)
        self.enterContext(patch.object(host_instances, "user_home", return_value=self.home))
        self.root = write_instance(self.home / "instances/one")
        document = json.loads((self.root / "instance.json").read_text())
        document.update(name="one", instance_id="one-id")
        document["network"]["bind"] = "0.0.0.0"
        (self.root / "instance.json").write_text(json.dumps(document))
        host_instances.register(load(self.root))
        self.states = {c: {"active": "active", "pid": 1} for c in document["services"]["components"]}
        self.enterContext(patch.object(instance_services, "status", side_effect=lambda instance: self.states))

    def test_catalog_lists_registered_instances_and_unreadable_ones(self):
        """安装与实例回归：只列出本机登记的实例，读不到清单的标为无法读取。"""
        broken = self.home / "instances/broken"
        broken.mkdir()
        registry = host_instances.read_registry()
        registry["broken"] = str(broken)
        host_instances.registry_path().write_text(json.dumps(registry))
        with patch.object(manager, "access_urls", return_value=[]):
            items = {item["name"]: item for item in manager.catalog(str(self.root))["instances"]}
        self.assertEqual(set(items), {"one", "broken"})
        self.assertEqual(items["one"]["status"], "running")
        self.assertTrue(items["one"]["current"])
        self.assertEqual(items["broken"]["status"], "unavailable")
        with self.assertRaisesRegex(ValueError, "not registered"):
            manager.state_for("../one")

    def test_partial_status_names_stopped_services_and_hides_urls(self):
        """安装与实例回归：部分服务运行时列出未运行的服务，入口未运行时不显示地址。"""
        self.states["web"] = {"active": "inactive", "pid": 0}
        item = manager.inspect_instance("one")
        self.assertEqual(item["status"], "partial")
        self.assertEqual(item["stopped_services"], ["web"])
        self.assertEqual(item["urls"], [])
        self.assertTrue(item["can_start"] and item["can_stop"])

    def test_incomplete_creation_is_not_started(self):
        """安装与实例回归：创建未完成的实例不可启动。"""
        record = self.root / instance_services.CREATION_RECORD
        record.parent.mkdir(exist_ok=True)
        record.write_text(json.dumps({"phase": "database", "status": "failed", "error": "psql exited 3"}))
        self.states.update({c: {"active": "inactive", "pid": 0} for c in self.states})
        self.assertFalse(manager.inspect_instance("one")["can_start"])
        with patch.object(manager, "launch") as launch:
            with self.assertRaisesRegex(ValueError, "creation has not completed"):
                manager.enqueue("start", "one")
            launch.assert_not_called()

    def test_duplicate_request_and_failed_dispatch(self):
        """安装与实例回归：duplicate request and failed dispatch。"""
        with patch.object(manager, "launch"):
            job = manager.enqueue("stop", "one")
        with patch.object(manager, "job_alive", return_value=True), self.assertRaises(ValueError):
            manager.enqueue("start", "one")
        record = job["operation"]
        record["status"] = "completed"
        manager.atomic_json(manager.control_path(self.root), record)
        with patch.object(manager, "launch", side_effect=OSError("dispatch failure")):
            with self.assertRaises(OSError):
                manager.enqueue("start", "one")
        self.assertEqual(manager.operation(self.root)["status"], "failed")

    def test_worker_uses_lifecycle_and_records_failures(self):
        """安装与实例回归：worker uses lifecycle and records failures。"""
        with patch.object(manager, "launch"):
            job = manager.enqueue("stop", "one")["operation"]
        with patch.object(manager.time, "sleep"), patch.object(instance_services, "lifecycle") as lifecycle:
            self.assertEqual(manager.execute("one", job["id"])["status"], "completed")
            lifecycle.assert_called_once()
            self.assertEqual(lifecycle.call_args.args, ("stop", self.root))
            if manager.sys.platform.startswith("linux"):
                self.assertGreater(lifecycle.call_args.kwargs["shutdown_timeout"], 0)
                self.assertLessEqual(lifecycle.call_args.kwargs["shutdown_timeout"], 20)
        with patch.object(manager, "launch"):
            job = manager.enqueue("start", "one")["operation"]
        with patch.object(manager.time, "sleep"), \
                patch.object(instance_services, "lifecycle", side_effect=ValueError("ownership changed")):
            self.assertEqual(manager.execute("one", job["id"])["error"], "ownership changed")

    def test_runtime_and_record_symlinks_are_rejected(self):
        """安装与实例回归：runtime and record symlinks are rejected。"""
        shutil.rmtree(self.root / "runtime", ignore_errors=True)
        (self.root / "runtime").symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(ValueError):
            manager.enqueue("stop", "one")

    def test_linux_job_outlives_web_cgroup(self):
        """安装与实例回归：linux job outlives web cgroup。"""
        record = {"id": "test", "unit": "tfi-control-test"}
        (self.root / "runtime").mkdir(exist_ok=True)
        with patch.object(manager.sys, "platform", "linux"), patch.object(manager.subprocess, "run") as run:
            manager.launch(self.root, record)
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["systemd-run", "--user"])
        self.assertIn("--property=Type=exec", args)
        self.assertIn("--setenv=PORTAL_INSTANCE_ROOT=" + str(self.root), args)
        self.assertIn("_run", args)
        self.assertNotIn("shell", run.call_args.kwargs)

    @unittest.skipIf(os.name == "nt", "POSIX service-owner environment")
    def test_control_environment_does_not_inherit_instance_secrets(self):
        """安装与实例回归：control environment does not inherit instance secrets。"""
        import pwd
        with patch.dict(os.environ, {"HOME": "/private/instance/home",
                                    "PORTAL_DATABASE_URL": "secret", "PORTAL_INSTANCE_ROOT": str(self.root),
                                    "PGPASSWORD": "secret", "PYTHONPATH": "/other/code"}):
            env = manager.supervisor_environment()
        self.assertEqual(env["HOME"], pwd.getpwuid(os.getuid()).pw_dir)
        self.assertFalse({"PORTAL_DATABASE_URL", "PORTAL_INSTANCE_ROOT", "PGPASSWORD", "PYTHONPATH"} & env.keys())

    def test_control_environment_preserves_uppercase_windows_runtime_keys(self):
        values = {"SYSTEMROOT": "C:\\Windows", "COMSPEC": "C:\\Windows\\System32\\cmd.exe",
                  "PATHEXT": ".EXE;.CMD", "PROCESSOR_ARCHITECTURE": "AMD64"}
        with patch.dict(os.environ, {**values, "PGPASSWORD": "secret", "PORTAL_DATABASE_URL": "secret"}):
            env = manager.supervisor_environment()
        self.assertEqual({key: env.get(key) for key in values}, values)
        self.assertEqual(env['PYTHONUTF8'], '1')
        self.assertNotIn("PGPASSWORD", env)
        self.assertNotIn("PORTAL_DATABASE_URL", env)

    def test_urls_use_bind_and_host_interfaces(self):
        """安装与实例回归：地址按监听地址与主机网卡给出，不把通配地址当网址。"""
        interfaces = '[{"addr_info":[{"family":"inet","local":"10.138.30.36"},' \
                     '{"family":"inet","local":"127.0.0.1"},{"family":"inet6","local":"fe80::1"}]}]'
        instance = load(self.root)
        with patch.object(manager.sys, "platform", "linux"), \
                patch.object(manager.subprocess, "run", return_value=Mock(stdout=interfaces)), \
                patch.object(manager.socket, "getaddrinfo", return_value=[]), \
                patch.object(manager.socket, "socket", side_effect=OSError):
            self.assertEqual(manager.access_urls(instance), ["http://10.138.30.36:28086/"])
        with patch.object(manager.sys, "platform", "linux"), \
                patch.object(manager.subprocess, "run", side_effect=FileNotFoundError), \
                patch.object(manager.socket, "getaddrinfo", return_value=[(None, None, None, None, ("192.168.1.4", 0))]), \
                patch.object(manager.socket, "socket", side_effect=OSError):
            self.assertEqual(manager.access_urls(instance), ["http://192.168.1.4:28086/"])
        instance.document["network"]["bind"] = "127.0.0.1"
        self.assertEqual(manager.access_urls(instance), ["http://127.0.0.1:28086/"])


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
        """安装与实例回归：admin gate applies to reads and mutations。"""
        with patch.object(manager, "command") as command:
            self.assertEqual(self.http.instances_get(None, None, None)[0], 403)
            self.assertEqual(self.http.instances_post(None, None, None)[0], 403)
            command.assert_not_called()
        self.admin = True
        with patch.object(manager, "command", return_value={"operation": {"status": "queued"}}) as command:
            self.assertEqual(self.http.instances_post(None, None, None)[0], 202)
            command.assert_called_once_with("start", name="one")

    def test_no_arbitrary_commands_or_paths(self):
        """安装与实例回归：no arbitrary commands or paths。"""
        self.admin = True
        with patch.object(manager, "command") as command:
            for payload in ({**self.payload, "root": "/other"}, {"name": "one", "action": "cleanup"},
                            {"name": [], "action": "start"}, []):
                self.payload = payload
                self.assertEqual(self.http.instances_post(None, None, None)[0], 400)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
