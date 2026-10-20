from __future__ import annotations

import pytest

from management_portal.society_service import project_domain


def test_lineage_workbench_rejects_missing_project(subtests, isolated_portal_case):
    """Lineage 工作台未选择 Project 时报错要求选择已有 Project"""
    for payload in (
        {},
    ):
        with subtests.test(payload=payload), isolated_portal_case() as case_path:
            with pytest.raises(ValueError, match="需要选择已有 Project"):
                project_domain.validate_existing_project_selection(
                    payload,
                )


def test_lineage_workbench_accepts_only_resolved_existing_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lineage 工作台只接受经 project_root_for_id 解析为已有的 Project"""
    resolved: list[str] = []
    monkeypatch.setattr(
        project_domain,
        "project_root_for_id",
        lambda project_id: resolved.append(project_id),
    )

    project_domain.validate_existing_project_selection({
        "project_id": "RoboLab",
    })

    assert resolved == ["RoboLab"]
