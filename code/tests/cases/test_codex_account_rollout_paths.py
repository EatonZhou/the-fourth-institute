import json

import pytest

from tests.cases.account_registration_test_support import register_codex

THREAD = '01a056bf-cf6a-7a03-a94b-ebf1f0ad882d'


@pytest.fixture
def registry(tmp_path, monkeypatch):
    return register_codex(monkeypatch, tmp_path, default="zero")


@pytest.mark.parametrize('directory', ['sessions', 'archived_sessions'])
def test_rollout_lookup_follows_migrated_history_directory(tmp_path, registry, directory):
    """查找 codex rollout 文件时跟随迁移到其他位置的历史目录链接"""
    root = registry.get('zero').codex_home / directory / '2026'
    root.mkdir(parents=True)
    migrated = tmp_path / 'migrated-month'
    (migrated / '31').mkdir(parents=True)
    rollout = migrated / '31' / f'rollout-{THREAD}.jsonl'
    rollout.write_text('native record\n')
    (root / '08').symlink_to(migrated, target_is_directory=True)
    found = registry.find_thread_rollout('zero', THREAD)
    assert found is not None
    assert found.resolve() == rollout


def test_rollout_lookup_skips_dangling_file_matches(tmp_path, registry):
    """查找 codex rollout 文件时跳过指向不存在文件的符号链接"""
    root = registry.get('zero').codex_home / 'sessions'
    root.mkdir(parents=True)
    (root / f'a-{THREAD}.jsonl').symlink_to(tmp_path / 'missing')
    valid = root / f'b-{THREAD}.jsonl'
    valid.write_text('native record\n')
    assert registry.find_thread_rollout('zero', THREAD) == valid


def test_rollout_lookup_never_crosses_account_or_archived_policy(registry):
    """查找 codex rollout 文件不跨账户，排除归档时不查 archived_sessions"""
    root = registry.get('one').codex_home / 'archived_sessions'
    root.mkdir(parents=True)
    valid = root / f'rollout-{THREAD}.jsonl'
    valid.write_text('native record\n')
    assert registry.find_thread_rollout('zero', THREAD) is None
    assert registry.find_thread_rollout('one', THREAD, include_archived=False) is None
    assert registry.find_thread_rollout('one', THREAD) == valid


def test_rollout_lookup_prunes_symlink_cycles(tmp_path, registry, monkeypatch):
    """查找 codex rollout 文件时不重复遍历循环的符号链接目录"""
    import os
    root = registry.get('zero').codex_home / 'sessions'
    (root / '2026').mkdir(parents=True)
    (root / '2026' / 'loop').symlink_to(root, target_is_directory=True)
    walk = os.walk
    visited = []

    def bounded_walk(*args, **kwargs):
        for entry in walk(*args, **kwargs):
            visited.append(entry[0])
            assert len(visited) <= 5, 'A linked directory must not be traversed repeatedly'
            yield entry

    monkeypatch.setattr(os, 'walk', bounded_walk)
    assert registry.find_thread_rollout('zero', THREAD) is None


def test_rollout_lookup_rejects_noncanonical_thread_ids(registry):
    """查找 codex rollout 文件时拒绝空的或非规范的 thread ID"""
    assert registry.find_thread_rollout('zero', '../other/*') is None
    assert registry.find_thread_rollout('zero', '') is None
