from pathlib import Path
import subprocess
import pytest
from scripts import portal_dev_commit as development, portal_release as release


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    for args in [('init', '-q'), ('config', 'user.name', 'Test'), ('config', 'user.email', 'test@example.invalid')]:
        development.git(root, *args)
    for name in ('a.txt', 'b.txt', 'removed.txt'):
        (root / name).write_text('original')
    (root / '.gitignore').write_text('*.secret\nportal_instances/\n/release-manifest.json\n__pycache__/\n')
    development.git(root, 'add', '.')
    development.git(root, 'commit', '-qm', 'base')
    return root


def test_released_commit_validation_uses_branch_history(repo, tmp_path, monkeypatch):
    """按 Release 分支历史校验已发布提交，拒绝未发布、标签、缩写和无关历史的提交"""
    initial = development.git(repo, 'rev-parse', 'HEAD').decode().strip()
    root = tmp_path / 'release'
    development.git(repo, 'worktree', 'add', '-b', release.RELEASE_BRANCH, str(root), initial)
    monkeypatch.setattr(release, 'RELEASE_ROOT', root)
    assert release.require_released_commit(initial) == initial

    (repo / 'a.txt').write_text('not published yet')
    development.git(repo, 'commit', '-am', 'next')
    next_commit = development.git(repo, 'rev-parse', 'HEAD').decode().strip()
    with pytest.raises(ValueError, match='尚未进入'):
        release.require_released_commit(next_commit)
    release.advance(root, next_commit)
    assert release.require_released_commit(next_commit) == next_commit
    assert release.require_released_commit(initial) == initial

    development.git(repo, 'tag', '-a', 'annotated', '-m', 'tag', next_commit)
    tag = development.git(repo, 'rev-parse', 'annotated').decode().strip()
    for value in ('f' * 40, tag):
        with pytest.raises(ValueError, match='不是有效'):
            release.require_released_commit(value)
    for value in (None, '', [], 'HEAD', initial[:12], '--help'):
        with pytest.raises(ValueError, match='完整'):
            release.require_released_commit(value)

    # A valid local commit on another line of history was never released.
    tree = development.git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip()
    unrelated = development.git(repo, 'commit-tree', tree, '-m', 'unrelated root').decode().strip()
    with pytest.raises(ValueError, match='尚未进入'):
        release.require_released_commit(unrelated)
    development.git(repo, 'update-ref', '-d', f'refs/heads/{release.RELEASE_BRANCH}')
    with pytest.raises(ValueError, match='无法校验'):
        release.require_released_commit(initial)
    monkeypatch.setattr(release, 'RELEASE_ROOT', tmp_path / 'absent')
    with pytest.raises(ValueError, match='不存在'):
        release.require_released_commit(initial)


def test_dev_commit_preserves_concurrent_edits_and_advances_branch(repo, monkeypatch):
    """Dev 选定路径提交只记录选中文件和已暂存内容，保留他人未提交与并发修改"""
    base = development.git(repo, 'rev-parse', 'HEAD').decode().strip()
    (repo / 'a.txt').write_text('staged publication')
    (repo / 'b.txt').write_text('unrelated unstaged edit')
    (repo / 'removed.txt').unlink()
    (repo / 'new.txt').write_text('already staged work')
    development.git(repo, 'add', 'new.txt')
    original_git = development.git

    def editing_git(root, *args):
        result = original_git(root, *args)
        if args[:2] == ('add', '-A'):
            (repo / 'a.txt').write_text('later concurrent edit')
        return result

    monkeypatch.setattr(development, 'git', editing_git)
    sha = development.commit(repo, ('a.txt', 'removed.txt'), 'selected batch')
    assert development.git(repo, 'rev-parse', 'HEAD').decode().strip() == sha
    assert development.git(repo, 'rev-parse', 'HEAD^').decode().strip() == base
    assert development.git(repo, 'show', 'HEAD:a.txt') == b'staged publication'
    assert development.git(repo, 'show', 'HEAD:b.txt') == b'original'
    assert development.git(repo, 'show', 'HEAD:new.txt') == b'already staged work'
    assert development.changed_paths(repo, base, sha) == ['a.txt', 'new.txt', 'removed.txt']
    assert not development.git(repo, 'diff', '--cached', '--name-only')
    assert (repo / 'a.txt').read_text() == 'later concurrent edit'
    assert (repo / 'b.txt').read_text() == 'unrelated unstaged edit'
    with pytest.raises(ValueError):
        development.commit(repo, ('../elsewhere',), 'invalid')


