"""Git dependencies of a Creation artifact, persisted in its existing journal.

A paired ref transaction proves branch ownership. Worktree administration is
claimed by inode only after validating both Git pointers against the owned
artifact; retries never prune unrelated worktrees or branches.
"""
from pathlib import Path
import subprocess
from management_common.process_runtime import run_process

from management_common.directory_paths import cleanup_location, cleanup_path


def _git(repo, *args, input=None, missing_ok=False, timeout=1800):
    result = run_process(['git', '-C', str(repo), *args], input=input, text=True, encoding='utf-8',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode and not (missing_ok and result.returncode == 1):
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


class GitWorktreeCreation:
    def __init__(self, artifact, save, key, *, check):
        self.artifact, self.save, self.key = artifact, save, key
        self.check = check

    def _git(self, repo, *args, **kwargs):
        self.check()
        result = _git(repo, *args, timeout=self.artifact['git_dependency']['command_timeout'], **kwargs)
        self.check()
        return result

    def add(self, stage, repo, base, branch, *, timeout):
        dependency = {'repo': cleanup_location(repo), 'base': base, 'branch': branch,
                      'command_timeout': timeout,
                      'owner_ref': 'refs/tfi-creations/' + self.key.split(':')[-1]}
        self.artifact['git_dependency'] = dependency
        self.save()
        self._git(repo, 'worktree', 'add', '--detach', str(stage), base)
        self.capture_admin()

    def capture_admin(self):
        from .creation import _assert_identity, _identity
        dependency = self.artifact['git_dependency']
        if dependency.get('admin'):
            return
        for location in [self.artifact.get('stage'), self.artifact['path']]:
            if not location:
                continue
            root = cleanup_path(location)
            if not root.exists():
                continue
            _assert_identity(root, self.artifact.get('identity'))
            pointer = root / '.git'
            if not pointer.exists():
                continue
            if pointer.is_symlink() or not pointer.is_file():
                raise ValueError('Creation Git pointer was replaced')
            text = pointer.read_text(encoding='utf-8').strip()
            if not text.startswith('gitdir: '):
                raise ValueError('Creation Git pointer is invalid')
            admin = Path(text.removeprefix('gitdir: '))
            if not admin.is_absolute():
                admin = (root / admin).resolve()
            repo = cleanup_path(dependency['repo'])
            common = Path(self._git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
            if admin.parent != common / 'worktrees' or Path((admin / 'gitdir').read_text(encoding='utf-8').strip()) != pointer:
                raise ValueError('Creation Git worktree association changed')
            dependency.update(admin=cleanup_location(admin), admin_identity=_identity(admin))
            self.save()
            return

    def publish(self, stage, target):
        self.check()
        from management_portal.directory_move_owner import repair_git_worktrees
        repair_git_worktrees(stage, target)
        dependency = self.artifact['git_dependency']
        repo = cleanup_path(dependency['repo'])
        owner, branch, base = dependency['owner_ref'], dependency['branch'], dependency['base']
        owned = self._git(repo, 'rev-parse', '--verify', '--quiet', owner, missing_ok=True)
        if not owned:
            self._git(repo, 'update-ref', '--stdin', input=(
                f'start\ncreate refs/heads/{branch} {base}\ncreate {owner} {base}\nprepare\ncommit\n'))
        elif owned != base:
            raise ValueError('Creation Git branch ownership changed')
        self._git(target, 'checkout', branch)

    def cleanup(self):
        from .creation import _remove_owned
        self.capture_admin()
        dependency = self.artifact['git_dependency']
        repo = cleanup_path(dependency['repo'])
        owner, branch, base = dependency['owner_ref'], dependency['branch'], dependency['base']
        if repo.exists():
            owned = self._git(repo, 'rev-parse', '--verify', '--quiet', owner, missing_ok=True)
            if owned:
                if owned != base:
                    raise ValueError('Creation Git branch ownership changed')
                # Expected-value deletion refuses a branch changed by another
                # writer. Failure remains durable and visible to maintenance.
                self._git(repo, 'update-ref', '--stdin', input=(
                    f'start\ndelete refs/heads/{branch} {base}\ndelete {owner} {base}\nprepare\ncommit\n'))
        if dependency.get('admin'):
            self.check()
            _remove_owned(cleanup_path(dependency['admin']), dependency['admin_identity'])
