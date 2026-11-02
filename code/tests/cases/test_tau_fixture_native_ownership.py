"""Owned TAU directories use native ownership checks on every platform."""
from pathlib import Path
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from management_portal.test_framework import tau_fixture_paths as fixtures


class NativeFixtureOwnership(unittest.TestCase):
    def test_standalone_can_be_registered_when_temp_is_instance_runtime(self):
        from management_common import instance_locations
        from management_common.instance_manifest import InstanceManifest
        with tempfile.TemporaryDirectory() as temporary:
            instance = InstanceManifest(Path(temporary).resolve() / 'instance', {})
            runtime = instance.directory('runtime') / 'tmp'
            runtime.mkdir(parents=True)
            instance.area('features').mkdir(parents=True)
            evidence = instance.directory('runtime') / 'case'
            with patch.object(instance_locations, 'current_instance', return_value=instance), \
                    patch.object(fixtures.tempfile, 'gettempdir', return_value=str(runtime)):
                path = fixtures.prepare(evidence)
                self.assertTrue(instance.reference(path))
                self.assertFalse(fixtures._inside_checkout(path))
                self.assertEqual(fixtures.prepare(evidence), path)
                fixtures.cleanup(evidence)
                self.assertFalse(path.exists())

    def test_content_reuse_and_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, content = base / 'evidence', base / 'content'
            with patch.object(fixtures, 'fixture_root', return_value=content):
                self.assertEqual(fixtures.prepare_content(root), content)
                self.assertEqual(fixtures.prepare_content(root), content)
                self.assertEqual(fixtures.inspect_content(root), content)
                content.rename(base / 'original')
                content.mkdir(mode=0o700)
                (content / 'keep').write_text('replacement')
                with self.assertRaisesRegex(ValueError, 'replaced'):
                    fixtures.inspect_content(root)
                self.assertTrue((content / 'keep').exists())

    def test_standalone_reuse_and_owned_cleanup(self):
        from management_common import instance_locations
        from management_common.instance_manifest import InstanceManifest
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / 'evidence'
            with patch.object(fixtures, '_bases', return_value=[base]), \
                    patch.object(instance_locations, 'current_instance', return_value=InstanceManifest(base / 'instance', {})), \
                    patch.object(fixtures, '_inside_checkout', return_value=False):
                path = fixtures.prepare(root)
                # Git objects are read-only on Windows. Deleting one link must
                # not make another link to the same object writable.
                original = root / 'retained-object'
                original.write_text('owned')
                os.link(original, path / 'artifact')
                original.chmod(stat.S_IREAD)
                self.assertEqual(fixtures.prepare(root), path)
                self.assertEqual(fixtures.cleanup(root), [str(path)])
                self.assertFalse(path.exists())
                self.assertTrue(root.exists())
                self.assertEqual(original.read_text(), 'owned')
                if os.name == 'nt':
                    self.assertTrue(original.stat().st_file_attributes & 0x1)
                original.chmod(stat.S_IREAD | stat.S_IWRITE)


if __name__ == '__main__':
    unittest.main()