def test_full_commit_records_add_modify_delete_and_reuses_unchanged_head(repo):
    """全量提交记录新增、修改和删除并排除密钥文件，无变化时复用当前 HEAD

    :design: decision_08bb2d9141f9, decision_850e6248d680"""
    (repo / 'a.txt').write_text('full')
    (repo / 'new.txt').write_text('new')
    (repo / 'removed.txt').unlink()
    (repo / 'token.secret').write_text('excluded')
    sha = development.commit(repo, (), 'full batch', all_files=True)
    assert not development.git(repo, 'status', '--porcelain')
    assert b'token.secret' not in development.git(repo, 'ls-tree', '-r', '--name-only', sha)
    assert development.commit(repo, (), 'no changes', all_files=True) == sha


def test_release_branch_advances_to_each_published_commit_and_imports_common_source(repo, tmp_path, monkeypatch):
    """Release 分支快进到每次发布的提交并从共享源码导入公共包，分支有独立提交时停止发布"""
    # Release keeps tracked package bytes; runtime imports use the common owner.
    monkeypatch.setattr(release, 'SHARED_PATHS', ('management_common', 'management_portal/execution'))
    common = tmp_path / 'common-root'
    monkeypatch.setattr(release, 'SHARED_ROOT', common)
    for root, value in [(repo, 'committed'), (common, 'live')]:
        for name in ('management_common', 'management_portal/execution'):
            (root / name).mkdir(parents=True, exist_ok=True)
            (root / name / '__init__.py').write_text(f'value = {value!r}\n')
    source = Path(__file__).resolve().parents[2]
    for root in (repo, common):
        for name in ('portal_paths.py', 'product_environment.py', 'site_config.py', 'site_compatibility.py', 'postgres_configuration.py'):
            (root / 'management_common' / name).write_bytes((source / 'management_common' / name).read_bytes())
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(tmp_path / 'absent-site.json'))
    monkeypatch.setenv('PORTAL_PATHS_CONFIG', str(tmp_path / 'absent-paths.json'))
    (repo / 'scripts').mkdir()
    (repo / 'scripts/portal_source_layout.py').write_bytes((source / 'scripts/portal_source_layout.py').read_bytes())
    (repo / 'management_portal/__init__.py').write_bytes((source / 'management_portal/__init__.py').read_bytes())
    (repo / 'management_portal/web_only.py').write_text("value = 'release'\n")
    base = development.commit(repo, (), 'packages', all_files=True)
    root = tmp_path / 'release'
    assert not release.is_deployed(root) and release.released_commit(root) == ''
    with pytest.raises(ValueError):
        release.advance(root, base)
    development.git(repo, 'worktree', 'add', '-b', release.RELEASE_BRANCH, str(root), base)
    release.write_manifest(root, base, changed=[], message='base')
    assert release.released_commit(root) == base

    (repo / 'a.txt').write_text('next')
    (repo / 'removed.txt').unlink()
    sha = development.commit(repo, ('a.txt', 'removed.txt'), 'next')
    assert release.load_release_manifest(root)['source_commit'] == base
    release.advance(root, sha)
    assert release.released_commit(root) == sha
    assert development.git(repo, 'rev-parse', release.RELEASE_BRANCH).decode().strip() == sha
    assert development.git(root, 'symbolic-ref', '--short', 'HEAD').decode().strip() == release.RELEASE_BRANCH
    assert (root / 'a.txt').read_text() == 'next'
    assert not (root / 'removed.txt').exists()
    assert not (root / 'management_common').is_symlink()
    assert not development.git(root, 'status', '--porcelain')
    # One checkout and one branch serve Release; publishing never adds another.
    listed = development.git(repo, 'worktree', 'list', '--porcelain').decode()
    assert listed.count('worktree ') == 2 and str(root) in listed
    branches = development.git(repo, 'branch', '--format=%(refname:short)').decode().split()
    assert len(branches) == 2 and release.RELEASE_BRANCH in branches
    assert release.load_release_manifest(root)['source_commit'] == sha
    probe = 'import management_portal.web_only as w; import management_common as c; import management_portal.execution as e; print(w.value,c.value,e.value)'
    assert subprocess.check_output(['python3', '-c', probe], cwd=root).strip() == b'release live live'
    (common / 'management_common/__init__.py').write_text("value = 'new shared source'\n")
    assert subprocess.check_output(['python3', '-c', probe], cwd=root).strip() == b'release new shared source live'
    with pytest.raises(ValueError):
        release.advance(root, '0' * 40)
    assert release.released_commit(root) == sha
    assert release.load_release_manifest(root)['source_commit'] == sha
    # A commit made on the release branch alone is never discarded: the next
    # publication stops instead of moving the branch past it.
    (root / 'b.txt').write_text('changed only on release')
    development.git(root, 'commit', '-qam', 'release only')
    (repo / 'a.txt').write_text('later')
    later = development.commit(repo, ('a.txt',), 'later')
    with pytest.raises(ValueError, match='fast-forward'):
        release.advance(root, later)
    development.git(root, 'checkout', '-q', '--detach')
    with pytest.raises(ValueError, match='branch'):
        release.advance(root, later)
