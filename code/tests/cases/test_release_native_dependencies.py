"""Native programs prepared beside the code, with no service or live database access."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import xml.etree.ElementTree as ET

from scripts import tfi_release_native_dependencies as native
from scripts import tfi_release_install as installer
from scripts.windows import utf8_manifest as manifests

CATALOG = Path(__file__).resolve().parents[2] / 'config/release-native-dependencies.json'


class WindowsManifestTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Native Windows ACL inheritance')
    def test_extracted_tools_grant_actual_user_not_only_elevated_default_owner(self):
        from management_common.file_permissions import _WindowsSecurity, assert_private_file
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'tools.zip'
            with zipfile.ZipFile(archive, 'w') as out:
                out.writestr('bin/library.dll', b'fixture')
            root = Path(directory) / 'contents'
            native.unpack(archive, root, zipped=True)
            security = _WindowsSecurity()
            with security.handle(root, directory=True) as handle, security.security(handle) as (owner, _):
                self.assertEqual(security.sid_text(owner), security.current_sid)
            assert_private_file(root / 'bin/library.dll')

    def test_preserves_execution_level_and_existing_dependencies(self):
        original = b'''<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
        <dependency><dependentAssembly><assemblyIdentity name="Original"/></dependentAssembly></dependency>
        <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
        </requestedPrivileges></security></trustInfo></assembly>'''
        updated = manifests.utf8_manifest(original)
        root = ET.fromstring(updated)
        self.assertEqual(root.find(f'.//{{{manifests.APP}}}requestedExecutionLevel').get('level'), 'asInvoker')
        self.assertEqual(root.find(f'.//{{{manifests.ASM}}}assemblyIdentity').get('name'), 'Original')
        self.assertEqual(root.find(f'.//{{{manifests.SETTINGS}}}activeCodePage').text, 'UTF-8')
        self.assertEqual(manifests.utf8_manifest(updated), updated)

    def test_missing_and_legacy_code_pages(self):
        generated = manifests.utf8_manifest(None)
        legacy = generated.replace(b'UTF-8', b'Legacy')
        self.assertEqual(manifests.utf8_manifest(legacy), generated)
        with self.assertRaises(ET.ParseError):
            manifests.utf8_manifest(b'not a manifest')

    @unittest.skipUnless(os.name == 'nt', 'Native Windows resource APIs')
    def test_real_pe_resource_update_roundtrip_preserves_languages(self):
        with tempfile.TemporaryDirectory(prefix='中文 manifest ') as directory:
            copy = Path(directory) / 'python.exe'
            shutil.copy2(sys.executable, copy)
            resources = manifests.Resources()
            original = resources.read(copy)
            updated = {language: manifests.utf8_manifest(data) for language, data in original.items()}
            resources.write(copy, updated)
            self.assertEqual(resources.read(copy), updated)


class NativeDependenciesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='native-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.code = self.base / 'checkout/code'
        (self.code / 'config').mkdir(parents=True)
        shutil.copyfile(CATALOG, self.code / 'config/release-native-dependencies.json')

    def test_every_catalog_target_resolves_beside_the_code(self):
        for platform in ('linux', 'windows', 'macos'):
            for architecture in ('x86_64', 'aarch64'):
                host = {'platform': platform, 'architecture': architecture}
                for name in native.packages_for_host(host):
                    with self.subTest(platform=platform, architecture=architecture, name=name):
                        entry = native.catalog_entry(self.code, name, host=host)
                        self.assertTrue(entry['url'].startswith('https://'))
                        self.assertRegex(entry['sha256'], r'^[a-f0-9]{64}$')
                        self.assertEqual(Path(entry['directory']).name,
                                         f"{name}-{entry['version']}-{entry['sha256'][:12]}")
                        self.assertEqual(Path(entry['directory']).parent, self.base / 'checkout/dependencies/native')
        self.assertFalse((self.base / 'checkout/dependencies').exists())
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            native.catalog_entry(self.code, 'python')

    def test_redirected_dependency_directory_is_rejected(self):
        (self.base / 'elsewhere').mkdir()
        (self.base / 'checkout/dependencies').symlink_to(self.base / 'elsewhere')
        with self.assertRaisesRegex(ValueError, 'redirected'):
            native.catalog_entry(self.code, 'nats')

    def test_postgres_dependency_requires_a_complete_program_directory(self):
        root = self.base / 'pg'
        suffix = '.exe' if os.name == 'nt' else ''
        for program in ('initdb', 'postgres', 'pg_isready', 'psql'):
            (root / 'bin').mkdir(parents=True, exist_ok=True)
            (root / 'bin' / (program + suffix)).write_text('')
        with self.assertRaisesRegex(ValueError, 'createdb'):
            native.postgres_dependency(root)
        (root / 'bin' / ('createdb' + suffix)).write_text('')
        with self.assertRaisesRegex(ValueError, 'initialization files'):
            native.postgres_dependency(root)
        (root / 'share').mkdir()
        (root / 'share/postgres.bki').write_text('')
        self.assertEqual(native.postgres_dependency(root), {k: str(root / k) for k in ('bin', 'lib', 'share')})

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
        with self.assertRaisesRegex(RuntimeError, '安装包缺少'):
            native.download(entry, self.base / 'offline', offline=True)

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

    def test_failed_download_retries_without_adopting_unowned_programs(self):
        destination = Path(native.catalog_entry(self.code, 'nats')['directory'])
        with patch.object(native, 'download', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                native.prepare(self.code, ('nats',))
        self.assertFalse(destination.exists())
        archive = self.base / 'tool.tar'
        with tarfile.open(archive, 'w') as out:
            item = tarfile.TarInfo('package/' + ('nats-server.exe' if os.name == 'nt' else 'nats-server'))
            item.size, item.mode = 4, 0o755
            out.addfile(item, io.BytesIO(b'tool'))

        def fetch(entry, path, offline=False):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read_bytes())
            return path
        with patch.object(native, 'download', side_effect=fetch) as download, \
                patch.object(native, 'verify', return_value={'nats': 'fixture'}):
            native.prepare(self.code, ('nats',))
            native.prepare(self.code, ('nats',))
            download.assert_called_once()
        receipt = destination / 'tfi-native-dependency.json'
        self.assertEqual(json.loads(receipt.read_text())['status'], 'verified')
        receipt.unlink()
        with self.assertRaisesRegex(ValueError, 'unowned'):
            native.prepare(self.code, ('nats',))

    def test_host_mismatch_fails_before_native_download_or_root_creation(self):
        root = self.base / 'instance'
        (self.base / 'manifest.json').write_text('{}')
        with patch.object(installer, 'validate_engineering', return_value=({}, self.base)), \
                patch.object(installer, 'platform_preflight', side_effect=ValueError('wrong OS')), \
                patch.object(native, 'download') as download:
            with self.assertRaisesRegex(ValueError, 'wrong OS'):
                installer.create(self.base / 'manifest.json', name='fixture', root=root)
            download.assert_not_called()
        self.assertFalse(root.exists())

    def test_probe_failure_does_not_report_verified(self):
        entry = native.catalog_entry(self.code, 'postgres')
        failed = subprocess.CompletedProcess([], 1, '', 'missing library')
        with patch.object(native, 'run_native_capture', return_value=failed) as run:
            with self.assertRaisesRegex(RuntimeError, 'cannot run'):
                native.verify(entry)
        environment = run.call_args.kwargs['env']
        if os.name != 'nt':
            self.assertIn(str(Path(entry['directory']) / 'lib'), environment.values())


if __name__ == '__main__':
    unittest.main()
