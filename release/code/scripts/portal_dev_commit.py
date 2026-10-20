"""Commit selected Dev working files through the ordinary Git index and branch."""
from pathlib import Path, PurePosixPath
import subprocess


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE)


def commit(root: Path, requested: tuple[str, ...], message: str, *, all_files=False) -> str:
    # A detached Dev HEAD cannot record the publication on the development branch.
    git(root, 'symbolic-ref', '--quiet', 'HEAD')
    if all_files:
        git(root, 'add', '-A')
    else:
        if not requested:
            raise ValueError('Select --path (repeatable), or --all')
        available = set(git(root, 'ls-files', '-z', '--cached', '--others',
                            '--exclude-standard').decode().split('\0')) - {''}
        selected = set()
        for name in requested:
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or (path.parts and path.parts[0] == '.git'):
                raise ValueError(f'Expected a repository-relative path: {name}')
            name = str(path)
            matches = {item for item in available
                       if name == '.' or item == name or item.startswith(name + '/')}
            if not matches:
                raise ValueError(f'No tracked or non-ignored source path: {name}')
            selected.update(matches)
        git(root, 'add', '-A', '--', *[':(literal)' + name for name in sorted(selected)])
    # Pre-existing staged work may accompany a publication. Never reset the index
    # or working files: edits after staging remain available for the next commit.
    if git(root, 'diff', '--cached', '--name-only', '-z'):
        git(root, 'commit', '-m', message)
    return git(root, 'rev-parse', 'HEAD').decode().strip()


def changed_paths(root: Path, previous: str, current: str) -> list[str]:
    return sorted(filter(None, git(root, 'diff', '--name-only', '-z', previous, current).decode().split('\0')))
