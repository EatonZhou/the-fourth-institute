from __future__ import annotations

import pytest

from management_common.principal import Principal, actor_principal, message_origin, validate_user_id


def test_principal_types_are_disjoint_contracts(subtests, isolated_portal_case):
    """主体类型互斥且消息发送者与发起用户分别记录"""
    with subtests.test(contract='test_principal_types_are_disjoint'), isolated_portal_case() as case_path:
        resident = Principal('resident', 'resident_123456abcdef')
        assert Principal.from_dict(resident.to_dict()) == resident
        assert actor_principal(resident.id) == resident
        for identifier in (resident.id, 'resident_invalid', '@alice', 'portal', 'ai-socket-relay', 'mailbox', 'Mailbox', ''):
            with pytest.raises(ValueError):
                validate_user_id(identifier)
        assert validate_user_id('alice@example.com') == 'alice@example.com'
    with subtests.test(contract='test_message_sender_is_not_the_initiating_user'), isolated_portal_case() as case_path:
        origin = message_origin('resident_123456abcdef', initiated_by='alice')
        assert origin['sender'] == {'kind': 'resident', 'id': 'resident_123456abcdef'}
        assert origin['initiated_by'] == {'kind': 'user', 'id': 'alice'}
        assert origin['producer'] == {'kind': 'service', 'id': 'portal'}
    with subtests.test(contract='test_unknown_service_and_claim_fields_are_rejected'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError):
            Principal('service', 'worker-from-payload')
        with pytest.raises(ValueError):
            Principal.from_dict({'kind': 'user', 'id': 'alice', 'admin': True})
