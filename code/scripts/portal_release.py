"""The Release checkout: where Release Web runs and which commits have reached it."""
from __future__ import annotations
import re
import subprocess
from pathlib import Path
from management_common.instance_manifest import current_instance

# The code directory of the Release worktree, as registered in the selected
# instance manifest; its Git directory is one level up.
RELEASE_ROOT = current_instance().code_root('release')


def is_deployed(root: Path | None = None) -> bool:
    root = RELEASE_ROOT if root is None else root
    if not root.is_dir():
        return False
    result = subprocess.run(['git', 'rev-parse', '--is-inside-work-tree'],
                            cwd=root, capture_output=True, text=True, timeout=45)
    return result.returncode == 0 and result.stdout.strip() == 'true'


def require_released_commit(commit: object) -> str:
    """Validate a full commit ID against the history the Release checkout runs."""
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('publication_commit 必须是完整的 Git commit ID')
    if not is_deployed():
        raise ValueError('Release 检出目录不存在')
    resolved = subprocess.run(
        ['git', 'rev-parse', '--verify', f'{commit}^{{commit}}'],
        cwd=RELEASE_ROOT, capture_output=True, text=True, timeout=45)
    if resolved.returncode or resolved.stdout.strip() != commit:
        raise ValueError('publication_commit 不是有效的 Git commit')
    result = subprocess.run(
        ['git', 'merge-base', '--is-ancestor', commit, 'HEAD'],
        cwd=RELEASE_ROOT, capture_output=True, text=True, timeout=45)
    if result.returncode == 1:
        raise ValueError('publication_commit 尚未进入 Release')
    if result.returncode:
        raise ValueError(f'无法校验 Release 的提交历史：{result.stderr.strip()[-400:]}')
    return commit
