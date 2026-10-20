from pathlib import Path
import json
import os
import subprocess
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import tfi_release_install as installer
from scripts import tfi_release_upgrade as upgrade


class UpgradeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tfi-upgrade-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "instance"
        for name in ("code/scripts", "runtime/pg", "runtime/s", "config", "data/secrets", "data/workspace"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / "code/version.txt").write_text("old-code")
        (self.root / "runtime/pg/data").write_text("old-database")
        (self.root / "runtime/pg/PG_VERSION").write_text("16")
        (self.root / "config/site.json").write_text('{"local":true}')
        (self.root / "data/secrets/local-credential").write_text("local-only-test-value")
        (self.root / "data/secrets/local-credential").chmod(0o600)
        (self.root / "data/workspace/content").write_text("local-work")
        self.target = {"install_root": str(self.root), "service_prefix": "tfi-upgrade-test"}
        self.state = {"target": self.target, "manifest_sha256": "old-manifest", "status": "installed"}
        installer.write_json(self.root / installer.STATE_FILE, self.state)
        self.controller = Mock()
        self.controller.status.return_value = {"active": "inactive", "pid": 0}
        self.api = SimpleNamespace(
            STATE_FILE=installer.STATE_FILE, read_json=installer.read_json, write_json=installer.write_json,
            installation_lock=installer.installation_lock, digest=installer.digest,
            owned_state=lambda root: installer.read_json(Path(root) / installer.STATE_FILE),
            COMPONENTS=installer.COMPONENTS, check_available=Mock(), service_controller=lambda: self.controller,
            unit_name=lambda target, component: target["service_prefix"] + "-" + component + ".service",
            observed_status=Mock(return_value={"status": "installed"}), wait_database=Mock(),
            engineering_path=installer.engineering_path, prepare_dependencies=Mock())
        def save_stage(state, phase, status, **details):
            state["status"] = status
            installer.write_json(self.root / installer.STATE_FILE, state)
        self.api.save_stage = save_stage

    def journal(self):
        journal = {"id": "upgrade-" + "a" * 16 + "-1", "target": self.target,
                   "from_manifest_sha256": "old-manifest", "phase": "backup_ready"}
        parent = self.root / "maintenance" / journal["id"]
        parent.mkdir(parents=True)
        upgrade.snapshot(self.api, self.root, parent / "backup")
        return journal

    def test_restore_pairs_code_database_and_private_local_state(self):
        journal = self.journal()
        (self.root / "code/version.txt").write_text("new-code")
        (self.root / "runtime/pg/data").write_text("new-database")
        (self.root / "data/workspace/content").write_text("new-local-work")
        result = upgrade.restore(self.api, self.root, journal, retain_failed=True)
        self.assertEqual(result["status"], "restored_stopped")
        self.assertEqual((self.root / "code/version.txt").read_text(), "old-code")
        self.assertEqual((self.root / "runtime/pg/data").read_text(), "old-database")
        self.assertEqual((self.root / "data/workspace/content").read_text(), "local-work")
        self.assertEqual((self.root / "data/secrets/local-credential").stat().st_mode & 0o777, 0o600)
        failed = Path(result["failed_generation_backup"])
        self.assertEqual((failed / "runtime/pg/data").read_text(), "new-database")
        self.assertEqual((failed / "data/workspace/content").read_text(), "new-local-work")
        self.assertEqual(self.controller.stop.call_count, len(installer.COMPONENTS))

    def test_corrupt_backup_refuses_restore_without_stopping_or_mutating(self):
        journal = self.journal()
        parent = self.root / "maintenance" / journal["id"]
        (parent / "backup/code/version.txt").write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "integrity"):
            upgrade.restore(self.api, self.root, journal, retain_failed=True)
        self.controller.stop.assert_not_called()
        self.assertEqual((self.root / "code/version.txt").read_text(), "old-code")

    def test_foreign_journal_cannot_control_other_services(self):
        journal = self.journal()
        journal["target"] = {**self.target, "service_prefix": "source-dev"}
        installer.write_json(self.root / upgrade.JOURNAL, journal)
        with self.assertRaisesRegex(ValueError, "target"):
            upgrade.run(self.api, "recover", self.root)
        self.controller.stop.assert_not_called()

    def test_live_owned_process_prevents_snapshot_restore(self):
        journal = self.journal()
        self.controller.status.return_value = {"active": "active", "pid": 999}
        with self.assertRaisesRegex(RuntimeError, "stopped"):
            upgrade.restore(self.api, self.root, journal, retain_failed=True)
        self.assertEqual((self.root / "runtime/pg/data").read_text(), "old-database")

    def test_migration_failure_restores_full_site_and_preserves_failed_generation(self):
        manifest = Path(self.temp.name) / "manifest.json"
        manifest.write_text('{}')
        def fail_migration(*args):
            (self.root / "runtime/pg/data").write_text("partly-migrated")
            raise RuntimeError("migration rejected")
        with patch.object(upgrade, "preflight", return_value=({}, manifest.parent, {}, [])), \
             patch.object(upgrade, "check_quiescent"), \
             patch.object(upgrade, "migrate", side_effect=fail_migration), \
             self.assertRaisesRegex(RuntimeError, "migration/switch"):
            upgrade.run(self.api, "upgrade", self.root, manifest)
        self.assertEqual((self.root / "runtime/pg/data").read_text(), "old-database")
        self.assertEqual((self.root / "code/version.txt").read_text(), "old-code")
        journal = installer.read_json(self.root / upgrade.JOURNAL)
        self.assertEqual(journal["phase"], "restored_stopped")
        self.assertEqual((Path(journal["failed_generation_backup"]) / "runtime/pg/data").read_text(), "partly-migrated")

    def test_upgrade_retry_does_not_repeat_migration_or_import_release_data(self):
        local_site = '{"local":true,"site_name":"My existing portal"}'
        (self.root / "config/site.json").write_text(local_site)
        bundle = Path(self.temp.name) / "engineering"
        (bundle / "code/scripts").mkdir(parents=True)
        (bundle / "code/scripts/tfi_release_install.py").write_text("# new runtime")
        (bundle / "code/version.txt").write_text("new-code")
        manifest = {"code": {"path": "code"}, "run_id": "new-version-run"}
        installer.write_json(bundle / "manifest.json", manifest)
        with patch.object(upgrade, "preflight", return_value=(manifest, bundle, {}, [])), \
             patch.object(upgrade, "check_quiescent"), patch.object(upgrade, "migrate") as migrate, \
             patch.object(upgrade, "start_and_check", return_value={"status": "installed"}):
            result = upgrade.run(self.api, "upgrade", self.root, bundle / "manifest.json")
            self.assertEqual(result["upgrade"], "applied")
            repeated = upgrade.run(self.api, "upgrade", self.root, bundle / "manifest.json")
            self.assertEqual(repeated["upgrade"], "already_applied")
            migrate.assert_called_once()
        self.assertEqual((self.root / "config/site.json").read_text(), local_site)
        self.assertEqual((self.root / "data/workspace/content").read_text(), "local-work")
        self.assertEqual((self.root / "data/secrets/local-credential").read_text(), "local-only-test-value")

    def test_dependency_failure_restores_pair_without_starting_new_services(self):
        bundle = Path(self.temp.name) / "engineering"
        (bundle / "code/scripts").mkdir(parents=True)
        (bundle / "code/scripts/tfi_release_install.py").write_text("# new runtime")
        (bundle / "code/version.txt").write_text("new-code")
        manifest = {"code": {"path": "code"}, "run_id": "new-version-run"}
        installer.write_json(bundle / "manifest.json", manifest)
        self.api.prepare_dependencies.side_effect = RuntimeError("dependency verification failed")
        with patch.object(upgrade, "preflight", return_value=(manifest, bundle, {}, [])), \
             patch.object(upgrade, "check_quiescent"), patch.object(upgrade, "migrate"), \
             patch.object(upgrade, "start_and_check") as start, \
             self.assertRaisesRegex(RuntimeError, "migration/switch"):
            upgrade.run(self.api, "upgrade", self.root, bundle / "manifest.json")
        start.assert_not_called()
        self.api.prepare_dependencies.assert_called_once()
        self.assertEqual((self.root / "code/version.txt").read_text(), "old-code")
        self.assertEqual((self.root / "runtime/pg/data").read_text(), "old-database")
        self.assertEqual((self.root / "data/secrets/local-credential").read_text(), "local-only-test-value")

    def test_default_initialization_failure_restores_local_settings_with_database(self):
        bundle = Path(self.temp.name) / "engineering"
        (bundle / "code/scripts").mkdir(parents=True)
        (bundle / "code/scripts/tfi_release_install.py").write_text("# new runtime")
        (bundle / "code/version.txt").write_text("new-code")
        manifest = {"code": {"path": "code"}, "run_id": "new-version-run", "feature_resolution": {"valid": True}}
        installer.write_json(bundle / "manifest.json", manifest)
        installer.write_json(self.root / "runtime/engineering-manifest.json",
                             {"feature_resolution": {"included_features": ["existing-local-feature"]}})
        def fail_initialization(state, new_manifest, **options):
            self.assertEqual(options, {"mode": "upgrade", "existing_feature_ids": ["existing-local-feature"]})
            self.assertEqual((self.root / "code/version.txt").read_text(), "new-code")
            (self.root / "config/site.json").write_text('{"new":true}')
            (self.root / "runtime/pg/data").write_text("new-default-ledger")
            raise RuntimeError("default initialization failed")
        self.api.initialize_features = fail_initialization
        with patch.object(upgrade, "preflight", return_value=(manifest, bundle, {}, [])), \
             patch.object(upgrade, "check_quiescent"), patch.object(upgrade, "migrate"), \
             patch.object(upgrade, "start_and_check") as start, \
             self.assertRaisesRegex(RuntimeError, "migration/switch"):
            upgrade.run(self.api, "upgrade", self.root, bundle / "manifest.json")
        start.assert_not_called()
        self.assertEqual((self.root / "config/site.json").read_text(), '{"local":true}')
        self.assertEqual((self.root / "runtime/pg/data").read_text(), "old-database")
        self.assertEqual((self.root / "code/version.txt").read_text(), "old-code")

    def test_incomplete_backup_recovery_keeps_current_database(self):
        journal = {"id": "upgrade-" + "b" * 16 + "-1", "phase": "backing_up",
                   "target": self.target, "from_manifest_sha256": "old-manifest"}
        installer.write_json(self.root / upgrade.JOURNAL, journal)
        result = upgrade.run(self.api, "recover", self.root)
        self.assertEqual(result["status"], "restored_stopped")
        self.assertEqual((self.root / "runtime/pg/data").read_text(), "old-database")

    def test_crash_during_restore_can_retry_with_ownership_record(self):
        journal = self.journal()
        journal["phase"] = "restoring"
        installer.write_json(self.root / upgrade.JOURNAL, journal)
        shutil.rmtree(self.root / "code")
        result = upgrade.run(self.api, "recover", self.root)
        self.assertEqual(result["status"], "restored_stopped")
        self.assertEqual((self.root / "code/version.txt").read_text(), "old-code")

    def test_undeclared_upgrade_origin_is_rejected_before_dependencies_or_services(self):
        bundle = Path(self.temp.name) / "engineering"
        bundle.mkdir()
        manifest = {"run_id": "release", "upgrade": {"from_manifest_sha256": [], "migrations": []}}
        installer.write_json(bundle / "manifest.json", manifest)
        self.api.platform_preflight = Mock(return_value={})
        self.api.validate_engineering = Mock(return_value=(manifest, bundle))
        self.api.dependency_preflight = Mock()
        with self.assertRaisesRegex(ValueError, "supported upgrade origin"):
            upgrade.preflight(self.api, self.root, self.state, bundle / "manifest.json")
        self.api.dependency_preflight.assert_not_called()
        self.controller.stop.assert_not_called()
        self.assertFalse((self.root / "maintenance").exists())

    def test_backup_and_restore_do_not_follow_workspace_symlinks(self):
        external = Path(self.temp.name) / "external"
        external.mkdir()
        (external / "sentinel").write_text("not-owned")
        (self.root / "data/workspace/link").symlink_to(external, target_is_directory=True)
        journal = self.journal()
        result = upgrade.restore(self.api, self.root, journal, retain_failed=True)
        self.assertEqual(result["status"], "restored_stopped")
        self.assertEqual((external / "sentinel").read_text(), "not-owned")
        self.assertTrue((self.root / "data/workspace/link").is_symlink())
        inventory = installer.read_json(self.root / "maintenance" / journal["id"] / "backup-inventory.json")
        self.assertNotIn("data/workspace/link/sentinel", inventory)


