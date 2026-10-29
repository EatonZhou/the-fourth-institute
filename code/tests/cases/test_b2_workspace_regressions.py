""":module: spec.design-governance"""
from pathlib import Path
import subprocess

import pytest

from management_portal.society_service import lineage_handoff_backend as handoff
from management_portal.society_service import lineage_handoff_preparation as preparation
from management_portal.society_service import resident_watchboard_domain as watchboard
from management_portal.society_service import project_folders


def test_handoff_copy_limits_checked_before_copying(tmp_path, monkeypatch):
    """Workspace 交接复制前检查容量限制"""
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'large.txt').write_bytes(b'x' * 20)
    copied = []
    original = handoff.shutil.copy2

    def copy(src, dst, **kwargs):
        copied.append(src)
        return original(src, dst, **kwargs)

    monkeypatch.setattr(handoff.shutil, 'copy2', copy)
    item = {'source': {'path': str(source)}, 'transfer': {'mode': 'copy', 'method': 'filesystem_copy'}, 'target': {'path': str(tmp_path / 'target')}}
    with pytest.raises(ValueError, match='bytes'):
        handoff.materialize_handoff_item(item, allowed_target_roots=[tmp_path], checked_at='now', max_bytes=10)
    assert copied == []
    assert not (tmp_path / 'target').exists()


def test_handoff_git_diff_uses_selected_base(tmp_path):
    """交接按所选基准提交应用工作区差异"""
    repo = tmp_path / 'repo'
    repo.mkdir()

    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    git('init')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Fixture')
    (repo / 'file.txt').write_text('base\n')
    git('add', '.')
    git('commit', '-m', 'base')
    base = git('rev-parse', 'HEAD')
    (repo / 'file.txt').write_text('head\n')
    git('commit', '-am', 'head')
    (repo / 'file.txt').write_text('working\n')
    item = {'source': {'path': str(repo)}, 'transfer': {'mode': 'copy', 'method': 'git_worktree', 'parameters': {'branch': 'handoff', 'base_commit': base, 'include_worktree_changes': True}}, 'target': {'path': str(tmp_path / 'target')}}
    handoff.materialize_handoff_item(item, allowed_target_roots=[tmp_path], checked_at='now')
    assert (tmp_path / 'target/file.txt').read_text() == 'working\n'


def test_handoff_default_worktrees_have_distinct_paths(tmp_path):
    """多个交接工作树默认使用不同路径"""
    items = preparation.draft_items(lineage_id='lineage_aaaaaaaaaaaa', handoff_id='handoff_aaaaaaaaaaaa', source_id='resident_aaaaaaaaaaaa', target_title='target', payload={'items': [{'transfer': {'mode': 'copy', 'method': 'git_worktree'}}] * 2}, actor='reader', lineage_folder=tmp_path, source_workspace=lambda *_: {'path': str(tmp_path)}, root_path=lambda value: str(value or ''))
    assert len({item['target']['path'] for item in items}) == 2


def test_watchboard_root_tracks_owned_workspace_without_creating_files(tmp_path, monkeypatch):
    """watchboard 路径随自有 Workspace 移动且不创建文件

    :module: platform.core"""
    from management_portal.society_service import resident_record_domain

    monkeypatch.setattr(resident_record_domain, 'resident_owned_workspace_path', lambda record: Path(record['folder']) / 'workspace')
    record = {'folder': str(tmp_path / 'first')}
    assert watchboard._watchboard_root(record) == tmp_path / 'first/workspace/watchboard'
    record['folder'] = str(tmp_path / 'moved')
    assert watchboard._watchboard_root(record) == tmp_path / 'moved/workspace/watchboard'
    assert not (tmp_path / 'moved').exists()


def test_fork_watchboard_survives_source_removal(tmp_path, monkeypatch):
    """源 Resident 删除后分支仍保留 watchboard 文件"""
    import shutil
    from management_portal.society_service import resident_record_domain

    monkeypatch.setattr(resident_record_domain, 'resident_owned_workspace_path', lambda record: Path(record['folder']) / 'workspace')
    monkeypatch.setattr(watchboard.watchboard_lineage, 'installed', lambda record: record['folder'].endswith('/source'))
    source = {'folder': str(tmp_path / 'source')}
    child = {'folder': str(tmp_path / 'child')}
    root = watchboard._watchboard_root(source)
    root.mkdir(parents=True)
    (root / 'script.py').write_text('fixture')
    watchboard.inherit_watchboard(source, child)
    shutil.rmtree(tmp_path / 'source')
    assert not watchboard.has_watchboard(child)
    assert (watchboard._watchboard_root(child) / 'script.py').read_text() == 'fixture'


def test_workspace_projection_isolates_an_inconsistent_resident(tmp_path):
    """Workspace 投影隔离记录不一致的 Resident"""
    from management_portal.society_service.resident_workspace_storage import project_records

    bad, good = 'resident_aaaaaaaaaaaa', 'resident_bbbbbbbbbbbb'
    first, second = 'workspace_aaaaaaaaaaaa', 'workspace_bbbbbbbbbbbb'
    lineage = 'lineage_aaaaaaaaaaaa'
    rows = project_records(
        [{'resident_id': bad, 'lineage_id': lineage, 'owned_workspace_id': first},
         {'resident_id': good, 'lineage_id': lineage, 'owned_workspace_id': second}],
        {}, {first: str(tmp_path / 'old'), second: str(tmp_path / 'residents' / good / 'workspace')},
        {lineage: str(tmp_path)},
    )
    assert rows[0]['default_cwd'] == ''
    assert rows[1]['default_cwd'] == str(tmp_path / 'residents' / good / 'workspace')


def test_project_root_requires_registration_even_when_named_folder_exists(tmp_path, monkeypatch):
    """同名目录已存在时项目根仍须登记"""
    from management_portal.society_service import project_domain

    (tmp_path / 'Unregistered').mkdir()
    monkeypatch.setattr(project_domain, 'PROJECTS_ROOT', tmp_path)
    monkeypatch.setattr(project_domain.project_registry, 'load', lambda: {'projects': []})
    monkeypatch.setattr(project_domain.resource_client, 'project_roots', lambda: {})
    with pytest.raises((ValueError, KeyError)):
        project_folders.project_root_for_id('Unregistered')


def test_owned_workspace_projection_does_not_create_reference_bindings(tmp_path):
    """自有 Workspace 投影不创建引用绑定

    :module: platform.core"""
    from management_portal.society_service.resident_record_domain import (
        set_resident_owned_workspace_binding as _set_resident_owned_workspace_binding,
    )
    record = {"resident_id": "resident_aaaaaaaaaaaa", "folder_schema": "lineage-folder-v3", "workspace_path": str(tmp_path),
              "workspace_paths": [str(tmp_path / "unrequested")], "workspace_bindings": []}
    _set_resident_owned_workspace_binding(record)
    assert record["workspace_bindings"] == []
    assert record["workspace_paths"] == []


def test_reference_binding_replacement_rejects_missing_directory(tmp_path):
    """替换引用绑定时拒绝不存在的目录

    :module: platform.core"""
    from management_portal.society_service.resident_workspace_domain import (
        set_resident_reference_bindings as _set_resident_reference_bindings,
    )
    record = {"owned_workspace": {"path": str(tmp_path)}, "workspace_bindings": []}
    with pytest.raises(ValueError, match="不存在"):
        _set_resident_reference_bindings(record, [str(tmp_path / "missing")])
    assert record["workspace_bindings"] == []
