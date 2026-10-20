"""Portable contract tests; these deliberately do not claim Windows acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import zipfile
import hashlib

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(REPO))
from scripts import tfi_windows_launcher as launcher
from scripts import tfi_windows_user_services as services
from scripts import tfi_release_platform_windows as windows
from scripts import tfi_release_initialization as initialization
from scripts import tfi_release_install as installer
import prepare_install


class WindowsInstallerTests(unittest.TestCase):
    def target(self, root):
        return launcher.make_target(root, "sample_one", {"run_id": "fixture"},
                                    ports={"web": 28199, "postgres": 56432, "nats": 14223})

    def definition(self, base):
        target = self.target(base)
        root = Path(target["install_root"])
        root.mkdir(parents=True)
        (root / "runtime").mkdir()
        (root / "installation.json").write_text(json.dumps({"target": target}))
        value = json.loads(windows.unit_text(target, "web", []))
        return value, windows.unit_name(target, "web")

    def test_instances_share_programs_but_not_state_or_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            one = self.target(base)
            two = launcher.make_target(base, "sample_two", {"run_id": "fixture"})
            self.assertEqual(one["shared_code_root"], two["shared_code_root"])
            self.assertEqual(one["python"], two["python"])
            self.assertNotEqual(one["service_prefix"], two["service_prefix"])
            self.assertNotEqual(one["data_roots"], two["data_roots"])
            self.assertNotEqual(one["database_name"], two["database_name"])
            self.assertFalse(one.get("windows_service_account"))
            windows.dependency_preflight(one)

    def test_invalid_mode_and_instance_traversal_rejected(self):
        for value in ("../host", "C:\\host", "sample/one", "", "example-instance"):
            with self.assertRaises(ValueError):
                launcher.instance_name(value)
        with self.assertRaises(ValueError):
            windows.validate_service_mode({"windows_service_mode": "anything"})
        with self.assertRaises(ValueError):
            windows.dependency_preflight({"windows_service_mode": "scm"})

    def test_running_launcher_blocks_duplicate_window_and_uninstall_until_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with launcher.launcher_lock(base):
                with self.assertRaisesRegex(RuntimeError, "已经打开"):
                    with launcher.launcher_lock(base):
                        self.fail("A second owner acquired the launcher lock")
            # The lock file can remain; only the OS lock determines ownership.
            with launcher.launcher_lock(base):
                pass

    def test_child_environment_keeps_service_registry_and_instance_unicode_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "中文 platform"
            target = self.target(base)
            target.update(pg_bin=str(base / "postgres/bin"))
            registry = Path(tmp) / "operator-local/TFI/services"
            with patch.object(installer, "platform_adapter", return_value=windows), \
                    patch.dict(os.environ, {"LOCALAPPDATA": str(registry.parents[1])}, clear=True):
                env = installer.clean_environment(target)
                self.assertEqual(env["TFI_WINDOWS_SERVICE_DIRECTORY"], str(registry))
                self.assertEqual(env["PYTHONUTF8"], "1")
                self.assertTrue(Path(env["LOCALAPPDATA"]).is_relative_to(Path(target["install_root"])))
                self.assertTrue(Path(env["APPDATA"]).is_relative_to(Path(target["install_root"])))
                # Repeated boundaries must keep the original registry, rather
                # than moving it under the new per-instance LOCALAPPDATA.
                with patch.dict(os.environ, env, clear=True):
                    self.assertEqual(windows.unit_directory(), registry)
                    nested = installer.clean_environment(target)
                    self.assertEqual(nested["TFI_WINDOWS_SERVICE_DIRECTORY"], str(registry))

    def test_failed_native_download_can_be_stopped_without_deleting_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = self.target(base)
            (base / "code/scripts").mkdir(parents=True)
            (base / "code/scripts/tfi_release_install.py").write_text("# fixture")
            target.update(python=sys.executable, pg_bin=str(base / "missing-pg/bin"),
                          pg_share=str(base / "missing-pg/share"), nats_server=str(base / "missing-nats.exe"),
                          managed_dependencies=["postgres", "nats"])
            root = Path(target["install_root"])
            root.mkdir(parents=True)
            preserved = root / "data/keep.txt"
            preserved.parent.mkdir()
            preserved.write_text("instance data")
            registry = base / "service-definitions"
            absent = {"load": "not-found", "active": "inactive", "sub": "dead", "pid": 0}
            scm = Mock()
            scm.status.return_value = absent
            with patch.object(installer, "platform_adapter", return_value=windows), \
                    patch.object(windows, "unit_directory", return_value=registry), \
                    patch.object(services.WindowsServiceControl, "scm", return_value=scm), \
                    patch("scripts.tfi_release_provider_services.owned_services", return_value=None):
                normalized = installer.target_plan(target, protected_roots=[], allow_missing_native=True)
                installer.write_json(root / "installation.json", {
                    "schema": installer.SCHEMA, "target": normalized, "status": "failed",
                    "completed": [], "stages": [], "protected_roots": []})
                result = installer.lifecycle("stop", root)
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(preserved.read_text(), "instance data")
                # Starting still requires the actual programs and initialization.
                with self.assertRaisesRegex(ValueError, "Required executable"):
                    installer.lifecycle("start", root)

    def test_first_admin_opens_only_invitation_not_full_console_text(self):
        text = ("Setup link for the first administrator:\n"
                "  http://127.0.0.1:28199/join#invite=test-token\n"
                "Open in browser. Next steps: http://127.0.0.1:28199/guide#admin")
        self.assertEqual(launcher.invitation_url(text), "http://127.0.0.1:28199/join#invite=test-token")
        with self.assertRaises(ValueError):
            launcher.invitation_url("no invitation")

    def test_user_service_registration_is_idempotent_and_owned(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            value, name = self.definition(base)
            directory = base / "definitions"
            content = json.dumps(value)
            self.assertEqual(services.register(directory, {name: content}), [name])
            self.assertEqual(services.register(directory, {name: content}), [])
            with self.assertRaises(ValueError):
                services.register(directory, {name: json.dumps({**value, "python": str(base / "other.exe")})})
            self.assertEqual((directory / (name + ".json")).read_text(), content)

    def test_uninstall_unregisters_owned_definitions_and_preserves_instance_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            value, name = self.definition(base)
            directory = base / "definitions"
            target = json.loads((Path(value["root"]) / "installation.json").read_text())["target"]
            content = windows.unit_text(target, "web", [])
            services.register(directory, {name: content})
            unrelated = directory / "other-installation.json"
            unrelated.write_text("leave me")
            with patch.object(launcher.subprocess, "run") as run, patch.object(windows, "unit_directory", return_value=directory):
                launcher.stop_all(base, unregister=True)
                run.assert_called_once()
            self.assertTrue((Path(value["root"]) / "installation.json").is_file())
            self.assertFalse((directory / (name + ".json")).exists())
            self.assertEqual(unrelated.read_text(), "leave me")

    def test_recycled_pid_never_reports_active_or_gets_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            value, name = self.definition(base)
            directory = base / "definitions"
            services.register(directory, {name: json.dumps(value)})
            services.state_path(value).write_text(json.dumps(
                {"pid": 15, "created": 10, "child_pid": 16, "child_created": 11}))
            controller = services.WindowsServiceControl(directory)
            with patch.object(services, "process_stamp", return_value=999):
                self.assertEqual(controller.status(name)["pid"], 0)
                controller.stop(name)
            self.assertFalse(services.state_path(value).with_suffix(".stop").exists())

    def test_active_owned_child_and_supervisor_are_both_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            value, name = self.definition(base)
            directory = base / "definitions"
            services.register(directory, {name: json.dumps(value)})
            services.state_path(value).write_text(json.dumps(
                {"pid": 15, "created": 10, "child_pid": 16, "child_created": 11}))
            controller = services.WindowsServiceControl(directory)
            with patch.object(services, "process_stamp", side_effect=lambda pid: {15: 10, 16: 11}[pid]):
                self.assertEqual(controller.status(name)["active"], "active")
            with patch.object(services, "process_stamp", side_effect=lambda pid: {15: 10, 16: None}[pid]):
                self.assertEqual(controller.status(name)["active"], "inactive")
                self.assertEqual(controller.status(name)["pid"], 15)

    def test_public_seed_checksum_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "example-seed.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("../escape", "x")
            receipt = {"seed_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
            (root / "windows-package.json").write_text(json.dumps(receipt))
            with self.assertRaises(ValueError):
                launcher.unpack_seed(root)
            self.assertFalse((root / "instance/example_instance/escape").exists())
            archive.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "校验失败"):
                launcher.unpack_seed(root)

    def test_installer_requires_x64_payload_and_keeps_data_on_uninstall(self):
        script = (REPO / "packaging/windows/setup.nsi").read_text()
        self.assertIn("RunningX64", script)
        self.assertIn("RequestExecutionLevel user", script)
        self.assertNotIn('RMDir /r "$INSTDIR\\instance"', script)
        self.assertNotIn('RMDir /r "$INSTDIR"', script)

    def test_auto_freeze_verification_does_not_override_supervision_or_failed_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self.target(Path(tmp))
            state = {"target": target, "manifest_sha256": "abc", "completed": ["database"]}
            api = Mock()
            evidence = {"passed": False, "violations": {"pending": 1}, "run_id": "fixture",
                        "supervisor_resident_id": "", "manifest_sha256": "abc"}
            api.run_checked.return_value = json.dumps(evidence)
            with self.assertRaisesRegex(ValueError, "Freeze evidence"):
                initialization.apply(api, state, {"feature_resolution": {"states": {}}})
            api.service_controller.assert_not_called()
            target["supervisor_resident_id"] = "resident_123456789abc"
            api.reset_mock()
            with self.assertRaisesRegex(ValueError, "Supervised"):
                initialization.apply(api, state, {"feature_resolution": {"states": {}}})
            api.run_checked.assert_not_called()

    def test_windows_derived_bundle_does_not_mutate_seed_or_linux_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "instance/example_instance/seed"
            for folder in ("database", "data/accounts_root", "data/secrets_dir"):
                (seed / folder).mkdir(parents=True, exist_ok=True)
            (seed / "database/distribution.sql").write_text("-- fixture\n")
            (root / "code").mkdir()
            (root / "code/app.py").write_text("pass\n")
            (root / "INSTALL.md").write_text("fixture")
            spec = {"schema": "tfi.instance-seed.v1", "engineering": {
                "files": {"accounts_root": "data/accounts_root", "secrets_dir": "data/secrets_dir"},
                "selected_targets": ["linux"], "features": []}, "inventory": prepare_install.inventory(seed)}
            marker = seed / "manifest.json"
            marker.write_text(json.dumps(spec))
            original = marker.read_bytes()
            bundle = prepare_install.prepare(root, target_platforms=["windows"])
            built = json.loads((bundle / "manifest.json").read_text())
            self.assertEqual(built["selected_targets"], ["windows"])
            self.assertEqual(built["support_matrix"][0]["verification"], "not_run")
            self.assertEqual(marker.read_bytes(), original)
            self.assertFalse((root / ".build/example_instance").exists())


if __name__ == "__main__":
    unittest.main()
