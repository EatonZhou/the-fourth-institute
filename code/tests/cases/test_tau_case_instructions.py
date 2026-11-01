"""Run-local caller constraints are fixed per selected case, never in the catalog."""
from types import SimpleNamespace

import pytest

from management_portal.test_framework import planner, tau
from management_portal.test_framework.models import TestCase as Case, Implementation, CatalogError
from management_portal.test_framework.registry import RunnerRegistry
from tests.cases.test_test_framework import _verified_dev_target


def make_planner(monkeypatch, runner='tau'):
    cases = [Case(
        id=f'test_00000000000{i}', key=f'case-{i}', title=f'Case {i}', description='Use the page',
        importance='P1', module='operations.test-framework', aspects=('functional',),
        is_design_point=False, design_refs=(), lifecycle='active',
        implementations=(Implementation(id=f'impl-{i}', runner=runner, entrypoint=f'synthetic::{i}',
                                       source='tests/cases/test_tau_case_instructions.py', profiles=()),),
    ) for i in [1, 2]]
    monkeypatch.setattr(planner, 'source_digests', lambda *_args: {})
    return planner.RunPlanner(SimpleNamespace(query=lambda _select: cases), RunnerRegistry(tau_available=True))


def test_run_constraints_are_scoped_frozen_and_reach_prompt(monkeypatch, tmp_path):
    plan_builder = make_planner(monkeypatch)
    note = 'Only the cross-route step may use account three. Restore two afterward.'
    request = {'implementation': {'runners': ['tau'], 'case_instructions': {'test_000000000001': note}}}
    plan = plan_builder.preview(request)
    assert plan['selected'][0]['case_instructions'] == note
    assert 'case_instructions' not in plan['selected'][1]
    assert 'case_instructions' not in plan['selected'][0]['implementation']
    request['implementation']['case_instructions']['test_000000000001'] = 'Changed later'
    assert plan['request']['implementation']['case_instructions']['test_000000000001'] == note
    assert plan_builder.preview(request)['plan_id'] != plan['plan_id']
    selected = {**plan['selected'][0], 'resident_account_id': 'two'}
    prompt = tau.build_prompt(selected, tau.TauConfig(tmp_path / 'state.json'), tmp_path,
                              _verified_dev_target(), 'testrun_0000000000000001')
    assert note in prompt
    assert '除本轮补充要求明确限定的例外步骤外' in prompt
    assert '全部 Codex Resident 必须使用账号 two' in prompt
    other = tau.build_prompt({**plan['selected'][1], 'resident_account_id': 'two'},
                             tau.TauConfig(tmp_path / 'state.json'), tmp_path,
                             _verified_dev_target(), 'testrun_0000000000000001')
    assert note not in other
    assert '除本轮补充要求明确限定的例外步骤外' not in other
    assert '禁止使用其他账号作为失败后的备用路线' in other


@pytest.mark.parametrize('value', [[], 'note', {'test_000000000001': ''},
                                  {'test_000000000001': True}, {'test_ffffffffffff': 'not selected'}])
def test_case_constraints_reject_invalid_or_unselected_targets(monkeypatch, value):
    with pytest.raises(CatalogError, match='case_instructions'):
        make_planner(monkeypatch).preview({'implementation': {'case_instructions': value}})


def test_case_constraints_cannot_be_attached_to_local_runner(monkeypatch):
    with pytest.raises(CatalogError, match='仅用于'):
        make_planner(monkeypatch, runner='pytest').preview({
            'implementation': {'case_instructions': {'test_000000000001': 'local runner cannot consume this'}}})
