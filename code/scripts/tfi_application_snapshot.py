"""Private offline application snapshots, including internal directory aliases."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import re

from management_common.directory_links import is_directory_link, create_directory_link, remove_directory_link
from management_common.file_permissions import create_private_file, ensure_private_directory


LOCKS = {".install.lock", ".configuration.lock"}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root, *, content=True):
    root = Path(root).absolute()
    result = {}
    def visit(parent):
        for path in sorted(parent.iterdir()):
            if path.name in LOCKS or path.relative_to(root).as_posix() == '.build/windows/launcher.lock':
                continue
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if is_directory_link(path):
                target = path.resolve(strict=True)
                if not target.is_relative_to(root) or not target.is_dir():
                    raise ValueError(f"External or non-directory link cannot be backed up: {relative}")
                result[relative] = {"link": target.relative_to(root).as_posix(),
                                    "kind": "junction" if getattr(path, 'is_junction', lambda: False)() else "symlink"}
            elif getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError(f"Unsupported reparse point: {relative}")
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = {"directory": True}
                visit(path)
            elif stat.S_ISREG(info.st_mode):
                result[relative] = {"size": info.st_size, **(
                    {"sha256": digest(path)} if content else {"mtime_ns": info.st_mtime_ns})}
            elif not stat.S_ISSOCK(info.st_mode):
                raise ValueError(f"Unsupported backup file type: {relative}")
    visit(root)
    return result


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as src, create_private_file(destination) as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    shutil.copystat(source, destination)


def copy_tree(source, destination):
    source, destination = Path(source), Path(destination)
    expected = inventory(source)
    destination.mkdir(mode=0o700)
    ensure_private_directory(destination)
    for name, row in expected.items():
        path = destination / name
        if row.get('directory'):
            path.mkdir(parents=True, exist_ok=True)
        elif 'sha256' in row:
            copy_file(source / name, path)
    for name, row in expected.items():
        if 'link' in row:
            path, target = destination / name, destination / row['link']
            if row['kind'] == 'junction':
                create_directory_link(path, target)
            else:
                path.symlink_to(os.path.relpath(target, path.parent), target_is_directory=True)
    if inventory(destination) != expected or inventory(source) != expected:
        raise ValueError("Snapshot changed during copying or failed its integrity check")
    return expected


def backup_bytes(backup, name, expected):
    row = expected[name]
    value = (Path(backup) / 'files' / row['sha256']).read_bytes()
    if hashlib.sha256(value).hexdigest() != row['sha256']:
        raise ValueError('Backup integrity check failed')
    return value


def verify_backup(backup, expected):
    backup = Path(backup)
    if json.loads((backup / 'inventory.json').read_text(encoding='utf-8')) != expected:
        raise ValueError('Backup inventory integrity mismatch')
    blobs = {}
    for name, row in expected.items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or not path.parts:
            raise ValueError('Unsafe backup path')
        if 'link' in row:
            target = Path(row['link'])
            if target.is_absolute() or '..' in target.parts or row.get('kind') not in {'junction', 'symlink'}:
                raise ValueError('Unsafe backup link')
        if 'sha256' in row:
            sha = row['sha256']
            if not re.fullmatch('[0-9a-f]{64}', sha):
                raise ValueError('Invalid backup digest')
            blobs[sha] = row['size']
    actual = inventory(backup / 'files')
    if actual != {sha: {'sha256': sha, 'size': size} for sha, size in blobs.items()}:
        raise ValueError('Backup integrity check failed')


def create_backup(source, destination):
    """Store bytes by digest, avoiding longer Windows backup paths.

    Logical paths and internal aliases remain in the checked inventory. A
    backup is not runnable and must be restored through the maintenance owner.
    """
    source, destination = Path(source), Path(destination)
    expected = inventory(source)
    ensure_private_directory(destination)
    ensure_private_directory(destination / 'files')
    for name, row in expected.items():
        if 'sha256' in row:
            blob = destination / 'files' / row['sha256']
            if not blob.exists():
                copy_file(source / name, blob)
    with create_private_file(destination / 'inventory.json') as stream:
        stream.write(json.dumps(expected, sort_keys=True).encode('utf-8'))
    verify_backup(destination, expected)
    if inventory(source) != expected:
        raise ValueError('Application changed during offline backup')
    return expected


def remove(path):
    if is_directory_link(path):
        remove_directory_link(path)
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def restore(source, destination, expected, *, packed=False):
    """Keep open installer lock/interpreter files in place on Windows.

    Source has already passed checksum verification; link targets are rebased
    into destination. Unchanged base runtime files are never overwritten.
    """
    source, destination = Path(source), Path(destination)
    if packed:
        verify_backup(source, expected)
    elif inventory(source) != expected:
        raise ValueError("Backup integrity check failed; restore refused")
    actual = inventory(destination)
    for name in sorted(actual, key=lambda s: len(Path(s).parts), reverse=True):
        if name not in expected or ('link' in actual[name] and actual[name] != expected[name]):
            remove(destination / name)
    for name, row in expected.items():
        path = destination / name
        if row == actual.get(name):
            continue
        if row.get('directory'):
            if path.exists() and not path.is_dir():
                remove(path)
            path.mkdir(parents=True, exist_ok=True)
        elif 'sha256' in row:
            if path.exists():
                remove(path)
            copy_file(source / 'files' / row['sha256'] if packed else source / name, path)
    for name, row in expected.items():
        if 'link' in row and row != actual.get(name):
            path, target = destination / name, destination / row['link']
            if row['kind'] == 'junction':
                create_directory_link(path, target)
            else:
                path.symlink_to(os.path.relpath(target, path.parent), target_is_directory=True)
    if inventory(destination) != expected:
        raise ValueError("Restored application differs from its verified backup")
