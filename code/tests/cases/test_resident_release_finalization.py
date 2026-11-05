"""Explicit release recovery preserves failures while reconciling the same deletion.

:module: lineage.service
:importance: P1
:design: decision_d19b9909a674
"""
import pytest
from contextlib import nullcontext
from types import SimpleNamespace
from management_portal import lineage_workflows
from management_portal.hatg.contracts import InstructionError


def test_deleted_resident_release_can_recover_a_previous_definitive_failure(monkeypatch):
    """旧删除确认已明确失败时，新恢复不能永久重放该失败幂等键。"""
    attempts = []
    args = {'resident_id': 'resident_0123456789ab', 'lineage_id': 'lineage_0123456789ab'}
    def call(actor, instruction, arguments, **options):
        assert actor == 'allowed'
        if instruction == 'GetResident':
            raise InstructionError('instruction.not_found', 'deleted')
        assert instruction == 'DeleteResident'
        assert arguments == args
        attempts.append(options['key'])
        if len(attempts) == 1:
            raise InstructionError('instruction.invalid_arguments', 'Lineage not found', commit_state='not_committed')
        return {'resident_id': args['resident_id']}
    monkeypatch.setattr(lineage_workflows.isa_client, 'call', call)
    bindings = lineage_workflows.WorkflowBindings()
    with pytest.raises(InstructionError, match='Lineage not found'):
        bindings.delete_resident(actor='allowed', **args)
    assert bindings.delete_resident(actor='allowed', **args) == {'resident_id': args['resident_id']}
    assert len(set(attempts)) == 2


def test_release_does_not_replace_an_access_error_with_a_delete(monkeypatch):
    """读取权限拒绝仍原样返回，不能以恢复为由发出删除。"""
    calls = []
    def call(actor, instruction, arguments, **options):
        calls.append(instruction)
        raise InstructionError('instruction.forbidden', 'denied')
    monkeypatch.setattr(lineage_workflows.isa_client, 'call', call)
    with pytest.raises(InstructionError, match='denied'):
        lineage_workflows.WorkflowBindings().delete_resident(actor='denied',
            resident_id='resident_0123456789ab', lineage_id='lineage_0123456789ab')
    assert calls == ['GetResident']


def test_existing_resident_release_retries_definitive_graph_failure(monkeypatch):
    """清理在图提交前失败、版本未变化时，修复后也能重试而非永久重放旧错误。"""
    rid, lid = 'resident_0123456789ab', 'lineage_0123456789ab'
    attempts = []
    graph = {'revision': 7, 'nodes': [{'id': rid}], 'program': {}}
    monkeypatch.setattr(lineage_workflows, 'lineage_graph', lambda *_: graph)
    bindings = object.__new__(lineage_workflows.WorkflowBindings)
    store = SimpleNamespace(for_resident=lambda *_: [], lineage_lock=lambda *_: nullcontext())
    monkeypatch.setattr(lineage_workflows.WorkflowBindings, 'store', property(lambda _: store))
    def call(actor, instruction, arguments, **options):
        if instruction == 'GetResident':
            return {'resident': {'resident_id': rid, 'lineage_id': lid}}
        if instruction == 'ApplyLineageChanges':
            assert options['version'] == 7
            attempts.append(options['key'])
            if len(attempts) == 1:
                raise InstructionError('instruction.invalid_arguments', 'old directory contract', commit_state='not_committed')
        return {}
    monkeypatch.setattr(lineage_workflows.isa_client, 'call', call)
    with pytest.raises(InstructionError, match='old directory contract'):
        bindings.delete_resident(actor='allowed', resident_id=rid, lineage_id=lid)
    bindings.delete_resident(actor='allowed', resident_id=rid, lineage_id=lid)
    assert len(set(attempts)) == 2
