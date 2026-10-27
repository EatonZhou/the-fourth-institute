from pathlib import Path

import pytest

from management_common.skill_paths import normalize_additional_skills
from management_portal.society_service import resident_capability_materials as materials


@pytest.fixture
def skill_root(tmp_path, monkeypatch):
    skill = tmp_path / "example-skill" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("Use this skill when requested.")
    monkeypatch.setattr(materials, "normalize_additional_skills",
                        lambda refs: normalize_additional_skills(refs, skill_root=str(tmp_path)))
    monkeypatch.setattr(materials, "portal_path", lambda _: tmp_path.parent, raising=False)
    return skill


def test_non_list_supplied_material_is_not_iterated_as_skill_names(skill_root):
    assert materials.selected_skills({}, "example-skill", resident_id="resident_test") == ([], [])


def test_disabled_path_also_removes_equivalent_supplied_path(skill_root):
    alternate = str(skill_root.parent / ".." / "example-skill" / "SKILL.md")
    settings = {"capability_config": {"skills": [{"ref": str(skill_root), "enabled": False}]}}
    assert materials.selected_skills(settings, [alternate], resident_id="resident_test")[0] == []


def test_resource_skill_is_resolved_as_own_resident_and_recorded_as_reference(skill_root, monkeypatch):
    from management_portal.execution import harness_isa_tool
    calls = []
    def resolve(actor, payload):
        calls.append((actor, payload))
        return {"status": "committed", "result": {"target": {"resolved_path": str(skill_root)}}}
    monkeypatch.setattr(harness_isa_tool, "run_instruction", resolve)
    refs, observations = materials.selected_skills({"capability_config": {"skills": [
        {"ref": "skill_aaaaaaaaaaaa", "required": True}]}}, [], resident_id="resident_test")
    assert refs == [str(skill_root)] and calls[0][0] == "resident_test"
    assert observations[0]["body_sent"] is False


def test_unavailable_optional_is_unknown_and_required_fails(skill_root):
    optional = {"capability_config": {"skills": [{"ref": "absent-skill"}]}}
    assert materials.selected_skills(optional, [], resident_id="resident_test")[1][0]["observation"] == "unknown"
    optional["capability_config"]["skills"][0]["required"] = True
    with pytest.raises(ValueError, match="Required Skill unavailable"):
        materials.selected_skills(optional, [], resident_id="resident_test")
