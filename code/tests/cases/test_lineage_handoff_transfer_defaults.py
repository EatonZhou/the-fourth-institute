from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from management_portal.society_service import lineage_handoff_backend


def test_unconfigured_transfer_defaults_to_direct_in_both_entrypoints(tmp_path, monkeypatch, subtests, isolated_portal_case):
    """未配置交接方式时规范化和执行都按 direct 处理，不复制文件"""
    for transfer in [None, {}, {"mode": "direct"}, {"method": "direct"}]:
        with subtests.test(transfer=transfer), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                tmp_path = case_path
                source = tmp_path / "source"
                selected = source / "nested" / "example.txt"
                selected.parent.mkdir(parents=True)
                selected.write_text("synthetic handoff content", encoding="utf-8")
                unused_target = tmp_path / "unused-target"
                item = {
                    "item_id": "sample",
                    "source": {"path": str(source), "scope": "nested/example.txt"},
                    "target": {"path": str(unused_target)},
                }
                if transfer is not None:
                    item["transfer"] = transfer
                original = deepcopy(item)
                copier = Mock(side_effect=AssertionError("direct handoff must not copy"))
                monkeypatch.setattr(lineage_handoff_backend.shutil, "copytree", copier)
                monkeypatch.setattr(lineage_handoff_backend.shutil, "copy2", copier)
                monkeypatch.setattr(lineage_handoff_backend, "inspect_git_workspace", lambda _path: {})

                normalized = lineage_handoff_backend.normalize_handoff_fields(
                    {"items": [item]}, now="2026-09-12T00:00:00Z"
                )["items"][0]
                assert normalized["transfer"]["mode"] == "direct"
                assert normalized["transfer"]["method"] == "direct"
                direct_result = lineage_handoff_backend.materialize_handoff_item(
                    item, allowed_target_roots=[], checked_at="2026-09-12T00:00:00Z"
                )
                normalized_result = lineage_handoff_backend.materialize_handoff_item(
                    normalized, allowed_target_roots=[], checked_at="2026-09-12T00:00:00Z"
                )

                assert direct_result == normalized_result
                assert direct_result["target_path"] == str(source)
                assert direct_result["target_scope"] == "nested/example.txt"
                assert direct_result["file_count"] == 0
                assert direct_result["byte_size"] == 0
                assert not unused_target.exists()
                assert selected.read_text(encoding="utf-8") == "synthetic handoff content"
                assert item == original
                copier.assert_not_called()


def test_update_without_transfer_preserves_existing_choice(subtests, isolated_portal_case):
    """更新交接项时未给交接方式则保留原有的方式和参数"""
    for method in ["filesystem_copy", "git_worktree"]:
        with subtests.test(method=method), isolated_portal_case() as case_path:
            existing = {"items": [{
                "item_id": "sample",
                "source": {"path": "/synthetic/source"},
                "transfer": {"mode": "copy", "method": method, "parameters": {"branch": "fixture"}},
                "target": {"path": "/synthetic/target"},
            }]}
            normalized = lineage_handoff_backend.normalize_handoff_fields(
                {"items": [{"item_id": "sample", "label": "Renamed"}]},
                existing,
                now="2026-09-12T00:00:00Z",
            )

            assert normalized["items"][0]["transfer"]["mode"] == "copy"
            assert normalized["items"][0]["transfer"]["method"] == method
            assert normalized["items"][0]["transfer"]["parameters"]["branch"] == "fixture"
            assert normalized["items"][0]["label"] == "Renamed"


def test_explicit_copy_retains_existing_filesystem_copy_method_default() -> None:
    """只指定 copy 模式时默认使用 filesystem_copy"""
    normalized = lineage_handoff_backend.normalize_handoff_fields(
        {"items": [{"transfer": {"mode": "copy"}}]},
        now="2026-09-12T00:00:00Z",
    )

    assert normalized["items"][0]["transfer"]["mode"] == "copy"
    assert normalized["items"][0]["transfer"]["method"] == "filesystem_copy"


def test_explicit_invalid_transfer_is_not_replaced_by_default(subtests, isolated_portal_case):
    """显式给出的无效交接方式直接报错，不被替换为默认值"""
    for transfer, error in [
    ({"mode": "invalid"}, "工作交接方式"),
    ({"method": "invalid"}, "不支持的工作区准备方式"),
    ({"mode": "direct", "method": "git_worktree"}, "直接交接必须使用 direct"),
    ({"mode": "copy", "method": "direct"}, "创建副本必须选择"),
]:
        with subtests.test(transfer=transfer, error=error), isolated_portal_case() as case_path:
            with pytest.raises(ValueError, match=error):
                lineage_handoff_backend.normalize_handoff_fields(
                    {"items": [{"transfer": transfer}]}, now="2026-09-12T00:00:00Z"
                )


def test_default_direct_still_rejects_a_missing_source(tmp_path: Path) -> None:
    """默认的直接交接仍拒绝不存在的来源路径"""
    with pytest.raises(FileNotFoundError):
        lineage_handoff_backend.materialize_handoff_item(
            {"source": {"path": str(tmp_path / "missing")}},
            allowed_target_roots=[],
            checked_at="2026-09-12T00:00:00Z",
        )
