"""Reads follow the live PAR permission entries."""
from types import SimpleNamespace

import pytest

from management_portal import permission_system
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401

R = 'resident_111111111111'


def test_event_reads_follow_current_entries(permissions):
    """事件读取随当前权限表项即时变化"""
    permissions.place(Fact('resident', R))
    assert permission_system.readable_ids('GetConversation', 'resident_id', [R, R], actor='bob') == {R}
    deny = permissions.deny('bob', R)
    assert permission_system.readable_ids('GetConversation', 'resident_id', [R], actor='bob') == set()
    # The identity read asks for @viewer too, so the same deny hides it.
    assert permission_system.readable_ids('GetResident', 'resident_id', [R], actor='bob') == set()
    store = permissions.store
    with store.transaction() as connection:
        store.delete(connection, where='entry_id = %s', parameters=(deny.entry_id,))
    assert permission_system.readable_ids('GetConversation', 'resident_id', [R], actor='bob') == {R}


def test_unreadable_instance_does_not_become_an_allow(permissions, monkeypatch):
    """权限实例不可读时读取权限检查直接报错"""
    def unavailable(*_args, **_kwargs):
        raise RuntimeError('instance unavailable')
    monkeypatch.setattr(permissions.implementation.store, 'snapshot', unavailable)
    with pytest.raises(RuntimeError, match='instance unavailable'):
        permission_system.readable_ids('GetConversation', 'resident_id', [R], actor='CASE-A')


def test_sse_hides_conversation_and_identity_after_deny(permissions):
    """deny 表项写入后 SSE 隐藏会话，身份读取同样被隐藏"""
    from management_portal import serve_management_portal as portal
    permissions.place(Fact('resident', R))
    notification = SimpleNamespace(subject=f'portal.resident.changed.{R}.conversation', payload={
        'kind': 'conversation_event', 'event': {'resident_id': R, 'event_type': 'conversation.message.appended',
                                              'cursor': 1, 'payload': {'message': {'text': 'protected reply'}}},
    })
    assert portal._portal_change_payload('bob', [notification], {'resident'})['resident_events']
    permissions.deny('bob', R)
    result = portal._portal_change_payload('bob', [notification], {'resident'})
    assert result['resident_events'] == []
    assert 'protected reply' not in str(result)
    assert permission_system.readable_ids('GetResident', 'resident_id', [R], actor='bob') == set()
