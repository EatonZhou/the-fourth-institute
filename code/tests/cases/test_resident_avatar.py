"""验证 Resident 持久外观绑定、跨页面一致性和受保护读取边界。

:module: platform.core
:importance: P1
"""
from concurrent.futures import ProcessPoolExecutor
import json
from unittest.mock import Mock

import pytest

from management_portal import resident_avatar
from management_portal.avatar_database import database
from management_portal.society_service import active_agent_projection
from management_portal.principal_backpack import PrincipalBackpack
from tests.cases.test_principal_backpack import Access, Resources, RESIDENT


def _allocate(resident_id):
    return resident_avatar.get_avatar(resident_id)


def test_avatar_first_allocation_is_atomic_across_processes_and_restarts():
    """Resident 头像并发初始化与重启保持一致

    :design: decision_e30897dd3dc3, decision_5bc3fdfc7eec
    """
    with ProcessPoolExecutor(max_workers=4) as pool:
        avatars = list(pool.map(_allocate, [RESIDENT] * 16))
    assert all(avatar == avatars[0] for avatar in avatars)
    # A new store object, as after a restart, reads the same binding.
    assert resident_avatar.resident_avatar_store().get(RESIDENT) == avatars[0]
    assert resident_avatar.get_existing_avatar(RESIDENT) == avatars[0]


def test_avatar_duplicates_are_allowed_and_existing_binding_is_not_redrawn(monkeypatch):
    """Resident 头像允许重复且不重新抽取

    :design: decision_e30897dd3dc3, decision_5bc3fdfc7eec
    """
    draw = Mock(return_value=3)
    monkeypatch.setattr(resident_avatar, "_draw_sprite", draw)
    store = resident_avatar.resident_avatar_store()
    first = store.get(RESIDENT)
    assert store.get("resident_000000000002") == first
    assert draw.call_count == 2
    draw.side_effect = AssertionError("existing binding must not be rerolled")
    assert resident_avatar.resident_avatar_store().get(RESIDENT) == first
    first["sprite_index"] = 0
    assert store.get(RESIDENT)["sprite_index"] == 3


def test_avatar_bad_identity_fails_without_writing():
    """非法身份不产生头像绑定

    :design: decision_e30897dd3dc3, decision_5bc3fdfc7eec
    """
    store = resident_avatar.resident_avatar_store()
    with pytest.raises(ValueError):
        store.get("../escape")
    assert resident_avatar.get_avatar("../escape") is None
    with database().read() as connection:
        assert connection.execute("SELECT count(*) FROM avatars").fetchone()[0] == 0


def test_existing_avatar_read_returns_binding_without_allocating():
    """只读已有头像时返回已绑定的头像，没有绑定也不新分配

    :importance: P2"""
    store = resident_avatar.resident_avatar_store()
    assert resident_avatar.get_existing_avatar(RESIDENT) is None
    with database().read() as connection:
        assert connection.execute("SELECT count(*) FROM avatars").fetchone()[0] == 0
    binding = store.get(RESIDENT)
    assert resident_avatar.get_existing_avatar(RESIDENT) == binding
    assert store.get_existing("resident_000000000099") is None
    with pytest.raises(ValueError):
        store.get_existing("../escape")
    with database().read() as connection:
        assert connection.execute("SELECT count(*) FROM avatars").fetchone()[0] == 1


