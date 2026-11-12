"""目录变更追踪与稳定身份解析的工作量合同。

:module: operations.test-framework
:design: decision_8273271b67b1
:risk: no-external-side-effects
"""
from pathlib import Path

import pytest

from management_portal.test_framework import catalog_source as source


def test_catalog_revision_and_discovery_share_one_source_listing(tmp_path, monkeypatch):
    """冷构建与缓存刷新均只发现一次源码，新增删除仍使目录失效。"""
    root = tmp_path / 'tests' / 'cases'
    root.mkdir(parents=True)
    first = root / 'test_first.py'
    first.write_text('def test_first(): pass\n')
    calls = []
    explicit_calls = []
    original = source.source_paths
    original_explicit = source.explicit_paths

    def listing(path):
        calls.append(path)
        return original(path)

    def explicit_listing(path):
        explicit_calls.append(path)
        return original_explicit(path)

    monkeypatch.setattr(source, 'source_paths', listing)
    monkeypatch.setattr(source, 'explicit_paths', explicit_listing)
    initial = source.build_catalog(tmp_path)
    assert len(calls) == 1
    assert len(explicit_calls) == 1
    assert initial.discovered == {'tests/cases/test_first.py::test_first'}
    calls.clear()
    explicit_calls.clear()
    assert source.build_catalog(tmp_path) is initial
    assert len(calls) == 1
    assert len(explicit_calls) == 1
    first.unlink()
    (root / 'test_second.py').write_text('def test_second(): pass\n')
    calls.clear()
    explicit_calls.clear()
    updated = source.build_catalog(tmp_path)
    assert updated.revision != initial.revision
    assert updated.discovered == {'tests/cases/test_second.py::test_second'}
    assert len(calls) == 1
    assert len(explicit_calls) == 1


def test_moved_identity_resolution_groups_old_entries_once(monkeypatch):
    """大量唯一移动按旧新入口线性分组，不对每个孤儿重新扫描全部孤儿。"""
    size = 128
    pins = {f'tests/cases/test_old.py::test_item_{i}': f'test_{i:012x}' for i in range(size)}
    entries = {f'tests/cases/test_new.py::test_item_{i}' for i in range(size)}
    calls = []

    def counted_path(value):
        calls.append(value)
        return Path(value)

    monkeypatch.setattr(source, 'Path', counted_path)
    current, orphans = source.effective_case_ids(pins, entries)
    assert current == {f'tests/cases/test_new.py::test_item_{i}': f'test_{i:012x}' for i in range(size)}
    assert not orphans
    assert len(calls) <= 3 * size


@pytest.mark.parametrize('old,new,expected', [
    (['a/test_old.py::test_same'], ['a/test_new.py::test_same'], True),
    (['a/test_old.py::test_same'], ['b/test_new.py::test_same'], False),
    (['a/test_old.py::test_same'], ['a/test_new.py::test_same', 'a/test_other.py::test_same'], False),
    (['a/test_old.py::test_same', 'a/test_other.py::test_same'], ['a/test_new.py::test_same'], False),
])
def test_identity_following_preserves_directory_and_ambiguity_rules(old, new, expected):
    """仅同目录且两侧唯一的移动沿用身份，歧义和跨目录必须保留孤儿。"""
    pins = {entry: f'test_{i:012x}' for i, entry in enumerate(old)}
    current, orphans = source.effective_case_ids(pins, set(new))
    if expected:
        assert current == {new[0]: pins[old[0]]}
        assert not orphans
    else:
        assert not current
        assert orphans == pins


def test_existing_identity_is_not_reassigned_to_an_unpinned_peer():
    """仍存在的固定入口不被同名新入口或孤儿覆盖，输入映射保持原样。"""
    existing = 'a/test_keep.py::test_same'
    old = 'a/test_old.py::test_same'
    new = 'a/test_new.py::test_same'
    pins = {existing: 'test_111111111111', old: 'test_222222222222'}
    before = dict(pins)
    current, orphans = source.effective_case_ids(pins, {existing, new})
    assert current == {existing: pins[existing], new: pins[old]}
    assert not orphans
    assert pins == before
