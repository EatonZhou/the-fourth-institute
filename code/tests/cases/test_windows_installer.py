"""发行安装与实例隔离回归。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects"""
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
from contextlib import contextmanager
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
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
        """安装与实例回归：instances share programs but not state or identity。"""
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
        """安装与实例回归：invalid mode and instance traversal rejected。"""
        for value in ("../host", "C:\\host", "sample/one", "", "example-instance"):
            with self.assertRaises(ValueError):
                launcher.instance_name(value)
        with self.assertRaises(ValueError):
            windows.validate_service_mode({"windows_service_mode": "anything"})
        with self.assertRaises(ValueError):
            windows.dependency_preflight({"windows_service_mode": "scm"})

    def test_running_launcher_blocks_duplicate_window_and_uninstall_until_closed(self):
        """安装与实例回归：running launcher blocks duplicate window and uninstall until closed。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with launcher.launcher_lock(base):
                with self.assertRaisesRegex(RuntimeError, "已经打开"):
                    with launcher.launcher_lock(base):
                        self.fail("A second owner acquired the launcher lock")
            # The lock file can remain; only the OS lock determines ownership.
            with launcher.launcher_lock(base):
                pass

    def test_long_path_success_restarts_unelevated_after_releasing_launcher_lock(self):
        """安装与实例回归：long path success restarts unelevated after releasing launcher lock。"""
        events = []

        @contextmanager
        def lock(_):
            events.append("locked")
            yield
            events.append("released")

        def spawn(argv, **kwargs):
            self.assertEqual(events, ["locked", "released"])
            self.assertEqual(argv, [sys.executable, str(Path(launcher.__file__))])
            self.assertNotIn("--enable-long-paths", argv)

        with patch.object(launcher, "os", SimpleNamespace(name="nt")), \
                patch.object(sys, "argv", ["launcher"]), \
                patch.object(launcher, "launcher_lock", side_effect=lock), \
                patch.object(launcher, "launch_desktop", return_value=True), \
                patch.object(launcher.subprocess, "CREATE_NO_WINDOW", 0, create=True), \
                patch.object(launcher.subprocess, "Popen", side_effect=spawn) as process:
            launcher.main()
            process.assert_called_once()

    def test_long_path_setup_shows_failures_and_only_restarts_after_success(self):
        """安装与实例回归：long path setup shows failures and only restarts after success。"""
        for failure in (None, RuntimeError("authorization cancelled")):
            with self.subTest(failure=failure):
                root = Mock()
                root.after.side_effect = lambda delay, callback: setattr(root, "scheduled", callback)
                root.mainloop.side_effect = lambda: root.scheduled()
                messagebox = Mock()
                tk = SimpleNamespace(ttk=Mock(), messagebox=messagebox)
                with patch.dict(sys.modules, {"tkinter": tk}), \
                        patch.object(launcher, "enable_long_paths_elevated", side_effect=failure), \
                        patch.object(launcher.threading, "Thread",
                                     side_effect=lambda target, **kwargs: Mock(start=target)):
                    restart = launcher.wait_for_long_path_setup(root)
                self.assertEqual(restart, failure is None)
                root.deiconify.assert_called_once()
                root.destroy.assert_called_once()
                if failure:
                    messagebox.showerror.assert_called_once()
                else:
                    messagebox.showerror.assert_not_called()

    def test_child_environment_keeps_service_registry_and_instance_unicode_paths(self):
        """安装与实例回归：child environment keeps service registry and instance unicode paths。"""
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
        """安装与实例回归：failed native download can be stopped without deleting data。"""
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
        """安装与实例回归：first admin opens only invitation not full console text。"""
        text = ("Setup link for the first administrator:\n"
                "  http://127.0.0.1:28199/join#invite=test-token\n"
                "Open in browser. Next steps: http://127.0.0.1:28199/guide#admin")
        self.assertEqual(launcher.invitation_url(text), "http://127.0.0.1:28199/join#invite=test-token")
        with self.assertRaises(ValueError):
            launcher.invitation_url("no invitation")

    def test_user_service_registration_is_idempotent_and_owned(self):
        """安装与实例回归：user service registration is idempotent and owned。"""
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
        """安装与实例回归：uninstall unregisters owned definitions and preserves instance data。"""
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
        """安装与实例回归：recycled pid never reports active or gets stopped。"""
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

    def test_exited_process_with_exit_code_259_is_not_alive(self):
        # STILL_ACTIVE is also a legal application exit code. Only the process
        # handle's signalled state proves whether it has actually terminated.
        """安装与实例回归：exited process with exit code 259 is not alive。"""
        api = SimpleNamespace(**{name: Mock() for name in (
            "OpenProcess", "CloseHandle", "WaitForSingleObject",
            "GetProcessTimes", "GetExitCodeProcess")})
        api.OpenProcess.return_value = 123
        api.WaitForSingleObject.return_value = 0  # WAIT_OBJECT_0: exited

        def exit_code(handle, output):
            output._obj.value = 259
            return True

        api.GetExitCodeProcess.side_effect = exit_code
        with patch.object(services.ctypes, "WinDLL", return_value=api, create=True):
            self.assertIsNone(services.process_stamp(45))
        api.CloseHandle.assert_called_once_with(123)

    def test_active_owned_child_and_supervisor_are_both_required(self):
        """安装与实例回归：active owned child and supervisor are both required。"""
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
        """安装与实例回归：public seed checksum and traversal。"""
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

    def test_interrupted_seed_manifest_is_replaced_before_retry(self):
        """安装与实例回归：interrupted seed manifest is replaced before retry。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "example-seed.zip"
            manifest = '{"schema":"fixture","inventory":[]}'
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("manifest.json", manifest)
                zipped.writestr("data/share_root/example.txt", "complete")
            (root / "windows-package.json").write_text(json.dumps({
                "seed_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}))
            seed = root / "instance/example_instance/seed"
            seed.mkdir(parents=True)
            (seed / "manifest.json").write_text('{"schema":')
            launcher.unpack_seed(root)
            self.assertEqual((seed / "manifest.json").read_text(), manifest)
            self.assertEqual((seed / "data/share_root/example.txt").read_text(), "complete")
            # A completed extraction must not rewrite instance-owned materials.
            (seed / "data/share_root/example.txt").write_text("do not overwrite")
            launcher.unpack_seed(root)
            self.assertEqual((seed / "data/share_root/example.txt").read_text(), "do not overwrite")

    def test_installer_requires_x64_payload_and_keeps_data_on_uninstall(self):
        """安装与实例回归：installer requires x64 payload and keeps data on uninstall。"""
        script = (REPO / "code/scripts/windows/setup.nsi").read_text()
        self.assertIn("RunningX64", script)
        self.assertIn("RequestExecutionLevel user", script)
        self.assertNotIn('RMDir /r "$INSTDIR\\instance"', script)
        self.assertNotIn('RMDir /r "$INSTDIR"', script)

    def test_auto_freeze_verification_does_not_override_supervision_or_failed_evidence(self):
        """安装与实例回归：auto freeze verification does not override supervision or failed evidence。"""
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
        """安装与实例回归：windows derived bundle does not mutate seed or linux bundle。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "instance/example_instance/seed"
            files = {key: "data/" + key for key in installer.KEYS}
            for folder in ("database", *files.values()):
                (seed / folder).mkdir(parents=True, exist_ok=True)
            (seed / "database/distribution.sql").write_text("-- fixture\n")
            (root / "code/scripts").mkdir(parents=True)
            (root / "code/app.py").write_text("pass\n")
            (root / "code/scripts/tfi_release_install.py").write_text("# installer fixture\n")
            (root / "INSTALL.md").write_text("fixture")
            spec = {"schema": "tfi.instance-seed.v1", "engineering": {
                "files": files, "run_id": "fixture", "supervisor_resident_id": "",
                "database": {"path": "database/distribution.sql", "format": "postgresql-sql",
                             "sha256": prepare_install.digest(seed / "database/distribution.sql")},
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
            # Exercise the actual consumer, not just the producer's output shape.
            validated, _ = installer.validate_engineering(bundle / "manifest.json",
                                                          self.target(root))
            self.assertEqual(validated["installer"]["entrypoint"], "code/scripts/tfi_release_install.py")
            validated["installer"] = {"path": validated["installer"]["entrypoint"]}
            with self.assertRaisesRegex(ValueError, "installer.entrypoint"):
                installer.validate_engineering_layout(validated, bundle)


if __name__ == "__main__":
    unittest.main()
