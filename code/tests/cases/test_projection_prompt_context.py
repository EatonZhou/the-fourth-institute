"""
:importance: P1
:design: decision_f67ae5e70793, decision_4bc03b20c30f, decision_34158fdd4078
"""
import json

import pytest

from management_portal.workbench_projections.prompt_context import PROMPT_BUDGET, fit_prompt, resource_snapshot
from management_portal.workbench_projections.service import ProjectionService
from management_portal.workbench_projections.store import ProjectionError


def test_large_generation_context_uses_versioned_authorized_references(tmp_path):
    """大型生成上下文使用带版本的授权资源引用"""
    from management_portal.workbench_projections.store import Store

    service = ProjectionService(Store())
    app = {"workbench_id": "workbench_111111111111", "revision": 7, "title": "Existing page", "bindings": {}, "html": "<section>" + "content" * 20000 + "</section>"}
    graph = {"lineage_id": "lineage_111111111111", "revision": 9, "title": "Team", "nodes": [{"resident_id": "resident_111111111111", "title": "Researcher", "last_run": {"summary": "trace" * 20000}}]}
    requirements = "Keep every user requirement: " + "detail " * 600
    prompt = service.generation_prompt(app, graph, requirements, "job_prompt_context")
    assert len(prompt) <= PROMPT_BUDGET
    assert requirements in prompt
    assert app["workbench_id"] in prompt and graph["lineage_id"] in prompt
    assert '"revision": 7' in prompt and '"revision": 9' in prompt
    assert '"read_required": true' in prompt
    assert app["html"] not in prompt and graph["nodes"][0]["last_run"]["summary"] not in prompt
    assert "先完整读取 SDK 文档：" in prompt and "/workbench_projections/sdk.md" in prompt


def test_prompt_budget_never_silently_truncates_user_requirements_contracts(subtests, isolated_portal_case):
    """提示预算不足时拒绝静默截断用户要求"""
    with subtests.test(contract='test_prompt_budget_never_silently_truncates_user_requirements'), isolated_portal_case() as case_path:
        snapshots = {'graph': resource_snapshot('lineage_111111111111', 3, {'program': 'large' * 20000})}
        with pytest.raises(ProjectionError) as error:
            fit_prompt(lambda values: 'user request' * 3000 + json.dumps(values), snapshots)
        assert error.value.code == 'projection.prompt_too_large'
    with subtests.test(contract='test_small_resource_snapshot_remains_inline_with_identity'), isolated_portal_case() as case_path:
        assert resource_snapshot('workbench_111111111111', 4, {'html': '<p>sample</p>'}) == {'resource_address': 'workbench_111111111111', 'revision': 4, 'html': '<p>sample</p>'}
