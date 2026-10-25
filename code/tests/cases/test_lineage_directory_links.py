"""Lineage folder aliases work without Windows symbolic-link privileges."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from management_common.directory_links import is_directory_link
from management_portal.society_service import lineage_storage as storage


class DirectoryLinks(unittest.TestCase):
    def test_native_bind_move_restore_and_unbind_preserve_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'Project 中文' / 'agent'
            source.mkdir(parents=True)
            (source / 'keep.txt').write_text('retained', encoding='utf-8')
            args = {'folder_root': root / 'Agents'}
            identity = 'lineage_123456abcdef'
            canonical = storage.bind_lineage_folder(identity, source, **args)
            self.assertTrue(is_directory_link(canonical))
            self.assertEqual(storage.bind_lineage_folder(identity, source, **args), canonical)
            target = root / 'Moved folder' / 'agent'
            storage.relocate_lineage_folder(identity, target, **args)
            self.assertEqual(canonical.resolve(), target.resolve())
            self.assertFalse(source.exists())
            self.assertEqual((canonical / 'keep.txt').read_text(), 'retained')
            storage.relocate_lineage_folder(identity, canonical, **args)
            self.assertFalse(is_directory_link(canonical))
            self.assertFalse(target.exists())
            storage.relocate_lineage_folder(identity, source, **args)
            with self.assertRaises(storage.LineageStorageConflictError):
                storage.unbind_lineage_folder(identity, expected_target=target, **args)
            storage.unbind_lineage_folder(identity, expected_target=source, **args)
            self.assertFalse(canonical.exists())
            self.assertEqual((source / 'keep.txt').read_text(), 'retained')
            self.assertEqual(sorted(p.name for p in (root / 'Agents').iterdir()), ['.lineage-folders.lock'])

    def test_failed_alias_switch_rolls_back_folder_move(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            (source / 'keep').write_text('value')
            args = {'folder_root': root / 'Agents'}
            identity = 'lineage_123456abcdef'
            canonical = storage.bind_lineage_folder(identity, source, **args)
            with patch.object(storage, 'replace_directory_link', side_effect=OSError('injected')):
                with self.assertRaisesRegex(OSError, 'injected'):
                    storage.relocate_lineage_folder(identity, root / 'target', **args)
            self.assertEqual(canonical.resolve(), source)
            self.assertEqual((source / 'keep').read_text(), 'value')
            self.assertFalse((root / 'target').exists())
            storage.unbind_lineage_folder(identity, **args)


if __name__ == '__main__':
    unittest.main()
