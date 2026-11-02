import json
import tempfile
import unittest
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch

from scripts import migrate_instance_windows_services as windows
from scripts.migrate_instance_units import Replacement


class WindowsConversionTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'native Windows process inventory')
    def test_native_process_reference_and_ignore(self):
        script = self.base / 'running.py'
        script.write_text('import time; time.sleep(60)', encoding='utf-8')
        process = subprocess.Popen([sys.executable, str(script)])
        try:
            self.assertIn(process.pid, {r['pid'] for r in windows.processes_using([self.base])})
            self.assertNotIn(process.pid, {r['pid'] for r in windows.processes_using([self.base], ignore=[process.pid])})
        finally:
            process.terminate()
            process.wait(timeout=10)

    def test_subscription_mapping_preserves_state_and_requires_existing_directories(self):
        from scripts.migrate_instance_registrations import subscription_directories
        directory = self.base / 'existing-login'
        directory.mkdir()
        (directory / 'session.txt').write_text('existing session')
        row = dict(account_id='old', kind='subscription_login',
                   config={'is_default': True, 'reuse_local_login': True})
        before = json.dumps(row)
        result = subscription_directories([row], {'old': str(directory)})
        self.assertEqual(result, {'old': {'is_default': True, 'account_dir': str(directory)}})
        self.assertEqual(json.dumps(row), before)
        self.assertEqual((directory / 'session.txt').read_text(), 'existing session')
        with self.assertRaisesRegex(ValueError, 'exactly'):
            subscription_directories([row], {})
        with self.assertRaisesRegex(ValueError, 'missing'):
            subscription_directories([row], {'old': str(self.base / 'absent')})
        self.assertFalse((self.base / 'absent').exists())

    def test_subscription_mapping_rejects_duplicates_and_ignores_api_accounts(self):
        from scripts.migrate_instance_registrations import subscription_directories
        rows = [dict(account_id=name, kind='subscription_login', config={}) for name in ('a', 'b')]
        with self.assertRaisesRegex(ValueError, 'duplicates'):
            subscription_directories(rows, {'a': str(self.base), 'b': str(self.base)})
        self.assertEqual(subscription_directories([dict(account_id='api', kind='api_profile', config={})], {}), {})

    def test_windows_prefix_mapping_is_case_insensitive_and_component_bounded(self):
        from scripts.instance_storage_migration import PrefixMap
        mapping = PrefixMap([(r'C:\Old\Projects', r'D:\New\Projects')])
        self.assertEqual(mapping.map(r'c:\old\projects\中文\file.txt'), r'D:\New\Projects\中文\file.txt')
        self.assertIsNone(mapping.map(r'C:\Old\Projects-other\file.txt'))

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'instance'
        (self.root / 'runtime').mkdir(parents=True)
        self.units = self.base / 'units'
        self.units.mkdir()
        self.target = dict(windows_service_mode='user', service_prefix='tfi-test',
                           python='C:/python.exe', shared_code_root='C:/portal/code')
        self.marker = self.units / 'tfi-test-web.json'
        self.definition = dict(mode='user', name='tfi-test-web', root=str(self.root),
                               python='C:/python.exe', code='C:/portal/code', component='web')
        self.marker.write_text(json.dumps(self.definition), encoding='utf-8')
        p = patch.object(windows, 'legacy_directory', return_value=self.units)
        p.start()
        self.addCleanup(p.stop)

    def test_foreign_definition_and_scm_are_rejected(self):
        windows.definitions(self.root, self.target)
        self.definition['root'] = str(self.base / 'another-instance')
        self.marker.write_text(json.dumps(self.definition), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'ownership'):
            windows.definitions(self.root, self.target)
        with self.assertRaisesRegex(ValueError, 'SCM'):
            windows.definitions(self.root, dict(self.target, windows_service_mode='scm'))

    def test_orphan_child_and_provider_supervisor_block_conversion(self):
        record = self.root / 'runtime/codex-extra.process.json'
        record.write_text(json.dumps(dict(pid=100, created=1, child_pid=101, child_created=2)))
        with patch('scripts.tfi_windows_user_services.read_record', side_effect=lambda p:p.read_text()), \
             patch('scripts.tfi_windows_user_services.process_stamp', side_effect=lambda pid:2 if pid == 101 else None):
            with self.assertRaisesRegex(ValueError, 'must be stopped'):
                windows.require_stopped(self.root, self.target)
        with patch('scripts.tfi_windows_user_services.read_record', side_effect=lambda p:p.read_text()), \
             patch('scripts.tfi_windows_user_services.process_stamp', return_value=None):
            windows.require_stopped(self.root, self.target)

    def test_changed_definition_prevents_any_mutation(self):
        original = self.marker.read_text()
        replacement = self.base / 'new/web.json'
        items = [Replacement(self.marker, None, original), Replacement(replacement, 'new', None)]
        self.marker.write_text('changed')
        with patch('scripts.tfi_windows_user_services.read_record', side_effect=lambda p:p.read_text()):
            with self.assertRaisesRegex(ValueError, 'changed after preflight'):
                windows.apply(items)
        self.assertEqual(self.marker.read_text(), 'changed')
        self.assertFalse(replacement.exists())

    def test_failed_publish_restores_old_definition(self):
        original = self.marker.read_text()
        replacement = self.base / 'new/web.json'
        items = [Replacement(self.marker, None, original), Replacement(replacement, 'new', None)]
        def write(path, text):
            if path == replacement:
                raise OSError('simulated publish failure')
            path.write_text(text)
        with patch('scripts.tfi_windows_user_services.read_record', side_effect=lambda p:p.read_text()), \
             patch('scripts.tfi_windows_user_services.write_record', side_effect=write), \
             patch('scripts.tfi_windows_user_services.remove_record', side_effect=lambda p:p.unlink(missing_ok=True)):
            with self.assertRaisesRegex(OSError, 'publish failure'):
                windows.apply(items)
        self.assertEqual(self.marker.read_text(), original)
        self.assertFalse(replacement.exists())
