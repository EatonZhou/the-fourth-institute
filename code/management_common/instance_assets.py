"""Conflict-aware product assets. User contents are never an implicit baseline."""
import hashlib
from pathlib import Path
import os
import tempfile

from management_common.configuration_files import preserve_config_permissions


def publish(target, text):
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix='.' + target.name)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            if target.exists():
                preserve_config_permissions(temporary, target)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def reconcile(root, contents, ledger, *, apply=False):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('Product root must not be a symlink')
    result = {'files': [], 'blockers': []}
    for relative, text in contents.items():
        rel = Path(relative)
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('Unsafe product asset path')
        target = root / rel
        if any(path.is_symlink() for path in [target, *target.parents]):
            raise ValueError('Product asset must not traverse a symlink')
        expected = digest(text.encode())
        actual = digest(target.read_bytes()) if target.exists() else None
        if actual == expected:
            status = 'current'
        elif actual is not None and actual != ledger.get(relative):
            status = 'conflict'
            result['blockers'].append({'path': str(target), 'error': 'user_modified_or_unknown_baseline'})
        else:
            status = 'replace' if actual else 'create'
            if apply:
                target.parent.mkdir(parents=True, exist_ok=True)
                publish(target, text)
        result['files'].append({'path': str(target), 'status': status, 'sha256': expected})
        if apply and status != 'conflict':
            ledger[relative] = expected
    return result
