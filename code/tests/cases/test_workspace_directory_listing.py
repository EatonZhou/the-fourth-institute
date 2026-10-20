""":module: spec.design-governance"""
import pytest

from management_portal.society_service import workspace_domain


def test_directory_listing_is_complete_lazy_and_authorized(tmp_path, monkeypatch):
    """目录列表完整列出全部子项且不展开子目录，未认证或经符号链接访问时拒绝"""
    monkeypatch.setattr(workspace_domain, "SHARED_COLLABORATION_ROOTS", (tmp_path,))
    root = tmp_path / "work"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    (nested / "hidden-until-expanded.md").write_text("content")
    for i in range(230):
        (root / f"file-{i}.md").write_text("content")
    (root / "link").symlink_to(nested, target_is_directory=True)
    result = workspace_domain.get_workspace_link_file(str(root), actor="reader")
    assert len(result["children"]) == 231
    assert result["children"][0]["name"] == "nested"
    assert not any("content" in row for row in result["children"])
    assert not any(row["name"] == "hidden-until-expanded.md" for row in result["children"])
    assert workspace_domain.get_workspace_link_file(str(root), actor="reader", meta_only=True)["kind"] == "directory"
    with pytest.raises(PermissionError):
        workspace_domain.get_workspace_link_file(str(root), actor="")
    with pytest.raises(ValueError, match="symlink"):
        workspace_domain.get_workspace_link_file(str(root / "link"), actor="reader")
