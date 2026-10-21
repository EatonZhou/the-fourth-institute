"""A Campaign freezes its own TAU declaration, not unrelated JSON neighbours.

:module: operations.test-framework
:importance: P1
:design: decision_8273271b67b1, decision_c953eab654cd
"""
from copy import deepcopy
import hashlib
import json

import pytest

from management_portal.test_framework import planner
from management_portal.test_framework.catalog import CatalogRepository, ROOT, planned_source_changed
from management_portal.test_framework.models import CatalogError
from management_portal.test_framework.registry import RunnerRegistry


@pytest.fixture(params=['tfi.portal-tau-cases.v1', 'tfi.portal-spec-design-cases.v1'])
def planned_tau(tmp_path, monkeypatch, request):
    document = json.loads((ROOT / 'tests/cases/tau_cases.json').read_text())
    document['schema'] = request.param
    document['cases'] = document['cases'][:2]
    filename = ('tau_cases.json' if request.param == 'tfi.portal-tau-cases.v1'
                else 'spec_design_cases.json')
    relative = 'tests/cases/' + filename
    for case in document['cases']:
        for implementation in case['implementations']:
            implementation['source'] = relative
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(document))
    monkeypatch.setattr(planner, 'ROOT', tmp_path)
    plan_builder = planner.RunPlanner(CatalogRepository(root=tmp_path, verify_sources=False),
                                      RunnerRegistry(tau_available=True))
    request = {'select': {'case_ids': [document['cases'][0]['id']]},
               'implementation': {'runners': ['tau']}}
    item = plan_builder.preview(request)['selected'][0]
    assert not planned_source_changed(item, tmp_path)
    return tmp_path, source, document, item, plan_builder, request


@pytest.mark.parametrize('change', ['neighbour', 'add', 'layout'])
def test_tau_plan_survives_unrelated_declaration_changes(planned_tau, change):
    """TAU 新计划不因相邻用例修改而失效

    独立计划在相邻用例编辑、新增和 JSON 重排后仍可执行；源码文件摘要继续保留。

    :risk: no-external-side-effects
    """
    root, source, document, item, _, _ = planned_tau
    if change == 'neighbour':
        document['cases'][1]['implementations'][0]['task_prompt'] += '\nUnrelated preparation.'
    elif change == 'add':
        neighbour = deepcopy(document['cases'][1])
        neighbour['id'] = 'test_0123456789ab'
        document['cases'].append(neighbour)
    else:
        document['cases'].reverse()
        for case in document['cases']:
            for implementation in case['implementations']:
                implementation['line'] = 999
    source.write_text(json.dumps(document, indent=4, sort_keys=True))
    assert hashlib.sha256(source.read_bytes()).hexdigest() != item['source_sha256']
    assert not planned_source_changed(item, root)


@pytest.mark.parametrize('change', ['task', 'risk', 'remove', 'duplicate', 'schema', 'invalid', 'missing'])
def test_tau_plan_rejects_changed_or_unverifiable_declaration(planned_tau, change):
    """TAU 自身声明变化及不可验证源码继续拒绝

    所选任务或风险改变、删除、重复声明、未知格式及缺失源码均使已冻结的计划失效；任务改动后新计划冻结新的声明，声明不合法、消失或重复时无法生成新计划。

    :risk: no-external-side-effects
    """
    root, source, document, item, builder, request = planned_tau
    if change == 'task':
        document['cases'][0]['implementations'][0]['task_prompt'] += '\nChanged task.'
    elif change == 'risk':
        document['cases'][0]['implementations'][0]['risk'] = 'unspecified'
    elif change == 'remove':
        document['cases'].pop(0)
    elif change == 'duplicate':
        document['cases'].append(deepcopy(document['cases'][0]))
    elif change == 'schema':
        document['schema'] += '-changed'
    source.write_text('not json' if change == 'invalid' else json.dumps(document))
    if change == 'missing':
        source.unlink()
    assert planned_source_changed(item, root)
    if change == 'task':
        # The catalog follows the source: a new plan freezes the changed declaration.
        renewed = builder.preview(request)['selected'][0]
        assert renewed['source_case_sha256'] != item['source_case_sha256']
        assert not planned_source_changed(renewed, root)
    elif change in {'risk', 'remove', 'duplicate', 'invalid', 'missing'}:
        with pytest.raises(CatalogError):
            builder.preview(request)


def test_legacy_plans_and_local_tests_keep_whole_file_checks(planned_tau):
    """既有计划与本地代码测试保留全文件源码校验

    已有 TAU 计划不追溯改写，pytest 源码变更继续失效。

    :risk: no-external-side-effects
    """
    root, source, document, item, _, _ = planned_tau
    legacy = {k: v for k, v in item.items() if k != 'source_case_sha256'}
    source.write_text(json.dumps(document, indent=2))
    assert planned_source_changed(legacy, root)
    local = root / 'test_example.py'
    local.write_text('def test_example():\n    assert True\n')
    item = {'implementation': {'source': 'test_example.py', 'runner': 'pytest'},
            'source_sha256': hashlib.sha256(local.read_bytes()).hexdigest()}
    assert not planned_source_changed(item, root)
    local.write_text(local.read_text() + '# shared helper changed\n')
    assert planned_source_changed(item, root)
