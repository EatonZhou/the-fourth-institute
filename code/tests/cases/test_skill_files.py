from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from skill_board_lan import skill_backend


class _SkillService:
    def __init__(self, primary: Path, root: Path) -> None:
        self.actors: list[str] = []
        self.record = {
            "resource_id": "skill_0123456789ab",
            "resource_type": "skill",
            "facets": ["skill", "knowledge"],
            "source": {
                "adapter": "skill",
                "path": str(primary),
            },
            "attributes": {
                "content_root": str(root),
                "single_file": False,
            },
        }

    @staticmethod
    def validate_id(resource_id: str, resource_type: str) -> None:
        if (
            resource_id != "skill_0123456789ab"
            or resource_type != "skill"
        ):
            raise ValueError("invalid Skill id")

    def get(self, resource_id: str, *, actor: str = "") -> dict:
        if resource_id != "skill_0123456789ab":
            raise KeyError(resource_id)
        self.actors.append(actor)
        return self.record


def test_skill_package_primary_file_and_catalog_order_contracts(tmp_path, subtests, isolated_portal_case):
    """Skill 包的主说明选择顺序与目录分组顺序

    Skill 包按 SKILL.md、同名 Markdown、skill.yaml 的 primary_file 顺序确定主说明，拒绝包外路径和重复声明；Skill 目录按 shared、project、third_party 分组排序。

    :design: decision_f48c2514bf33
    """
    with subtests.test(contract='test_skill_primary_file_has_one_predictable_precedence'), isolated_portal_case() as case_path:
        package = case_path / "example-skill"
        nested = package / "references"
        nested.mkdir(parents=True)
        skill = package / "SKILL.md"
        package_named = package / "example-skill.md"
        router = package / "router.md"
        nested_skill = nested / "SKILL.md"
        for path in (skill, package_named, router, nested_skill):
            path.write_text(f"# {path.name}\n", encoding="utf-8")
        files = skill_backend._candidate_markdown_files(package)
        assert skill_backend._choose_primary_file(package, files) == skill

        skill.unlink()
        files = skill_backend._candidate_markdown_files(package)
        assert skill_backend._choose_primary_file(package, files) == package_named

        (package / "skill.yaml").write_text(
            "primary_file: references/SKILL.md\n",
            encoding="utf-8",
        )
        assert skill_backend._choose_primary_file(package, files) == nested_skill.resolve()

        (package / "skill.yaml").write_text(
            "primary_file: ../outside.md\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="package Markdown"):
            skill_backend._choose_primary_file(package, files)

        (package / "skill.yml").write_text(
            "primary_file: references/SKILL.md\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="both skill.yaml and skill.yml"):
            skill_backend._choose_primary_file(package, files)
    with subtests.test(contract='test_skill_catalog_preserves_source_group_precedence'), isolated_portal_case() as case_path:
        controller = (Path(__file__).resolve().parents[2] / 'management_portal/static/resource_catalog.js').read_text(encoding='utf-8')
        assert '({ shared: 0, project: 1, third_party: 2 })' in controller
        assert 'const rankDifference = sourceRank(left) - sourceRank(right);' in controller


def test_skill_save_resolves_resource_with_current_actor(tmp_path: Path) -> None:
    """保存 Skill 文本时以当前用户身份解析资源"""
    package = tmp_path / "shared" / "example-skill"
    primary = package / "SKILL.md"
    package.mkdir(parents=True)
    primary.write_text("# Original\n", encoding="utf-8")
    service = _SkillService(primary, package)

    with (
        mock.patch(
            "management_portal.resources.get_resource_service",
            return_value=service,
        ),
        mock.patch.object(skill_backend, "SHARED_SKILLS_ROOT", tmp_path / "shared"),
    ):
        status, payload = skill_backend.save_address_text(
            "skill_0123456789ab",
            "# Updated\n",
            actor="sample_reader",
        )

    assert status == 200
    assert payload["skill_id"] == "skill_0123456789ab"
    assert service.actors == ["sample_reader"]
    assert primary.read_text(encoding="utf-8") == "# Updated\n"


def test_skill_save_with_stale_version_keeps_the_newer_text(tmp_path: Path) -> None:
    """带着过期版本保存 Skill 时拒绝写入，保留别人的新内容并报告当前版本"""
    import hashlib

    from knowledge_board_lan import knowledge_backend

    package = tmp_path / "shared" / "example-skill"
    primary = package / "SKILL.md"
    package.mkdir(parents=True)
    primary.write_text("# Original\n", encoding="utf-8")
    read_version = hashlib.sha256(primary.read_bytes()).hexdigest()
    primary.write_text("# Someone else\n", encoding="utf-8")
    current = hashlib.sha256(primary.read_bytes()).hexdigest()
    service = _SkillService(primary, package)

    with (
        mock.patch(
            "management_portal.resources.get_resource_service",
            return_value=service,
        ),
        mock.patch.object(skill_backend, "SHARED_SKILLS_ROOT", tmp_path / "shared"),
    ):
        status, payload = skill_backend.save_address_text(
            "skill_0123456789ab", "# Mine\n", actor="sample_reader", expected_version=read_version,
        )
        assert (status, payload["current_version"]) == (409, current)
        assert primary.read_text(encoding="utf-8") == "# Someone else\n"
        with mock.patch.object(knowledge_backend, "resolve_item_address", return_value={
            "kb_id": "skill_0123456789ab", "address": "skill_0123456789ab", "type": "skill",
            "selected_path": str(primary), "selected_file_id": "primary",
        }), pytest.raises(knowledge_backend.KnowledgeVersionConflict) as conflict:
            knowledge_backend.update_item_text(
                "skill_0123456789ab", "# Mine\n", actor="sample_reader", expected_version=read_version,
            )
        assert conflict.value.current_version == current
        status, _ = skill_backend.save_address_text(
            "skill_0123456789ab", "# Mine\n", actor="sample_reader", expected_version=current,
        )
    assert status == 200
    assert primary.read_text(encoding="utf-8") == "# Mine\n"
