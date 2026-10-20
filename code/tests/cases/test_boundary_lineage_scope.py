"""Canonical source membership is checked again at the definition commit."""
from types import SimpleNamespace

import pytest

from management_portal.hatg.catalog import instruction_catalog
from management_portal.hatg.contracts import InstructionError
from management_portal.society_service import lineage_definition_scope as scope
from management_portal.society_service import lineage_domain as graphs
from tests.cases.isa_test_support import ACTOR, ok, run
from tests.cases.test_isa_lineage_lifecycle import lineage, resident, graph, future_creation_store  # noqa: F401


def test_source_arguments_are_required():
    """源作用域指令要求显式提供 lineage_id"""
    catalog = instruction_catalog()
    for name in scope.SOURCE_ARGUMENTS:
        assert next(p for p in catalog[name].parameters if p.name == 'lineage_id').required


def test_wrong_source_rejected_before_resident_mutation(lineage):
    """源 Lineage 不匹配时拒绝修改 Resident"""
    rid = resident(lineage, 'Protected source')
    other = ok('CreateLineage', {'title': 'Other', 'project_id': 'project_test'}, key='other')['lineage_id']
    result = run('UpdateResident', {'lineage_id': other, 'resident_id': rid, 'title': 'Wrong'})
    assert result.error['code'] == 'instruction.invalid_arguments'
    assert ok('GetResident', {'resident_id': rid})['resident']['title'] == 'Protected source'


def test_concurrent_source_removal_is_rechecked_under_commit_lock(lineage):
    """提交锁内重新检查并发移除的源对象并回滚修改"""
    component = ok('CreateComponent', {'lineage_id': lineage, 'kind': 'event_source',
                                      'config': {'event_type': 'manual.triggered'}})['component_id']
    args = {'lineage_id': lineage, 'object_ids': [component]}
    inv = SimpleNamespace(argument=lambda name: args.get(name))

    def delayed_handler(_):
        # A concurrent write runs with its own invocation context.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(1) as pool:
            pool.submit(graphs.delete_lineage_program_component, lineage, {'component_id': component}, ACTOR).result()
        graphs.create_lineage_program_component(lineage, {'kind': 'event_source',
            'config': {'event_type': 'manual.triggered'}}, ACTOR)
    with pytest.raises(InstructionError, match='对象不属于'):
        scope.guard_source_scope('MoveLineageObjects', delayed_handler)(inv)
    assert graph(lineage)['program']['components'] == []
