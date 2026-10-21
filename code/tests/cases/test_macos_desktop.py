"""macOS 桌面初始化与包边界。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import json
import os
from pathlib import Path
import tempfile
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from scripts import tfi_macos_launcher as launcher
from scripts import tfi_release_install as installer
from scripts.macos.publish import validate


class MacDesktop(unittest.TestCase):
    def test_launch_agents_stay_with_os_user_when_application_home_is_isolated(self):
        from scripts import tfi_release_platform_macos as adapter
        from scripts.portal_service_control import ServiceControl
        with tempfile.TemporaryDirectory() as tmp:
            platform = Path(tmp).resolve()
            registry = platform / "operator/Library/LaunchAgents"
            registry.mkdir(parents=True)
            target = launcher.make_target(platform, "desktop", {"run_id": "test"})
            target.update(pg_bin=str(platform / "pg/bin"))
            with patch.dict(os.environ, {"HOME": str(platform / "instance/home"),
                                        "TFI_MACOS_SERVICE_DIRECTORY": str(registry)}), \
                    patch.object(installer, "platform_adapter", return_value=adapter):
                self.assertEqual(adapter.unit_directory(), registry)
                self.assertEqual(adapter.service_controller().agent_root, registry)
                self.assertEqual(ServiceControl(platform="macos").agent_root, registry)
                env = installer.clean_environment(target)
                self.assertEqual(env["TFI_MACOS_SERVICE_DIRECTORY"], str(registry))
                self.assertEqual(env["HOME"], str(Path(target["install_root"]) / "home"))

    def test_payload_copy_is_relocatable_and_refuses_other_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, installed = Path(tmp) / "app", Path(tmp) / "用户 Library"
            bundle.mkdir()
            (bundle / "macos-package.json").write_text('{"source_commit":"one"}')
            (bundle / "python3.12").write_text("runtime")
            (bundle / "python3").symlink_to("python3.12")
            launcher.materialize(bundle, installed)
            self.assertEqual((installed / "python3").read_text(), "runtime")
            (installed / "user-data").write_text("keep")
            launcher.materialize(bundle, installed)
            (bundle / "macos-package.json").write_text('{"source_commit":"two"}')
            with self.assertRaisesRegex(ValueError, "其他版本"):
                launcher.materialize(bundle, installed)
            self.assertEqual((installed / "user-data").read_text(), "keep")

    def test_payload_rejects_external_links_and_preserves_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, installed = Path(tmp) / "app", Path(tmp) / "installed"
            bundle.mkdir()
            (bundle / "macos-package.json").write_text('{}')
            (bundle / "escape").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(ValueError, "越界"):
                launcher.materialize(bundle, installed)
            self.assertFalse(installed.exists())

    def test_retry_preserves_case_choice_and_uses_bundled_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            platform = Path(tmp)
            manifest = platform / ".build/example_instance-macos/engineering/manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"run_id":"fixture"}')
            flow = launcher.MacStartup(platform, included=False)
            with patch.object(launcher, "unpack_seed") as unpack, patch.object(flow, "execute") as execute:
                flow.initialize()
                self.assertFalse(unpack.call_args.kwargs["included"])
                self.assertIn("--without-examples", execute.call_args_list[0].args[0])
                target = json.loads((platform / ".build/macos/target.json").read_text())
                self.assertEqual(target["python"], str(platform / "dependencies/python/bin/python3"))
                self.assertNotIn("windows_service_mode", target)
                self.assertTrue(target["offline_dependencies"])
                # Even a pre-database retry cannot silently change the selection.
                flow.included = True
                flow.initialize()
                self.assertFalse(unpack.call_args.kwargs["included"])
                flow.root.mkdir(parents=True)
                (flow.root / "installation.json").write_text('{}')
                unpack.reset_mock()
                flow.initialize()
                unpack.assert_not_called()

    def test_macos_database_uses_portable_locale_with_utf8_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = {"install_root": tmp, "pg_bin": "/native/bin", "pg_share": "/native/share",
                      "service_prefix": "tfi-fixture", "ports": {"postgres": 54444}}
            from scripts import tfi_release_platform_macos as adapter
            with patch.object(installer, "platform_adapter", return_value=adapter), \
                    patch.object(installer, "run_checked") as run, \
                    patch.object(installer, "service_controller"), patch.object(installer, "wait_database"):
                installer.initialize_database({"target": target})
            self.assertIn("--locale=C", run.call_args.args[0])
            self.assertIn("--encoding=UTF8", run.call_args.args[0])

    def test_publish_requires_both_architectures_and_actual_digest(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for suffix, arch in (("arm64", "aarch64"), ("x64", "x86_64")):
                dmg = directory / f"TheFourthInstitute-macOS-{suffix}.dmg"
                dmg.write_bytes(b"test dmg")
                sha = hashlib.sha256(dmg.read_bytes()).hexdigest()
                dmg.with_name(dmg.name + ".sha256").write_text(sha + "  " + dmg.name + "\n")
                dmg.with_suffix(".json").write_text(json.dumps({
                    "source_commit": "commit", "source_dirty": False, "target": "macos/" + arch,
                    "prepared_engineering_validation": "passed", "native_installation_smoke": "passed: fixture",
                    "dmg_bytes": dmg.stat().st_size, "dmg_sha256": sha,
                }))
            self.assertEqual(len(validate(directory, "commit")), 6)
            dmg.write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                validate(directory, "commit")


    def test_interrupted_settings_write_can_retry_without_partial_json(self):
        for name in ("options.json", "target.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                platform = Path(tmp)
                manifest = platform / ".build/example_instance-macos/engineering/manifest.json"
                manifest.parent.mkdir(parents=True)
                manifest.write_text('{"run_id":"fixture"}')
                flow = launcher.MacStartup(platform, included=False)
                replace = Path.replace
                def interrupted(path, target):
                    if Path(target).name == name:
                        raise OSError("interrupted before publication")
                    return replace(path, target)
                with patch.object(launcher, "unpack_seed") as unpack, patch.object(flow, "execute"):
                    with patch.object(Path, "replace", interrupted), self.assertRaises(OSError):
                        flow.initialize()
                    self.assertFalse((platform / ".build/macos" / name).exists())
                    flow.initialize()
                    self.assertFalse(unpack.call_args.kwargs["included"])
                    target = json.loads((platform / ".build/macos/target.json").read_text())
                    self.assertTrue(target["offline_dependencies"])

    def test_native_smoke_keeps_original_failure_if_cleanup_also_fails(self):
        from scripts.macos import build
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(build, "command", side_effect=[ValueError("install failed"), RuntimeError("cleanup failed")]):
            with self.assertRaises(ExceptionGroup) as caught:
                build.smoke(Path("/fake/app"), Path(tmp), False)
        self.assertEqual([str(exc) for exc in caught.exception.exceptions], ["install failed", "cleanup failed"])

    def test_stop_and_unregister_include_only_owned_macos_accounts(self):
        import hashlib
        from scripts import tfi_release_provider_services as providers
        from scripts import tfi_release_platform_macos as adapter
        from scripts.tfi_instance_layout import code_root
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            registry = base / "LaunchAgents"
            def account_job(platform):
                target = launcher.make_target(platform, "desktop", {"run_id": "fixture"})
                root = Path(target["data_roots"]["instances_root"]).resolve()
                scope = hashlib.sha256(os.fsencode(root)).hexdigest()[:10]
                name = f"portal-codex-app-server@fixture-{scope}.service"
                home = Path(target["data_roots"]["accounts_root"]) / "codex/fixture"
                home.mkdir(parents=True)
                marker = home / "user-data"
                marker.write_text("preserve")
                argv = [target["python"], "-m", "management_portal.codex_app_server_host",
                        "--socket", str(root / "sockets/codex-fixture.sock"),
                        "--binary", "/fixture/codex", "--account-home", str(home)]
                content = adapter.command_unit_text(name, argv, cwd=code_root(target), log_root=root / "logs", log_name=name)
                adapter.register_services(registry, {name: content})
                return target, name, marker
            target, name, marker = account_job(base / "ours")
            _, foreign, _ = account_job(base / "other")
            controller = Mock()
            controller.status.return_value = {"pid": 0, "active": "inactive"}
            with patch.object(providers.sys, "platform", "darwin"), patch.object(providers, "_run") as systemctl:
                plan = providers.owned_services(target, registry)
                self.assertEqual(plan["names"], [name])
                providers.stop_services(plan, controller)
                controller.stop.assert_called_once_with(name)
                providers.remove_template(target, registry)
                systemctl.assert_not_called()
            self.assertFalse(adapter.unit_path(registry, name).exists())
            self.assertTrue(adapter.unit_path(registry, foreign).is_file())
            self.assertEqual(marker.read_text(), "preserve")

    def test_macos_account_cleanup_rejects_foreign_definition(self):
        import hashlib
        from scripts import tfi_release_provider_services as providers
        from scripts import tfi_release_platform_macos as adapter
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            target = launcher.make_target(base, "desktop", {"run_id": "fixture"})
            root = Path(target["data_roots"]["instances_root"]).resolve()
            scope = hashlib.sha256(os.fsencode(root)).hexdigest()[:10]
            name = f"portal-codex-app-server@fixture-{scope}.service"
            registry = base / "LaunchAgents"
            content = adapter.command_unit_text(name, ["/other/python", "-m", "management_portal.codex_app_server_host",
                "--socket", "/other/s.sock", "--binary", "/fixture/codex", "--account-home", "/other/account"],
                cwd="/other/code", log_root="/other/logs", log_name=name)
            adapter.register_services(registry, {name: content})
            with patch.object(providers.sys, "platform", "darwin"), self.assertRaisesRegex(ValueError, "another instance"):
                providers.remove_template(target, registry)
            self.assertEqual(adapter.unit_path(registry, name).read_text(), content)


@unittest.skipUnless(sys.platform == "darwin", "Validated in the native macOS build")
class MacDesktopGUI(unittest.TestCase):
    def test_copy_failure_stays_responsive_and_can_retry(self):
        import tkinter as tk
        from scripts import tfi_windows_launcher as desktop
        with tempfile.TemporaryDirectory() as tmp:
            bundle, destination = Path(tmp).resolve() / "app", Path(tmp).resolve() / "installed"
            bundle.mkdir()
            (bundle / "macos-package.json").write_text('{}')
            flow = launcher.MacStartup(destination, included=False, bundle=bundle)
            copy_started, allow_copy = threading.Event(), threading.Event()
            attempts, ticks, completed = [], [], []
            original_copy = launcher.materialize
            def copy(source, target, **kwargs):
                attempts.append(threading.get_ident())
                copy_started.set()
                if not allow_copy.wait(5):
                    raise TimeoutError("GUI event loop did not run while copying")
                if len(attempts) == 1:
                    raise OSError("copy interrupted")
                original_copy(source, target, **kwargs)
            root = tk.Tk()
            root.withdraw()
            def heartbeat():
                if copy_started.is_set():
                    ticks.append(True)
                    if len(ticks) >= 3:
                        allow_copy.set()
                root.after(20, heartbeat)
            def pump_until(predicate):
                deadline = time.monotonic() + 8
                while not predicate() and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                self.assertTrue(predicate(), "GUI operation did not finish")
            try:
                with patch.object(launcher, "materialize", side_effect=copy), \
                        patch.object(desktop.DefaultStartup, "run", return_value="http://127.0.0.1:12345/") as run, \
                        patch.object(desktop.webbrowser, "open", side_effect=lambda url: completed.append(url) or True):
                    window = launcher.Launcher(root, flow=flow)
                    root.after(20, heartbeat)
                    pump_until(lambda: bool(attempts) and not window.busy)
                    self.assertGreaterEqual(len(ticks), 3)
                    self.assertNotEqual(attempts[0], threading.get_ident())
                    self.assertIn("copy interrupted", window.status.get())
                    self.assertFalse(destination.exists(), "Failure log must not poison the copy destination")
                    run.assert_not_called()
                    window.begin()
                    pump_until(lambda: bool(completed))
                    run.assert_called_once()
                    self.assertTrue((destination / "macos-package.json").is_file())
            finally:
                allow_copy.set()
                try:
                    root.destroy()
                except tk.TclError:
                    pass


if __name__ == "__main__":
    unittest.main()
