"""Private offline application snapshots, including internal directory aliases."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat

from management_common.directory_links import is_directory_link, create_directory_link, remove_directory_link
from management_common.file_permissions import create_private_file, ensure_private_directory


LOCKS = {".install.lock", ".configuration.lock"}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root):
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
                result[relative] = {"sha256": digest(path), "size": info.st_size}
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


def remove(path):
    if is_directory_link(path):
        remove_directory_link(path)
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def restore(source, destination, expected):
    """Keep open installer lock/interpreter files in place on Windows.

    Source has already passed checksum verification; link targets are rebased
    into destination. Unchanged base runtime files are never overwritten.
    """
    source, destination = Path(source), Path(destination)
    if inventory(source) != expected:
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
            copy_file(source / name, path)
    for name, row in expected.items():
        if 'link' in row and row != actual.get(name):
            path, target = destination / name, destination / row['link']
            if row['kind'] == 'junction':
                create_directory_link(path, target)
            else:
                path.symlink_to(os.path.relpath(target, path.parent), target_is_directory=True)
    if inventory(destination) != expected:
        raise ValueError("Restored application differs from its verified backup")
