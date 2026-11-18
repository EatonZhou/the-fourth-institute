"""Current handoff state is a read projection, not a rewrite of its receipt."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from management_portal.society_service import resident_restart_domain as domain


@pytest.mark.parametrize('state,expected', [
    ('unbound','pending_first_input'), ('empty','pending_first_input'),
    ('accepted','delivered'), ('reset','discarded'),
    ('wrong_route','pending_first_input'), ('wrong_operation','pending_first_input'),
    ('unknown','pending_first_input'),
])
def test_handoff_projection_uses_current_binding_and_reset_without_writing(monkeypatch,state,expected):
    record = {'resident_id':'resident_111111111111','backend':'claude','backend_route_id':'claude_one',
        'session_restart':{'restart_id':'restart_one','context_result':'pending_first_input',
            'pending_handoff':{'operation_id':'restart_one','strategy':'compact',
                'target':{'provider':'claude','route':'claude_one'},'context_reset_at':'','prompt':'material'}}}
    binding = {'provider':'claude','backend_route_id':'claude_one','backend_session_id':'session_one'}
    if state=='unbound': binding={}
    if state=='wrong_route': binding['backend_route_id']='claude_two'
    if state=='wrong_operation': record['session_restart']['pending_handoff']['operation_id']='other'
    if state=='reset': record['context_reset_at']='2026-11-18T10:00:00Z'
    original=deepcopy(record)
    monkeypatch.setattr(domain.resident_record_domain,'resolve_resident_runtime_target',lambda *a,**kw: SimpleNamespace(record=record,resident_id=record['resident_id']))
    monkeypatch.setattr(domain.resident_backend,'session_record',lambda *a: binding)
    monkeypatch.setattr(domain,'_session_restart_source',lambda *a: {})
    def accepted(*a,**kw):
        if state=='unknown': raise RuntimeError('observation unavailable')
        return state not in {'empty','unbound'}
    monkeypatch.setattr(domain.execution_port,'sessions',lambda:SimpleNamespace(has_context=accepted))
    result=domain.get_resident_session_restart(record['resident_id'],'viewer')
    assert result['restart']['context_result']==expected
    assert record==original
