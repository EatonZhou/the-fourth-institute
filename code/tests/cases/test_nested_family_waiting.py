"""Nested Family requests expose admission waits rather than an executable queue.

:module: lineage.family
:design: decision_44cf587c1721
"""
from tests.cases.test_family_quota import quota_family, team, owners, ACTOR, _record  # noqa: F401
from tests.cases.family_test_support import set_role
from management_portal.society_service import family_facade, family_quota, resident_domain, resident_conversation_stream
import pytest


@pytest.mark.parametrize('waiting', ['quota', 'scope'])
def test_outer_facade_exposes_inner_admission_wait(quota_family, monkeypatch, waiting):
    f = quota_family
    outer = f.spare
    set_role(outer, family_id='fbi_waitouter', actor=ACTOR)
    monkeypatch.setattr(family_facade, 'family_member_ids', lambda record:
                        {f.facade} if record['resident_id'] == outer else {f.member})
    root = 'msg_1234567890abcdee'
    resident_domain.deliver_resident_message_content(outer, ACTOR, 'Check this request', message_id=root)
    first = family_quota.relay_message(outer, root, f.facade, ACTOR)
    assert first['status'] == 'relayed'
    kwargs = {'scope_gap':'missing specialist', 'reason':'ask steward'} if waiting == 'scope' else {}
    held = family_quota.relay_message(f.facade, first['message_id'], f.member, ACTOR, **kwargs)
    assert held['status'] == ('waiting_for_steward' if waiting == 'scope' else 'waiting_for_quota_review')
    repo = resident_conversation_stream.get_repository()
    shown = family_facade.project_messages(_record(outer), [repo.message(outer, root)], input_states={root:'completed'})
    assert shown[0]['relay']['state'] == waiting + '_review'
    # Actions still target the request held by the inner facade, not an unaccepted member copy.
    assert shown[0]['relay']['resident_id'] == f.facade
    assert shown[0]['relay']['message_id'] == first['message_id']
    assert repo.input_state(f.facade, first['message_id'])['state'] == 'queued'
