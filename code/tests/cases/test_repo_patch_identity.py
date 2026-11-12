"""Repo patch blocks retain file identity independently of display order.

:module: workflow.repository
:design: decision_6476c69b33da
:importance: P1
"""
import subprocess

import pytest

from management_portal import repo_workspace_history as history


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args]).decode().strip()


@pytest.mark.parametrize('names', [
    ['a.txt', 'b.txt'],
    ['a space.txt', 'b\t中文".txt', 'c\nline.txt', 'd\\slash.txt', 'e\a.txt'],
])
def test_real_git_patches_follow_paths_not_record_order(tmp_path, names):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'fixture')
    git(tmp_path, 'config', 'user.email', 'fixture@example.invalid')
    for index, name in enumerate(names):
        (tmp_path / name).write_text(f'unique-content-{index}\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'fixture')
    commit = {'sha': git(tmp_path, 'rev-parse', 'HEAD'), 'parents': []}
    files, truncated = history._commit_file_records(tmp_path, commit)
    assert not truncated
    # Git user formatting preferences must not change the API's path grammar.
    git(tmp_path, 'config', 'diff.noprefix', 'true')
    patch = history._commit_patch_bytes(tmp_path, commit).decode()
    files.reverse()
    history._attach_commit_patches(files, patch)
    for record in files:
        index = names.index(record['path'])
        assert f'+unique-content-{index}\n' in record['patch']


def test_unknown_and_incomplete_headers_are_not_assigned_by_position():
    files = [{'path': 'visible.txt', 'original_path': '', 'patch': ''}]
    history._attach_commit_patches(files,
        'diff --git a/other.txt b/other.txt\n--- a/other.txt\n+++ b/other.txt\n@@ -1 +1 @@\n-x\n+y\n')
    assert files[0]['patch'] == ''
    history._attach_commit_patches(files, 'diff --git a/visible.txt b/visible')
    assert files[0]['patch'] == ''


def test_rename_copy_binary_and_delete_use_both_header_paths():
    files = [
        {'path': 'new.txt', 'original_path': 'old.txt', 'patch': ''},
        {'path': 'copy.txt', 'original_path': 'source.txt', 'patch': ''},
        {'path': 'blob.bin', 'original_path': '', 'patch': ''},
        {'path': 'gone.txt', 'original_path': '', 'patch': ''},
    ]
    blocks = [
        'diff --git a/old.txt b/new.txt\nsimilarity index 100%\nrename from old.txt\nrename to new.txt\n',
        'diff --git a/source.txt b/copy.txt\nsimilarity index 100%\ncopy from source.txt\ncopy to copy.txt\n',
        'diff --git a/blob.bin b/blob.bin\nBinary files a/blob.bin and b/blob.bin differ\n',
        'diff --git a/gone.txt b/gone.txt\ndeleted file mode 100644\n--- a/gone.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n',
    ]
    history._attach_commit_patches(files, ''.join(reversed(blocks)))
    assert [item['patch'] for item in files] == blocks


def test_commit_with_submodule_log_does_not_steal_text_file_patch(tmp_path, monkeypatch):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'fixture')
    git(tmp_path, 'config', 'user.email', 'fixture@example.invalid')
    file = tmp_path / 'z-text.txt'
    file.write_text('before\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'base')
    base = git(tmp_path, 'rev-parse', 'HEAD')
    git(tmp_path, 'update-index', '--add', '--cacheinfo', f'160000,{base},a-submodule')
    git(tmp_path, 'commit', '-qm', 'link')
    parent = git(tmp_path, 'rev-parse', 'HEAD')
    file.write_text('after\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'update-index', '--add', '--cacheinfo', f'160000,{parent},a-submodule')
    git(tmp_path, 'commit', '-qm', 'change')
    git(tmp_path, 'config', 'diff.submodule', 'log')
    monkeypatch.setattr(history, '_checkout', lambda _id: ({}, tmp_path))
    result = history.read_commit('fixture', 'HEAD')
    records = {item['path']: item for item in result['files']}
    assert '+after\n' in records['z-text.txt']['patch']
    assert '+after\n' not in records['a-submodule']['patch']
