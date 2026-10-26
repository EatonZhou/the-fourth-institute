"""Deployment ownership, drift, transaction and legacy-import regressions."""
import copy
import json
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from unittest.mock import patch

from management_common import instance_config as owner, portal_paths
from management_common import site_config
from scripts import tfi_instance_config as config, tfi_release_install as installer
from tests.cases import test_tfi_release_install as fixtures


class InstanceConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InstallationIsolationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.target = installer.target_plan(self.fixture.target)
        self.root = Path(self.target['install_root'])
        installer.prepare_configuration(self.target)
        self.state = {'schema': owner.LEGACY_SCHEMA, 'target': self.target,
                      'protected_roots': [], 'manifest_sha256': 'manifest',
                      'status': 'installed', 'completed': ['prepare', 'database']}
        owner.atomic_json(self.root / owner.STATE_FILE, self.state)
        owner.atomic_json(self.root / owner.TARGET_FILE, self.target)
        self.units = self.fixture.base / 'units'
        self.units.mkdir()
        self.unit_patch = patch.object(installer, 'unit_directory', return_value=self.units)
        self.unit_patch.start()
        self.addCleanup(self.unit_patch.stop)

    def adopt(self):
        return config.apply(self.root, adopt=True)

    def test_reading_legacy_configuration_does_not_create_files(self):
        self.assertFalse((self.root / owner.LOCK_FILE).exists())
        self.assertEqual(owner.read_installation(self.root)['target'], self.target)
        self.assertFalse((self.root / owner.LOCK_FILE).exists())

    def test_reader_waits_for_target_and_state_to_commit_together(self):
        self.adopt()
        changed = copy.deepcopy(self.target)
        changed['ports']['web'] += 1
        with ThreadPoolExecutor(max_workers=1) as pool:
            with owner.configuration_lock(self.root, create=True):
                owner.atomic_json(self.root / owner.TARGET_FILE, changed)
                future = pool.submit(owner.read_installation, self.root)
                with self.assertRaises(TimeoutError):
                    future.result(timeout=0.05)
                state = owner.read_json(self.root / owner.STATE_FILE)
                state['configuration_sha256'] = owner.configuration_digest(changed)
                owner.atomic_json(self.root / owner.STATE_FILE, state)
            self.assertEqual(future.result(timeout=2)['target'], changed)

    def prepare_pair(self, gateway=None):
        target = copy.deepcopy(self.target)
        sources = {}
        for name in ('dev', 'release'):
            code = self.fixture.base / name / 'code'
            (code / 'management_portal').mkdir(parents=True)
            (code / 'management_portal/asgi.py').write_text('')
            sources[name] = str(code)
        development = {'schema': 'tfi.development-runtime.v1',
                       'dev_code': sources['dev'], 'release_code': sources['release'],
                       'web_ports': {'dev': 28087, 'release': 28088},
                       'router_port': target['ports']['web']}
        if gateway:
            development['gateway_unit'] = gateway
            value = installer.clean_environment(target)['PORTAL_DATABASE_URL'].replace('%', '%%')
            (self.units / gateway).write_text('[Service]\nEnvironment="PORTAL_DATABASE_URL=' + value + '"\n')
        target['development'] = development
        for name, data in config.projections(self.root, target).items():
            config._write_bytes(self.root / name, data)
        for path, data in config.paired_outputs(self.root, target, self.units).items():
            config._write_bytes(path, data)
        return target

    def test_component_startup_reads_canonical_pair_instead_of_generated_copy(self):
        target = self.prepare_pair()
        self.adopt()
        owner.atomic_json(self.root / 'config/development.json', {'stale': 'ignored by startup'})
        _, environment = installer.component_command(owner.read_installation(self.root)['target'], 'worker')
        self.assertEqual(environment['PYTHONPATH'], target['development']['dev_code'])
        self.assertEqual(owner.read_development(self.root), target['development'])
        with self.assertRaisesRegex(ValueError, 'drift'):
            config.check(self.root)

    def test_site_reader_uses_canonical_sources_and_preserves_business_settings(self):
        target = self.prepare_pair()
        self.adopt()
        path = self.root / 'config/site.json'
        site = owner.read_json(path)
        site['site_name'] = 'My Portal'
        site['deployment']['source_root'] = '/stale/dev/code'
        site['deployment']['release_root'] = '/stale/release/code'
        owner.atomic_json(path, site)
        with patch.dict('os.environ', {'PORTAL_SITE_CONFIG': str(path)}):
            result = site_config.read_site_config()
        self.assertEqual(result['site_name'], 'My Portal')
        self.assertEqual(result['deployment']['source_root'], target['development']['dev_code'])
        self.assertEqual(result['deployment']['release_root'], target['development']['release_code'])
        with self.assertRaisesRegex(ValueError, 'drift'):
            config.check(self.root)

    def test_v2_preserves_applied_feature_timer_activation_without_embedded_target(self):
        self.adopt()
        state = owner.read_installation(self.root)
        state['feature_initialization'] = {'status': 'applied', 'manifest_sha256': state['manifest_sha256'],
                                           'result': {'lineage_timer_required': True}}
        owner.write_installation(self.root, state)
        self.assertNotIn('target', owner.read_json(self.root / owner.STATE_FILE))
        self.assertEqual(installer.clean_environment(self.target)['PORTAL_LINEAGE_TIMER_INSTANCE'],
                         self.target['service_prefix'])
        changed = copy.deepcopy(self.target)
        changed['ports']['web'] += 1
        self.assertEqual(installer.clean_environment(changed)['PORTAL_LINEAGE_TIMER_INSTANCE'],
                         'disabled-until-configured')

    def test_stale_encoded_database_endpoint_in_provider_unit_is_detected(self):
        self.adopt()
        unit = self.units / (self.target['service_prefix'] + '-claude-code.service')
        unit.write_text('[Service]\nEnvironment="PORTAL_DATABASE_URL=postgresql://old@/db?host=%%2Fold%%2Fsocket"\n')
        with self.assertRaisesRegex(ValueError, 'PORTAL_DATABASE_URL'):
            config.check(self.root)

    def test_public_gateway_environment_is_updated_with_the_instance(self):
        self.prepare_pair('public-gateway.service')
        self.adopt()
        target = owner.read_installation(self.root)['target']
        changed = copy.deepcopy(target)
        changed['ports']['postgres'] += 1
        with patch.object(config, 'assert_stopped'), patch.object(config.subprocess, 'run'):
            config.apply(self.root, changed, expected_sha256=owner.configuration_digest(target))
        unit = (self.units / 'public-gateway.service').read_text()
        self.assertIn('port=' + str(changed['ports']['postgres']), unit)
        self.assertTrue(config.check(self.root)['ok'])

    def test_changed_paired_service_definition_is_detected(self):
        self.prepare_pair()
        self.adopt()
        unit = self.units / (self.target['service_prefix'] + '-dev-web.service')
        unit.write_text(unit.read_text().replace('RestartSec=3', 'RestartSec=10'))
        with self.assertRaisesRegex(ValueError, 'Paired service configuration drift'):
            config.check(self.root)

    def test_import_removes_duplicate_target_and_preserves_runtime_values(self):
        before = {name: (self.root / name).read_bytes() for name in config.projections(self.root, self.target)}
        result = self.adopt()
        stored = owner.read_json(self.root / owner.STATE_FILE)
        self.assertTrue(result['ok'])
        self.assertEqual(stored['schema'], owner.STATE_SCHEMA)
        self.assertNotIn('target', stored)
        self.assertEqual(owner.read_installation(self.root)['target'], self.target)
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in before})
        self.assertEqual(config.apply(self.root, adopt=True)['configuration_sha256'], result['configuration_sha256'])

    def test_import_refuses_ambiguous_legacy_sources(self):
        different = copy.deepcopy(self.target)
        different['ports']['web'] += 1
        owner.atomic_json(self.root / owner.TARGET_FILE, different)
        with self.assertRaisesRegex(ValueError, 'disagrees'):
            self.adopt()
        self.assertEqual(owner.read_json(self.root / owner.STATE_FILE)['schema'], owner.LEGACY_SCHEMA)

    def test_unapplied_edit_is_rejected_instead_of_falling_back(self):
        self.adopt()
        changed = copy.deepcopy(self.target)
        changed['ports']['web'] += 1
        owner.atomic_json(self.root / owner.TARGET_FILE, changed)
        with self.assertRaisesRegex(ValueError, 'Unapplied'):
            owner.read_installation(self.root)
        with patch.object(portal_paths, 'CONFIG_PATH', self.root / 'config/paths.json'):
            with self.assertRaisesRegex(ValueError, 'Unapplied'):
                portal_paths.paths()

    def test_business_reader_uses_master_and_old_writer_cannot_bypass_owner(self):
        self.adopt()
        owner.atomic_json(self.root / 'config/paths.json', {'ignored': 'not a deployment source'})
        with patch.object(portal_paths, 'CONFIG_PATH', self.root / 'config/paths.json'):
            self.assertEqual(portal_paths.read_config(), self.target['data_roots'])
            with self.assertRaisesRegex(ValueError, 'owned by'):
                portal_paths.write_config(self.target['data_roots'])
        with self.assertRaisesRegex(ValueError, 'drift'):
            config.check(self.root)
        config.apply(self.root)
        self.assertTrue(config.check(self.root)['ok'])

    def test_changed_configuration_requires_source_version_and_stopped_consumers(self):
        self.adopt()
        changed = copy.deepcopy(self.target)
        changed['ports']['web'] += 1
        with patch.object(config, 'assert_stopped', side_effect=ValueError('busy')) as stopped:
            with self.assertRaisesRegex(ValueError, 'expected-sha256'):
                config.apply(self.root, changed)
            with self.assertRaisesRegex(ValueError, 'since planning'):
                config.apply(self.root, changed, expected_sha256='stale')
            with self.assertRaisesRegex(ValueError, 'busy'):
                config.apply(self.root, changed, expected_sha256=owner.configuration_digest(self.target))
            self.assertEqual(stopped.call_count, 1)
        self.assertEqual(owner.read_installation(self.root)['target'], self.target)

    def test_one_root_change_updates_projection_and_provider_environment(self):
        self.adopt()
        old = self.target['data_roots']['accounts_root']
        unit = self.units / (self.target['service_prefix'] + '-codex-app-server@.service')
        unit.write_text(f'[Service]\nWorkingDirectory={old}/codex/%i\nEnvironment="CODEX_HOME={old}/codex/%i"\nExecStart="/native/codex" "--home" "{old}/codex/%i"\n')
        changed = copy.deepcopy(self.target)
        new = str(self.root / 'data/new-accounts')
        Path(new).mkdir(parents=True)
        changed['data_roots']['accounts_root'] = new
        with patch.object(config, 'assert_stopped'), patch.object(config.subprocess, 'run') as run:
            config.apply(self.root, changed, expected_sha256=owner.configuration_digest(self.target))
        self.assertIn(new + '/codex/%i', unit.read_text())
        self.assertNotIn(old, unit.read_text())
        self.assertEqual(owner.read_json(self.root / 'config/paths.json'), changed['data_roots'])
        self.assertEqual(owner.read_installation(self.root)['target'], changed)
        self.assertNotIn('target', owner.read_json(self.root / owner.STATE_FILE))
        run.assert_called_once()

    def test_interrupted_apply_restores_exact_before_images(self):
        self.adopt()
        original = (self.root / owner.STATE_FILE).read_bytes()
        changed = copy.deepcopy(self.target)
        changed['ports']['web'] += 1
        write = config._write_bytes
        def fail(path, data):
            if str(path) == str(self.root / owner.STATE_FILE):
                raise OSError('injected commit failure')
            return write(path, data)
        with patch.object(config, 'assert_stopped'), patch.object(config, '_write_bytes', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'injected'):
                config.apply(self.root, changed, expected_sha256=owner.configuration_digest(self.target))
        with patch.object(config, 'assert_stopped'):
            self.assertEqual(config.recover(self.root)['status'], 'rolled_back')
        self.assertEqual((self.root / owner.STATE_FILE).read_bytes(), original)
        self.assertTrue(config.check(self.root)['ok'])

    def test_recovery_does_not_overwrite_concurrent_edit(self):
        self.adopt()
        changed = copy.deepcopy(self.target)
        changed['ports']['web'] += 1
        write = config._write_bytes
        def fail(path, data):
            if str(path) == str(self.root / owner.STATE_FILE):
                raise OSError('injected')
            return write(path, data)
        with patch.object(config, 'assert_stopped'), patch.object(config, '_write_bytes', side_effect=fail):
            with self.assertRaises(OSError):
                config.apply(self.root, changed, expected_sha256=owner.configuration_digest(self.target))
        (self.root / 'config/network.env').write_text('operator edited this\n')
        with self.assertRaisesRegex(ValueError, 'concurrent'):
            config.recover(self.root)
        self.assertEqual((self.root / 'config/network.env').read_text(), 'operator edited this\n')

    def test_single_configuration_update_never_moves_or_aliases_data(self):
        self.adopt()
        old = Path(self.target['data_roots']['accounts_root'])
        (old / 'session').write_text('history')
        changed = copy.deepcopy(self.target)
        new = self.root / 'data/new-account-root'
        new.mkdir()
        changed['data_roots']['accounts_root'] = str(new)
        with patch.object(config, 'assert_stopped'):
            config.apply(self.root, changed, expected_sha256=owner.configuration_digest(self.target))
        self.assertEqual((old / 'session').read_text(), 'history')
        self.assertFalse(old.is_symlink())
        self.assertFalse((new / 'session').exists())


if __name__ == '__main__':
    unittest.main()
