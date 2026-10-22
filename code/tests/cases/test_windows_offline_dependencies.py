"""Windows initial setup must succeed from bundled wheels or fail without networking.

:module: operations.deployment
:design: decision_d88306d0bd17
"""
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from scripts.windows import python_dependencies as wheels
from scripts import tfi_release_dependencies as dependencies
from scripts import tfi_release_native_dependencies as native
from scripts import tfi_windows_launcher as launcher

CODE = Path(__file__).resolve().parents[2]


class OfflineDependencies(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='离线 wheels ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'code'
        self.source.mkdir()
        (self.source / 'requirements.txt').write_text('example\n')
        self.cache = self.root / 'wheels'
        self.cache.mkdir()
        self.lock = self.source / 'scripts/windows/requirements-core.lock'
        self.lock.parent.mkdir(parents=True)
        self.lock.write_text('')

    def wheel(self, name, requires='', tag='py3-none-any'):
        path = self.cache / f'{name}-1.0-{tag}.whl'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(f'{name}-1.0.dist-info/METADATA',
                             f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\nRequires-Python: >=3.9\n{requires}')
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.lock.open('a') as stream:
            stream.write(f'{name}==1.0 --hash=sha256:{sha}\n')
        return path

    def test_cross_build_checks_windows_markers_and_current_roots(self):
        self.wheel('example', 'Requires-Dist: colorama; sys_platform == "win32"\n'
                   'Requires-Dist: linux_only; sys_platform == "linux"\n')
        with self.assertRaisesRegex(ValueError, 'colorama'):
            wheels.validate(self.source, self.cache, self.lock)
        self.wheel('colorama')
        self.assertEqual(len(wheels.validate(self.source, self.cache, self.lock)), 2)
        (self.source / 'requirements.txt').write_text('example>=2\n')
        with self.assertRaisesRegex(ValueError, 'compatible dependency'):
            wheels.validate(self.source, self.cache, self.lock)

    def test_incompatible_and_damaged_wheels_block_build(self):
        path = self.wheel('example', tag='cp311-cp311-win_amd64')
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            wheels.validate(self.source, self.cache, self.lock)
        path.write_bytes(path.read_bytes() + b'damage')
        with self.assertRaisesRegex(ValueError, 'lock'):
            wheels.validate(self.source, self.cache, self.lock)

    def test_valid_cache_builds_complete_payload_without_download(self):
        wheel = self.wheel('example')
        with patch.object(wheels.subprocess, 'run', side_effect=AssertionError('network')):
            receipt = wheels.bundle(self.root, self.root / 'payload', self.cache)
        payload = self.root / 'payload' / receipt['path']
        self.assertEqual((payload / wheel.name).read_bytes(), wheel.read_bytes())
        self.assertEqual((payload / 'requirements.lock').read_bytes(), self.lock.read_bytes())

    def test_launcher_enforces_offline_receipt_for_both_case_choices(self):
        (self.root / 'windows-package.json').write_text(json.dumps({'initialization_requires_network': False}))
        for included in (True, False):
            target = launcher.make_target(self.root, 'example_instance', {'run_id': 'test', 'include_examples': included},
                                          ports={'web': 28199})
            self.assertTrue(target['offline_dependencies'])
            self.assertTrue(target['bundled_python_dependencies'])

    def test_bootstrap_ignores_operator_pip_targets_and_uses_local_lock(self):
        script = runpy.run_path(str(CODE / 'scripts/bootstrap_portal'))
        environment = self.root / 'venv'
        python = script['venv_python'](environment)
        python.parent.mkdir(parents=True)
        python.touch()
        (self.cache / 'requirements.lock').write_text('example==1.0\n')
        calls = []
        globals_ = script['install'].__globals__
        with patch.dict(globals_, interpreter_version=lambda _: (3, 12, 15),
                        run_process=lambda command, **kw: calls.append((command, kw))), \
                patch.dict(os.environ, {'PIP_TARGET': '/do-not-touch', 'PIP_INDEX_URL': 'https://invalid.example'}):
            result = script['main'](['install', 'core', '--venv', str(environment), '--wheelhouse', str(self.cache)])
        self.assertEqual(result, 0)
        command, options = calls[0]
        for argument in ('-I', '--isolated', '--no-index', '--require-hashes', '--only-binary=:all:'):
            self.assertIn(argument, command)
        self.assertNotIn('PIP_TARGET', options['env'])
        self.assertNotIn('PIP_INDEX_URL', options['env'])
        self.assertEqual(options['env']['PIP_CONFIG_FILE'], os.devnull)
        self.assertIn('check', calls[1][0])
        self.assertTrue(all(command[0] == str(python) for command, _ in calls))
        self.assertTrue(all(command[command.index('-X') + 1] == 'utf8' for command, _ in calls))
        calls.clear()
        def failed(*args, **kwargs):
            calls.append(args[0])
            raise subprocess.CalledProcessError(1, args[0])
        with patch.dict(globals_, interpreter_version=lambda _: (3, 12, 15), run_process=failed):
            self.assertEqual(script['main'](['install', 'core', '--venv', str(environment),
                                            '--wheelhouse', str(self.cache)]), 2)
        self.assertEqual(len(calls), 1)  # No online retry and no successful follow-up.

    def test_initialization_consumes_local_wheels_and_retries_reuse_environment(self):
        root = self.root / 'instance/example_instance'
        (root / 'runtime').mkdir(parents=True)
        target = {'install_root': str(root), 'shared_code_root': str(self.source), 'python': 'bundled-python.exe',
                  'offline_dependencies': True, 'bundled_python_dependencies': True}
        state = {'target': target, 'manifest_sha256': 'fixture'}
        wheelhouse = self.root / 'dependencies/python-wheels'
        wheelhouse.mkdir(parents=True)
        (wheelhouse / 'requirements.lock').write_text('')
        python = self.root / 'dependencies/fixture/venv/Scripts/python.exe'
        calls = []
        def doctor(*args, **kw):
            return SimpleNamespace(returncode=0 if python.exists() else 1,
                                   stdout=json.dumps([{'ok': True}]))
        def install(command, **kwargs):
            calls.append(command)
            self.assertEqual(command[-2:], ['--wheelhouse', str(wheelhouse)])
            python.parent.mkdir(parents=True)
            python.touch()
            return SimpleNamespace(returncode=0)
        api = SimpleNamespace(run_utf8_capture=doctor, run_process=install, clean_environment=lambda _: {},
                              native_tool=lambda _: 'python.exe', save_stage=lambda *args: None)
        with patch.object(dependencies, 'profiles', return_value=['core']), \
                patch.object(dependencies, 'environment_key', return_value='fixture'):
            dependencies._prepare(api, state, {})
            self.assertEqual(state['dependencies']['source'], 'bundled-offline-wheels')
            dependencies._prepare(api, state, {})
            self.assertEqual(state['dependencies']['source'], 'verified-existing-installation')
            self.assertEqual(len(calls), 1)
            python.unlink()
            (wheelhouse / 'requirements.lock').unlink()
            with self.assertRaisesRegex(RuntimeError, '缺少离线'):
                dependencies._prepare(api, state, {})
            self.assertEqual(len(calls), 1)

    def test_missing_native_archive_never_downloads_in_offline_mode(self):
        entry = {'name': 'nats', 'version': 'test', 'sha256': 'a' * 64}
        with patch.object(native, 'urlopen', side_effect=AssertionError('network')):
            with self.assertRaisesRegex(RuntimeError, '缺少内置依赖'):
                native.download(entry, self.root / 'missing.archive', offline=True)


if __name__ == '__main__':
    unittest.main()
