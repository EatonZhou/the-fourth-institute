"""
:module: workflow.wall
:design: decision_06defc47e960
"""
from __future__ import annotations

import multiprocessing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal import wall_backend as wall, wall_documents as docs
from management_portal.society_service.errors import SocietyServiceUnavailable
from tests.cases.test_wall import wall_project  # noqa: F401
from tests.cases.test_wall_resident_cleanup import allocation  # noqa: F401












def test_wall_mixed_case_index_and_late_link_target(tmp_path):
    """WALL 沿用大小写混写的索引文件，链接目标出现后才列入，report 文档不列入"""
    (tmp_path / 'iMProve.Md').write_text('- [feature](feature.md)\n')
    assert docs.index_sources(tmp_path) == []
    (tmp_path / 'feature.md').write_text('# Feature\nContent\n')
    assert [source.address for source in docs.index_sources(tmp_path)] == ['feature.md']
    wall._ensure_local_index(tmp_path, 'second.md', 'improvement')
    assert not (tmp_path / 'IMPROVE.md').exists()
    assert 'second.md' in (tmp_path / 'iMProve.Md').read_text()
    (tmp_path / 'feature.md').write_text('---\nkind: report\n---\n# Report\n')
    assert not any(source.address == 'feature.md' for source in docs.index_sources(tmp_path))


def test_wall_reject_preserves_fixed_project_residents(wall_project, allocation):
    """WALL 驳回计划时保留固定分配的 Project Resident，不释放它"""
    roles, releases, _ = allocation
    roles['wall-work:portal:regression'] = 'fixed'
    path = wall_project / 'plan.md'
    path.write_text('<!-- wall: {"resident_id": "fixed"} -->\n' + path.read_text())
    wall.action({'action': 'reject', 'block': 'plan.md#plan'}, 'owner')
    assert roles['wall-work:portal:regression'] == 'fixed'
    assert not any('fixed' in removed for removed in releases)


def test_wall_resource_write_carries_expected_version(monkeypatch, tmp_path):
    """WALL 写入资源文档时带上期望版本，版本冲突时报错"""
    update = mock.Mock(side_effect=ValueError('version conflict'))
    monkeypatch.setattr(docs, 'get_resource_service', lambda: SimpleNamespace(update=update))
    with pytest.raises(ValueError, match='version conflict'):
        docs.write_source(tmp_path, 'decision_0123456789ab', 'changed', 'alice', expected_version=7)
    assert update.call_args.args[1]['expected_version'] == 7


