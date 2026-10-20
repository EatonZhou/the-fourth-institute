"""The Release checkout: the one `release` branch it runs and how Dev commits reach it."""
from __future__ import annotations
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from scripts.portal_source_layout import SHARED_PATHS, SHARED_ROOT
from management_common.portal_paths import resolve_directory
from management_common.site_config import read_site_config
from management_portal.shared_state import atomic_replace_json

RELEASE_ROOT = resolve_directory(read_site_config().get('deployment', {}).get(
    'release_root', str(SHARED_ROOT.parent / 'tfi-release')))
RELEASE_BRANCH = 'release'
MANIFEST_NAME = 'release-manifest.json'


def load_release_manifest(root: Path | None = None) -> dict:
    root = RELEASE_ROOT if root is None else root
    try:
        payload = json.loads((root / MANIFEST_NAME).read_text())
    except (FileNotFoundError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def link(target: Path, path: Path) -> None:
    temporary = path.with_name('.' + path.name + f'-{os.getpid()}')
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, path)


def is_deployed(root: Path | None = None) -> bool:
    root = RELEASE_ROOT if root is None else root
    return (root / '.git').is_file()


def released_commit(root: Path | None = None) -> str:
    """The commit the Release checkout runs; empty when Release is not deployed."""
    root = RELEASE_ROOT if root is None else root
    if not is_deployed(root):
        return ''
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()


def require_released_commit(commit: object) -> str:
    """Validate a full commit ID against the actual Release branch history."""
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('publication_commit 必须是完整的 Git commit ID')
    if not is_deployed():
        raise ValueError('Release 检出目录不存在')
    resolved = subprocess.run(
        ['git', 'rev-parse', '--verify', f'{commit}^{{commit}}'],
        cwd=RELEASE_ROOT, capture_output=True, text=True, timeout=15)
    if resolved.returncode or resolved.stdout.strip() != commit:
        raise ValueError('publication_commit 不是有效的 Git commit')
    result = subprocess.run(
        ['git', 'merge-base', '--is-ancestor', commit, f'refs/heads/{RELEASE_BRANCH}'],
        cwd=RELEASE_ROOT, capture_output=True, text=True, timeout=15)
    if result.returncode == 1:
        raise ValueError('publication_commit 尚未进入 Release 分支')
    if result.returncode:
        raise ValueError(f'无法校验 Release 分支：{result.stderr.strip()[-400:]}')
    return commit


def write_manifest(root: Path, commit: str, *, changed: list[str], message: str) -> None:
    # Release Web reads this file when it starts: the shared source location
    # tells it where to import the packages both instances share.
    atomic_replace_json(root / MANIFEST_NAME, {
        'schema': 'tfi.portal-publication.v1', 'source_commit': commit,
        'dev_commit': commit, 'release_commit': commit,
        'built_at': datetime.now(timezone.utc).isoformat(),
        'message': message, 'paths': changed,
        'shared_source': str(SHARED_ROOT), 'shared_paths': list(SHARED_PATHS),
    })


def advance(root: Path, commit: str) -> None:
    """Fast-forward the `release` branch in the Release checkout to a Dev commit.

    The branch only ever receives Dev commits, so the move is always a
    fast-forward; files deleted in those commits disappear from Release too.
    A branch holding commits that Dev does not have stops the publication
    instead of discarding them.
    """
    if not is_deployed(root):
        raise ValueError(f'Release is not a registered Git checkout: {root}')
    branch = subprocess.run(['git', 'symbolic-ref', '--quiet', '--short', 'HEAD'],
                            cwd=root, capture_output=True, text=True).stdout.strip()
    if branch != RELEASE_BRANCH:
        raise ValueError(f'Release checkout must be on branch {RELEASE_BRANCH!r}, found {branch or "no branch"!r}')
    previous = released_commit(root)
    completed = subprocess.run(['git', 'merge', '--ff-only', '--quiet', commit],
                               cwd=root, capture_output=True, text=True)
    if completed.returncode:
        raise ValueError(f'Release branch cannot fast-forward to {commit}: '
                         f'{(completed.stderr or completed.stdout).strip()[-400:]}')
    head = released_commit(root)
    if head != commit:
        raise ValueError(f'Release branch is at {head}, expected {commit}')
    changed = subprocess.check_output(
        ['git', 'diff', '--name-only', previous, head], cwd=root, text=True).splitlines()
    message = subprocess.check_output(
        ['git', 'show', '-s', '--format=%B', head], cwd=root, text=True).strip()
    write_manifest(root, head, changed=changed, message=message)
