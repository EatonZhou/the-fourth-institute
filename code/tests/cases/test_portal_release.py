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
    (root / '.gitignore').write_text('*.secret\nportal_instances/\n__pycache__/\n')
    development.git(root, 'add', '.')
    development.git(root, 'commit', '-qm', 'base')
    return root


def test_released_commit_validation_uses_release_worktree_history(repo, tmp_path, monkeypatch):
    """按 Release 工作树的提交历史校验已发布提交（代码目录在工作树之下），拒绝未发布、标签、缩写和无关历史的提交"""
    (repo / 'code').mkdir()
    (repo / 'code/app.py').write_text('value = 1\n')
    development.git(repo, 'add', '.')
    development.git(repo, 'commit', '-qm', 'code')
    initial = development.git(repo, 'rev-parse', 'HEAD').decode().strip()
    worktree = tmp_path / 'release'
    development.git(repo, 'worktree', 'add', '-q', '-b', 'published', str(worktree), initial)
    monkeypatch.setattr(release, 'RELEASE_ROOT', worktree / 'code')
    assert release.is_deployed()
    assert release.require_released_commit(initial) == initial

    (repo / 'a.txt').write_text('not published yet')
    development.git(repo, 'commit', '-qam', 'next')
    next_commit = development.git(repo, 'rev-parse', 'HEAD').decode().strip()
    with pytest.raises(ValueError, match='尚未进入'):
        release.require_released_commit(next_commit)
    development.git(worktree, 'merge', '--ff-only', '-q', next_commit)
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
    (tmp_path / 'plain').mkdir()
    for absent in (tmp_path / 'absent', tmp_path / 'plain'):
        monkeypatch.setattr(release, 'RELEASE_ROOT', absent)
        with pytest.raises(ValueError, match='不存在'):
            release.require_released_commit(initial)


def test_dev_commit_preserves_concurrent_edits(repo, monkeypatch):
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
