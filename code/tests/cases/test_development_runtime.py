"""Dev/Release Web share installed service identity, sockets and database.

:module: operations.deployment
:design: decision_08bb2d9141f9
:profiles: local.fixture
:risk: no-external-side-effects
"""
import json
import os
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from scripts.tfi_development_runtime import adapt_component, affected, restart_components
from scripts import tfi_development_runtime as paired


class PairedRuntimeTest(unittest.TestCase):
    def test_explicit_site_without_pair_never_uses_checkout_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / "config/site.json"
            site.parent.mkdir()
            site.write_text('{}')
            with patch.dict('os.environ', {'PORTAL_SITE_CONFIG': str(site)}), \
                    patch('scripts.tfi_dev.configuration', side_effect=AssertionError('another instance discovered')):
                self.assertIsNone(paired.configured_root())
                (site.parent / 'development.json').write_text('{}')
                # A locator alone is not an installed pair. Legacy installations
                # retain their bounded import contract until adopted.
                self.assertIsNone(paired.configured_root())
                (Path(directory) / 'installation.json').write_text(json.dumps({
                    'schema': 'tfi.release-installation.v1',
                    'target': {'install_root': directory}}))
                self.assertEqual(paired.configured_root(), Path(directory))

    def test_web_changes_code_and_port_without_changing_shared_state(self):
        env = {"PORTAL_DATABASE_URL": "same-database", "PORTAL_INSTANCE": "release",
               "PORTAL_RESOURCE_SERVICE_SOCKET": "same-resource.sock",
               "PORTAL_EXECUTION_SERVICE_SOCKET": "same-execution.sock",
               "PORTAL_SOCIETY_SERVICE_SOCKET": "same-society.sock"}
        config = {"dev_code": "/dev/code", "release_code": "/release/code",
                  "web_ports": {"dev": 19001, "release": 19002}}
        command, result = adapt_component({}, "web", ["python", "--port", "19000"], env, config)
        self.assertEqual(command[command.index("--port") + 1], "19002")
        self.assertEqual(result["PYTHONPATH"], "/release/code")
        self.assertEqual(result["PORTAL_SHARED_SOURCE_ROOT"], "/dev/code")
        self.assertTrue(all(result[k] == v for k, v in env.items()))
        _worker_command, worker = adapt_component({}, "worker", ["python"], env, config)
        self.assertEqual(worker["PYTHONPATH"], "/dev/code")
        self.assertEqual(worker["PORTAL_EXECUTION_WORKER_SOURCE_ROOT"], "/dev/code")
        self.assertEqual(worker["PORTAL_INSTANCE"], "release")

    def test_activation_targets_only_affected_application_components(self):
        loaded = {"resource": {"management_portal.resources.server", "management_portal.resource_index_backend",
                               "management_portal.society_service.personal_ama"},
                  "execution": {"management_portal.execution.service"},
                  "society": {"management_portal.society_service.server", "management_portal.prompt_injection"},
                  "worker": {"management_portal.execution.worker", "management_portal.codex_app_server_client"}}
        self.assertEqual(affected(["code/core_skills/tfi-agent-rule/SKILL.md"], loaded), [])
        self.assertEqual(affected(["code/management_portal/static/portal_nav.js"], loaded), ["web", "dev-web"])
        self.assertEqual(affected(["code/management_portal/society_service/server.py"], loaded),
                         ["society", "web", "dev-web"])
        # A module outside the service packages restarts every service that imported it.
        self.assertEqual(affected(["code/management_portal/prompt_injection.py"], loaded),
                         ["society", "web", "dev-web"])
        self.assertEqual(affected(["code/management_portal/resource_index_backend.py"], loaded),
                         ["resource", "web", "dev-web"])
        self.assertEqual(affected(["code/management_portal/codex_app_server_client.py"], loaded),
                         ["worker", "web", "dev-web"])
        self.assertEqual(affected(["code/management_portal/society_service/personal_ama.py"], loaded),
                         ["resource", "society", "web", "dev-web"])
        # Not imported yet: the services read the new source when they first need it.
        self.assertEqual(affected(["code/management_portal/paper_store.py"], loaded), ["web", "dev-web"])
        # Without a record of its imports a service is restarted for any changed module.
        self.assertEqual(affected(["code/management_portal/paper_store.py"],
                                  {k: v for k, v in loaded.items() if k != "worker"}),
                         ["worker", "web", "dev-web"])
        # The permission port runs in every service with a HATG kernel, ResourceService included.
        self.assertEqual(affected(["code/management_portal/permission_system/temporary.py"], loaded),
                         ["resource", "execution", "society", "web", "dev-web"])
        all_components = affected(["code/management_common/protocol.py"], loaded)
        self.assertIn("worker", all_components)
        self.assertNotIn("postgres", all_components)
        self.assertNotIn("nats", all_components)

    def test_service_processes_record_lazily_imported_portal_modules(self):
        from management_common import loaded_modules
        with tempfile.TemporaryDirectory() as directory:
            folder = loaded_modules.directory(Path(directory))
            env = {**os.environ, loaded_modules.DIRECTORY_ENV: str(folder),
                   loaded_modules.COMPONENT_ENV: "resource",
                   "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
            script = ("import sys\n"
                      "from management_common import loaded_modules\n"
                      "loaded_modules.track()\n"
                      "import management_common.format_tags\n"
                      "print(len(loaded_modules.loaded(loaded_modules.directory(sys.argv[1]))['resource']))\n"
                      "sys.stdout.flush(); sys.stdin.read()\n")
            with subprocess.Popen([sys.executable, "-c", script, directory], env=env, text=True,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE) as process:
                self.assertGreater(int(process.stdout.readline()), 1)
                names = loaded_modules.loaded(folder)["resource"]
                self.assertIn("management_common.loaded_modules", names)
                self.assertIn("management_common.format_tags", names)
                self.assertNotIn("json", names)
                process.communicate("")
            # A record of a process that has exited is dropped.
            self.assertEqual(loaded_modules.loaded(folder), {})
            self.assertEqual(list(folder.iterdir()), [])
        self.assertEqual(loaded_modules.module_name("management_portal/execution/__init__.py"),
                         "management_portal.execution")
        self.assertIsNone(loaded_modules.module_name("scripts/tfi_dev.py"))

    def test_restart_uses_one_shared_pool_and_selected_web(self):
        self.assertEqual(restart_components("dev", ["web", "lineage"]), ["dev-web", "society"])
        self.assertEqual(restart_components("release", ["web", "lineage"]), ["web", "society"])
        with self.assertRaises(ValueError):
            restart_components("dev", ["postgres"])

    def test_explicit_restart_waits_for_a_fresh_replacement_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "runtime").mkdir()
            from management_common.instance_config import write_installation
            write_installation(root, {"target": {"install_root": str(root), "service_prefix": "test",
                                                "development": {"web_ports": {"dev": 19001}}}})
            (root / "config/development.json").write_text('{"web_ports":{"dev":19001}}')
            health_path = root / "runtime/worker-health.json"
            old = {"ok": True, "updated_at": 100, "current": {"worker_id": "old", "build_id": "same"}}
            new = {"ok": True, "updated_at": 102, "current": {"worker_id": "new", "build_id": "same"}}
            health_path.write_text(json.dumps(old))
            snapshots = iter([
                {**new, "updated_at": 100},  # Stale receipt must not satisfy this request.
                {**new, "ok": False},       # Replacement must be ready.
                new,
            ])
            def advance(_seconds):
                health_path.write_text(json.dumps(next(snapshots)))
            with patch.object(paired, "control") as control, \
                    patch.object(paired, "health", return_value={"ok": True}) as web, \
                    patch.object(paired.time, "time", return_value=101), \
                    patch.object(paired.time, "sleep", side_effect=advance) as sleep:
                result = paired.restart(root, "dev", ["execution-worker"])
            control.assert_called_once_with("kill", "--kill-whom=main", "--signal=USR1", "test-worker.service")
            self.assertEqual(sleep.call_count, 3)
            self.assertEqual(result["execution_worker"]["current"]["worker_id"], "new")
            web.assert_called_once_with(19001, "dev")

    def test_explicit_restart_does_not_accept_an_unchanged_healthy_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime").mkdir()
            (root / "runtime/worker-health.json").write_text(
                '{"ok":true,"updated_at":102,"current":{"worker_id":"old"}}')
            with patch.object(paired.time, "monotonic", side_effect=[0, 0, 2]), \
                    patch.object(paired.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "has not activated a new Worker"):
                    paired.wait_worker_replacement(root, "old", requested_at=101, timeout_seconds=1)

    def test_generation_only_restart_keeps_reload_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            from management_common.instance_config import write_installation
            write_installation(root, {"target": {"install_root": str(root), "service_prefix": "test",
                                                "development": {"web_ports": {"dev": 19001}}}})
            (root / "config/development.json").write_text('{"web_ports":{"dev":19001}}')
            with patch.object(paired, "control") as control, \
                    patch.object(paired, "health", return_value={"ok": True}):
                result = paired.restart(root, "dev", ["execution-worker"], force_worker_restart=False)
            control.assert_called_once_with("kill", "--kill-whom=main", "--signal=HUP", "test-worker.service")
            self.assertTrue(result["ok"])

    def test_publication_records_a_release_event_only_for_a_new_healthy_commit(self):
        """发布激活成功且 Release 提交变化时记录一次发布事件，提交未变或激活失败时不记录"""
        old, new = "a" * 40, "b" * 40
        for previous, ok, recorded in ((old, True, True), (new, True, False), (old, False, False)):
            with self.subTest(previous=previous, ok=ok), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "runtime").mkdir()
                (root / "runtime/development-activation.json").write_text(json.dumps({"commit": previous}))
                request = root / "runtime/request.json"
                receipt = root / "runtime/request-result.json"
                request.write_text(json.dumps({"commit": new, "paths": ["code/scripts/a.py"],
                                               "receipt": str(receipt), "parent_commit": old}))
                with patch("management_common.instance_config.read_development",
                           return_value={"release_code": directory}), \
                        patch.object(paired.subprocess, "check_output", return_value=new + "\n"), \
                        patch.object(paired, "activate", return_value={"ok": ok, "commit": new}), \
                        patch.object(paired, "record_release",
                                     return_value={"ok": True, "sequence": 7}) as record:
                    result = paired.apply_request(root, request)
                self.assertEqual(record.call_count, int(recorded))
                if recorded:
                    self.assertEqual(record.call_args.args[2], new)
                    self.assertEqual(record.call_args.args[1]["parent_commit"], old)
                self.assertEqual(json.loads(receipt.read_text()).get("release_event"),
                                 {"ok": True, "sequence": 7} if recorded else None)
                self.assertEqual(result["requested_commit"], new)

    def test_service_command_preserves_explicit_restart_intent_for_a_pair(self):
        from scripts import portal_services
        for force in (False, True):
            with self.subTest(force=force), \
                    patch.object(paired, "configured_root", return_value=Path("/paired")), \
                    patch.object(paired, "restart", return_value={"ok": True}) as restart:
                portal_services.restart("dev", components=["execution-worker"], force_worker_restart=force)
                restart.assert_called_once_with(Path("/paired"), "dev", ["execution-worker"],
                    force_worker_restart=force)


if __name__ == "__main__":
    unittest.main()
