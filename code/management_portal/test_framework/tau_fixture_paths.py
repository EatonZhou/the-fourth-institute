"""Run-owned scratch directories for tests that must start outside a Git checkout."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from management_portal.shared_state import atomic_replace_json


def _bases() -> list[Path]:
    return list(dict.fromkeys(Path(p).resolve() for p in (tempfile.gettempdir(), '/var/tmp', '/tmp')))


def _name(root: Path) -> str:
    return 'tfi-tau-fixture-' + hashlib.sha256(str(root.resolve()).encode()).hexdigest()


def _inside_checkout(path: Path) -> bool:
    return any((parent / '.git').exists() or (parent / '.git').is_symlink()
               for parent in (path, *path.parents))


def _receipt(root: Path) -> Path:
    path = root / 'standalone-fixture.json'
    if path.is_symlink():
        raise ValueError('TAU fixture receipt cannot be a symlink')
    return path


def inspect(root: Path) -> tuple[Path, dict] | None:
    """Validate ownership before cleanup; never accept an arbitrary manifest path."""
    root = root.resolve()
    receipt = _receipt(root)
    if not receipt.exists():
        return None
    record = json.loads(receipt.read_text())
    path = Path(record['path'])
    if path.name != _name(root) or path.parent not in _bases() or path.is_symlink():
        raise ValueError('TAU standalone fixture path is not owned by this case')
    if record.get('evidence_dir') != str(root):
        raise ValueError('TAU standalone fixture belongs to another case')
    if path.exists():
        stat = path.stat()
        if (not path.is_dir() or stat.st_uid != os.getuid()
                or [stat.st_dev, stat.st_ino] != record.get('identity')):
            raise ValueError('TAU standalone fixture directory was replaced')
    return path, record


def prepare(root: Path) -> Path:
    root = root.resolve()
    existing = inspect(root)
    if existing is not None:
        path, _ = existing
        if not path.is_dir():
            raise ValueError('TAU standalone fixture was already removed')
        return path
    root.mkdir(parents=True, exist_ok=True)
    for base in _bases():
        if not base.is_dir() or _inside_checkout(base):
            continue
        path = base / _name(root)
        # An unreceipted existing directory is never adopted or deleted.
        path.mkdir(mode=0o700)
        stat = path.stat()
        atomic_replace_json(_receipt(root), {
            'path': str(path), 'evidence_dir': str(root),
            'identity': [stat.st_dev, stat.st_ino],
        })
        return path
    raise ValueError('No standalone TAU fixture location outside Git checkouts is available')


def cleanup(root: Path) -> list[str]:
    existing = inspect(root)
    if existing is None:
        return []
    path, _ = existing
    if path.exists():
        shutil.rmtree(path)
    return [str(path)]