def test_home_and_backpack_share_avatar_after_authorization_across_viewers(monkeypatch):
    """Home 与背包在读取鉴权后使用同一头像

    :design: decision_e30897dd3dc3, decision_5bc3fdfc7eec
    """
    resources = Resources()
    backpack = PrincipalBackpack(access=Access(), resources=resources)
    catalog = Mock()
    catalog.get_identity_records.return_value = [{"resident_id": RESIDENT, "title": "Researcher"}]
    catalog.state_store.resident_work_ids.return_value = [RESIDENT]
    monkeypatch.setattr(active_agent_projection.resident_catalog, "get_catalog", lambda: catalog)
    message = {"message_id": "initial", "author_id": "alice"}
    monkeypatch.setattr(active_agent_projection.resident_protocol, "read_resident_activity", lambda _: {RESIDENT: {"status": "running", "active_message": dict(message), "queued_messages": [{"message_id": "queued"}]}})
    monkeypatch.setattr(active_agent_projection, "_capacity_snapshot", lambda: (4, {}))
    monkeypatch.setattr(active_agent_projection, "_resident_account_id", lambda _: "zero")
    from management_portal import permission_system
    claims = Mock(side_effect=lambda instruction, parameter, ids, **kw: set(ids) if kw['actor'] != 'denied' else set())
    monkeypatch.setattr(permission_system, 'readable_ids', claims)
    detail = backpack.detail("alice", RESIDENT)
    avatar = detail["principal"]["avatar"]
    for actor in ["alice", "bob"]:
        message.update(message_id="another-" + actor, author_id=actor)
        payload = active_agent_projection.collect(actor=actor)
        for row in payload["running_runs"] + payload["queued_runs"]:
            fields = resident_avatar.projection_fields(avatar)
            assert {key: row[key] for key in fields} == fields
        assert claims.call_args.kwargs["actor"] == actor
    assert resources.calls[0] == (RESIDENT, "alice")
    assert backpack.detail("bob", RESIDENT)["principal"]["avatar"] == avatar
    hidden = active_agent_projection.collect(actor="denied")
    assert RESIDENT not in json.dumps(hidden)
    assert "avatar" not in hidden["running_runs"][0]
    resources.get = Mock(side_effect=PermissionError("denied"))
    with pytest.raises(PermissionError):
        backpack.detail("alice", "resident_000000000099")
    assert resident_avatar.get_existing_avatar("resident_000000000099") is None


def test_unread_conversation_entries_keep_canonical_avatar_and_refresh_signature(monkeypatch):
    """未读对话条目带 Resident 绑定的头像，头像变化只改内容签名不改活动签名

    :module: resident.identity
    :importance: P2
    """
    from management_portal import agent_conversation_index_backend as conversations
    from management_portal import conversation_index_projection as projection

    monkeypatch.setattr(projection, "resident_state_snapshots", lambda actor, ids: {})
    child_id = "resident_000000000098"
    hidden_id = "resident_000000000099"
    completion = {
        "schema_version": 1, "request_actor": "alice", "completion_id": "reply",
        "input_message_id": "input", "completed_at": "2026-09-23T00:01:00Z",
    }
    engaged = {
        "actor_last_user_message_at": "2026-09-23T00:00:00Z",
        "last_user_actor": "alice", "unread_signature": completion,
    }
    payload = {"residents": [{
        "resident_id": RESIDENT, **engaged,
        "family_residents_preview": [{"resident_id": child_id, **engaged}],
    }, {"resident_id": hidden_id}]}
    entries = projection.resident_entries("alice", payload)
    assert {entry["resident_id"] for entry in entries} == {RESIDENT, child_id}
    assert resident_avatar.get_existing_avatar(hidden_id) is None
    for entry in entries:
        fields = resident_avatar.projection_fields(resident_avatar.get_avatar(entry["resident_id"]))
        assert {key: entry[key] for key in fields} == fields

    preferences = {"rows": {
        entry["key"]: {"unread": True, "pinned": False, "read_revision": 1, "dismissed_through": ""} for entry in entries
    }}
    result = conversations.decorate_entries(conversations.normalized_entries(entries), preferences)
    assert result["unread_count"] == 2
    assert all("sprite_index" in entry for entry in result["entries"])
    without_avatars = [
        {key: value for key, value in entry.items() if key not in {"sprite_index", "sprite_rarity"}}
        for entry in entries
    ]
    previous = conversations.decorate_entries(without_avatars, preferences)
    assert result["content_signature"] != previous["content_signature"]
    assert {entry["key"]: entry["unread"] for entry in result["entries"]} == {
        entry["key"]: entry["unread"] for entry in previous["entries"]
    }
    monkeypatch.setattr(projection, "get_avatar", lambda _: None)
    unavailable = projection.resident_entries("alice", payload)
    assert {entry["resident_id"] for entry in unavailable} == {RESIDENT, child_id}
    assert all("sprite_index" not in entry for entry in unavailable)
