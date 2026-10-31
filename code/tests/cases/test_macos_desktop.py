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
                home = Path(target["install_root"]) / "runtime/provider-accounts/codex/fixture"
                home.mkdir(parents=True)
                marker = home / "user-data"
                marker.write_text("preserve")
                argv = [target["python"], "-m", "management_portal.codex_app_server_host",
                        "--socket", str(root / "sockets/codex-fixture.sock"),
                        "--binary", "/fixture/codex", "--account-config", str(home / "launch.json")]
                content = adapter.command_unit_text(name, argv, cwd=code_root(target), log_root=root / "logs", log_name=name)
                adapter.register_services(registry, {name: content})
                return target, name, marker
            target, name, marker = account_job(base / "ours")
            _, foreign, _ = account_job(base / "other")
            controller = Mock()
            controller.status.return_value = {"pid": 0, "active": "inactive"}
            # launchd has our job loaded from our own definition path.
            controller.run.return_value = SimpleNamespace(
                returncode=0, stdout=f"\tpath = {adapter.unit_path(registry, name)}\n")
            with patch.object(providers.sys, "platform", "darwin"), patch.object(providers, "_run") as systemctl, \
                    patch.object(adapter, "service_controller", return_value=controller):
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
                "--socket", "/other/s.sock", "--binary", "/fixture/codex", "--account-config", "/other/launch.json"],
                cwd="/other/code", log_root="/other/logs", log_name=name)
            adapter.register_services(registry, {name: content})
            with patch.object(providers.sys, "platform", "darwin"), self.assertRaisesRegex(ValueError, "another instance"):
                providers.remove_template(target, registry)
            self.assertEqual(adapter.unit_path(registry, name).read_text(), content)


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


@unittest.skipUnless(sys.platform == "darwin", "Requires native launchd and Codex")
class MacCodexNative(unittest.TestCase):
    def test_launchd_starts_codex_with_short_and_long_socket_paths(self):
        from contextlib import ExitStack
        from management_common import site_config
        from management_portal.execution import account_hosting
        from management_portal import codex_app_server_host as host
        from management_portal.codex_app_server_client import CodexAppServerClient
        from scripts import portal_service_units as units, portal_instance_storage as storage
        from scripts import tfi_release_platform_macos as adapter
        binary = os.environ.get("TFI_TEST_CODEX_BINARY")
        self.assertTrue(binary and Path(binary).is_file(), "Set TFI_TEST_CODEX_BINARY to the validation-only Codex CLI")
        with tempfile.TemporaryDirectory(prefix="tfi-codex-check-", dir="/tmp") as tmp:
            base = Path(tmp).resolve()
            registry = base / "LaunchAgents"
            clean = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(base / "home"),
                     "TFI_MACOS_SERVICE_DIRECTORY": str(registry), "LANG": "en_US.UTF-8"}
            site_env = {"PORTAL_NETWORK_CONFIG": str(base / "network.env"),
                        "PYTHONPATH": str(Path(launcher.__file__).parents[1])}
            for variant in ("short", "实例" * 40):
                with self.subTest(variant="short" if variant == "short" else "long"), ExitStack() as stack:
                    logical = base / variant / "codex.sock"
                    physical = host.unix_socket_path(logical)
                    name = base.name + ("-short" if variant == "short" else "-long") + ".service"
                    stack.enter_context(patch.dict(os.environ, clean, clear=True))
                    stack.enter_context(patch.object(units, "instance_root", return_value=base))
                    stack.enter_context(patch.object(units, "_codex_unit", return_value=name))
                    stack.enter_context(patch.object(site_config, "read_site_config", return_value={
                        "deployment": {"providers": {"codex_binary": binary}}}))
                    stack.enter_context(patch.object(storage, "site_environment", return_value=site_env))
                    stack.enter_context(patch.object(account_hosting, "account_home", return_value=base / "empty-account"))
                    stack.enter_context(patch.object(account_hosting, "codex_socket_path", return_value=logical))
                    _, directory, job, content, controller = units._native_codex_service("fixture")
                    adapter.register_services(directory, {job: content})
                    client = CodexAppServerClient(logical, request_timeout_seconds=10)
                    try:
                        controller.start(job)
                        deadline = time.monotonic() + 30
                        while not physical.exists() and time.monotonic() < deadline:
                            time.sleep(.05)
                        if not physical.exists():
                            logs = "\n".join(p.read_text(errors="replace")[-3000:] for p in (base / "logs").glob("*"))
                            self.fail("launchd did not publish the Codex endpoint: " + logs)
                        self.assertTrue(client.request("model/list", {})["data"])
                        self.assertIsNone(client.request("account/read", {}).get("account"))
                    finally:
                        client.close()
                        controller.stop(job)
                        adapter.unit_path(directory, job).unlink(missing_ok=True)
                    deadline = time.monotonic() + 10
                    while physical.exists() and time.monotonic() < deadline:
                        time.sleep(.05)
                    self.assertFalse(physical.exists(), "Stopped host left its socket behind")
                    if physical != logical:
                        physical.parent.rmdir()


if __name__ == "__main__":
    unittest.main()
