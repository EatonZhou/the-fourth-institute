"""A facade task asks authority on that facade, never its private parent.

:module: lineage.team
:design: decision_44cf587c1721, decision_bd5217d08c09
"""
import pytest

from management_portal import permission_system
from management_portal.permission_system import personal_ama
from management_portal.society_service import family_task_dispatches, family_tasks, family_facade, resident_record_domain
from tests.cases.test_personal_ama_delegation import world, permissions, lend, AMA, LINEAGE, CALLER  # noqa: F401


@pytest.mark.parametrize('mode', [permission_system.PAR, permission_system.OPEN])
@pytest.mark.parametrize('grant,allowed', [('@viewer', False), ('@contributor', True)])
def test_exact_facade_loan_dispatches_without_opening_parent(world, monkeypatch, mode, grant, allowed):
    from management_portal.society_service import resident_access
    monkeypatch.setattr(permission_system, 'configured_mode', lambda: mode)
    monkeypatch.setattr(resident_access, 'actor_can_manage_residents', lambda actor: True)
    facade = {'resident_id': AMA, 'personal_owner_id': 'owner', 'lineage_id': LINEAGE}
    family = {'facade': AMA, 'steward': 'resident_111111111111', 'router': 'resident_222222222222'}
    monkeypatch.setattr(resident_record_domain, 'find_resident', lambda rid: facade if rid == AMA else {'resident_id': rid})
    monkeypatch.setattr(family_facade, 'is_facade', lambda r: r is facade)
    monkeypatch.setattr(family_tasks, 'context', lambda r: ({}, {'id': 'fbi_test', 'family': family}))
    sent = []
    monkeypatch.setattr(family_task_dispatches, 'deliver', lambda *args, **kw: sent.append((args, kw)) or {'message_id': 'msg_task'})
    lend(world, resource=AMA, a=grant)
    if mode == permission_system.OPEN:
        # Saved PAR loans are inactive in Open; the private owner boundary
        # remains authoritative without consulting the disabled engine.
        monkeypatch.setattr(permission_system, 'active', lambda: pytest.fail('Open loaded PAR'))
    assert not personal_ama.reaches(CALLER, 'owner', LINEAGE)
    if allowed and mode == permission_system.PAR:
        result = family_task_dispatches.dispatch(AMA, 'quota', CALLER, source_message_id='msg_original')
        assert result['message_id'] == 'msg_task'
        assert sent[0][0][3]['message_id'] == 'msg_original'
        assert sent[0][0][4] == CALLER
    else:
        with pytest.raises(KeyError):
            family_task_dispatches.dispatch(AMA, 'quota', CALLER, source_message_id='msg_original')
        assert not sent
    if mode == permission_system.OPEN:
        result = family_task_dispatches.dispatch(AMA, 'quota', 'owner', source_message_id='msg_owner')
        assert result['message_id'] == 'msg_task'
        assert len(sent) == 1
        assert sent[0][0][3]['message_id'] == 'msg_owner'
        assert sent[0][0][4] == 'owner'
