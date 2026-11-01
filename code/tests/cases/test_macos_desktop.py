"""macOS 桌面初始化与包边界。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import json
import os
import shutil
from pathlib import Path
import tempfile
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import tfi_macos_launcher as launcher
from scripts import tfi_release_install as installer
from scripts.macos.publish import validate


class MacDesktop(unittest.TestCase):
    def test_launch_agents_belong_to_the_os_user_record(self):
        import pwd
        from scripts import tfi_release_platform_macos as adapter
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HOME": tmp}):
            expected = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library/LaunchAgents"
            self.assertEqual(adapter.unit_directory(), expected)
            self.assertEqual(adapter.service_controller().agent_root, expected)

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

    def test_retry_preserves_case_choice_and_uses_bundled_python_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            platform = Path(tmp)
            flow = launcher.MacStartup(platform, included=False)
            self.assertFalse(flow.included())
            # Even a pre-database retry cannot silently change the selection.
            self.assertFalse(launcher.MacStartup(platform, included=True).included())
            self.assertTrue(flow.offline())
            command = flow.command("start", "--instance", "desktop")
            self.assertEqual(command[0], str(platform / "dependencies/python/bin/python3"))
            self.assertEqual(Path(command[1]), platform / "code/scripts/tfi_release_install.py")
            instance_root = Path(tmp) / "elsewhere/desktop"
            self.assertEqual(launcher.MacStartup(platform, instance_root=instance_root).create_arguments(),
                             ["--root", instance_root])
            self.assertEqual(flow.create_arguments(), [])

    def test_macos_database_uses_portable_locale_with_utf8_encoding(self):
        from tests.cases.instance_test_support import write_instance
        from management_common.instance_manifest import load
        with tempfile.TemporaryDirectory() as tmp:
            instance = load(write_instance(Path(tmp) / "instance"))
            with patch.object(installer, "windows", return_value=False), \
                    patch.object(installer.sys, "platform", "darwin"), \
                    patch.object(installer, "postgres_environment", return_value={}), \
                    patch.object(installer, "run_checked") as run:
                installer.initialize_database(instance)
            self.assertIn("--locale=C", run.call_args.args[0])
            self.assertIn("--encoding=UTF8", run.call_args.args[0])

    def test_code_update_stops_instances_then_swaps_code_and_writes_receipt_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, platform = Path(tmp) / "app", Path(tmp) / "Library/TFI"
            receipt = {"schema": "tfi.macos-package.v1", "target": "macos/aarch64", "python": {"v": 1},
                       "bundled_native_dependencies": [{"name": "postgres"}], "source_dirty": False,
                       "prepared_engineering_validation": "passed", "source_commit": "new"}
            for root, commit, text in ((bundle, "new", "new code"), (platform, "old", "old code")):
                (root / "code").mkdir(parents=True)
                (root / "code/module.py").write_text(text)
                (root / "branding").mkdir()
                (root / "macos-package.json").write_text(json.dumps({**receipt, "source_commit": commit}))
            (platform / "dependencies/python/bin").mkdir(parents=True)
            (platform / "user-data").write_text("keep")
            order = []
            with patch.object(launcher, "stop_all", side_effect=lambda *a, **k: order.append(
                    (platform / "code/module.py").read_text())) as stop:
                self.assertTrue(launcher.changed(bundle, platform))
                launcher.replace_code(bundle, platform)
            stop.assert_called_once_with(platform, python=platform / "dependencies/python/bin/python3")
            self.assertEqual(order, ["old code"])
            self.assertEqual((platform / "code/module.py").read_text(), "new code")
            self.assertEqual((platform / "user-data").read_text(), "keep")
            self.assertFalse(launcher.changed(bundle, platform))
            self.assertFalse(list(platform.glob(".*.old")) or list(platform.glob(".*.new")))

    def test_code_update_refuses_a_different_bundled_runtime_before_stopping(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, platform = Path(tmp) / "app", Path(tmp) / "Library/TFI"
            for root, arch in ((bundle, "macos/x86_64"), (platform, "macos/aarch64")):
                root.mkdir(parents=True)
                (root / "macos-package.json").write_text(json.dumps({
                    "schema": "tfi.macos-package.v1", "target": arch, "python": {"v": 1},
                    "bundled_native_dependencies": [1], "source_dirty": False,
                    "prepared_engineering_validation": "passed"}))
            with patch.object(launcher, "stop_all") as stop, self.assertRaisesRegex(ValueError, "芯片"):
                launcher.replace_code(bundle, platform)
            stop.assert_not_called()

    def test_changed_package_requires_explicit_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, platform = Path(tmp) / "app", Path(tmp) / "Library/TFI"
            for root, commit in ((bundle, "new"), (platform, "old")):
                root.mkdir(parents=True)
                (root / "macos-package.json").write_text(json.dumps({"source_commit": commit}))
            flow = launcher.MacStartup(platform, bundle=bundle)
            with patch.object(launcher, "replace_code") as replace, self.assertRaisesRegex(ValueError, "--update"):
                flow.run()
            replace.assert_not_called()
            with patch.object(launcher, "replace_code") as replace, \
                    patch.object(launcher.DefaultStartup, "run", return_value="http://127.0.0.1:1/"):
                launcher.MacStartup(platform, bundle=bundle, update=True).run()
            replace.assert_called_once()

    def test_stop_and_unregister_use_the_platform_python_and_shared_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            platform = Path(tmp)
            with patch.object(launcher, "stop_all") as stop:
                launcher.stop(platform, unregister=True)
            stop.assert_called_once_with(platform, unregister=True,
                                         python=platform / "dependencies/python/bin/python3")

    def test_publish_requires_both_architectures_and_actual_digest(self):
        import hashlib
        from scripts.macos import publish as publisher
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
                    "offline_dependency_validation": "passed: fixture",
                    "initialization_requires_network": False, "native_preparation_requires_network": False,
                    "bundled_python_dependencies": {
                        "target": "cp312-macosx_15_0_" + ("arm64" if arch == "aarch64" else "x86_64"),
                        "path": "dependencies/python-wheels", "lock_sha256": "a" * 64,
                        "files": [{"path": "fixture.whl"}],
                    },
                    "signed": True, "signing_identity": "ad-hoc",
                    "app_signature_verification": "passed", "dmg_app_signature_verification": "passed",
                    "dmg_bytes": dmg.stat().st_size, "dmg_sha256": sha,
                }))
            assets = validate(directory, "commit")
            self.assertEqual(len(assets), 6)
            with patch.object(publisher.subprocess, "run") as run:
                publisher.publish(directory, "commit")
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(len(commands), 2)
            self.assertEqual(commands[0][:3], ["gh", "release", "create"])
            self.assertEqual(commands[0][-6:], assets)
            self.assertEqual(Path(commands[1][1]).name, "tfi_preview_retention.py")
            self.assertEqual(commands[1][2:], ["--platform", "macos"])
            self.assertTrue(all(call.kwargs["check"] for call in run.call_args_list))
            receipt = dmg.with_suffix(".json")
            record = json.loads(receipt.read_text())
            for field in ("app_signature_verification", "dmg_app_signature_verification", "signed", "signing_identity",
                          "offline_dependency_validation", "initialization_requires_network",
                          "native_preparation_requires_network", "bundled_python_dependencies"):
                invalid = dict(record)
                invalid.pop(field)
                receipt.write_text(json.dumps(invalid))
                with self.subTest(field=field), patch.object(publisher.subprocess, "run") as run, self.assertRaises(ValueError):
                    publisher.publish(directory, "commit")
                run.assert_not_called()
            invalid = {**record, "bundled_python_dependencies": {
                **record["bundled_python_dependencies"], "target": "cp312-win_amd64"}}
            receipt.write_text(json.dumps(invalid))
            with patch.object(publisher.subprocess, "run") as run, self.assertRaises(ValueError):
                publisher.publish(directory, "commit")
            run.assert_not_called()
            receipt.write_text(json.dumps(record))
            dmg.write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                validate(directory, "commit")
            with patch.object(publisher.subprocess, "run") as run, self.assertRaises(ValueError):
                publisher.publish(directory, "commit")
            run.assert_not_called()

    def test_failed_macos_upload_does_not_start_retention(self):
        import subprocess
        from scripts.macos import publish as publisher
        failure = subprocess.CalledProcessError(1, ["gh", "release", "create"])
        with patch.object(publisher, "validate", return_value=["arm64.dmg", "x64.dmg"]), \
                patch.object(publisher.subprocess, "run", side_effect=failure) as run, \
                self.assertRaises(subprocess.CalledProcessError):
            publisher.publish(Path("unused"), "commit")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:3], ["gh", "release", "create"])


    def test_interrupted_options_write_can_retry_without_partial_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            platform = Path(tmp)
            flow = launcher.MacStartup(platform, included=False)
            replace = os.replace

            def interrupted(source, target):
                if Path(target).name == "options.json":
                    raise OSError("interrupted before publication")
                return replace(source, target)
            with patch.object(os, "replace", interrupted), self.assertRaises(OSError):
                flow.included()
            self.assertFalse((platform / ".build/macos/options.json").exists())
            self.assertFalse(flow.included())
            self.assertEqual(json.loads((platform / ".build/macos/options.json").read_text()),
                             {"include_examples": False})

    def test_native_smoke_keeps_original_failure_if_retire_also_fails(self):
        from scripts.macos import build
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(build, "registered_instance", side_effect=[None, {"root": tmp}]), \
                patch.object(build, "command", side_effect=[ValueError("install failed"), RuntimeError("retire failed")]):
            with self.assertRaises(ExceptionGroup) as caught:
                build.smoke(Path("/fake/app"), Path(tmp), Path(tmp) / "instance", False)
        self.assertEqual([str(exc) for exc in caught.exception.exceptions], ["install failed", "retire failed"])

    def test_long_address_instance_root_exceeds_web_rpc_but_fits_postgres(self):
        from scripts.macos import build
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = build.long_rpc_root(Path(tmp).resolve())
        self.assertGreaterEqual(len(os.fsencode(root / build.WEB_SOCKET)), 104)
        self.assertLess(len(os.fsencode(root / build.POSTGRES_SOCKET)), 104)



@unittest.skipUnless(sys.platform == "darwin", "Requires native codesign")
class MacAppSignature(unittest.TestCase):
    def test_brand_icon_is_a_complete_icns(self):
        from scripts.macos import build
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "tfi.icns"
            build.build_icon(Path(launcher.__file__).parents[1] / "scripts/windows/branding/logo.png", target)
            data = target.read_bytes()
            self.assertEqual(data[:4], b"icns")
            self.assertEqual(int.from_bytes(data[4:8], "big"), len(data))
            decoded = Path(tmp) / "decoded.iconset"
            build.command(["iconutil", "-c", "iconset", "-o", decoded, target])
            self.assertTrue((decoded / "icon_16x16.png").is_file())
            self.assertTrue((decoded / "icon_512x512@2x.png").is_file())

    def test_complete_bundle_signature_rejects_changed_resource_and_metadata(self):
        import plistlib
        import subprocess
        from scripts.macos import build
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Fixture.app"
            contents = app / "Contents"
            executable = contents / "MacOS/fixture"
            executable.parent.mkdir(parents=True)
            source = Path(tmp) / "fixture.c"
            source.write_text("int main(void) { return 0; }\n")
            subprocess.run(["cc", str(source), "-o", str(executable)], check=True)
            info = contents / "Info.plist"
            info.write_bytes(plistlib.dumps({"CFBundleIdentifier": "org.thefourthinstitute.fixture",
                                             "CFBundleExecutable": "fixture", "CFBundlePackageType": "APPL"}))
            resource = contents / "Resources/fixture.txt"
            resource.parent.mkdir()
            resource.write_text("original")
            build.seal_app(app)
            for path in (resource, info):
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.assertRaises(RuntimeError):
                    build.verify_app(app)
                path.write_bytes(original)
                build.verify_app(app)


@unittest.skipUnless(sys.platform == "darwin", "Validated in the native macOS build")
class MacDesktopGUI(unittest.TestCase):
    def test_welcome_displays_existing_brand_artwork(self):
        import tkinter as tk
        root = tk.Tk()
        try:
            bundle = Path(launcher.__file__).parents[1] / "scripts/windows"
            label = launcher.welcome_logo(root, root, bundle)
            root.update()
            self.assertTrue(label.winfo_ismapped())
            self.assertLessEqual(label.image.width(), 128)
        finally:
            root.destroy()

    def test_copy_failure_stays_responsive_and_can_retry(self):
        import tkinter as tk
        from scripts import tfi_windows_launcher as desktop
        with tempfile.TemporaryDirectory() as tmp:
            bundle, destination = Path(tmp).resolve() / "app", Path(tmp).resolve() / "installed"
            bundle.mkdir()
            (bundle / "macos-package.json").write_text('{}')
            shutil.copytree(Path(launcher.__file__).parent / 'windows/branding', bundle / 'branding')
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
                    self.assertGreater(window.mark.width(), 0)
                    self.assertFalse(destination.exists(), 'Branding must load before first copy')
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
