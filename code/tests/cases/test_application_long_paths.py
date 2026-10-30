"""Run directly with the shipped Python on Windows with LongPathsEnabled=0."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from management_common.filesystem_paths import filesystem_path as native, windows_extended_path
from management_common.directory_links import create_directory_link, remove_directory_link
from management_common.file_permissions import assert_private_file, atomic_write_private_bytes
from scripts import tfi_application_snapshot as snapshot


class PathSpellingTests(unittest.TestCase):
    def test_drive_unc_and_idempotence(self):
        for logical, extended in [
            ('C:\\app\\data\\file', '\\\\?\\C:\\app\\data\\file'),
            ('\\\\server\\share\\data', '\\\\?\\UNC\\server\\share\\data'),
        ]:
            self.assertEqual(windows_extended_path(logical), extended)
            self.assertEqual(windows_extended_path(extended), extended)


class LongPathSnapshotTests(unittest.TestCase):
    def test_inventory_backup_copy_restore_and_links(self):
        if os.name == 'nt':
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r'SYSTEM\CurrentControlSet\Control\FileSystem') as key:
                self.assertEqual(winreg.QueryValueEx(key, 'LongPathsEnabled')[0], 0,
                                 'Native acceptance requires LongPathsEnabled=0')
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            app = base / 'app'
            relative = '/'.join(['instance/example/data/staging', 'provider-' + 'x' * 70,
                                 'node_modules-' + 'y' * 70, 'vendor-' + 'z' * 70])
            directory = app / relative
            file = directory / 'codex-command-runner.exe'
            native(directory).mkdir(parents=True)
            native(file).write_bytes(b'local retained business bytes')
            self.assertGreater(len(str(file)), 260)
            if os.name == 'nt':
                with self.assertRaises(OSError):
                    file.lstat()
            alias = directory.parent / 'current'
            create_directory_link(alias, directory)
            inventory = snapshot.inventory(app)
            metadata = snapshot.inventory(app, content=False)
            name = file.relative_to(app).as_posix()
            self.assertEqual(metadata[name]['size'], 29)
            self.assertNotIn('sha256', metadata[name])
            self.assertEqual(inventory[alias.relative_to(app).as_posix()]['link'], relative)
            self.assertFalse(any('?' in key or '\\' in key for key in inventory))
            # The backup root itself is long, exercising private ancestor pins.
            backup_parent = base / ('backup-' + 'b' * 90) / ('parent-' + 'c' * 90) / ('more-' + 'd' * 70)
            for packed in (False, True):
                backup = backup_parent / ('packed' if packed else 'tree')
                native(backup.parent).mkdir(parents=True, exist_ok=True)
                expected = (snapshot.create_backup if packed else snapshot.copy_tree)(app, backup)
                self.assertEqual(expected, inventory)
                self.assertEqual(snapshot.inventory(app), expected)
                native(file).write_bytes(b'changed')
                extra = directory / 'failed-generation' / 'extra'
                native(extra.parent).mkdir()
                native(extra).write_text('new')
                remove_directory_link(alias)
                snapshot.restore(backup, app, expected, packed=packed)
                self.assertEqual(snapshot.inventory(app), expected)
                self.assertEqual(native(alias / file.name).read_bytes(), b'local retained business bytes')
                assert_private_file(file)
                if packed:
                    self.assertEqual(snapshot.backup_bytes(backup, name, expected), native(file).read_bytes())
                    blob = backup / 'files' / expected[name]['sha256']
                    native(blob).write_bytes(b'corrupt')
                    with self.assertRaisesRegex(ValueError, 'integrity'):
                        snapshot.restore(backup, app, expected, packed=True)
                    self.assertEqual(snapshot.inventory(app), expected)
            private = directory / 'private.json'
            atomic_write_private_bytes(private, b'one')
            atomic_write_private_bytes(private, b'two')
            assert_private_file(private)
            self.assertEqual(native(private).read_bytes(), b'two')
            # Extended spellings must not turn external links into internal ones.
            outside = base / 'outside'
            outside.mkdir()
            create_directory_link(directory / 'external', outside)
            with self.assertRaisesRegex(ValueError, 'External'):
                snapshot.inventory(app)
            snapshot.remove(app)
            self.assertTrue(outside.is_dir())
            snapshot.remove(backup_parent)


if __name__ == '__main__':
    unittest.main(verbosity=2)
