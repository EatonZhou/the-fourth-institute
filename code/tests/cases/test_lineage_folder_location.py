"""Lineage folders are computed from the Lineage's Project, never reached through entry links.

:design: decision_721c64f9bdcc, decision_d88306d0bd17
"""
from __future__ import annotations

import pytest

from management_portal.society_service import lineage_storage as storage
from tests.cases.instance_test_support import select_instance

LINEAGE = "lineage_123456abcdef"


def test_project_lineage_lives_in_project_agents_and_others_in_lineages_area(monkeypatch, tmp_path):
    """属于项目的 Lineage 目录在 <项目>/agents/<lineage_id>，不属于项目的在 lineages 区域

    目录按所属项目计算，不在项目知识库下，也不经过入口软链接。
    """
    instance = select_instance(monkeypatch, tmp_path / "instance")
    project = instance.area("projects") / "示例项目"
    assert storage.lineage_folder_for_project(LINEAGE, "") == instance.area("lineages") / LINEAGE
    assert storage.lineage_folder_for_project(LINEAGE, "示例项目") == project / "agents" / LINEAGE
    for project_id in ("../escape", "a/b", ".."):
        with pytest.raises(ValueError):
            storage.lineage_folder_for_project(LINEAGE, project_id)
    with pytest.raises(ValueError):
        storage.lineage_folder_for_project("not-a-lineage", "")


def test_relocation_moves_the_folder_without_leaving_a_link(monkeypatch, tmp_path):
    """修改所属项目时整个 Lineage 目录移到新位置，原位置不留入口

    目标已存在、目录名不是同一个 Lineage ID 时拒绝，不移动任何文件。
    """
    monkeypatch.setattr(storage, "lineages_root", lambda: tmp_path / "lineages")
    source = tmp_path / "lineages" / LINEAGE
    (source / "residents").mkdir(parents=True)
    (source / "LINEAGE.md").write_text("retained", encoding="utf-8")
    target = tmp_path / "projects" / "Project 中文" / "agents" / LINEAGE
    assert storage.relocate_lineage_folder(source, target) == target
    assert not source.exists() and not source.is_symlink()
    assert (target / "LINEAGE.md").read_text(encoding="utf-8") == "retained"
    with pytest.raises(storage.LineageStorageConflictError):
        storage.relocate_lineage_folder(target, target.with_name("lineage_000000000000"))
    occupied = tmp_path / "lineages" / LINEAGE
    occupied.mkdir()
    with pytest.raises(storage.LineageStorageConflictError):
        storage.relocate_lineage_folder(target, occupied)
    assert (target / "LINEAGE.md").exists()
    storage.relocate_lineage_folder(target, target)
    assert target.is_dir()
