"""Private native dependency preparation, with no service or live database access."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from management_common.release_dependencies import declaration
from scripts import tfi_release_native_dependencies as native
from scripts import tfi_release_install as installer


class NativeDependenciesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='native-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.target = {'install_root': str(self.base / 'instance')}
        self.manifest = {'runtime_dependencies': declaration(['tau'])}

    def test_all_catalog_targets_plan_without_creating_installation(self):
        for platform in ('linux', 'windows', 'macos'):
            for architecture in ('x86_64', 'aarch64'):
                with self.subTest(platform=platform, architecture=architecture):
                    target, entries = native.plan(self.target, self.manifest,
                        host={'platform': platform, 'architecture': architecture})
                    self.assertEqual(target['managed_dependencies'], ['nats', 'node', 'postgres'])
                    self.assertEqual(len(entries), 3)
                    for entry in entries:
                        self.assertTrue(entry['url'].startswith('https://'))
                        self.assertRegex(entry['sha256'], r'^[a-f0-9]{64}$')
                    self.assertFalse(Path(target['install_root']).exists())

    def test_core_does_not_require_node_and_explicit_tools_are_preserved(self):
        _, entries = native.plan(self.target, {'runtime_dependencies': declaration([])})
        self.assertEqual({e['name'] for e in entries}, {'nats', 'postgres'})
        explicit = {**self.target, 'pg_bin': '/operator/pg/bin', 'pg_share': '/operator/pg/share',
                    'nats_server': '/operator/nats', 'node_bin': '/operator/node'}
        target, entries = native.plan(explicit, self.manifest)
        self.assertEqual(entries, [])
        self.assertEqual(target['pg_bin'], explicit['pg_bin'])
        self.assertEqual(target['node_bin'], explicit['node_bin'])

    def test_partial_pg_configuration_and_redirected_native_directory_rejected(self):
        with self.assertRaisesRegex(ValueError, 'both pg_bin'):
            native.plan({**self.target, 'pg_bin': '/operator/pg'}, self.manifest)
        root = Path(self.target['install_root'])
        (root / 'runtime').mkdir(parents=True)
        (root / 'runtime/native').symlink_to(self.base)
        with self.assertRaisesRegex(ValueError, 'redirected'):
            native.plan(self.target, self.manifest)

    def test_download_hash_and_https_are_checked(self):
        payload = b'pinned tool bytes'
        entry = {'name': 'nats', 'version': 'test', 'url': 'https://example.invalid/tool',
                 'sha256': hashlib.sha256(payload).hexdigest()}
        def response(url):
            stream = io.BytesIO(payload)
            stream.geturl = lambda: url
            stream.status = 200
            stream.headers = {}
            return stream
        with patch.object(native, 'urlopen', return_value=response(entry['url'])):
            native.download(entry, self.base / 'good')
        with patch.object(native, 'urlopen', return_value=response(entry['url'])):
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                native.download({**entry, 'sha256': '0' * 64}, self.base / 'bad')
        with patch.object(native, 'urlopen', return_value=response('http://example.invalid/tool')):
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                native.download(entry, self.base / 'redirect')

    def test_archives_cannot_write_outside_destination(self):
        archive = self.base / 'unsafe.zip'
        with zipfile.ZipFile(archive, 'w') as out:
            out.writestr('../escaped', 'private')
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            native.unpack(archive, self.base / 'zip', zipped=True)
        archive = self.base / 'unsafe.tar'
        with tarfile.open(archive, 'w') as out:
            link = tarfile.TarInfo('link')
            link.type, link.linkname = tarfile.SYMTYPE, str(self.base / 'escaped')
            out.addfile(link)
        with self.assertRaises(tarfile.FilterError):
            native.unpack(archive, self.base / 'tar')
        self.assertFalse((self.base / 'escaped').exists())

    def test_failed_download_retries_without_adopting_unowned_tools(self):
        _, entries = native.plan(self.target, {})
        entry = next(e for e in entries if e['name'] == 'nats')
        api = SimpleNamespace(write_json=installer.write_json)
        destination = Path(entry['directory'])
        with patch.object(native, 'download', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                native.prepare(api, self.target, [entry])
        self.assertFalse(destination.exists())
        archive = self.base / 'tool.tar'
        with tarfile.open(archive, 'w') as out:
            item = tarfile.TarInfo('package/nats-server')
            item.size, item.mode = 4, 0o755
            out.addfile(item, io.BytesIO(b'tool'))
        def fetch(entry, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read_bytes())
        with patch.object(native, 'download', side_effect=fetch) as download, \
             patch.object(native, 'verify', return_value={'nats': entry['version']}):
            native.prepare(api, self.target, [entry])
            native.prepare(api, self.target, [entry])
            download.assert_called_once()
        receipt = destination / 'tfi-native-dependency.json'
        self.assertEqual(json.loads(receipt.read_text())['status'], 'verified')
        receipt.unlink()
        with self.assertRaisesRegex(ValueError, 'unowned'):
            native.prepare(api, self.target, [entry])

    def test_host_mismatch_fails_before_native_download_or_root_creation(self):
        manifest = self.base / 'manifest.json'
        manifest.write_text('{}')
        with patch.object(installer, 'platform_preflight', side_effect=ValueError('wrong OS')), \
             patch.object(native, 'download') as download:
            with self.assertRaisesRegex(ValueError, 'wrong OS'):
                installer.install(manifest, self.target)
            download.assert_not_called()
        self.assertFalse(Path(self.target['install_root']).exists())

    def test_probe_failure_does_not_report_verified(self):
        target, entries = native.plan(self.target, self.manifest)
        api = SimpleNamespace(clean_environment=lambda _: {}, run_native=Mock(
            return_value=subprocess.CompletedProcess([], 1, '', 'missing library')))
        with self.assertRaisesRegex(RuntimeError, 'cannot run'):
            native.verify(api, target, entries[0])


if __name__ == '__main__':
    unittest.main()