@unittest.skipUnless(os.environ.get("TFI_UPGRADE_TEST_PG_BIN"), "explicit isolated PostgreSQL test binary required")
class MigrationTransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.pg = Path(os.environ["TFI_UPGRADE_TEST_PG_BIN"])
        cls.temp = tempfile.TemporaryDirectory(prefix="tfi-migration-pg-")
        cls.base = Path(cls.temp.name)
        cls.cluster = cls.base / "pg"
        cls.socket = cls.base / "socket"
        cls.socket.mkdir()
        cls.env = {"PATH": "/usr/bin:/bin", "HOME": str(cls.base), "LANG": "C.UTF-8"}
        if os.environ.get("TFI_UPGRADE_TEST_PG_LIB"):
            cls.env["LD_LIBRARY_PATH"] = os.environ["TFI_UPGRADE_TEST_PG_LIB"]
        subprocess.run([str(cls.pg / "initdb"), "-D", str(cls.cluster), "-U", "tfi", "-A", "trust", "--no-locale", "--encoding=UTF8"],
                       env=cls.env, capture_output=True, check=True)
        subprocess.run([str(cls.pg / "pg_ctl"), "-D", str(cls.cluster), "-l", str(cls.base / "postgres.log"),
                        "-o", f"-k {cls.socket} -p 56433 -c listen_addresses=''", "-w", "start"],
                       env=cls.env, capture_output=True, check=True)
        cls.dsn = f"host={cls.socket} port=56433 user=tfi dbname=postgres"
        cls.api = SimpleNamespace(clean_environment=lambda target: {"PORTAL_DATABASE_URL": cls.dsn},
                                  engineering_path=installer.engineering_path)

    @classmethod
    def tearDownClass(cls):
        subprocess.run([str(cls.pg / "pg_ctl"), "-D", str(cls.cluster), "-m", "fast", "-w", "stop"],
                       env=cls.env, capture_output=True, check=True)
        cls.temp.cleanup()

    def setUp(self):
        import psycopg
        with psycopg.connect(self.dsn, autocommit=True) as c:
            c.execute("DROP SCHEMA IF EXISTS tfi_installer CASCADE")
            c.execute("DROP TABLE IF EXISTS public.local_data")
            c.execute("CREATE TABLE public.local_data(id integer PRIMARY KEY, value text)")
            c.execute("INSERT INTO public.local_data VALUES(1,'preserved-local-data')")

    def migration(self, name, sql):
        path = self.base / (name + ".sql")
        path.write_text(sql)
        return {"id": name, "path": path.name, "sha256": installer.digest(path)}

    def test_real_migration_is_idempotent_and_preserves_local_rows(self):
        import psycopg
        item = self.migration("add_note", "ALTER TABLE public.local_data ADD COLUMN note text DEFAULT 'migrated'")
        upgrade.migrate(self.api, {}, self.base, [item])
        upgrade.migrate(self.api, {}, self.base, [item])
        with psycopg.connect(self.dsn) as c:
            self.assertEqual(c.execute("SELECT value,note FROM public.local_data").fetchone(), ("preserved-local-data", "migrated"))
            self.assertEqual(c.execute("SELECT count(*) FROM tfi_installer.migrations").fetchone()[0], 1)
        changed = {**item, "sha256": "different"}
        with self.assertRaisesRegex(ValueError, "checksum"):
            upgrade.migrate(self.api, {}, self.base, [changed])

    def test_real_failed_migration_rolls_back_all_changes_and_ledger(self):
        import psycopg
        item = self.migration("failure", "UPDATE public.local_data SET value='must-roll-back'; SELECT 1/0")
        with self.assertRaises(psycopg.errors.DivisionByZero):
            upgrade.migrate(self.api, {}, self.base, [item])
        with psycopg.connect(self.dsn) as c:
            self.assertEqual(c.execute("SELECT value FROM public.local_data").fetchone()[0], "preserved-local-data")
            self.assertIsNone(c.execute("SELECT to_regclass('tfi_installer.migrations')").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
