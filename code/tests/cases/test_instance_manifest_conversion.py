import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from datetime import datetime, timezone
from unittest.mock import patch

from scripts import migrate_instance_manifest as migration
from scripts.migrate_instance_manifest import conversion_manifest


class ManifestConversionTests(unittest.TestCase):
    def setUp(self):
        units = patch.object(migration.migrate_instance_units, 'plan', return_value=[])
        units.start()
        self.addCleanup(units.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "instances/test"
        (self.root / "config").mkdir(parents=True)
        self.target = {
            "install_root": str(self.root), "shared_code_root": str(self.base / "release/code"),
            "service_prefix": "tfi-test", "ports": {"web": 18086, "postgres": 15432, "nats": 14222},
            "bind": "127.0.0.1", "python": "/old/python/never/use",
            "data_roots": {"share_root": str(self.base / "share")},
            **{"pg_" + key: str(self.base / "postgres" / key) for key in ("bin", "lib", "share")},
        }
        self.state = {"schema": "tfi.release-installation.v2", "manifest_sha256": "seed-digest",
                      "stages": [{"time": 1700000000}]}
        self.site = {"deployment": {"installation_id": "preserve-this-identity"}}
        self.write()

    def write(self):
        self.state["configuration_sha256"] = hashlib.sha256(json.dumps(self.target, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        for name, data in (("installation.json", self.state), ("config/target.json", self.target), ("config/site.json", self.site)):
            (self.root / name).write_text(json.dumps(data))

    def test_plan_preserves_identity_and_does_not_modify_sources(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*.json")}
        doc = conversion_manifest(self.root, components=["postgres", "nats", "web"])
        self.assertEqual(doc["instance_id"], "preserve-this-identity")
        self.assertEqual(doc["areas"]["projects"], str(self.base / "share/Projects"))
        self.assertNotIn("python", json.dumps(doc))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*.json")})
        self.assertFalse((self.root / "instance.json").exists())

    def test_gateway_uses_verified_external_port_and_removes_internal_router(self):
        self.target['development'] = {
            'gateway_unit': 'old-gateway.service',
            'dev_code': str(self.base / 'dev/code'),
            'web_ports': {'release': 28400, 'dev': 28401},
        }
        self.target['ports']['web'] = 28399
        self.site['deployment']['router'] = {'bind': '0.0.0.0', 'port': 18084}
        self.write()
        network = self.root / 'gateway.env'
        network.write_text('PORTAL_ACCESS_MODE=local\nPORTAL_PORT=18084\n')
        with self.assertRaisesRegex(ValueError, 'actual gateway'):
            conversion_manifest(self.root, components=['web', 'dev-web', 'router'])
        doc = conversion_manifest(self.root, components=['web', 'dev-web', 'router'], gateway_network=network)
        self.assertEqual(doc['network']['entry_port'], 18084)
        self.assertEqual(doc['network']['bind'], '127.0.0.1')
        self.assertEqual(doc['services']['components'], ['dev-web', 'gateway', 'web'])
        self.assertEqual(doc['deployments']['dev']['web_port'], 28401)
        site = migration.conversion_site(self.root, gateway_network=network)
        self.assertEqual(site['deployment']['router'], {'allowed_networks': ['127.0.0.0/8', '::1/128']})

    def test_unapplied_old_configuration_is_rejected(self):
        self.target["ports"]["web"] = 18087
        (self.root / "config/target.json").write_text(json.dumps(self.target))
        with self.assertRaisesRegex(ValueError, "unapplied"):
            conversion_manifest(self.root, components=["web"])

    def test_conversion_does_not_regenerate_missing_identity(self):
        self.site["deployment"].pop("installation_id")
        self.write()
        with self.assertRaisesRegex(ValueError, "identity is required"):
            conversion_manifest(self.root, components=["web"])

    def test_existing_manifest_is_never_overwritten(self):
        (self.root / "instance.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "one-time"):
            conversion_manifest(self.root, components=["web"])

    def test_conversion_can_select_separately_installed_code_without_moving_data(self):
        code = self.base / 'new-program/code'
        (code / 'scripts').mkdir(parents=True)
        (code / 'scripts/tfi_release_install.py').write_text('# fixture')
        before = (self.root / 'config/target.json').read_bytes()
        doc = conversion_manifest(self.root, components=['web'], code_root=code)
        self.assertEqual(doc['deployments']['release']['code_root'], str(code))
        self.assertEqual(doc['instance_id'], 'preserve-this-identity')
        self.assertEqual((self.root / 'config/target.json').read_bytes(), before)
        self.assertFalse((self.root / 'instance.json').exists())

    def apply(self, *, backup=None):
        proposal = conversion_manifest(self.root, components=["web"])
        completed = {"status": "success", "operation": "content", "succeeded": True,
                     "run_id": "backup-fixture", "finished_at": datetime.now(timezone.utc).isoformat()}
        with patch("management_common.host_instances.registry_path", return_value=self.base / "registry/instances.json"):
            return migration.apply_conversion(self.root, proposal=proposal, backup=backup or completed)

    def test_apply_moves_files_preserves_identity_and_registers_without_starting(self):
        secrets = self.root / "old-secrets"
        secrets.mkdir()
        (secrets / "key").write_text("fixture-only")
        (self.root / "config/postgres.env").write_text("fixture-only")
        (self.root / "config/generation.env").write_text("fixture-generation")
        self.target["data_roots"]["secrets_dir"] = str(secrets)
        self.site["deployment"].update(source_root="/old/code", pulse={"lineage_id": "lineage_fixture"})
        self.write()
        original = (self.root / "config/site.json").read_bytes()
        with patch.object(migration, "require_stopped") as stopped:
            result = self.apply()
        stopped.assert_called_once()
        self.assertFalse(result["services_activated"])
        self.assertEqual(json.loads((self.root / "instance.json").read_text())["instance_id"], "preserve-this-identity")
        self.assertEqual(json.loads((self.root / "site.json").read_text()), {"deployment": {"pulse": {"lineage_id": "lineage_fixture"}}})
        self.assertEqual(json.loads((self.base / "registry/instances.json").read_text()), {"test": str(self.root)})
        self.assertEqual((self.root / "secrets/key").read_text(), "fixture-only")
        self.assertTrue((self.root / "secrets/postgres.env").is_file())
        self.assertTrue((self.root / "runtime/deployments/release/generation.env").is_file())
        self.assertFalse(secrets.exists())
        self.assertEqual((self.root / "config/site.json").read_bytes(), original)

    def test_apply_moves_database_and_auth_keys_without_changing_key_material(self):
        old = self.root / 'home/private'
        old.mkdir(parents=True)
        key = old / 'signing.key'
        key.write_bytes(b'fixture authentication bytes')
        key.chmod(0o600)
        database = self.root / 'data/database'
        database.mkdir(parents=True)
        (database / 'PG_VERSION').write_text('16')
        host = self.root / 'config/host'
        host.mkdir()
        egress = host / 'custom-egress.env'
        egress.write_text('HTTPS_PROXY="http://fixture-proxy"\n')
        self.site['deployment'].update(auth={'session_signing_key_path': str(key)},
                                       providers={'egress_env_file': str(egress)})
        self.write()
        inode = key.stat().st_ino
        with patch.object(migration, 'require_stopped'):
            self.apply()
        self.assertEqual((self.root / 'secrets/session_signing.key').stat().st_ino, inode)
        self.assertFalse(key.exists())
        self.assertEqual((self.root / 'database/PG_VERSION').read_text(), '16')
        self.assertFalse(database.exists())
        self.assertTrue((self.root / 'secrets/host/provider-egress.env').is_file())
        self.assertFalse(egress.exists())

    def test_apply_refuses_running_service_before_mutating_configuration(self):
        with patch.object(migration, "require_stopped", side_effect=ValueError("service running")):
            with self.assertRaisesRegex(ValueError, "service running"):
                self.apply()
        self.assertFalse((self.root / "instance.json").exists())
        self.assertFalse((self.root / "site.json").exists())

    def test_apply_refuses_skipped_backup(self):
        with patch.object(migration, "require_stopped") as stopped:
            with self.assertRaisesRegex(ValueError, "successful new content backup"):
                self.apply(backup={"status": "skipped_locked", "succeeded": True})
        stopped.assert_not_called()

    def test_apply_failure_restores_moved_files_and_keeps_registry_unchanged(self):
        source = self.root / "config/postgres.env"
        source.write_text("fixture-only")
        write = migration._write_new
        def fail_manifest(path, value):
            if path.name == "instance.json":
                raise OSError("fixture write failure")
            write(path, value)
        with patch.object(migration, "require_stopped"), patch.object(migration, "_write_new", side_effect=fail_manifest):
            with self.assertRaisesRegex(OSError, "fixture write failure"):
                self.apply()
        self.assertTrue(source.is_file())
        self.assertFalse((self.root / "site.json").exists())
        self.assertFalse((self.root / "instance.json").exists())
        self.assertFalse((self.base / "registry/instances.json").exists())

    def test_apply_refuses_stale_proposal(self):
        proposal = conversion_manifest(self.root, components=["web"])
        proposal["network"]["entry_port"] += 1
        completed = {"status": "success", "operation": "content", "succeeded": True,
                     "run_id": "backup-fixture", "finished_at": datetime.now(timezone.utc).isoformat()}
        with patch("management_common.host_instances.registry_path", return_value=self.base / "registry/instances.json"):
            with self.assertRaisesRegex(ValueError, "no longer matches"):
                migration.apply_conversion(self.root, proposal=proposal, backup=completed)

    def test_apply_refuses_existing_destination(self):
        (self.root / "site.json").write_text("untouched")
        with patch.object(migration, "require_stopped") as stopped:
            with self.assertRaisesRegex(ValueError, "cannot overwrite"):
                self.apply()
        stopped.assert_not_called()
        self.assertEqual((self.root / "site.json").read_text(), "untouched")

    def test_service_check_includes_old_gateway_and_unlisted_prefixed_services(self):
        self.target["development"] = {"gateway_unit": "old-public-gateway.service"}
        self.write()
        inspected = []
        def run(command, **kwargs):
            if "list-units" in command:
                return subprocess.CompletedProcess(command, 0, "tfi-test-extra.service loaded active running extra\n", "")
            inspected.append(command[3])
            state = "active" if command[3] == "tfi-test-extra.service" else "inactive"
            return subprocess.CompletedProcess(command, 0, f"LoadState=loaded\nActiveState={state}\nMainPID=0\nControlPID=0\nControlGroup=\n", "")
        with patch.object(migration.sys, "platform", "linux"), patch.object(migration.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(ValueError, "tfi-test-extra.service"):
                migration.require_stopped(self.root, {"services": {"prefix": "tfi-test", "components": ["web"]}})
        self.assertIn("old-public-gateway.service", inspected)

    def test_service_check_rejects_unobservable_unit(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0 if "list-units" in command else 1, "", "fixture manager unavailable")
        with patch.object(migration.sys, "platform", "linux"), patch.object(migration.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(ValueError, "Cannot inspect"):
                migration.require_stopped(self.root, {"services": {"prefix": "tfi-test", "components": ["web"]}})

    def test_backup_age_and_operation_are_checked(self):
        completed = {"status": "success", "operation": "content", "succeeded": True,
                     "run_id": "backup-fixture", "finished_at": "2020-01-01T00:00:00Z"}
        with self.assertRaisesRegex(ValueError, "last day"):
            migration.require_backup(completed)
        completed["operation"] = "critical"
        with self.assertRaisesRegex(ValueError, "content backup"):
            migration.require_backup(completed)


if __name__ == "__main__":
    unittest.main()
