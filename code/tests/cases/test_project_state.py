from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from management_portal import project_state
from management_portal.resources.providers import project as project_provider


def complete_payload(project_id: str = "Demo") -> dict:
    return {
        "schema_version": "tfi.project_state.v0.2",
        "project": {
            "id": project_id,
            "name": project_id,
            "status": "active",
            "current_phase": "unknown",
            "objective": "Deliver a durable project view.",
            "project_level_success_criteria": "unknown",
        },
        "snapshot": {
            "generated_at": "2026-07-30T05:54:36+08:00",
            "knowledge_revision": "sha256:test",
            "source_coverage": {
                "knowledge_items": 12,
                "git_checked": False,
            },
            "blind_spots": [],
        },
        "current_attention": [],
        "roadmap": {
            "confirmed_milestones": [],
            "candidate_milestones": [],
        },
        "capabilities": [{
            "id": "capability-unknown",
            "name": "Unknown delivery",
        }],
        "architecture": {
            "workstreams": [],
            "components": [],
            "interfaces": [],
            "dependencies": [],
            "ownership_gaps": [],
        },
        "decisions": {
            "active": [],
            "provisional": [],
            "superseded": [],
            "conflicted": [{
                "id": "conflict-demo",
                "statement": "Two owners claim the same lifecycle.",
            }],
        },
        "lineage": {"summary": "", "residents": []},
        "risks": [],
        "open_questions": [],
        "material_timeline": [],
        "knowledge_health": {},
        "human_review": {"count": 0, "highest_priority": []},
    }


def write_state(project_root: Path, payload: object) -> Path:
    path = project_root / project_state.PROJECT_STATE_SOURCE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def test_complete_project_state_normalizes_empty_and_unknown_sections_contracts(tmp_path, subtests, isolated_portal_case):
    """项目状态归一化空章节并明确缺失状态和来源边界"""
    with subtests.test(contract='test_complete_project_state_normalizes_empty_and_unknown_sections'), isolated_portal_case() as case_path:
        tmp_path = case_path
        write_state(tmp_path, complete_payload())
        result = project_state.load_project_state('Demo', tmp_path)
        assert result['view_schema_version'] == 'portal.project-page.v2'
        assert result['status'] == 'ready'
        assert result['source']['code'] == 'ok'
        assert result['roadmap']['confirmed_milestones'] == []
        assert result['lineage']['residents'] == []
        capability = result['capabilities'][0]
        assert [capability['decision_state'], capability['implementation_state'], capability['test_state'], capability['release_state'], capability['runtime_state']] == ['unknown'] * 5
        assert result['decisions']['conflicted'][0]['id'] == 'conflict-demo'
        assert result['brief']['headline'] == 'Deliver a durable project view.'
        assert result['brief']['signals'] == {'capabilities': 1, 'residents': 0, 'human_review': 0, 'conflicts': 1, 'risks': 0}
    with subtests.test(contract='test_project_state_marks_missing_sections_partial'), isolated_portal_case() as case_path:
        tmp_path = case_path
        payload = complete_payload()
        payload.pop('architecture')
        payload.pop('human_review')
        write_state(tmp_path, payload)
        result = project_state.load_project_state('Demo', tmp_path)
        assert result['status'] == 'partial'
        assert result['source']['code'] == 'partial_sections'
        assert result['missing_sections'] == ['architecture', 'human_review']
        assert result['architecture']['components'] == []
        assert result['human_review']['count'] == 0
    with subtests.test(contract='test_project_state_missing_file_is_not_an_error'), isolated_portal_case() as case_path:
        tmp_path = case_path
        result = project_state.load_project_state('Demo', tmp_path)
        assert result['status'] == 'missing'
        assert result['source']['code'] == 'missing_file'
        assert result['project']['id'] == 'Demo'
    with subtests.test(contract='test_project_state_rejects_source_escape'), isolated_portal_case() as case_path:
        tmp_path = case_path
        with pytest.raises(PermissionError, match='inside the registered Project'):
            project_state.load_project_state('Demo', tmp_path, source_relative_path='../PROJECT_STATE.yaml')


def test_project_state_names_the_project_only_by_its_registered_id(tmp_path):
    """项目状态仅使用注册标识命名项目"""
    # AI员工's PROJECT_STATE.yaml carried a project name of its own, and after a
    # rename the file's ID is the old one: either would give Agents a second name.
    payload = complete_payload("Old")
    payload["project"]["name"] = "AlignMe"
    write_state(tmp_path / "named", payload)
    (tmp_path / "empty").mkdir()
    for root in ("named", "empty"):
        project = project_state.load_project_state("AI员工", tmp_path / root)["project"]
        assert project["id"] == "AI员工" and "name" not in project


