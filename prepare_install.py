#!/usr/bin/env python3
"""Restore empty bundle directories omitted by Git and verify the release files."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent / 'release'
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for name, value in manifest['files'].items():
        path = Path(value)
        if path.is_absolute() or '..' in path.parts or path.parts != ('data', name):
            raise ValueError('Invalid data directory in manifest')
        (root / path).mkdir(parents=True, exist_ok=True)
    expected = {item['path']: item for item in manifest['inventory']}
    actual = {}
    for path in root.rglob('*'):
        if path.is_symlink() or path.name == '.git':
            raise ValueError('Release contains a symlink or Git metadata')
        if path.is_file() and path != manifest_path:
            relative = path.relative_to(root).as_posix()
            if relative not in expected:
                raise ValueError('Unlisted release file: ' + relative)
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            if digest.hexdigest() != expected[relative]['sha256'] or path.stat().st_size != expected[relative]['size']:
                raise ValueError('Release checksum mismatch: ' + relative)
            actual[relative] = True
    if actual.keys() != expected.keys():
        raise ValueError('Release files are missing')
    for name in ('accounts_root', 'secrets_dir'):
        if any((root / manifest['files'][name]).iterdir()):
            raise ValueError('Account and secret directories must be empty')
    print('v0.9 release verified. Continue with release/INSTALL.md.')


if __name__ == '__main__':
    main()
