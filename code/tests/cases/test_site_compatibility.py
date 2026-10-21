"""Canonical runtime names and explicit installation parameters."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from management_common.product_environment import read_product_env, mirror_product_env
from management_common.format_tags import schema_matches


class SiteCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.site = Path(self.directory.name) / 'site.json'
        self.config = {'deployment': {'compatibility': {
            'environment_aliases': {
                'PORTAL_SHARE_ROOT': ['ARCHIVE_SHARE_ROOT'],
                'PORTAL_RESIDENT_ID': ['ARCHIVE_RESIDENT_ID'],
            },
            'format_namespaces': ['archive.'],
            'domain_values': {'postgres_unit': 'archive-postgresql.service'},
        }}}
        self.save()
        self.environment = patch.dict(os.environ, {'PORTAL_SITE_CONFIG': str(self.site)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def save(self):
        self.site.write_text(json.dumps(self.config), encoding='utf-8')

    def test_historical_environment_is_ignored(self):
        self.assertIsNone(read_product_env('PORTAL_SHARE_ROOT', environ={'ARCHIVE_SHARE_ROOT': '/archive/share'}))

    def test_canonical_identity_ignores_historical_input(self):
        self.assertEqual(read_product_env('PORTAL_RESIDENT_ID', environ={
            'PORTAL_RESIDENT_ID': 'current', 'ARCHIVE_RESIDENT_ID': 'retired'}), 'current')

    def test_injection_emits_only_canonical_field(self):
        values = {}
        mirror_product_env(values, 'PORTAL_RESIDENT_ID', 'resident_example')
        self.assertEqual(values, {'PORTAL_RESIDENT_ID': 'resident_example'})

    def test_obsolete_alias_configuration_has_no_effect(self):
        self.config['deployment']['compatibility']['environment_aliases'] = []
        self.save()
        self.assertEqual(read_product_env('PORTAL_SHARE_ROOT', environ={'PORTAL_SHARE_ROOT': '/current'}), '/current')

    def test_retired_identity_aliases_do_not_supply_identity(self):
        self.config['deployment']['compatibility']['environment_aliases']['PORTAL_RESIDENT_ID'].append('PRIOR_RESIDENT_ID')
        self.save()
        self.assertIsNone(read_product_env('PORTAL_RESIDENT_ID', environ={
            'ARCHIVE_RESIDENT_ID': 'one', 'PRIOR_RESIDENT_ID': 'two'}))

    def test_obsolete_alias_collisions_do_not_affect_canonical_fields(self):
        self.config['deployment']['compatibility']['environment_aliases']['PORTAL_SHARE_ROOT'] = ['ARCHIVE_RESIDENT_ID']
        self.save()
        self.assertEqual(read_product_env('PORTAL_SHARE_ROOT', environ={'PORTAL_SHARE_ROOT': '/current'}), '/current')

    def test_alias_configuration_cannot_capture_canonical_identity(self):
        self.config['deployment']['compatibility']['environment_aliases']['PORTAL_RESIDENT_ID'] = ['PORTAL_LINEAGE_ID']
        self.save()
        self.assertIsNone(read_product_env('PORTAL_RESIDENT_ID', environ={'PORTAL_LINEAGE_ID': 'lineage_example'}))


    def test_present_empty_canonical_value_wins(self):
        self.assertEqual(read_product_env('PORTAL_SHARE_ROOT', environ={
            'PORTAL_SHARE_ROOT': '', 'ARCHIVE_SHARE_ROOT': '/archive/share'}), '')



    def test_format_alias_cannot_reenable_retired_namespace(self):
        self.assertFalse(schema_matches('archive.record.v2', 'tfi.record.v2'))
        self.assertFalse(schema_matches('archive.record.v1', 'tfi.record.v2'))
        self.assertTrue(schema_matches('tfi.record.v2', 'tfi.record.v2'))

    def test_changed_alias_configuration_is_ignored(self):
        self.config['deployment']['compatibility']['format_namespaces'] = ['previous.']
        self.save()
        self.assertFalse(schema_matches('previous.record.v2', 'tfi.record.v2'))
        self.assertFalse(schema_matches('archive.record.v2', 'tfi.record.v2'))

    def test_malformed_retired_namespace_configuration_is_ignored(self):
        from management_common.site_compatibility import format_namespaces
        self.config['deployment']['compatibility']['format_namespaces'] = {'not': 'a namespace list'}
        self.save()
        self.assertEqual(format_namespaces(), ())
        self.assertTrue(schema_matches('tfi.record.v2', 'tfi.record.v2'))

    def test_absent_mapping_does_not_guess(self):
        self.config = {}
        self.save()
        self.assertIsNone(read_product_env('PORTAL_SHARE_ROOT', environ={'ARCHIVE_SHARE_ROOT': '/old'}))
        values = {}
        mirror_product_env(values, 'PORTAL_SHARE_ROOT', '/new')
        self.assertEqual(values, {'PORTAL_SHARE_ROOT': '/new'})




    def test_explicit_deployment_paths_file_is_read_without_rewriting(self):
        from management_common import portal_paths
        previous = Path(self.directory.name) / 'previous-paths.json'
        expected = {key: str(Path(self.directory.name) / key) for key in portal_paths.KEYS}
        previous.write_text(json.dumps(expected))
        original = previous.read_bytes()
        self.config['deployment']['compatibility']['domain_values']['paths_config'] = str(previous)
        self.save()
        with patch.object(portal_paths, 'CONFIG_PATH', Path(self.directory.name) / 'absent.json'), patch.dict(os.environ):
            os.environ.pop('PORTAL_PATHS_CONFIG', None)
            self.assertEqual(portal_paths.read_config(), expected)
        self.assertEqual(previous.read_bytes(), original)

    def test_configured_paths_source_is_selected_without_existence_fallback(self):
        from management_common import portal_paths
        default = Path(self.directory.name) / 'default-paths.json'
        default.write_text(json.dumps({key: str(Path(self.directory.name) / key)
                                       for key in portal_paths.KEYS}))
        self.config['deployment']['compatibility']['domain_values']['paths_config'] = str(
            Path(self.directory.name) / 'configured-but-missing.json')
        self.save()
        with patch.object(portal_paths, 'CONFIG_PATH', default), patch.dict(os.environ):
            os.environ.pop('PORTAL_PATHS_CONFIG', None)
            self.assertEqual(portal_paths.read_config(), {})


    def test_explicit_postgres_identity_and_unit_are_preserved(self):
        from management_common.postgres_configuration import postgres_settings
        self.config['deployment']['postgres'] = {
            'role': 'archive_operator', 'database': 'archive_records', 'port': 55439,
            'prefix': self.directory.name, 'base': self.directory.name,
        }
        self.save()
        actual = postgres_settings()
        self.assertEqual(actual['unit'], 'archive-postgresql.service')
        self.assertEqual((actual['role'], actual['database'], actual['port']),
                         ('archive_operator', 'archive_records', 55439))

    def test_systemd_template_consumes_configured_unit_and_paths(self):
        from scripts.portal_instance_storage import render_unit
        self.config['deployment']['postgres'] = {
            'prefix': self.directory.name, 'base': self.directory.name, 'port': 55439,
        }
        self.save()
        source = Path(__file__).resolve().parents[2] / 'systemd'
        database = render_unit(source / 'portal-postgresql.service')
        service = render_unit(source / 'portal-dev-execution-service.service')
        self.assertIn('port=55439', database)
        self.assertIn(self.directory.name + '/data', database)
        self.assertIn('Requires=archive-postgresql.service', service)
        self.assertNotIn('@PORTAL_', database + service)

    def test_launchd_labels_preserve_explicit_deployment_prefix(self):
        from scripts.portal_service_control import ServiceControl
        self.assertEqual(ServiceControl.label('portal-dev.service'), 'com.tfi.portal.portal-dev')
        self.config['deployment']['compatibility']['domain_values']['launchd_label_prefix'] = 'com.archive.portal.'
        self.save()
        self.assertEqual(ServiceControl.label('portal-dev.service'), 'com.archive.portal.portal-dev')
        self.assertEqual(ServiceControl(platform='darwin', uid=123)._launch_target('portal-dev.service'),
                         'gui/123/com.archive.portal.portal-dev')


if __name__ == '__main__':
    unittest.main()
