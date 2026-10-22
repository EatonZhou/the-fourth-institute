"""Native macOS wheel selection and local-only first-launch dependency recovery.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from scripts.windows import python_dependencies as wheels
from scripts import tfi_macos_launcher as launcher
from scripts import tfi_release_dependencies as dependencies


class MacOfflineDependencies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='Mac 离线 ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'code'
        self.source.mkdir()
        (self.source / 'requirements.txt').write_text('example\n')
        (self.source / 'scripts').mkdir()
        (self.source / 'scripts/bootstrap_portal').write_text('fixture')
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.rows = []

    def wheel(self, name, requires='', tag='py3-none-any'):
        path = self.cache / f'{name}-1.0-{tag}.whl'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(f'{name}-1.0.dist-info/METADATA',
                             f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\nRequires-Python: >=3.9\n{requires}')
        self.rows.append(f'{name}==1.0 --hash=sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}\n')
        return path

    def lock(self, target):
        path = self.source / wheels.target_settings(target)[3]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(self.rows))
        return path

    def test_macos_markers_extras_and_universal_wheel(self):
        self.wheel('example', 'Requires-Dist: native[extra]; sys_platform == "darwin"\n'
                   'Requires-Dist: windows_only; sys_platform == "win32"\n', tag='cp312-cp312-macosx_11_0_universal2')
        for target in ('macos/aarch64', 'macos/x86_64'):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'native'):
                wheels.validate(self.source, self.cache, self.lock(target), target=target)
        self.wheel('native', 'Requires-Dist: extra_dep; extra == "extra"\n')
        with self.assertRaisesRegex(ValueError, 'extra_dep'):
            wheels.validate(self.source, self.cache, self.lock('macos/aarch64'), target='macos/aarch64')
        self.wheel('extra_dep')
        for target in ('macos/aarch64', 'macos/x86_64'):
            self.assertEqual(len(wheels.validate(self.source, self.cache, self.lock(target), target=target)), 3)

    def test_wrong_cpu_and_abi_rejected_before_packaging(self):
        path = self.wheel('example', tag='cp312-cp312-macosx_11_0_arm64')
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            wheels.validate(self.source, self.cache, self.lock('macos/x86_64'), target='macos/x86_64')
        path.unlink()
        self.rows.clear()
        self.wheel('example', tag='cp311-cp311-macosx_11_0_arm64')
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            wheels.validate(self.source, self.cache, self.lock('macos/aarch64'), target='macos/aarch64')

    def test_cached_native_bundle_uses_architecture_lock_without_downloading(self):
        self.wheel('example', tag='cp312-cp312-macosx_11_0_arm64')
        target = 'macos/aarch64'
        lock = self.lock(target)
        with patch.object(wheels.subprocess, 'run', side_effect=AssertionError('unexpected download')):
            receipt = wheels.bundle(self.root, self.root / 'payload', self.cache, target=target)
        self.assertEqual(receipt['target'], 'cp312-macosx_15_0_arm64')
        self.assertEqual(receipt['lock_sha256'], hashlib.sha256(lock.read_bytes()).hexdigest())
        self.assertEqual((self.root / 'payload' / receipt['path'] / 'requirements.lock').read_bytes(), lock.read_bytes())

    def test_target_uses_packaged_dependencies_for_either_case_choice(self):
        for included in (False, True):
            platform = self.root / str(included)
            manifest = platform / '.build/example_instance-macos/engineering/manifest.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"run_id":"fixture"}')
            (platform / 'macos-package.json').write_text(json.dumps({
                'bundled_python_dependencies': {'lock_sha256': 'fixture'}}))
            flow = launcher.MacStartup(platform, included=included)
            with patch.object(launcher, 'unpack_seed'), patch.object(flow, 'execute'):
                flow.initialize()
            target = json.loads((platform / '.build/macos/target.json').read_text())
            self.assertTrue(target['offline_dependencies'])
            self.assertTrue(target['bundled_python_dependencies'])

    def test_local_repair_reuses_environment_and_missing_payload_has_no_fallback(self):
        root = self.root / 'instance/desktop'
        (root / 'runtime').mkdir(parents=True)
        target = {'install_root': str(root), 'shared_code_root': str(self.source),
                  'python': str(self.root / 'dependencies/python/bin/python3'),
                  'offline_dependencies': True, 'bundled_python_dependencies': True}
        state = {'target': target, 'manifest_sha256': 'fixture'}
        wheelhouse = self.root / 'dependencies/python-wheels'
        wheelhouse.mkdir(parents=True)
        lock = wheelhouse / 'requirements.lock'
        lock.write_text('fixture generation')
        key = dependencies.environment_key(target, ['core'])
        lock.write_text('different generation')
        self.assertNotEqual(key, dependencies.environment_key(target, ['core']))
        lock.write_text('fixture generation')
        python = self.root / 'dependencies' / key / 'venv/bin/python'
        calls = []
        def doctor(*args, **kwargs):
            return SimpleNamespace(returncode=0 if python.exists() else 1,
                                   stdout=json.dumps([{'ok': True}]))
        def install(command, **kwargs):
            calls.append(command)
            self.assertEqual(command[0], target['python'])
            self.assertEqual(command[-2:], ['--wheelhouse', str(wheelhouse)])
            python.parent.mkdir(parents=True)
            python.touch()
            return SimpleNamespace(returncode=0)
        api = SimpleNamespace(run_utf8_capture=doctor, run_process=install, clean_environment=lambda _: {},
                              native_tool=lambda _: 'python', save_stage=lambda *args: None)
        with patch.object(dependencies, 'profiles', return_value=['core']):
            dependencies._prepare(api, state, {})
            self.assertEqual(state['dependencies']['source'], 'bundled-offline-wheels')
            dependencies._prepare(api, state, {})
            self.assertEqual(len(calls), 1)
            self.assertEqual(state['dependencies']['source'], 'verified-existing-installation')
            python.unlink()
            lock.unlink()
            with self.assertRaisesRegex(RuntimeError, '缺少离线'):
                dependencies._prepare(api, state, {})
            self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
