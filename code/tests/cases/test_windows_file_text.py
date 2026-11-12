"""UTF-8 file handoffs and Git worktree pointers under legacy Windows locales."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from management_portal.quality_review_transfer import TransferStore
from management_portal.resources.creation_git import GitWorktreeCreation
from management_portal.resources.creation import _identity


class WindowsFileTextTests(unittest.TestCase):
    def test_transfer_manifest_unicode(self):
        content = '中文 English café 🧪'.encode('utf-8')
        checksum = hashlib.sha256(content).hexdigest()
        address = 'workspace-fixture/file/资料 English 🧪.txt'
        resources = SimpleNamespace(resolve=lambda *a, **k: {'size': len(content)},
            read_content=lambda *a, **k: (content, 'text/plain', {}))
        write = Path.write_text
        with tempfile.TemporaryDirectory() as temporary, patch.object(Path, 'write_text',
                lambda self, data, encoding=None, errors=None, **kw:
                    write(self, data, encoding=encoding or 'cp1252', errors=errors, **kw)):
            store = TransferStore(temporary, resources)
            manifest = store.freeze('review-fixture', [{'address': address, 'sha256': checksum}], 'actor')
            stored = json.loads((Path(temporary)/'review-fixture-subject/manifest.json').read_bytes())
            self.assertEqual(stored, manifest)
            self.assertEqual(stored['entries'][0]['name'], '资料 English 🧪.txt')
            self.assertEqual(store.read('review-fixture', manifest, checksum)[0], content)
            self.assertEqual(store.freeze('review-fixture', [{'address': address, 'sha256': checksum}], 'actor'), manifest)

    def test_git_creation_unicode_pointers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repo, stage = root/'仓库 English', root/'交接 English'
            def git(*args):
                return subprocess.run(['git', *map(str,args)], check=True, capture_output=True, encoding='utf-8').stdout.strip()
            git('init', repo)
            git('-C', repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'fixture')
            git('-C', repo, 'worktree', 'add', '--detach', stage)
            from management_common.directory_paths import cleanup_location, cleanup_path
            artifact = {'stage': cleanup_location(stage), 'path': cleanup_location(root/'target'),
                'identity': _identity(stage), 'git_dependency': {'repo': cleanup_location(repo), 'command_timeout': 30}}
            read = Path.read_text
            with patch.object(Path, 'read_text', lambda self, encoding=None, errors=None:
                    read(self, encoding=encoding or 'cp1252', errors=errors)):
                owner = GitWorktreeCreation(artifact, lambda: None, 'fixture', check=lambda: None)
                owner.capture_admin()
            admin = cleanup_path(artifact['git_dependency']['admin'])
            self.assertEqual(admin.parent, repo/'.git/worktrees')
            # Slash spelling must not hide an unrelated worktree backlink.
            artifact['git_dependency'].pop('admin')
            artifact['git_dependency'].pop('admin_identity')
            (admin/'gitdir').write_text(str(root/'foreign/.git'), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'association changed'):
                owner.capture_admin()


if __name__ == '__main__':
    unittest.main()
