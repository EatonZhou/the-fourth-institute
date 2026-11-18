"""Explicit account selection must not spend target quota to save a choice."""
import pytest

from management_portal.society_service import resident_backend, resident_restart_domain
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_session_restart import _restart_domain


@pytest.mark.parametrize('strategy', ['compact', 'visible'])
def test_manual_handoff_selects_target_without_model_or_quota(monkeypatch, strategy):
    record, operations, saved = _restart_domain(monkeypatch)
    detached = []
    monkeypatch.setattr(resident_backend, 'delete_session_identity', lambda *a, **kw: detached.append(kw))
    monkeypatch.setattr(execution_port, 'health', lambda: pytest.fail('Selection must not inspect target quota'))
    result = resident_restart_domain.restart_resident_session(record['resident_id'], 'alice', {
        'target_provider': 'codex', 'target_account': 'one',
        'context_strategy': strategy,
    })
    assert result['status'] == 'completed' and result['configuration_only']
    assert result['context_result'] == 'pending_first_input'
    assert record['backend'] == 'codex' and record['backend_route_id'] == 'one'
    assert record['session_state'] == 'pending_create'
    assert not operations and detached
    handoff = record['session_restart']['pending_handoff']
    assert handoff['operation_id'] == result['restart_id']
    assert handoff['strategy'] == strategy
    assert handoff['target'] == {'provider': 'codex', 'route': 'one'}
    assert 'old request' in handoff['prompt'] and 'old result' in handoff['prompt']
    assert saved[-1]['session_restart']['pending_handoff'] == handoff
    assert record['intake_open'] is False


def test_manual_handoff_save_failure_restores_previous_binding(monkeypatch):
    from management_portal.society_service import resident_record_domain
    record, operations, _ = _restart_domain(monkeypatch)
    restored = []
    monkeypatch.setattr(resident_backend, 'delete_session_identity', lambda *a, **kw: None)
    monkeypatch.setattr(resident_backend, 'restore_replaced_session', lambda *a: restored.append(a))
    def fail_save(*a, **kw):
        raise RuntimeError('configuration save failed')
    monkeypatch.setattr(resident_record_domain, 'save_resident_record', fail_save)
    with pytest.raises(RuntimeError, match='configuration save failed'):
        resident_restart_domain.restart_resident_session(record['resident_id'], 'alice', {
            'target_account': 'one', 'context_strategy': 'compact',
        })
    assert len(restored) == 1 and not operations
    assert restored[0][1]['previous_binding']['backend_session_id'] == 'session_old'
    assert record.get('backend_route_id', 'zero') == 'zero'
    assert not record.get('session_restart')


def test_manual_native_preparation_bypasses_quota_only_for_candidate(monkeypatch):
    record, operations, _ = _restart_domain(monkeypatch)
    result = resident_restart_domain.restart_resident_session(record['resident_id'], 'alice', {
        'target_account': 'one', 'context_strategy': 'preserve',
    })
    assert result['status'] == 'accepted'
    step = operations[0]['step']
    assert operations[0]['operation'] != 'turn'
    assert step['quota_override'] == {
        'binding_id': step['prepared_binding_id'], 'revision': 1, 'enabled': True,
    }
    assert not record.get('quota_reserve_override')
