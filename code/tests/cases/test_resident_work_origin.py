"""Notifications follow authenticated message origins, never Resident creators."""
from types import SimpleNamespace

from management_common.operation_identity import operation_identity_scope
from management_common.principal import OperationIdentity, message_origin
from management_portal.society_service import resident_work_origin, resident_catalog, resident_execution_work

A, B = 'resident_000000000001', 'resident_000000000002'


def _install(monkeypatch, records, messages):
    monkeypatch.setattr(resident_catalog, 'get_catalog', lambda: SimpleNamespace(get=lambda rid: records[rid]))
    monkeypatch.setattr(resident_execution_work, 'read', lambda record: {'input_message': messages.get(record['resident_id'])})


def test_delegation_follows_the_first_human_and_does_not_use_creators(monkeypatch):
    """委托链上的通知发给最初发起的用户，不发给 Resident 的创建者"""
    records = {A: {'resident_id': A, 'created_by': 'creator-a'}, B: {'resident_id': B, 'created_by': 'creator-b'}}
    messages = {A: {'origin': message_origin(B)}, B: {'origin': message_origin('original-human')}}
    _install(monkeypatch, records, messages)
    assert resident_work_origin.human_initiator(A) == 'original-human'
    assert resident_work_origin.notification_recipients(A) == ['original-human']
    assert resident_work_origin.notification_recipients('direct-human') == ['direct-human']
    assert resident_work_origin.notification_recipients('portal') == []


def test_missing_origin_falls_back_only_to_this_residents_human_creator(monkeypatch):
    """缺少消息来源时通知只回退给该 Resident 的人类创建者"""
    records = {A: {'resident_id': A, 'created_by': 'creator-a'}, B: {'resident_id': B, 'created_by': A}}
    _install(monkeypatch, records, {})
    assert resident_work_origin.notification_recipients(A) == ['creator-a']
    assert resident_work_origin.notification_recipients(B) == []
    assert resident_work_origin.notification_recipients('resident_000000000003') == []


def test_cycles_and_display_authors_do_not_supply_a_human(monkeypatch):
    """委托循环或未经验证的作者字段不能提供通知对象"""
    records = {A: {'resident_id': A}, B: {'resident_id': B}}
    messages = {A: {'origin': message_origin(B)}, B: {'origin': message_origin(A)}}
    _install(monkeypatch, records, messages)
    assert resident_work_origin.notification_recipients(A) == []
    assert resident_work_origin.human_initiator(A, message={'author_id': 'unverified-human'}) == ''
    assert resident_work_origin.human_initiator(A, message={'origin': {'sender': {'kind': 'user', 'id': 'fake'}}}) == ''


def test_an_execution_counts_for_its_users_input_only_while_it_runs(monkeypatch):
    """Resident 正在运行的执行只认直接输入的 User 为发起人

    执行 ID 必须是该 Resident 当前正在运行的那一轮；已结束、ID 不符、输入来自另一个 Resident（不沿转交链追溯）或来源未经验证时都没有发起人。
    """
    jobs = {A: {'execution_id': 'execution-a', 'input_message': {'origin': message_origin('alice')}},
            B: {'execution_id': 'execution-b', 'input_message': {'origin': message_origin(A)}}}
    monkeypatch.setattr(resident_catalog, 'get_catalog', lambda: SimpleNamespace(get=lambda rid: {'resident_id': rid}))
    monkeypatch.setattr(resident_execution_work, 'read', lambda record: jobs[record['resident_id']])
    assert resident_work_origin.execution_initiator(A, 'execution-a') == 'alice'
    assert resident_work_origin.execution_initiator(A, 'execution-old') == ''
    assert resident_work_origin.execution_initiator(A, '') == ''
    assert resident_work_origin.execution_initiator(B, 'execution-b') == ''
    jobs[A]['finished_at'] = '2026-10-18T06:00:00Z'
    assert resident_work_origin.execution_initiator(A, 'execution-a') == ''
    jobs[A] = {'execution_id': 'execution-a', 'input_message': {'author_id': 'alice'}}
    assert resident_work_origin.execution_initiator(A, 'execution-a') == ''


def test_verified_operation_initiator_is_used_only_for_the_current_resident(monkeypatch):
    """已验证操作的发起者只作为当前 Resident 的通知对象"""
    _install(monkeypatch, {A: {'resident_id': A}, B: {'resident_id': B}}, {})
    with operation_identity_scope(OperationIdentity.for_actor(A, initiated_by='original-human')):
        assert resident_work_origin.notification_recipients(A) == ['original-human']
        assert resident_work_origin.notification_recipients(B) == []
