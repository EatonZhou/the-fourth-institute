"""Targeted process-record repair preserves installed state.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import tfi_windows_launcher as launcher
from scripts.windows import repair_services as repair


class ServiceRepair(unittest.TestCase):
    def test_patch_is_idempotent_and_preserves_database_manifest_and_process_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            platform = Path(tmp)
            root = platform / 'instance/example_instance'
            root.mkdir(parents=True)
            (root / 'installation.json').write_text(json.dumps({'target': {
                'shared_code_root': str(platform / 'code'), 'install_root': str(root)}}))
            receipt = platform / 'windows-package.json'
            receipt.write_text(json.dumps({'source_commit': repair.VERSIONS[-1]}))
            process = root / 'runtime/nats.process.json'
            process.parent.mkdir()
            process.write_text('{"pid":123,"created":456}')
            data = root / 'database.bin'
            data.write_bytes(b'keep database')
            manifest = platform / '.build/w/engineering/manifest.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_bytes(b'original manifest')
            target = platform / 'code/scripts/tfi_windows_user_services.py'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'fixture old code')
            payload = Path(__file__).resolve().parents[2] / 'scripts/tfi_windows_user_services.py'
            before = {p: p.read_bytes() for p in (root / 'installation.json', receipt, process, data, manifest)}
            with patch.object(launcher, 'launcher_lock', return_value=nullcontext()), \
                    patch.object(launcher, 'stop_all') as stop, \
                    patch.object(repair, 'OLD_SHA', hashlib.sha256(target.read_bytes()).hexdigest()):
                self.assertEqual(repair.repair(platform, payload), 'repaired')
                self.assertEqual(repair.repair(platform, payload), 'already repaired')
                stop.assert_not_called()
                target.write_bytes(b'operator modified')
                with self.assertRaisesRegex(ValueError, 'modified'):
                    repair.repair(platform, payload)
                self.assertEqual(target.read_bytes(), b'operator modified')
            self.assertTrue(all(p.read_bytes() == contents for p, contents in before.items()))
            self.assertEqual((platform / '.build/windows/repairs/process-records/services.before.py').read_bytes(), b'fixture old code')

    def test_bad_payload_does_not_touch_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp) / 'bad.py'
            payload.write_text('wrong')
            with self.assertRaisesRegex(ValueError, 'payload'):
                repair.repair(tmp, payload)
            self.assertEqual(list(Path(tmp).iterdir()), [payload])


if __name__ == '__main__':
    unittest.main()
