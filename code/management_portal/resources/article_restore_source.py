"""Bounded recursive source snapshot through the existing ResourceService reader."""
from __future__ import annotations

import hashlib
import json

from .article_restore_preview import digest, relative_path

MAX_FILES = 256
MAX_BYTES = 32 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024


def snapshot(service, address, actor):
    files, manifest, directories = {}, [], []
    total = 0

    def walk(relative, depth):
        nonlocal total
        if depth > 12 or len(directories) >= 256:
            raise ValueError('Recovery source directory budget exceeded')
        current = address.rstrip('/') + ('/' + relative.replace('%', '%25') if relative else '')
        data, _mime, resolved = service.read_content(current, actor=actor, max_bytes=MAX_FILE)
        if len(data) > MAX_FILE:
            raise ValueError('Recovery source byte budget exceeded')
        if resolved.get('target_kind') != 'directory':
            raise ValueError('Recovery source is not a directory')
        listing = json.loads(data)
        if not isinstance(listing, dict) or listing.get('truncated') or listing.get('hidden_count'):
            raise ValueError('Recovery source listing is incomplete')
        entries = listing.get('entries')
        if not isinstance(entries, list) or len(entries) > 256:
            raise ValueError('Recovery source listing budget exceeded')
        directories.append(relative)
        seen = set()
        for entry in sorted(entries, key=lambda item: item['name']):
            name = entry['name']
            if len(relative_path(name).parts) != 1 or name in seen:
                raise ValueError('Recovery source contains an invalid or duplicate name')
            seen.add(name)
            child = relative + '/' + name if relative else name
            if entry.get('kind') in {'symlink', 'other'}:
                raise ValueError('Recovery source contains an unsupported filesystem object')
            if entry.get('kind') == 'directory':
                walk(child, depth + 1)
                continue
            if len(files) >= MAX_FILES:
                raise ValueError('Recovery source file budget exceeded')
            body, _mime, item = service.read_content(address.rstrip('/') + '/' + child.replace('%', '%25'),
                                                    actor=actor, max_bytes=MAX_FILE)
            total += len(body)
            if item.get('target_kind') != 'file' or len(body) > MAX_FILE or total > MAX_BYTES:
                raise ValueError('Recovery source file or byte budget exceeded')
            files[child] = body
            manifest.append({'path': child, 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()})

    walk('', 0)
    description = {'files': sorted(manifest, key=lambda item: item['path']), 'directories': sorted(directories)}
    return {'version': digest(description), 'manifest': description, 'bytes': total}, files


def prepare_files(stage, files, directories):
    # The caller owns this new stage inode through Creation. Paths came from
    # normalized service addresses; no filesystem source paths are reopened.
    for name in directories:
        if name:
            stage.joinpath(*relative_path(name).parts).mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        path = stage.joinpath(*relative_path(name).parts)
        with path.open('xb') as stream:
            stream.write(body)


def verify_prepared(root, expected):
    """Recheck the owned published tree through the no-follow content reader."""
    from types import SimpleNamespace
    from .providers import folder_files

    address = 'article_000000000000/file'
    directories = set(expected['manifest']['directories'])
    def read(location, *, actor, max_bytes):
        relative = location.removeprefix(address).lstrip('/').replace('%25', '%')
        resolved = {'exists': True, 'address': location, 'relative_path': relative,
                    'root_id': 'article_000000000000',
                    'target_kind': 'directory' if relative in directories else 'file'}
        return folder_files.read_content(resolved, root, max_bytes=max_bytes)
    actual, _ = snapshot(SimpleNamespace(read_content=read), address, '')
    if actual != expected:
        raise ValueError('Prepared recovery tree changed before commit')
