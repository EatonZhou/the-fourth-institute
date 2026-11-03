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
            pg = root / '中文 空格 postgres'
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
                self.assertFalse(Path(args[1]).is_absolute())
                self.assertFalse(Path(args[3]).is_absolute())
                self.assertTrue(args[1].isascii() and args[3].isascii())
                member = args[2][3:]
                (Path(kw['cwd']) / args[3] / member).write_bytes(content.get(member, b'cabinet'))
                return SimpleNamespace(returncode=0)
            directory = pg / 'bin'
            with patch.object(crt, 'specification', return_value=spec), \
                    patch.object(crt, 'run_native_capture', side_effect=expand), \
                    patch.object(crt, 'download', return_value=archive) as download:
                content['u4'] = b'corrupted'
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    crt.prepare(root, directory, 'x86_64', offline=True)
                self.assertFalse((pg / 'bin/vcruntime140.dll').exists())
                content['u4'] = b'license'
                receipt = crt.prepare(root, directory, 'x86_64', offline=True)
                self.assertTrue(download.call_args.kwargs['offline'])
                self.assertEqual(download.call_args.args[1].parent, root / 'archives')
                self.assertEqual((pg / 'bin/vcruntime140.dll').read_bytes(), b'CRT')
                download.side_effect = AssertionError('retry must reuse verified files')
                self.assertEqual(crt.prepare(root, directory, 'x86_64', offline=True), receipt)

    def test_missing_archive_stays_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            pg = Path(tmp) / 'pg'
            (pg / 'bin').mkdir(parents=True)
            with patch.object(native, 'urlopen', side_effect=AssertionError('network')):
                with self.assertRaisesRegex(RuntimeError, '缺少内置依赖'):
                    crt.prepare(Path(tmp), pg / 'bin', 'x86_64', offline=True)

    def test_native_failure_keeps_windows_status(self):
        failed = SimpleNamespace(returncode=0xc0000135, stdout='')
        with patch.object(native, 'run_native_capture', return_value=failed), \
                self.assertRaisesRegex(RuntimeError, '0xC0000135.*DLL missing'):
            native.verify({'directory': '.', 'name': 'postgres', 'platform': 'windows/x86_64', 'version': '16'})


if __name__ == '__main__':
    unittest.main()
