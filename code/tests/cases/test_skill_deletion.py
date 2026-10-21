from __future__ import annotations

from pathlib import Path
from unittest import mock

from knowledge_board_lan import knowledge_backend
from skill_board_lan import skill_backend


class _SkillService:
    def __init__(self, record: dict) -> None:
        self.record = record

    @staticmethod
    def validate_id(resource_id: str, resource_type: str) -> None:
        if resource_type != "skill" or resource_id != "skill_0123456789ab":
            raise ValueError("invalid Skill id")

    def get(self, resource_id: str, *, actor: str = "") -> dict:
        if resource_id != "skill_0123456789ab":
            raise KeyError(resource_id)
        return self.record


def _skill_record(path: Path, root: Path, *, single_file: bool = False) -> dict:
    return {
        "resource_id": "skill_0123456789ab",
        "resource_type": "skill",
        "facets": ["skill", "knowledge"],
        "source": {"adapter": "skill", "path": str(path)},
        "attributes": {
            "content_root": str(root),
            "single_file": single_file,
        },
    }


def test_delete_shared_skill_moves_the_entire_package_to_trash_contracts(tmp_path, subtests, isolated_portal_case):
    """删除 Skill 时整个包或单文件移入回收站，拒绝嵌套地址和未登记路径"""
    with subtests.test(contract='test_delete_shared_skill_moves_the_entire_package_to_trash'), isolated_portal_case() as case_path:
        tmp_path = case_path
        shared_root = tmp_path / 'shared'
        third_party_root = shared_root / 'third-party'
        package = shared_root / 'example-skill'
        primary = package / 'SKILL.md'
        companion = package / 'references' / 'guide.md'
        companion.parent.mkdir(parents=True)
        primary.write_text('# Example\n', encoding='utf-8')
        companion.write_text('# Guide\n', encoding='utf-8')
        service = _SkillService(_skill_record(primary, package))
        with mock.patch('management_portal.resources.get_resource_service', return_value=service), mock.patch.object(skill_backend, 'SHARED_SKILLS_ROOT', shared_root), mock.patch.object(skill_backend, 'THIRD_PARTY_SKILLS_ROOT', third_party_root), mock.patch.object(skill_backend, '_parse_projects', return_value=[]):
            status, result = skill_backend.delete_skill('skill_0123456789ab')
        assert status == 200
        assert result['deleted'] is True
        assert result['path'] == str(package)
        assert not package.exists()
        assert (shared_root / '.trash' / 'example-skill' / 'SKILL.md').is_file()
        assert (shared_root / '.trash' / 'example-skill' / 'references' / 'guide.md').is_file()
    with subtests.test(contract='test_delete_project_single_file_skill_uses_project_knowledge_trash'), isolated_portal_case() as case_path:
        tmp_path = case_path
        shared_root = tmp_path / 'shared'
        project_root = tmp_path / 'Example'
        skill_file = project_root / 'knowledge_base' / 'skill' / 'example.md'
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text('# Example\n', encoding='utf-8')
        service = _SkillService(_skill_record(skill_file, skill_file.parent, single_file=True))
        with mock.patch('management_portal.resources.get_resource_service', return_value=service), mock.patch.object(skill_backend, 'SHARED_SKILLS_ROOT', shared_root), mock.patch.object(skill_backend, 'THIRD_PARTY_SKILLS_ROOT', shared_root / 'third-party'), mock.patch.object(skill_backend, '_parse_projects', return_value=[{'id': 'Example', 'path': str(project_root), 'status': 'active'}]):
            status, result = skill_backend.delete_skill('skill_0123456789ab')
        assert status == 200
        assert not skill_file.exists()
        assert result['trash_path'] == str(project_root / 'knowledge_base' / '.trash' / 'example.md')
        assert Path(result['trash_path']).read_text(encoding='utf-8') == '# Example\n'
    with subtests.test(contract='test_delete_skill_rejects_nested_addresses_and_unregistered_paths'), isolated_portal_case() as case_path:
        tmp_path = case_path
        outside_package = tmp_path / 'outside' / 'example-skill'
        primary = outside_package / 'SKILL.md'
        primary.parent.mkdir(parents=True)
        primary.write_text('# Example\n', encoding='utf-8')
        service = _SkillService(_skill_record(primary, outside_package))
        with mock.patch('management_portal.resources.get_resource_service', return_value=service), mock.patch.object(skill_backend, 'SHARED_SKILLS_ROOT', tmp_path / 'shared'), mock.patch.object(skill_backend, 'THIRD_PARTY_SKILLS_ROOT', tmp_path / 'shared' / 'third-party'), mock.patch.object(skill_backend, '_parse_projects', return_value=[]):
            nested_status, _nested = skill_backend.delete_skill('skill_0123456789ab/file/references/guide.md')
            outside_status, outside = skill_backend.delete_skill('skill_0123456789ab')
        assert nested_status == 400
        assert outside_status == 403
        assert outside['error'] == 'skill package path is not deletable'
        assert primary.is_file()


def test_knowledge_delete_delegates_skill_packages_to_skill_backend() -> None:
    """知识库删除 Skill 条目时交给 Skill 后端处理"""
    skill_id = "skill_0123456789ab"
    result = {
        "deleted": True,
        "resource_type": "skill",
        "skill_id": skill_id,
        "kb_id": skill_id,
        "path": "/share/Skills/example",
        "trash_path": "/share/Skills/.trash/example",
    }
    with (
        mock.patch.object(
            knowledge_backend,
            "resolve_item_record",
            return_value={"kb_id": skill_id, "type": "skill"},
        ),
        mock.patch.object(
            knowledge_backend.skill_backend,
            "delete_skill",
            return_value=(200, result),
        ) as delete_skill,
    ):
        deleted = knowledge_backend.delete_item(skill_id)

    assert deleted == {**result, "project_ids": []}
    delete_skill.assert_called_once_with(skill_id, actor="")
