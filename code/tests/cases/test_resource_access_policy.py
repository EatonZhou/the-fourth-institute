from __future__ import annotations


import pytest

from management_portal.resources.access import ResourceAccessPolicy


LINEAGE_ID = "lineage_111111111111"
RESIDENT_ID = "resident_222222222222"
INFORMATION_ID = "information_333333333333"


def test_actor_checks_need_an_active_user_or_a_resident():
    """有效主体检查只接受有效的 User 或 Resident：停用的 User 与 Portal 服务身份被拒绝。"""
    from management_portal.portal_access_control import get_portal_access_store, is_active_user_or_resident

    assert is_active_user_or_resident("bob")
    assert is_active_user_or_resident(RESIDENT_ID)
    assert not is_active_user_or_resident("portal")
    assert not is_active_user_or_resident("")
    get_portal_access_store().upsert_user("bob", actor="CASE-A", status="disabled")
    assert not is_active_user_or_resident("bob")


@pytest.mark.parametrize("actor", ["bob", RESIDENT_ID])
def test_users_and_residents_read_without_a_resource_level_check(actor):
    """用户与 Resident 读取资源时资源层不另做权限预查"""
    assert ResourceAccessPolicy().can_read({"resource_id": INFORMATION_ID, "attributes": {}}, actor=actor)


def test_signed_in_accounts_and_residents_read_resources_contracts(subtests, isolated_portal_case):
    """TAU 测试账号和 Resident 可读取资源，匿名调用被拒绝"""
    with subtests.test(contract='test_tau_account_reads_resources'), isolated_portal_case():
        policy = ResourceAccessPolicy()
        private_record = {'resource_id': INFORMATION_ID, 'attributes': {}}
        policy.authorize_read(private_record, actor='test-actor')
        policy.authorize_read(private_record, actor='test-actor-other')
        with pytest.raises(PermissionError):
            policy.authorize_read(private_record, actor='')
    with subtests.test(contract='test_resident_principal_reads_resources'), isolated_portal_case():
        policy = ResourceAccessPolicy()
        private_information = {'resource_id': INFORMATION_ID, 'attributes': {}}
        policy.authorize_read(private_information, actor=RESIDENT_ID)
        policy.authorize_read({'resource_id': LINEAGE_ID, 'attributes': {}}, actor=RESIDENT_ID)
