"""A facade task is dispatched only by the Family's own roles; others get the facade's normal refusal.

:module: lineage.team
:design: decision_44cf587c1721, decision_bd5217d08c09
"""
import pytest

from management_portal.society_service import family_task_dispatches, family_facade, resident_record_domain
from tests.cases.test_personal_ama_delegation import AMA, LINEAGE, CALLER

STEWARD = 'resident_111111111111'
ROUTER = 'resident_222222222222'


@pytest.fixture
def sent(monkeypatch):
    facade = {'resident_id': AMA, 'personal_owner_id': 'owner', 'lineage_id': LINEAGE}
    family = {'facade': AMA, 'steward': STEWARD, 'router': ROUTER}
    monkeypatch.setattr(resident_record_domain, 'find_resident', lambda rid: facade if rid == AMA else {'resident_id': rid})
    monkeypatch.setattr(family_facade, 'is_facade', lambda r: r is facade)
    monkeypatch.setattr(family_task_dispatches, '_context', lambda r: ({}, {'id': 'fbi_test', 'family': family}))
    calls = []
    monkeypatch.setattr(family_task_dispatches, 'deliver', lambda *args, **kw: calls.append((args, kw)) or {'message_id': 'msg_task'})
    return calls


@pytest.mark.parametrize('role', [AMA, STEWARD, ROUTER])
def test_family_roles_dispatch_a_facade_task(sent, role):
    result = family_task_dispatches.dispatch(AMA, 'quota', role, source_message_id='msg_original')
    assert result['message_id'] == 'msg_task'
    assert sent[0][0][3]['message_id'] == 'msg_original'
    assert sent[0][0][4] == role


@pytest.mark.parametrize('caller', [CALLER, 'owner'])
def test_other_callers_are_refused_as_a_hidden_personal_facade(sent, caller):
    with pytest.raises(KeyError):
        family_task_dispatches.dispatch(AMA, 'quota', caller, source_message_id='msg_original')
    assert not sent
