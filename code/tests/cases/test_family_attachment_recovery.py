"""Family failed recovery preserves the original request and accepted intake.

:module: lineage.family
:design: decision_a64b3ea2c3c5
"""
import pytest
from management_portal.society_service import family_quota, family_facade, resident_conversation_stream, resident_protocol
from tests.cases.test_family_recovery import family, delivery_resident, future_creation_store, _send, _record  # noqa: F401


@pytest.mark.parametrize('terminal', ['failed', 'cancelled', 'accepted', 'accepted-queue', 'committed'])
def test_rejected_route_recovery_does_not_leave_false_routing(family, monkeypatch, terminal):
    facade_id, member_id = family['facade'], family['right']
    repo = resident_conversation_stream.get_repository()
    root = _send(family, 'Original request')
    before = repo.message(facade_id, root)
    repo.set_input_state(facade_id, root, 'failed', error={'message':'original route failure'})
    delivered = []

    def relay(*args, **kwargs):
        if terminal == 'cancelled':
            repo.set_input_state(facade_id, root, 'cancelled')
        if terminal in {'accepted', 'accepted-queue'}:
            from management_portal.society_service import resident_message_contract
            mid = resident_message_contract.derived_message_identity('family-relay', facade_id, root, member_id)
            resident_protocol.deliver_resident_input({'resident_id':member_id, 'message':{
                'message_id':mid, 'role':'user', 'content_parts':before['content_parts']}}, 'sample_admin',
                delivery_context=resident_protocol.ResidentDeliveryContext(business_ref=family_facade.relay_mark(facade_id, root)))
            delivered.append(mid)
            if terminal == 'accepted-queue':
                message = repo.message
                monkeypatch.setattr(repo, 'message', lambda rid, message_id:
                                    None if (rid, message_id) == (member_id, mid) else message(rid, message_id))
        error = PermissionError('只能发送自己上传的附件')
        if terminal == 'committed':
            error.commit_state = 'committed'
        raise error

    monkeypatch.setattr(family_quota, '_relay_message', relay)
    with pytest.raises(PermissionError):
        family_quota.relay_message(facade_id, root, member_id, 'sample_admin', recover=True, reason='Retry original')
    assert repo.message(facade_id, root) == before
    state = repo.input_state(facade_id, root)['state']
    assert state == {'failed':'failed', 'cancelled':'cancelled', 'accepted':'queued',
                     'accepted-queue':'queued', 'committed':'queued'}[terminal]
    if terminal == 'failed':
        assert repo.input_state(facade_id, root)['error']['message'] == '只能发送自己上传的附件'
        assert family_quota.get_lineage_data_store().family_relays.get(_record(family)['lineage_id'],facade_id)['route_restorations'][root][-1]['status'] == 'failed'
    if delivered and terminal != 'accepted-queue':
        assert repo.message(member_id, delivered[0]) is not None
