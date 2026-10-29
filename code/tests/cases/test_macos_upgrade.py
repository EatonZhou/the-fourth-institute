"""macOS shared desktop update preserves local state and crash recovery.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import json
import os
from contextlib import nullcontext
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import tfi_macos_upgrade as upgrade
from scripts import tfi_macos_launcher as launcher
from scripts import tfi_release_install as installer


class MacUpgrade(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.platform, self.bundle = base / "用户 Library", base / "app"
        self.old = {"schema": "tfi.macos-package.v1", "target": "macos/aarch64",
                    "python": {"sha256": "fixed"}, "bundled_native_dependencies": [{"name": "postgres", "version": "16.15"}],
                    "source_commit": "a" * 40, "source_dirty": False,
                    "prepared_engineering_validation": "passed"}
        self.new = {**self.old, "source_commit": "b" * 40}
        for folder, receipt in ((self.platform, self.old), (self.bundle, self.new)):
            (folder / "code").mkdir(parents=True)
            (folder / "branding").mkdir()
            (folder / "code/requirements.txt").write_text("same requirements")
            (folder / "code/version.txt").write_text(receipt["source_commit"])
            installer.write_json(folder / upgrade.RECEIPT, receipt)
        root = self.platform / "instance/desktop"
        for name in ("data/database", "data/accounts", "data/share", "data/secrets", "config", "runtime"):
            (root / name).mkdir(parents=True)
        self.root = root
        self.target = {"install_root": str(root), "shared_code_root": str(self.platform / "code"),
                       "python": str(self.platform / "dependencies/python/bin/python3"),
                       "data_roots": {"share_root": str(root / "data/share"), "accounts_root": str(root / "data/accounts")}}
        self.state = {"target": self.target, "status": "installed", "manifest_sha256": "original-seed", "completed": ["database"],
                      "stages": [{"status": "installed"}]}
        installer.write_json(root / "installation.json", self.state)
        (root / "data/database/user.db").write_text("original database")
        (root / "data/accounts/auth.txt").write_text("fixture private account")
        (root / "data/accounts/auth.txt").chmod(0o600)
        (root / "data/share/my-project").write_text("my work")
        (root / "config/site.json").write_text('{"site_name":"my site"}')
        (self.platform / "dependencies/tools/codex").mkdir(parents=True)
        (self.platform / "dependencies/tools/codex/bin").write_text("user installed provider")
        installer.write_json(self.platform / ".build/macos/options.json", {"include_examples": False})
        (self.platform / ".build/m").mkdir()
        (self.platform / ".build/m/old-case-cache").write_text("do not copy")
        (self.bundle / "example-engineering.zip").write_text("do not import")
        self.flow = launcher.MacStartup(self.platform, Mock(), bundle=self.bundle, upgrade=True)
        self.api = SimpleNamespace(write_json=installer.write_json, owned_state=lambda r: upgrade.read(r / "installation.json"),
                                   service_controller=Mock(), unit_name=lambda t, c: c,
                                   wait_database=Mock(), lifecycle=Mock(), COMPONENTS=installer.COMPONENTS,
                                   unit_directory=lambda: self.platform.parent / "LaunchAgents",
                                   installation_lock=lambda r: nullcontext())
        self.api.service_controller.return_value.status.return_value = {"active": "inactive", "pid": 0}
        self.manager = upgrade.DesktopUpgrade(self.flow, api=self.api)
        self.quiet = patch("scripts.tfi_release_upgrade.check_quiescent")
        self.quiet.start()
        self.addCleanup(self.quiet.stop)
        self.providers = patch("scripts.tfi_release_provider_services.owned_services", return_value=None)
        self.providers.start()
        self.addCleanup(self.providers.stop)

    def test_success_preserves_data_accounts_tools_options_and_does_not_import_cases(self):
        self.assertEqual(self.manager.apply(lambda: "http://localhost"), "http://localhost")
        self.assertEqual(upgrade.read(self.platform / upgrade.RECEIPT), self.new)
        self.assertEqual((self.root / "data/database/user.db").read_text(), "original database")
        self.assertEqual((self.root / "data/share/my-project").read_text(), "my work")
        self.assertEqual((self.root / "data/accounts/auth.txt").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.platform / "dependencies/tools/codex/bin").read_text(), "user installed provider")
        self.assertFalse(upgrade.read(self.platform / ".build/macos/options.json")["include_examples"])
        self.assertFalse((self.platform / ".build/m").exists())
        self.assertFalse((self.platform / "example-engineering.zip").exists())
        self.assertEqual(upgrade.read(self.root / "installation.json"), self.state)
        journal = upgrade.read(self.manager.journal_path)
        _, backup, _ = self.manager.paths(journal)
        self.assertEqual(upgrade.read(backup / upgrade.RECEIPT), self.old)
        self.assertFalse(upgrade.pending(self.platform))

    def test_backup_container_is_private_before_stopping_services(self):
        # mkdir(parents=True, mode=0700) alone leaves intermediate parents
        # at the process umask (normally 0755).
        self.manager.control.mkdir(mode=0o755)
        self.manager.control.chmod(0o755)
        def check_storage(*args):
            self.assertEqual(self.manager.control.stat().st_mode & 0o777, 0o700)
            journal = upgrade.read(self.manager.journal_path)
            staging, _, _ = self.manager.paths(journal)
            self.assertEqual(staging.parent.stat().st_mode & 0o777, 0o700)
            return self.api.service_controller.return_value
        self.api.service_controller.side_effect = check_storage
        self.assertEqual(self.manager.apply(lambda: "ready"), "ready")

    def test_unverifiable_backup_permissions_leave_original_services_and_data_untouched(self):
        with patch.object(upgrade, "ensure_private_directory", side_effect=PermissionError("unsafe ACL")), \
                self.assertRaisesRegex(PermissionError, "unsafe ACL"):
            self.manager.apply(lambda: "ready")
        self.api.service_controller.assert_not_called()
        self.api.lifecycle.assert_not_called()
        self.assertFalse(self.manager.journal_path.exists())
        self.assertEqual((self.root / "data/accounts/auth.txt").read_text(), "fixture private account")

    def test_copy_restricts_files_before_payload_and_preserves_executable_modes(self):
        source = self.platform.parent / "copy-source"
        source.mkdir(mode=0o700)
        (source / "accounts").mkdir(mode=0o700)
        (source / "accounts/auth").write_bytes(b"fixture secret")
        (source / "accounts/auth").chmod(0o600)
        (source / "tool").write_bytes(b"executable fixture")
        (source / "tool").chmod(0o755)
        destination = self.platform.parent / "copy-destination"
        copied = []
        original = upgrade.copy_stream
        def inspect_before_write(src, dst, meter):
            self.assertEqual(os.fstat(dst.fileno()).st_mode & 0o777, 0o600)
            self.assertEqual(os.fstat(dst.fileno()).st_size, 0)
            copied.append(Path(src.name).name)
            return original(src, dst, meter)
        mask = os.umask(0o022)
        try:
            with patch.object(upgrade, "copy_stream", inspect_before_write):
                upgrade.copy_tree(source, destination, Mock())
        finally:
            os.umask(mask)
        self.assertCountEqual(copied, ["auth", "tool"])
        self.assertEqual((destination / "accounts/auth").stat().st_mode & 0o777, 0o600)
        self.assertEqual((destination / "tool").stat().st_mode & 0o777, 0o755)
        self.assertEqual((destination / "accounts/auth").read_bytes(), b"fixture secret")

    def test_start_failure_restores_paired_old_code_database_and_retains_new_writes(self):
        def fail():
            (self.root / "data/database/user.db").write_text("new migration and user write")
            raise RuntimeError("health refused")
        with self.assertRaisesRegex(RuntimeError, "已恢复原版"):
            self.manager.apply(fail)
        self.assertEqual(upgrade.read(self.platform / upgrade.RECEIPT), self.old)
        self.assertEqual((self.root / "data/database/user.db").read_text(), "original database")
        journal = upgrade.read(self.manager.journal_path)
        _, _, failed = self.manager.paths(journal)
        self.assertEqual((failed / "instance/desktop/data/database/user.db").read_text(), "new migration and user write")
        self.assertEqual(journal["phase"], "restored")

    def test_crash_between_directory_renames_recovers_without_data_loss(self):
        original = Path.rename
        def interrupted(path, target):
            if path.name == "staging":
                raise KeyboardInterrupt("power lost")
            return original(path, target)
        with patch.object(Path, "rename", interrupted), self.assertRaises(KeyboardInterrupt):
            self.manager.apply(lambda: "ready")
        self.assertFalse(self.platform.exists())
        self.assertTrue(upgrade.pending(self.platform))
        self.manager.recover()
        self.manager.recover()  # recovery is idempotent
        self.assertEqual(upgrade.read(self.platform / upgrade.RECEIPT), self.old)
        self.assertEqual((self.root / "data/share/my-project").read_text(), "my work")

    def test_live_service_refuses_rollback_and_keeps_both_generations(self):
        def fail():
            self.api.lifecycle.side_effect = RuntimeError("process still running")
            raise RuntimeError("health failed")
        with self.assertRaisesRegex(RuntimeError, "自动恢复也未完成"):
            self.manager.apply(fail)
        self.assertTrue(upgrade.pending(self.platform))
        journal = upgrade.read(self.manager.journal_path)
        _, backup, _ = self.manager.paths(journal)
        self.assertEqual(upgrade.read(backup / upgrade.RECEIPT), self.old)
        self.assertEqual(upgrade.read(self.platform / upgrade.RECEIPT), self.new)

    def test_copy_failure_keeps_original_and_can_retry(self):
        with patch.object(upgrade, "copy_tree", side_effect=OSError("disk full")), \
                self.assertRaisesRegex(RuntimeError, "已恢复原版"):
            self.manager.apply(lambda: "ready")
        self.assertEqual(upgrade.read(self.platform / upgrade.RECEIPT), self.old)
        self.assertEqual(self.manager.apply(lambda: "ready"), "ready")

    def test_newly_queued_work_after_first_check_prevents_copy_and_switch(self):
        with patch("scripts.tfi_release_upgrade.check_quiescent", side_effect=[None, RuntimeError("queued work")]), \
                patch.object(upgrade, "copy_tree") as copy, \
                self.assertRaisesRegex(RuntimeError, "queued work"):
            self.manager.apply(lambda: "ready")
        copy.assert_not_called()
        self.assertEqual(upgrade.read(self.platform / upgrade.RECEIPT), self.old)

    def test_incompatible_runtime_or_dependencies_refused_before_service_changes(self):
        for new in ({**self.new, "target": "macos/x64"}, {**self.new, "python": {"sha256": "different"}}):
            installer.write_json(self.bundle / upgrade.RECEIPT, new)
            with self.assertRaisesRegex(ValueError, "运行环境不同"):
                self.manager.apply(lambda: None)
        installer.write_json(self.bundle / upgrade.RECEIPT, self.new)
        (self.bundle / "code/requirements.txt").write_text("new dependency")
        with self.assertRaisesRegex(ValueError, "依赖已变更"):
            self.manager.apply(lambda: None)
        self.api.service_controller.assert_not_called()

    def test_offline_dependency_generation_change_requires_separate_directory(self):
        new = {**self.new, "bundled_python_dependencies": {"lock_sha256": "new generation"}}
        installer.write_json(self.bundle / upgrade.RECEIPT, new)
        with self.assertRaisesRegex(ValueError, "Python 依赖版本不同"):
            self.manager.apply(lambda: None)
        self.api.service_controller.assert_not_called()
        self.assertEqual(upgrade.read(self.platform / upgrade.RECEIPT), self.old)

    def test_foreign_journal_and_redirected_data_are_refused(self):
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.manager.paths({"platform": "/other", "id": "upgrade-" + "a" * 32})
        external = self.platform.parent / "external-accounts"
        (self.root / "data/accounts").rename(external)
        (self.root / "data/accounts").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "外部数据目录"):
            self.manager.apply(lambda: None)
        self.api.service_controller.assert_not_called()

    def test_launcher_dispatches_upgrade_only_when_requested_and_preserves_same_version(self):
        with patch.object(upgrade, "DesktopUpgrade") as manager, \
                patch.object(launcher.DefaultStartup, "run", return_value="ready"):
            self.flow.upgrade = False
            with self.assertRaisesRegex(ValueError, "--upgrade"):
                self.flow.run()
            manager.assert_not_called()
            self.flow.upgrade = True
            self.flow.run()
            manager.return_value.apply.assert_called_once()
            self.assertEqual(manager.return_value.apply.call_args.args[0](), "ready")
            manager.reset_mock()
            installer.write_json(self.platform / upgrade.RECEIPT, self.new)
            self.assertEqual(self.flow.run(), "ready")
            manager.assert_not_called()


class MacProviderOwnership(unittest.TestCase):
    def test_production_environment_is_owned_but_foreign_paths_and_overrides_are_rejected(self):
        import hashlib
        import plistlib
        from scripts import tfi_release_provider_services as services
        from scripts import tfi_release_platform_macos as adapter
        from scripts.portal_service_control import ServiceControl
        with tempfile.TemporaryDirectory() as tmp:
            platform = Path(tmp).resolve()
            root = platform / "instance/desktop"
            target = {"install_root": str(root), "shared_code_root": str(platform / "code"), "service_prefix": "tfi-desktop",
                      "python": str(platform / "dependencies/python/bin/python3"),
                      "data_roots": {"instances_root": str(root / "data/instances"), "accounts_root": str(root / "data/accounts")}}
            installer.write_json(root / "config/site.json", {})
            environment = {"PORTAL_INSTANCES_ROOT": str(root / "data/instances"),
                           "PORTAL_ACCOUNTS_ROOT": str(root / "data/accounts"), "PORTAL_SECRETS_DIR": str(root / "data/secrets"),
                           "PORTAL_NETWORK_CONFIG": str(root / "config/network.env"), "PORTAL_SITE_CONFIG": str(root / "config/site.json"),
                           "PORTAL_PATHS_CONFIG": str(root / "config/paths.json"), "PATH": "/native/bin:/usr/bin:/bin",
                           "HOME": str(root / "home"), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
            scope = hashlib.sha256(os.fsencode(root / "data/instances")).hexdigest()[:10]
            name = f"portal-codex-app-server@one-{scope}.service"
            directory = platform / "LaunchAgents"
            directory.mkdir()
            command = [target["python"], "-m", "management_portal.codex_app_server_host", "--socket",
                       str(root / "data/instances/sockets/codex-one.sock"), "--binary", "/local/codex",
                       "--account-home", str(root / "data/accounts/codex/one")]
            def write(env):
                content = adapter.command_unit_text(name, command, cwd=platform / "code",
                    log_root=root / "data/instances/logs", log_name=name, environment=env)
                (directory / (ServiceControl.label(name) + ".plist")).write_text(content)
            # launchd reports the job as not loaded, as on a stopped Mac.
            launchd = Mock()
            launchd.run.return_value = SimpleNamespace(returncode=113, stdout="")
            with patch.object(installer, "clean_environment", return_value=environment), \
                    patch.object(adapter, "service_controller", return_value=launchd):
                for env in (None, environment):
                    write(env)
                    self.assertEqual(services._macos_owned_services(target, directory)["names"], [name])
                for env in ({**environment, "HOME": "/other/site"}, {**environment, "DYLD_INSERT_LIBRARIES": "/other/lib"}):
                    write(env)
                    with self.assertRaisesRegex(ValueError, "environment"):
                        services._macos_owned_services(target, directory)
