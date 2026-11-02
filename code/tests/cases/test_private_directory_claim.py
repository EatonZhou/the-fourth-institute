"""Private operation directories accept atomic claims while public ancestors stay pinned."""
from pathlib import Path
import os
import tempfile
import unittest

from management_common import secure_paths as fs


class PrivateDirectoryClaimTests(unittest.TestCase):
    def test_file_claim_preserves_content(self):
        self.claim('file')

    def test_directory_claim_preserves_content(self):
        self.claim('directory')

    def claim(self, kind):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / 'source'
            if kind == 'directory':
                source.mkdir()
                (source / 'data').write_bytes(b'owned resource')
            else:
                source.write_bytes(b'owned resource')
            before = source.stat()
            with fs.open_directory(root) as parent:
                with fs.private_child(parent, 'claim', create=True) as claim:
                    fs.rename_child_no_replace(parent, 'source', claim, 'entry')
                    after = fs.lstat_child(claim, 'entry')
                    self.assertTrue(os.path.samestat(before, after))
                    self.assertFalse(source.exists())
                    destination = root / 'claim/entry'
                    self.assertEqual((destination / 'data' if kind == 'directory' else destination).read_bytes(), b'owned resource')

    def test_existing_claim_entry_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / 'source'
            source.write_bytes(b'owned')
            with fs.open_directory(root) as parent:
                with fs.private_child(parent, 'claim', create=True) as claim:
                    target = root / 'claim/entry'
                    target.write_bytes(b'foreign')
                    before = target.stat()
                    with self.assertRaises(FileExistsError):
                        fs.rename_child_no_replace(parent, 'source', claim, 'entry')
                    self.assertTrue(os.path.samestat(before, target.stat()))
                    self.assertEqual(target.read_bytes(), b'foreign')
                    self.assertEqual(source.read_bytes(), b'owned')

    @unittest.skipUnless(os.name == 'nt', 'Windows directory sharing')
    def test_public_parent_still_rejects_write_handle(self):
        import ctypes
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with fs.open_directory(root):
                api = fs._windows_api()
                handle = api.CreateFileW(str(root), 0x40000000, 3, None, 3, 0x02200000, None)
                if handle != ctypes.c_void_p(-1).value:
                    api.CloseHandle(handle)
                    self.fail('Public ancestor unexpectedly allowed a write handle')
                self.assertEqual(ctypes.get_last_error(), 32)


if __name__ == '__main__':
    unittest.main()
