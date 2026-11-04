"""Explicit release recovery preserves failures while reconciling the same deletion.

:module: lineage.service
:importance: P1
:design: decision_d19b9909a674
"""
import pytest
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
