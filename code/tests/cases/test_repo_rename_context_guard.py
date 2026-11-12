"""AI commit material checks both identities of renamed files.

:module: workflow.repository
:design: decision_6476c69b33da
:importance: P1
"""
import pytest

from management_portal import repo_workspace_auto_commit as owner
from management_portal.repo_workspace_checkout import RepoWorkspaceConflict


@pytest.mark.parametrize('original,path,blocked', [
    ('.env', 'settings.txt', True),
    ('secrets/token.txt', 'docs/token.txt', True),
    ('settings.txt', '.env', True),
    ('old.txt', 'new.txt', False),
    ('.env.example', 'example.txt', False),
])
def test_renamed_sensitive_source_is_rejected_before_material_read(tmp_path, monkeypatch, original, path, blocked):
    reads = []
    def read_git(*args, **kwargs):
        reads.append(args)
        return b''
    monkeypatch.setattr(owner, '_git_bytes', read_git)
    monkeypatch.setattr(owner, '_git', lambda *args, **kwargs: '')
    monkeypatch.setattr(owner, '_branch_name', lambda root: 'main')
    status = {'changes': [{'path': path, 'original_path': original, 'code': 'R', 'untracked': False}]}
    if blocked:
        with pytest.raises(RepoWorkspaceConflict):
            owner._commit_change_context('fixture', {}, tmp_path, status)
        assert reads == []
    else:
        result = owner._commit_change_context('fixture', {}, tmp_path, status)
        assert result['change_count'] == 1
        assert reads
