"""Keep the newest complete preview release and remove older releases and tags."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess


def version(tag, platform):
    match = re.fullmatch(rf'{platform}-preview-([0-9]{{8}})\.([0-9]+)', tag)
    return tuple(map(int, match.groups())) if match else None


def expected_assets(platform):
    if platform == 'windows':
        name = 'TheFourthInstitute-Setup-x64.exe'
        return {name, name + '.sha256', 'receipt.json'}
    if platform == 'macos':
        return {f'TheFourthInstitute-macOS-{arch}{suffix}'
                for arch in ('arm64', 'x64') for suffix in ('.dmg', '.dmg.sha256', '.json')}
    raise ValueError('Unsupported preview platform')


def complete(release, platform):
    if release.get('draft') or not release.get('prerelease') or not release.get('published_at'):
        return False
    uploaded = {asset['name'] for asset in release.get('assets', [])
                if asset.get('state') == 'uploaded' and asset.get('size', 0) > 0}
    return expected_assets(platform) <= uploaded


def verify_receipts(client, release, platform):
    """Verify uploaded build receipts and checksums without downloading installers."""
    assets = {asset['name']: asset for asset in release['assets']}
    builds = [('TheFourthInstitute-Setup-x64.exe', 'receipt.json', 'exe', None)]
    if platform == 'macos':
        builds = [(f'TheFourthInstitute-macOS-{arch}.dmg', f'TheFourthInstitute-macOS-{arch}.json',
                   'dmg', target) for arch, target in (('arm64', 'macos/aarch64'), ('x64', 'macos/x86_64'))]
    commits = set()
    for filename, receipt_name, key, target in builds:
        record = json.loads(client.read_asset(assets[receipt_name]))
        digest = record.get(key + '_sha256', '')
        commit = record.get('source_commit', '')
        if (record.get('source_dirty') is not False
                or record.get('prepared_engineering_validation') != 'passed'
                or not re.fullmatch(r'[0-9a-f]{40}', commit)
                or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or record.get(key + '_bytes') != assets[filename]['size']):
            raise ValueError('Replacement build receipt is invalid; nothing deleted')
        if target and (record.get('target') != target
                       or not record.get('native_installation_smoke', '').startswith('passed:')):
            raise ValueError('Replacement native macOS validation is missing; nothing deleted')
        checksum = client.read_asset(assets[filename + '.sha256']).strip()
        if checksum != digest + '  ' + filename:
            raise ValueError('Replacement checksum disagrees with receipt; nothing deleted')
        commits.add(commit)
    if len(commits) != 1:
        raise ValueError('Replacement architectures use different source commits; nothing deleted')


def plan(platform, releases, tags):
    """Only this platform's numbered previews are eligible for retention."""
    matching = [release for release in releases
                if version(release['tag_name'], platform) is not None]
    candidates = [release for release in matching if complete(release, platform)]
    if not candidates:
        raise ValueError(f'No complete published {platform} preview; nothing deleted')
    keep = max(candidates, key=lambda release: version(release['tag_name'], platform))
    boundary = version(keep['tag_name'], platform)
    old = sorted((release for release in matching
                  if version(release['tag_name'], platform) < boundary
                  and not release.get('draft') and release.get('prerelease')),
                 key=lambda release: version(release['tag_name'], platform))
    protected = {release['tag_name'] for release in matching if release not in old}
    # Releases and refs are separate API objects. Include orphan tags from a
    # previously interrupted cleanup, but never drafts or newer/incomplete tags.
    old_tags = sorted({tag['name'] for tag in tags
                       if version(tag['name'], platform) is not None
                       and version(tag['name'], platform) < boundary
                       and tag['name'] not in protected},
                      key=lambda tag: version(tag, platform))
    return {'platform': platform, 'keep': keep['tag_name'], 'keep_id': keep['id'],
            'delete_releases': [{'id': release['id'], 'tag': release['tag_name']} for release in old],
            'delete_tags': old_tags}


class GitHub:
    def __init__(self, repo):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ValueError('GH_REPO must be owner/repository')
        self.base = 'repos/' + repo + '/'

    def get(self, path, *, pages=False):
        command = ['gh', 'api', self.base + path]
        if pages:
            command += ['--paginate', '--slurp']
        response = subprocess.run(command, check=True, stdout=subprocess.PIPE, text=True)
        value = json.loads(response.stdout)
        return [item for page in value for item in page] if pages else value

    def read_asset(self, asset):
        if not 0 < asset['size'] <= 65536:
            raise ValueError('Unexpected receipt/checksum size; nothing deleted')
        response = subprocess.run(['gh', 'api', self.base + 'releases/assets/' + str(asset['id']),
                                   '-H', 'Accept: application/octet-stream'],
                                  check=True, stdout=subprocess.PIPE, text=True)
        return response.stdout

    def delete(self, path):
        response = subprocess.run(['gh', 'api', '--method', 'DELETE', self.base + path],
                                  capture_output=True, text=True)
        if response.returncode and '(HTTP 404)' not in response.stderr:
            raise RuntimeError('Cleanup failed: ' + response.stderr.strip())


def cleanup(client, platform, *, dry_run=False):
    result = plan(platform, client.get('releases?per_page=100', pages=True),
                  client.get('tags?per_page=100', pages=True))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if dry_run:
        return result
    # Recheck immediately before deletion: a removed/unpublished/incomplete
    # replacement must never cause removal of the remaining working version.
    keep = client.get('releases/' + str(result['keep_id']))
    if keep['tag_name'] != result['keep'] or not complete(keep, platform):
        raise ValueError('Replacement release changed; nothing deleted')
    verify_receipts(client, keep, platform)
    for release in result['delete_releases']:
        client.delete('releases/' + str(release['id']))
        print('Removed release and assets: ' + release['tag'], flush=True)
    for tag in result['delete_tags']:
        client.delete('git/refs/tags/' + tag)
        print('Removed tag: ' + tag, flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', required=True, choices=('windows', 'macos'))
    parser.add_argument('--repo', default=os.environ.get('GH_REPO', ''))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    cleanup(GitHub(args.repo), args.platform, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
