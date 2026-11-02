"""Atomic Workspace saves work while all directory ancestors stay pinned."""
import os
from pathlib import Path
import tempfile
import unittest
from management_common import secure_paths as paths


class AtomicWorkspaceFiles(unittest.TestCase):
    def test_new_file_and_replacement_under_directory_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with paths.open_directory(root) as directory, paths.lock_directory(directory):
                for content in (b'first version', b'replacement'):
                    fd = paths.open_child(directory, '.stage.tmp', os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                    with os.fdopen(fd, 'wb') as stream:
                        paths.prepare_child_permissions(directory, '.stage.tmp', 'document.txt', 0o664)
                        stream.write(content)
                    paths.replace_child(directory, '.stage.tmp', 'document.txt')
                    self.assertEqual((root / 'document.txt').read_bytes(), content)
                    self.assertFalse((root / '.stage.tmp').exists())
                if os.name == 'nt':
                    # Saving never relaxes the ancestor's no-write/no-delete pin.
                    with self.assertRaises(OSError):
                        fd = paths._windows_open(root, os.O_RDWR, directory=True)
                        os.close(fd)
                    with self.assertRaises(OSError):
                        root.rename(root.with_name(root.name + '-moved'))

    def test_failed_replacement_keeps_source_and_releases_handles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'directory').mkdir()
            (root / 'stage').write_bytes(b'original')
            with paths.open_directory(root) as directory:
                with self.assertRaises(OSError):
                    paths.replace_child(directory, 'stage', 'directory')
                self.assertEqual((root / 'stage').read_bytes(), b'original')
                paths.unlink_child(directory, 'stage')
            self.assertTrue((root / 'directory').is_dir())

    def test_replacement_cannot_escape_pinned_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'stage').write_bytes(b'original')
            with paths.open_directory(root) as directory:
                for name in ('../outside', 'nested/file', '.', '..'):
                    with self.assertRaises(ValueError):
                        paths.replace_child(directory, 'stage', name)
            self.assertEqual((root / 'stage').read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
