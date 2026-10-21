from __future__ import annotations

import json


from management_portal.test_framework.catalog import ROOT, CatalogRepository
from management_portal.test_framework.models import TestCase as FrameworkTestCase
from management_portal.test_framework.planner import RunPlanner
from management_portal.test_framework.registry import RunnerRegistry


def test_global_resource_search_tau_is_selectable_and_read_only(isolated_portal_case):
    """全局资源搜索 TAU 用例可选中执行，且没有外部副作用"""
    with isolated_portal_case():
        definitions = json.loads((ROOT / "tests/cases/tau_cases.json").read_text(encoding="utf-8"))
        source, = [case for case in definitions["cases"] if case["key"] == "tau.portal.global-resource-search"]
        source_case = FrameworkTestCase.parse(source)
        selected, = CatalogRepository().query({"case_ids": [source_case.id]})
        assert selected == source_case
        implementation, = selected.implementations
        assert implementation.runner == "tau"
        assert implementation.risk == "no-external-side-effects"
        assert "Ctrl+K" in implementation.task_prompt
        assert "全部类型" in implementation.task_prompt
        assert "Resident" in implementation.task_prompt
        plan = RunPlanner(CatalogRepository(), RunnerRegistry(tau_available=True)).preview({
            "select": {"case_ids": [source_case.id]},
            "implementation": {"runners": ["tau"], "profile": "live.controlled"},
        })
        assert plan["executable"] is True
        assert plan["selected_implementation_count"] == 1


def test_resident_feature_tau_cases_are_selectable_with_their_real_use_tasks(subtests, isolated_portal_case):
    """Resident 推荐和 Telepus 使用两个 TAU 用例可单独选中并带真实任务"""
    for case_key, module_id, design_ref in [
        (
            "tau.resident.which-resident-recommendation",
            "resident.identity",
            "decision_8fe87d812a4c",
        ),
        (
            "tau.resident.telepus-portal-use",
            "resident.conversation",
            "decision_692a484e9bea",
        ),
    ]:
        with subtests.test(case_key=case_key, module_id=module_id, design_ref=design_ref), isolated_portal_case() as case_path:
            definitions = json.loads((ROOT / "tests/cases/tau_cases.json").read_text(encoding="utf-8"))
            matches = [case for case in definitions["cases"] if case["key"] == case_key]
            assert len(matches) == 1
            source_case = FrameworkTestCase.parse(matches[0])
            repository = CatalogRepository()
            selected_cases = repository.query({"case_ids": [source_case.id]})

            assert len(selected_cases) == 1
            assert selected_cases[0] == source_case
            assert source_case.lifecycle == "active"
            assert source_case.importance == "P0"
            assert source_case.module == module_id
            assert design_ref in source_case.design_refs
            assert {"functional", "behavior", "ui"}.issubset(source_case.aspects)
            assert len(source_case.implementations) == 1
            implementation = source_case.implementations[0]
            assert implementation.runner == "tau"
            assert implementation.task_prompt.strip()
            assert implementation.risk == "external-side-effects"

            plan = RunPlanner(repository, RunnerRegistry(tau_available=True)).preview({
                "select": {"case_ids": [source_case.id]},
                "implementation": {"runners": ["tau"], "profile": "live.controlled"},
            })

            assert plan["executable"] is True
            assert plan["campaign"] == "tau"
            assert plan["excluded"] == []
            assert plan["selected_implementation_count"] == 1
            planned_case = plan["selected"][0]
            assert planned_case["implementation"] == implementation.public()
            assert planned_case["profile"] == "live.controlled"
            assert planned_case["risk"] == "external-side-effects"
            assert plan["execution"]["groups"][0]["module_id"] == module_id


def test_core_workflow_tau_cases_are_separate_selectable_real_use_tasks(subtests, isolated_portal_case):
    """HATG、资源、代码仓库和文章四个核心工作流 TAU 用例可单独选中并在对应模块运行"""
    expected = {
        "tau.hatg.general-orchestration-lifecycle": {
            "id": "test_fb827308b602",
            "module": "lineage.hatg",
            "importance": "P0",
            "design_ref": "decision_4adcc1ff2f2c",
            "lineage_fixture": True,
        },
        "tau.resource.full-mutation-index-lifecycle": {
            "id": "test_4aaf927bc094",
            "module": "resource.service",
            "importance": "P0",
            "design_ref": "decision_cf27d39e39cd",
            "lineage_fixture": False,
        },
        "tau.repository.workspace-edit-git-lifecycle": {
            "id": "test_c9ace10ade4e",
            "module": "workflow.repository",
            "importance": "P1",
            "design_ref": "decision_6476c69b33da",
            "lineage_fixture": False,
        },
        "tau.article.workspace-edit-build-lifecycle": {
            "id": "test_152ed7d1420d",
            "module": "workflow.article",
            "importance": "P1",
            "design_ref": "decision_956154cd02f6",
            "lineage_fixture": False,
        },
    }
    definitions = json.loads((ROOT / "tests/cases/tau_cases.json").read_text(encoding="utf-8"))
    by_key = {case["key"]: FrameworkTestCase.parse(case) for case in definitions["cases"]}
    repository = CatalogRepository()

    for case_key, contract in expected.items():
        with subtests.test(case_key=case_key), isolated_portal_case():
            source_case = by_key[case_key]
            assert source_case.id == contract["id"]
            assert source_case.lifecycle == "active"
            assert source_case.importance == contract["importance"]
            assert source_case.module == contract["module"]
            assert contract["design_ref"] in source_case.design_refs
            assert {"functional", "behavior", "ui", "architecture-conformance"}.issubset(source_case.aspects)

            implementation, = source_case.implementations
            assert implementation.runner == "tau"
            assert implementation.risk == "external-side-effects"
            assert bool(implementation.lineage_fixture) is contract["lineage_fixture"]
            assert "核验" in implementation.task_prompt and ("测试实例" in implementation.task_prompt or "受测实例" in implementation.task_prompt)
            assert "实际查看" in implementation.task_prompt

            selected, = repository.query({"case_ids": [source_case.id]})
            assert selected == source_case
            plan = RunPlanner(repository, RunnerRegistry(tau_available=True)).preview({
                "select": {"case_ids": [source_case.id]},
                "implementation": {"runners": ["tau"], "profile": "live.controlled"},
            })
            assert plan["executable"] is True
            assert plan["campaign"] == "tau"
            assert plan["selected_implementation_count"] == 1
            assert plan["execution"]["groups"][0]["module_id"] == contract["module"]
