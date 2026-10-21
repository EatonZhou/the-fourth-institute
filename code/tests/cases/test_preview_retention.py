"""Preview retention preserves a complete replacement before deleting history.

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import tfi_preview_retention as retention


def release(platform, number, *, draft=False, missing=None):
    contents = {}
    for arch, target in [('arm64', 'macos/aarch64'), ('x64', 'macos/x86_64')] if platform == 'macos' else [(None, None)]:
        filename = f'TheFourthInstitute-macOS-{arch}.dmg' if arch else 'TheFourthInstitute-Setup-x64.exe'
        key = 'dmg' if arch else 'exe'
        receipt_name = f'TheFourthInstitute-macOS-{arch}.json' if arch else 'receipt.json'
        record = {'source_commit': 'a' * 40, 'source_dirty': False, 'prepared_engineering_validation': 'passed',
                  key + '_bytes': 1, key + '_sha256': 'b' * 64,
                  'target': target, 'native_installation_smoke': 'passed: fixture'}
        contents[receipt_name] = json.dumps(record)
        contents[filename + '.sha256'] = 'b' * 64 + '  ' + filename + '\n'
    return {'id': number, 'tag_name': f'{platform}-preview-20261021.{number}',
            'draft': draft, 'prerelease': True, 'published_at': None if draft else '2026-10-21',
            'assets': [{'name': name, 'size': 1, 'state': 'uploaded', 'contents': contents.get(name, '')}
                       for name in retention.expected_assets(platform) if name != missing]}


class Client:
    def __init__(self, releases, tags=()):
        self.releases = releases
        self.tags = [{'name': name} for name in tags] + [{'name': row['tag_name']} for row in releases]
        self.deleted = []

    def get(self, path, **kwargs):
        if path.startswith('releases?'):
            return self.releases
        if path.startswith('tags?'):
            return self.tags
        return next(row for row in self.releases if str(row['id']) == path.split('/')[-1])

    def delete(self, path):
        self.deleted.append(path)

    def read_asset(self, asset):
        return asset['contents']


class PreviewRetentionTests(unittest.TestCase):
    def test_numeric_order_and_platform_boundary(self):
        rows = [release('windows', n) for n in (1, 9, 10)] + [release('macos', 8)]
        tags = [{'name': row['tag_name']} for row in rows]
        tags += [{'name': 'v1.0'}, {'name': 'windows-preview-testing'}, {'name': 'main'}]
        result = retention.plan('windows', rows, tags)
        self.assertEqual(result['keep'], 'windows-preview-20261021.10')
        self.assertEqual(result['delete_tags'], ['windows-preview-20261021.1', 'windows-preview-20261021.9'])
        self.assertEqual([row['id'] for row in result['delete_releases']], [1, 9])

    def test_date_takes_precedence_over_build_number(self):
        old, new = release('windows', 99), release('windows', 1)
        new['tag_name'] = 'windows-preview-20261022.1'
        self.assertEqual(retention.plan('windows', [new, old], [])['keep'], new['tag_name'])

    def test_each_macos_asset_is_required_and_new_incomplete_release_stays(self):
        for missing in retention.expected_assets('macos'):
            with self.subTest(missing=missing):
                client = Client([release('macos', 7), release('macos', 8), release('macos', 9, missing=missing)])
                result = retention.cleanup(client, 'macos')
                self.assertEqual(result['keep'], 'macos-preview-20261021.8')
                self.assertEqual(client.deleted, ['releases/7', 'git/refs/tags/macos-preview-20261021.7'])

    def test_partial_upload_empty_asset_and_draft_are_not_replacements(self):
        for field, value in [('state', 'starter'), ('size', 0)]:
            newest = release('windows', 3)
            newest['assets'][0][field] = value
            result = retention.plan('windows', [release('windows', 2), newest, release('windows', 4, draft=True)], [])
            self.assertEqual(result['keep'], 'windows-preview-20261021.2')
            self.assertEqual(result['delete_releases'], [])

    def test_no_complete_release_never_deletes(self):
        client = Client([release('macos', 1, missing='TheFourthInstitute-macOS-x64.dmg')])
        with self.assertRaisesRegex(ValueError, 'nothing deleted'):
            retention.cleanup(client, 'macos')
        self.assertEqual(client.deleted, [])

    def test_drafts_and_formal_releases_are_protected_but_orphan_tags_are_removed(self):
        draft = release('windows', 2, draft=True)
        formal = release('windows', 3)
        formal['prerelease'] = False
        client = Client([draft, formal, release('windows', 5)],
                        ['windows-preview-20261021.1', 'windows-preview-20261021.6', 'v1.0'])
        retention.cleanup(client, 'windows')
        self.assertEqual(client.deleted, ['git/refs/tags/windows-preview-20261021.1'])

    def test_dry_run_does_not_modify_releases_or_tags(self):
        client = Client([release('windows', 1), release('windows', 2)])
        retention.cleanup(client, 'windows', dry_run=True)
        self.assertEqual(client.deleted, [])

    def test_replacement_is_rechecked_before_any_deletion(self):
        client = Client([release('windows', 1), release('windows', 2)])
        original = client.get
        def changed(path, **kwargs):
            result = original(path, **kwargs)
            if path == 'releases/2':
                result = {**result, 'draft': True}
            return result
        client.get = changed
        with self.assertRaisesRegex(ValueError, 'nothing deleted'):
            retention.cleanup(client, 'windows')
        self.assertEqual(client.deleted, [])

    def test_failed_release_deletion_does_not_delete_its_tag(self):
        client = Client([release('windows', 1), release('windows', 2)])
        def fail(path):
            raise RuntimeError('permission denied')
        client.delete = fail
        with self.assertRaisesRegex(RuntimeError, 'permission denied'):
            retention.cleanup(client, 'windows')
        self.assertEqual(client.deleted, [])

    def test_invalid_native_receipt_or_checksum_cannot_replace_working_release(self):
        for field, value in [('source_dirty', True), ('native_installation_smoke', 'failed'),
                             ('target', 'macos/wrong'), ('source_commit', 'c' * 40), ('dmg_bytes', 42)]:
            with self.subTest(field=field):
                client = Client([release('macos', 8), release('macos', 9)])
                asset = next(row for row in client.releases[-1]['assets'] if row['name'] == 'TheFourthInstitute-macOS-x64.json')
                record = json.loads(asset['contents'])
                asset['contents'] = json.dumps({**record, field: value})
                with self.assertRaises(ValueError):
                    retention.cleanup(client, 'macos')
                self.assertEqual(client.deleted, [])
        client = Client([release('windows', 1), release('windows', 2)])
        next(row for row in client.releases[-1]['assets'] if row['name'].endswith('.sha256'))['contents'] = 'bad checksum'
        with self.assertRaisesRegex(ValueError, 'checksum'):
            retention.cleanup(client, 'windows')
        self.assertEqual(client.deleted, [])

    def test_api_fetches_all_pages_and_only_ignores_missing_deletions(self):
        client = retention.GitHub('owner/repo')
        with patch.object(retention.subprocess, 'run') as run:
            run.return_value.stdout = json.dumps([[{'id': 1}], [{'id': 2}]])
            self.assertEqual(client.get('releases?per_page=100', pages=True), [{'id': 1}, {'id': 2}])
            self.assertIn('--paginate', run.call_args.args[0])
            run.return_value.returncode = 1
            run.return_value.stderr = 'gh: Not Found (HTTP 404)'
            client.delete('releases/1')
            run.return_value.stderr = 'gh: Forbidden (HTTP 403)'
            with self.assertRaisesRegex(RuntimeError, 'Forbidden'):
                client.delete('releases/1')


if __name__ == '__main__':
    unittest.main()
