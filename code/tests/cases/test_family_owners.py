"""Family contacts belong to Family facts, independently of Resident ownership."""
import pytest

from management_common.operation_identity import operation_identity_scope
from management_common.principal import OperationIdentity, message_origin
from management_portal.society_service import family_owners, resident_execution_work
from management_portal.society_service.resident_record_domain import find_resident as _find_resident
from tests.cases.isa_test_support import ACTOR, ok
from tests.cases.test_family_familize import charters  # noqa: F401
from tests.cases.test_isa_lineage_lifecycle import lineage, resident, future_creation_store  # noqa: F401


def test_familize_registers_actual_human_and_keeps_contact_after_reexpansion(lineage, charters):
    """Familize 记录实际人类创建者且参数更新保留联系人"""
    member = resident(lineage, 'Member')
    family = ok('Familize', {'lineage_id': lineage, 'resident_ids': [member]})
    facade_id, steward_id = family['facade_resident_id'], family['steward_resident_id']
    assert _find_resident(facade_id)['family']['human_familizer_id'] == ACTOR
    assert 'family-work-guide.md' in charters[0][1]
    assert 'owner_ids' not in charters[0][1]
    ok('UpdateFamily', {'lineage_id': lineage, 'family_id': family['family_id'],
                                      'goal': 'Changed'})
    assert family_owners.recipients(_find_resident(steward_id), message={}) == [ACTOR]
    assert ok('GetResident', {'resident_id': facade_id})['resident']['family']['human_familizer_id'] == ACTOR


def test_work_human_overrides_familizer_and_ownership_is_irrelevant(lineage, charters, monkeypatch):
    """工作发起人优先作为联系人且不受所有权影响"""
    member = resident(lineage, 'Member')
    family = ok('Familize', {'lineage_id': lineage, 'resident_ids': [member]})
    facade = _find_resident(family['facade_resident_id'])
    steward = _find_resident(family['steward_resident_id'])
    message = {'origin': message_origin(facade['resident_id'], initiated_by='requesting-user')}
    assert family_owners.recipients(steward, message=message) == ['requesting-user']
    monkeypatch.setattr(resident_execution_work, 'read', lambda _: {'input_message': message})
    assert family_owners.recipients(steward) == ['requesting-user']
    assert family_owners.recipients(steward, message={'origin': message_origin('direct-user')}) == ['direct-user']
    with operation_identity_scope(OperationIdentity.for_actor(steward['resident_id'], initiated_by='unrelated-user')):
        assert family_owners.recipients(steward, message={}) == [ACTOR]
    assert family_owners.recipients(steward, message={}) == [ACTOR]


def test_resident_familizer_gets_no_inferred_human_contact(lineage, charters):
    """Resident 创建 Family 时不推断人类联系人"""
    member = resident(lineage, 'Member')
    family = ok('Familize', {'lineage_id': lineage, 'resident_ids': [member]}, actor=member)
    facade_id = family['facade_resident_id']
    with operation_identity_scope(OperationIdentity.for_actor(member, initiated_by=ACTOR)):
        family_owners.register_familizer(facade_id, member)
    assert _find_resident(facade_id)['family']['human_familizer_id'] == ''
    assert family_owners.recipients(_find_resident(family['steward_resident_id']), message={}) == []
    family_owners.register_familizer(facade_id, ACTOR)
    family_owners.register_familizer(facade_id, ACTOR)
    with pytest.raises(ValueError, match='different human familizer'):
        family_owners.register_familizer(facade_id, 'another-user')
    assert _find_resident(facade_id)['family']['human_familizer_id'] == ACTOR


def test_role_outage_reaches_actual_creator_through_facade_once(lineage, charters):
    """无法恢复的角色经门面对实际创建人投递，重查不产生新消息。"""
    from management_portal.society_service import resident_conversation_stream, resident_message_contract
    family = ok('Familize', {'lineage_id': lineage, 'resident_ids': [resident(lineage, 'Member')]})
    record = _find_resident(family['router_resident_id'])
    identity = 'router-outage-one'
    for _ in range(2):
        family_owners.message_creator(record, identity=identity, text='Router 暂无可用账号，请补充账号后恢复。')
    mid = resident_message_contract.derived_message_identity('family-role-alert', identity, ACTOR)
    message = resident_conversation_stream.get_repository().message(family['facade_resident_id'], mid)
    assert message is not None
    assert message['author_id'] == family['facade_resident_id']
    assert message['recipients'] == [{'kind': 'user', 'id': ACTOR}]
