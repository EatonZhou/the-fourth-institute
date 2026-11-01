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

    def test_creation_uses_packaged_dependencies_for_either_case_choice(self):
        for included in (False, True):
            platform = self.root / str(included)
            bundle = platform / '.build/m/engineering/manifest.json'
            flow = launcher.MacStartup(platform, included=included)
            with patch.object(flow, 'registered', return_value=False), \
                    patch.object(flow, 'engineering', return_value=bundle), \
                    patch.object(flow, 'execute') as execute:
                flow.initialize()
            command = execute.call_args.args[0]
            self.assertIn('--offline', command)
            self.assertEqual(command[0], str(platform / 'dependencies/python/bin/python3'))

    def test_local_repair_reuses_environment_and_missing_payload_has_no_fallback(self):
        base_python = self.root / 'dependencies/python/bin/python3'
        wheelhouse = self.root / 'dependencies/python-wheels'
        wheelhouse.mkdir(parents=True)
        lock = wheelhouse / 'requirements.lock'
        lock.write_text('fixture generation')
        from management_common.code_environment import environment_directory, environment_key
        key = environment_key(self.source)
        lock.write_text('different generation')
        self.assertNotEqual(key, environment_key(self.source))  # The wheel lock selects the environment.
        lock.write_text('fixture generation')
        python = environment_directory(self.source) / 'bin/python'
        calls = []

        def doctor(*args, **kwargs):
            return SimpleNamespace(returncode=0 if python.exists() else 1, stdout=json.dumps([{'ok': True}]))

        def install(command, **kwargs):
            calls.append(command)
            self.assertEqual(command[0], str(base_python))
            self.assertEqual(command[-2:], ['--wheelhouse', str(wheelhouse)])
            python.parent.mkdir(parents=True)
            python.touch()
            return SimpleNamespace(returncode=0)
        with patch.object(dependencies, 'run_utf8_capture', side_effect=doctor), \
                patch.object(dependencies, 'run_process', side_effect=install):
            options = {'base_python': base_python, 'offline': True}
            self.assertEqual(dependencies.prepare(self.source, **options)['source'], 'bundled-offline-wheels')
            self.assertEqual(dependencies.prepare(self.source, **options)['source'], 'verified-existing')
            self.assertEqual(len(calls), 1)
            python.unlink()
            lock.unlink()
            with self.assertRaisesRegex(RuntimeError, '缺少离线'):
                dependencies.prepare(self.source, **options)
            self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
