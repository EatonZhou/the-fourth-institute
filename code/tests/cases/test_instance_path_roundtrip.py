"""Stored host paths use forward slashes and remain canonical on Windows."""
import os
from pathlib import Path
import tempfile
import unittest

from management_common.instance_manifest import InstanceManifest, absolute_path


class CanonicalPathRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.manifest = InstanceManifest(self.root / 'instance', {})

    def test_external_reference_round_trip(self):
        path = self.root / 'external' / 'prospective.txt'
        reference = self.manifest.reference(path)
        self.assertEqual(reference, path.as_posix())
        self.assertEqual(self.manifest.resolve(reference), path)

    def test_native_and_portable_paths_resolve_identically(self):
        self.assertEqual(absolute_path(str(self.root)), self.root)
        self.assertEqual(absolute_path(self.root.as_posix()), self.root)

    def test_noncanonical_spellings_stay_rejected(self):
        for suffix in ('/../other', '//other', '/./other', '/'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                absolute_path(self.root.as_posix() + suffix)

    @unittest.skipUnless(os.name == 'nt', 'Windows separator contract')
    def test_mixed_separators_stay_rejected(self):
        with self.assertRaises(ValueError):
            absolute_path(str(self.root) + '/child')


if __name__ == '__main__':
    unittest.main()