def test_project_state_builds_one_human_brief_from_project_truth() -> None:
    """项目事实汇成人类摘要并保留关注事项与下一步"""
    payload = complete_payload()
    payload["project"]["phase_inference"] = {
        "statement": "核心边界已收敛，正在补运行证明。"
    }
    payload["current_attention"] = [
        {
            "type": "fact",
            "statement": "关键垂直链已在真实环境运行。",
        },
        {
            "type": "risk",
            "statement": "交付证据仍不均衡。",
        },
    ]
    payload["roadmap"]["candidate_milestones"] = [
        {"title": "明确 deadline 对应的项目交付、owner 与验收标准"},
        {"title": "运行最小 Memory Sync 垂直链"},
    ]
    payload["human_review"] = {
        "count": 2,
        "highest_priority": ["明确 deadline 对应的交付与 owner。"],
    }
    payload["lineage"]["residents"] = [
        {"resident_id": "resident_000000000001"}
    ]
    payload["risks"] = [{"statement": "项目成功标准未定义。"}]

    brief = project_state._build_project_brief(
        {
            "project": project_state._normalize_project(
                payload["project"],
                "Demo",
            ),
            "current_attention": payload["current_attention"],
            "roadmap": payload["roadmap"],
            "human_review": payload["human_review"],
            "lineage": payload["lineage"],
            "decisions": payload["decisions"],
            "capabilities": payload["capabilities"],
            "risks": payload["risks"],
            "open_questions": [],
        }
    )

    assert brief["current_state"] == "核心边界已收敛，正在补运行证明。"
    assert brief["progress"] == "关键垂直链已在真实环境运行。"
    assert brief["needs_attention"] == "明确 deadline 对应的交付与 owner。"
    assert brief["next_move"] == "运行最小 Memory Sync 垂直链"
    assert brief["next_move_state"] == "candidate"
    assert brief["signals"]["residents"] == 1
    assert brief["signals"]["human_review"] == 2


def test_project_state_returns_renderable_invalid_contracts(tmp_path, subtests, isolated_portal_case):
    """无效项目状态返回可渲染错误及具体原因"""
    for contents, code in [
        ("project: [\n", "invalid_yaml"),
        (
            yaml.safe_dump({
                **complete_payload(),
                "schema_version": "tfi.project_state.v9",
            }),
            "unsupported_schema",
        ),
        (
            yaml.safe_dump(complete_payload("Other")),
            "project_id_mismatch",
        ),
    ]:
        with subtests.test(contents=contents, code=code), isolated_portal_case() as case_path:
            tmp_path = case_path
            path = tmp_path / project_state.PROJECT_STATE_SOURCE
            path.parent.mkdir(parents=True)
            path.write_text(contents, encoding="utf-8")

            result = project_state.load_project_state("Demo", tmp_path)

            assert result["status"] == "invalid"
            assert result["source"]["status"] == "invalid"
            assert result["source"]["code"] == code
            assert result["project"]["id"] == "Demo"


def test_project_provider_reads_state_only_from_registered_project_root_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """项目状态从注册目录读取且未知项目返回不存在"""
    with subtests.test(contract='test_project_provider_reads_state_only_from_registered_project_root'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            projects_root = tmp_path / 'projects'
            project_root = projects_root / 'demo'
            project_root.mkdir(parents=True)
            write_state(project_root, complete_payload('demo'))
            monkeypatch.setattr(project_provider, 'PROJECTS_ROOT', projects_root)
            monkeypatch.setattr(project_provider, 'load_projects_payload', lambda *_args: {'projects': [{'id': 'demo', 'path': str(project_root)}]})
            result = project_provider.project_state('demo')
            assert result['resource_id'] == 'demo'
            assert result['status'] == 'ready'
            assert result['project']['id'] == 'demo'
    with subtests.test(contract='test_project_provider_reports_project_not_found'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(project_provider, 'PROJECTS_ROOT', tmp_path)
            monkeypatch.setattr(project_provider, 'load_projects_payload', lambda *_args: {'projects': []})
            with pytest.raises(KeyError, match='Project not found'):
                project_provider.project_state('missing')
