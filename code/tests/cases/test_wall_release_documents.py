from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal import wall_backend as wall
from management_portal import wall_documents as documents
from management_portal import wall_release_documents as release


@pytest.fixture
def project(tmp_path, monkeypatch):
    record = {"id": "fixture", "title": "Example", "root": str(tmp_path), "project_id": "demo"}
    monkeypatch.setattr(wall, "_read_projects", lambda: {"fixture": record})
    access = mock.Mock()
    monkeypatch.setattr(wall, "get_portal_access_store", lambda: access)
    (tmp_path / "SPEC.md").write_text("# Index\n\n## Core\n- [Design](design.md)\n")
    (tmp_path / "design.md").write_text("---\nversion:\n  number: 7\n---\n# Design\n\n## Rules\n- Stable behavior.\n\n```md\n# literal\n```\n\n> ## Quoted heading\n")
    return record, access


def generate():
    return wall.handle_post("/api/wall/documentation/preview", {"project": "fixture"}, "reader")


def test_preview_traces_source_bytes_and_preserves_markdown_without_writes(project):
    """发布文档预览记录每个来源的哈希与版本，保留代码块和引用里的 Markdown 且不写文件"""
    record, access = project
    root = Path(record["root"])
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    result = generate()
    manifest = result["manifest"]
    access.is_active.assert_called_with("reader")
    assert manifest["spec"]["sha256"] == hashlib.sha256(before["SPEC.md"]).hexdigest()
    source = manifest["sources"][0]
    assert source["version"] == 7
    assert source["sha256"] == hashlib.sha256(before["design.md"]).hexdigest()
    assert source["module_path"] == ["Core"]
    assert source["title"] == "Design"
    assert "## 1. Design" in result["markdown"]
    assert "### Rules" in result["markdown"]
    assert "```md\n# literal\n```" in result["markdown"]
    assert "> ## Quoted heading" in result["markdown"]
    assert result["document_sha256"] == hashlib.sha256(result["markdown"].encode()).hexdigest()
    assert before == {path.name: path.read_bytes() for path in root.iterdir()}


def test_only_spec_sources_are_compiled_in_index_order(project):
    """发布文档只按 SPEC.md 索引顺序编入当前设计来源，跳过改进项、未索引文件和重复条目"""
    record, _access = project
    root = Path(record["root"])
    (root / "SPEC.md").write_text("# Index\n- [Design](design.md)\n- [Improvements](IMPROVE.md)\n- thinking_aaaaaaaaaaaa\n- [Second](second.md)\n- [Duplicate](design.md)\n")
    (root / "second.md").write_text("Second\n======\n\nAnother current behavior.\n")
    (root / "IMPROVE.md").write_text("- [Future](future.md)\n")
    (root / "future.md").write_text("# Future\nNot current.\n")
    (root / ".wall").mkdir()
    (root / ".wall/design.md").write_text("# Unindexed\nNot indexed.\n")
    result = generate()
    assert [entry["address"] for entry in result["manifest"]["sources"]] == ["design.md", "second.md"]
    assert result["manifest"]["sources"][1]["title"] == "Second"
    assert result["manifest"]["sources"][1]["version"] is None
    assert "Not current" not in result["markdown"] and "Not indexed" not in result["markdown"]
    assert len(documents.index_sources(root)) > 2


def test_project_denial_happens_before_reading_sources(project, monkeypatch):
    """无 Project 访问权的用户在读取任何来源之前即被拒绝"""
    _record, access = project
    access.is_active.return_value = False
    read = mock.Mock()
    monkeypatch.setattr(release, "read_source", read)
    with pytest.raises(PermissionError):
        generate()
    read.assert_not_called()


def test_resource_reads_forward_actor_and_recheck_acl_on_each_generation(project, monkeypatch):
    """资源来源以请求者身份读取且每次生成都重新鉴权，无权时报错且不泄露标题"""
    record, _access = project
    root = Path(record["root"])
    (root / "SPEC.md").write_text("# Index\n- decision_aaaaaaaaaaaa\n")
    reader = mock.Mock(return_value=(b"---\nversion:\n  number: 2\n---\n# Authorized\n\nbody\n", "text/markdown", {}))
    monkeypatch.setattr(documents, "get_resource_service", lambda: SimpleNamespace(read_content=reader))
    assert generate()["manifest"]["sources"][0]["version"] == 2
    reader.assert_called_once_with("decision_aaaaaaaaaaaa", actor="reader")
    reader.side_effect = PermissionError("private title must not be disclosed")
    with pytest.raises(PermissionError, match="未生成部分文档") as failure:
        generate()
    assert "private title" not in str(failure.value)
    assert reader.call_count == 2


@pytest.mark.parametrize("failure", [KeyError("missing"), FileNotFoundError("missing")])
def test_missing_source_does_not_silently_export_partial_document(project, monkeypatch, failure):
    """SPEC.md 引用的正文缺失时报错，不导出残缺文档"""
    monkeypatch.setattr(release, "read_source", mock.Mock(side_effect=failure))
    with pytest.raises(ValueError, match="正文缺失"):
        generate()


def test_index_change_during_generation_rejects_result(project, monkeypatch):
    """生成发布文档期间 SPEC.md 发生变化时拒绝本次结果"""
    record, _access = project
    original = release.read_source

    def changing_read(root, address, actor):
        text = original(root, address, actor)
        (root / "SPEC.md").write_text("# Changed\n")
        return text

    monkeypatch.setattr(release, "read_source", changing_read)
    with pytest.raises(ValueError, match="已变化"):
        generate()


def test_source_change_changes_fingerprint_without_cache(project):
    """来源不变时发布文档指纹相同，来源内容变化后指纹随之改变"""
    record, _access = project
    first = generate()["manifest"]["fingerprint"]
    assert generate()["manifest"]["fingerprint"] == first
    (Path(record["root"]) / "design.md").write_text("# Changed\nNew behavior.\n")
    assert generate()["manifest"]["fingerprint"] != first


def test_local_source_cannot_escape_registered_root(project, tmp_path):
    """SPEC.md 引用 Project 根目录之外的本地文件时拒绝生成"""
    record, _access = project
    root = Path(record["root"])
    (root / "SPEC.md").write_text("- [Secret](../outside.md)\n")
    with pytest.raises(PermissionError):
        generate()


@pytest.mark.parametrize("limit", ["MAX_SOURCE_COUNT", "MAX_INPUT_BYTES"])
def test_generation_is_bounded(project, monkeypatch, limit):
    """发布文档生成超过来源数量或输入字节上限时报错"""
    monkeypatch.setattr(release, limit, 0)
    with pytest.raises(ValueError):
        generate()


def test_empty_or_missing_spec_has_actionable_failure(project):
    """SPEC.md 没有设计条目或不存在时，生成发布文档给出可操作的错误"""
    record, _access = project
    index = Path(record["root"]) / "SPEC.md"
    index.write_text("# No Design yet\n")
    with pytest.raises(ValueError, match="没有可生成"):
        generate()
    index.unlink()
    with pytest.raises(ValueError, match="没有 SPEC"):
        generate()
