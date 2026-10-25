"""App-local CRT integrity and offline installation.

:module: operations.deployment
:design: decision_15aab18c0ab3
"""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.windows import msvc_runtime as crt
from scripts import tfi_release_native_dependencies as native


class WindowsCrtTests(unittest.TestCase):
    def test_members_verified_before_install_and_retry_is_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pg = root / 'postgres'
            (pg / 'bin').mkdir(parents=True)
            archive = root / 'fixture.exe'
            archive.write_bytes(b'MSCFpayloadMSCFlicense')
            content = {'vcruntime140.dll_amd64': b'CRT', 'u4': b'license'}
            spec = {'version': 'test', 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                    'cabinets': {'payload': {'offset': 0, 'size': 11}, 'license': {'offset': 11, 'size': 11}},
                    'architectures': {'x86_64': {'cabinet_member': 'a12', 'files': {
                        'vcruntime140.dll': {'member': 'vcruntime140.dll_amd64',
                            'sha256': hashlib.sha256(b'CRT').hexdigest()}}}},
                    'license': {'member': 'u4', 'sha256': hashlib.sha256(b'license').hexdigest()}}
            # Cabinet extraction is a Windows primitive; integrity/atomic placement
            # and retry exercise production code with real files.
            def expand(args, **kw):
                self.assertEqual(Path(args[0]).name, 'expand.exe')
                member = args[2][3:]
                (Path(args[3]) / member).write_bytes(content.get(member, b'cabinet'))
                return SimpleNamespace(returncode=0)
            entry = {'directory': str(pg), 'platform': 'windows/x86_64'}
            with patch.object(crt, 'specification', return_value=spec), \
                    patch.object(crt, 'download', return_value=archive) as download:
                target = {'install_root': str(root), 'offline_dependencies': True}
                content['u4'] = b'corrupted'
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    crt.prepare(SimpleNamespace(run_native=expand), target, entry)
                self.assertFalse((pg / 'bin/vcruntime140.dll').exists())
                content['u4'] = b'license'
                receipt = crt.prepare(SimpleNamespace(run_native=expand), target, entry)
                self.assertTrue(download.call_args.kwargs['offline'])
                self.assertEqual((pg / 'bin/vcruntime140.dll').read_bytes(), b'CRT')
                download.side_effect = AssertionError('retry must reuse verified files')
                self.assertEqual(crt.prepare(None, target, entry), receipt)

    def test_missing_archive_stays_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            pg = Path(tmp) / 'pg'
            (pg / 'bin').mkdir(parents=True)
            with patch.object(native, 'urlopen', side_effect=AssertionError('network')):
                with self.assertRaisesRegex(RuntimeError, '缺少内置依赖'):
                    crt.prepare(None, {'install_root': tmp, 'offline_dependencies': True},
                                {'directory': str(pg), 'platform': 'windows/x86_64'})

    def test_native_failure_keeps_windows_status(self):
        api = SimpleNamespace(run_native=lambda *a, **kw: SimpleNamespace(returncode=0xc0000135, stdout=''))
        with self.assertRaisesRegex(RuntimeError, '0xC0000135.*DLL missing'):
            native.verify(api, {}, {'directory': '.', 'name': 'postgres', 'platform': 'windows/x86_64'})


if __name__ == '__main__':
    unittest.main()
