"""Owned TAU directories use native ownership checks on every platform."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from management_portal.test_framework import tau_fixture_paths as fixtures


class NativeFixtureOwnership(unittest.TestCase):
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
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / 'evidence'
            with patch.object(fixtures, '_bases', return_value=[base]), \
                    patch.object(fixtures, '_inside_checkout', return_value=False):
                path = fixtures.prepare(root)
                (path / 'artifact').write_text('owned')
                self.assertEqual(fixtures.prepare(root), path)
                self.assertEqual(fixtures.cleanup(root), [str(path)])
                self.assertFalse(path.exists())
                self.assertTrue(root.exists())


if __name__ == '__main__':
    unittest.main()
