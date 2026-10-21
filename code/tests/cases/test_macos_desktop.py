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
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
