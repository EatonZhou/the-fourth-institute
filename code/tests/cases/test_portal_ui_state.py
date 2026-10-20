from __future__ import annotations

import json

import pytest

from management_portal.portal_ui_state import PortalUIStateStore


def test_associations_are_actor_and_namespace_scoped_contracts(tmp_path, subtests, isolated_portal_case):
    """UI 关联按用户和命名空间隔离，删除是幂等的且不影响其他条目"""
    with subtests.test(contract='test_associations_are_actor_and_namespace_scoped'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = PortalUIStateStore()
        store.put('alice', 'surface.rooms', 'room-1', {'resident_id': 'resident_1'})
        store.put('alice', 'surface.other', 'room-1', {'resident_id': 'resident_2'})
        store.put('bob', 'surface.rooms', 'room-1', {'resident_id': 'resident_3'})
        assert store.get('alice', 'surface.rooms', 'room-1')['value'] == {'resident_id': 'resident_1'}
        assert store.get('alice', 'surface.other', 'room-1')['value'] == {'resident_id': 'resident_2'}
        assert store.get('bob', 'surface.rooms', 'room-1')['value'] == {'resident_id': 'resident_3'}
    with subtests.test(contract='test_remove_is_idempotent_and_does_not_touch_other_items'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = PortalUIStateStore()
        store.put('alice', 'surface.rooms', 'first', {'target': 'a'})
        store.put('alice', 'surface.rooms', 'second', {'target': 'b'})
        assert store.remove('alice', 'surface.rooms', 'first') is True
        assert store.remove('alice', 'surface.rooms', 'first') is False
        assert [item['key'] for item in store.list('alice', 'surface.rooms')] == ['second']
    with subtests.test(contract='test_associations_are_scoped_to_their_actor'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = PortalUIStateStore()
        store.put('alice', 'personal.ama', 'primary', {'resident_id': 'resident_0123456789ab'})
        assert store.get('alice', 'personal.ama', 'primary')['value'] == {'resident_id': 'resident_0123456789ab'}
        assert store.get('bob', 'personal.ama', 'primary') is None


@pytest.mark.parametrize("character", ["a", "汉", "😀"], ids=["ascii", "chinese", "emoji"])
def test_value_limit_is_one_mib_of_serialized_utf8(tmp_path, character):
    """UI 状态值上限为序列化后 1 MiB 的 UTF-8，超限写入被拒且不改动已存值"""
    store = PortalUIStateStore()
    limit = 1024 * 1024
    envelope_bytes = len(b'{"text":""}')
    repeat, padding = divmod(limit - envelope_bytes, len(character.encode("utf-8")))
    value = {"text": character * repeat + "a" * padding}
    assert len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) == limit

    store.put("alice", "surface.results", "latest", value)
    reopened = PortalUIStateStore()
    assert reopened.get("alice", "surface.results", "latest")["value"] == value
    assert reopened.get("bob", "surface.results", "latest") is None

    # Reject an oversized replacement before changing the saved result.
    oversized = {"text": value["text"] + "a"}
    with pytest.raises(ValueError, match="maximum 1048576 bytes"):
        reopened.put("alice", "surface.results", "latest", oversized)
    assert reopened.get("alice", "surface.results", "latest")["value"] == value
    with pytest.raises(ValueError, match="maximum 1048576 bytes"):
        reopened.put("alice", "surface.results", "new", oversized)
    assert reopened.get("alice", "surface.results", "new") is None
