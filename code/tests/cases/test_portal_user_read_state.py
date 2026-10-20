from __future__ import annotations

from management_portal import conversation_read_state
from management_portal import serve_management_portal as portal


def test_read_change_publishes_actor_scoped_resident_state(monkeypatch, isolated_portal_case):
    """已读状态变化发布带用户范围的 Resident 视图和会话索引变更"""
    changes = []
    monkeypatch.setattr(portal, "publish_projection_change",
                        lambda projection, target, **payload: changes.append((projection, target, payload)))
    key = "resident:resident_111111111111"
    with isolated_portal_case():
        portal._set_agent_conversation_read_state("sample_admin", key, unread=True)
    assert ("resident-view-state", "resident_111111111111",
            {"actor": "sample_admin", "fields": ["unread"]}) in changes
    assert ("agent-conversation-index", "current",
            {"actor": "sample_admin", "fields": ["unread"], "details": {"keys": [key]}}) in changes


def test_read_notification_reaches_local_and_other_portal_instances(monkeypatch):
    """已读通知同时发布到本地通知中心和其他 Portal 实例"""
    from management_portal.notifications import bus

    local = bus.NotificationHub()
    published = []
    monkeypatch.setattr(bus, "_HUB", local)
    monkeypatch.setattr(bus._PUBLISHER, "publish", lambda subject, payload: published.append((subject, payload)))
    notification = bus.publish_projection_change(
        "agent-conversation-index", "current", actor="sample_admin", fields=["unread"],
        details={"keys": ["resident:resident_111111111111"]},
    )
    assert local.sequence == 1
    assert published == [(notification.subject, notification.payload)]
    assert notification.payload["actor"] == "sample_admin"


def test_inbox_reconciliation_waits_for_fresh_snapshot_instead_of_serving_stale(monkeypatch):
    """收件箱对账在缓存失效后等待新快照，不返回过期数据"""
    cache = portal._CoalescingReadCache(30)
    monkeypatch.setattr(portal, "_AGENT_CONVERSATION_READ_CACHE", cache)
    current = {"unread_count": 1}
    monkeypatch.setattr(portal, "_agent_conversation_payload", lambda *a, **k: dict(current))
    assert portal._cached_agent_conversation_payload("sample_admin")["unread_count"] == 1
    cache.invalidate(preserve_stale=True)
    current["unread_count"] = 0
    assert portal._cached_agent_conversation_payload("sample_admin")["unread_count"] == 0


def test_event_target_reads_current_projection_without_waiting_for_cache_ttl(monkeypatch):
    """指定 Resident 的会话读取直接取当前投影，不等缓存过期"""
    current = {"unread": True}
    monkeypatch.setattr(portal, "_agent_conversation_payload", lambda *a, **k: dict(current))
    assert portal._cached_agent_conversation_payload("sample_admin", resident_id="resident_111111111111")["unread"]
    current["unread"] = False
    assert not portal._cached_agent_conversation_payload("sample_admin", resident_id="resident_111111111111")["unread"]


def test_targeted_conversation_payload_exposes_the_canonical_resident_cursor(monkeypatch) -> None:
    """指定 Resident 的会话负载带上该用户的规范 Resident 游标"""
    resident_id = "resident_777777777777"
    calls: list[str] = []
    def entries(_actor, resident_ids=None):
        calls.append("entries")
        return []
    def cursor(actor, target):
        calls.append("cursor")
        return 7 if (actor, target) == ("sample_admin", resident_id) else 0
    monkeypatch.setattr(
        portal,
        "_agent_conversation_entries",
        entries,
    )
    monkeypatch.setattr(
        portal.agent_conversation_index_backend,
        "canonical_resident_cursor",
        cursor,
    )
    monkeypatch.setattr(
        portal.resident_api,
        "resolve_resident_identity",
        lambda target, actor: {
            "resident_id": target,
            "lineage_id": "lineage_777777777777",
        },
    )
    monkeypatch.setattr(portal, "_portal_user_preferences", lambda _actor: {})

    payload = portal._agent_conversation_payload("sample_admin", resident_id=resident_id)

    assert payload["resident_id"] == resident_id
    assert payload["entries"] == []
    assert payload["resident_cursor"] == 7
    assert payload["projection_cursor"] == 0
    assert payload["target_present"] is False
    assert payload["target_state"] == "present"
    assert calls == ["cursor", "entries"]


def test_targeted_conversation_payload_returns_an_authoritative_deleted_tombstone_contracts(monkeypatch, subtests, isolated_portal_case):
    """目标 Resident 已删除或无权访问时，会话负载返回对应墓碑且不读取条目"""
    with subtests.test(contract='test_targeted_conversation_payload_returns_an_authoritative_deleted_tombstone'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            resident_id = 'resident_deadbeef0000'

            def deleted(_resident_id, _actor):
                raise KeyError(_resident_id)
            monkeypatch.setattr(portal.resident_api, 'resolve_resident_identity', deleted)
            monkeypatch.setattr(portal.agent_conversation_index_backend, 'canonical_resident_cursor', lambda *_args: (_ for _ in ()).throw(AssertionError('cursor must not be read')))
            monkeypatch.setattr(portal, '_agent_conversation_entries', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('entries must not be read')))
            monkeypatch.setattr(portal, '_portal_user_preferences', lambda _actor: {})
            payload = portal._agent_conversation_payload('sample_admin', resident_id=resident_id)
            assert payload['resident_id'] == resident_id
            assert payload['target_state'] == 'deleted'
            assert payload['target_present'] is False
            assert payload['resident_cursor'] == 0
            assert payload['entries'] == []
    with subtests.test(contract='test_targeted_conversation_payload_returns_an_authoritative_forbidden_tombstone'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            resident_id = 'resident_ffffffffffff'

            def forbidden(_resident_id, _actor):
                raise PermissionError(_resident_id)
            monkeypatch.setattr(portal.resident_api, 'resolve_resident_identity', forbidden)
            monkeypatch.setattr(portal, '_portal_user_preferences', lambda _actor: {})
            payload = portal._agent_conversation_payload('sample_admin', resident_id=resident_id)
            assert payload['target_state'] == 'forbidden'
            assert payload['target_present'] is False
            assert payload['entries'] == []


def _row(actor: str, key: str) -> dict:
    return conversation_read_state.row(conversation_read_state.load(actor), key)


def test_one_row_handles_manual_unread_read_and_new_alerts(isolated_portal_case):
    """一行阅读状态承担手动未读、已读和新提醒

    同一位用户的同一段对话只有一行：标为未读把阅读位置移到最新提醒之前，标为已读读到最新提醒；
    同一事件再次送达不再提醒，新事件才让对话重新未读；每次变化的修订号都更大，另一位用户不受影响。

    :design: decision_15f7df737ed5
    """
    key = "resident:resident_111111111111"
    with isolated_portal_case():
        assert _row("sample_admin", key) == {**_row("sample_admin", key), "unread": False, "read_revision": 0}
        unread = portal._set_agent_conversation_read_state("sample_admin", key, unread=True)
        assert unread["unread"] and unread["read_revision"] > 0
        read = portal._set_agent_conversation_read_state("sample_admin", key, unread=False)
        assert not read["unread"] and read["read_revision"] > unread["read_revision"]
        # Reading what is already read changes nothing.
        assert portal._set_agent_conversation_read_state("sample_admin", key, unread=False) == read
        assert conversation_read_state.alert(["@SAMPLE_ADMIN"], key, "reply-2") == ["sample_admin"]
        alerted = _row("sample_admin", key)
        assert alerted["unread"] and alerted["read_revision"] > read["read_revision"]
        portal._set_agent_conversation_read_state("sample_admin", key, unread=False)
        # The same event delivered again does not alert again.
        assert conversation_read_state.alert(["sample_admin"], key, "reply-2") == []
        assert not _row("sample_admin", key)["unread"]
        assert not _row("CASE-A", key)["unread"]


def test_clear_unread_is_atomic_and_new_reply_remains_unread(isolated_portal_case):
    """清除未读只读到当时的提醒，之后的新回复保持未读

    :design: decision_15f7df737ed5
    """
    key = "resident:resident_111111111111"
    with isolated_portal_case():
        conversation_read_state.alert(["sample_admin"], key, "reply-2")
        result = portal._clear_agent_conversations("sample_admin", mode="unread", entries=[{"key": key, "unread": True}])
        assert result["cleared_count"] == 1
        assert [item["key"] for item in result["items"]] == [key] and not result["items"][0]["unread"]
        assert not _row("sample_admin", key)["unread"]
        conversation_read_state.alert(["sample_admin"], key, "reply-3")
        assert _row("sample_admin", key)["unread"]
        portal._set_agent_conversation_read_state("sample_admin", key, unread=False)
        assert not _row("sample_admin", key)["unread"]


def test_cleared_conversation_returns_with_new_activity_or_alert(isolated_portal_case):
    """清除全部后对话在出现新活动或新提醒时重新显示

    “清除全部”记下每个条目当时的活动时间；活动时间不晚于它的条目不显示，置顶条目照常显示；
    之后活动时间更新或有新提醒的条目重新出现。

    :design: decision_15f7df737ed5
    """
    from management_portal import agent_conversation_index_backend as index

    old = {"key": "resident:resident_111111111111", "kind": "resident-main", "updated_at": "2026-10-16T01:00:00Z"}
    pinned = {"key": "resident:resident_222222222222", "kind": "resident-main", "updated_at": "2026-10-16T01:00:00Z"}
    alerted = {"key": "resident:resident_333333333333", "kind": "resident-main", "updated_at": "2026-10-16T01:00:00Z"}
    with isolated_portal_case():
        conversation_read_state.set_pinned("sample_admin", pinned["key"], True)
        result = portal._clear_agent_conversations(
            "sample_admin", mode="all", entries=[old, {**pinned, "pinned": True}, alerted])
        assert result["cleared_count"] == 2

        def listed(*entries):
            return [entry["key"] for entry in index.decorate_entries(
                list(entries), conversation_read_state.load("sample_admin"))["entries"]]

        assert listed(old, pinned, alerted) == [pinned["key"]]
        conversation_read_state.alert(["sample_admin"], alerted["key"], "reply-9")
        later = {**old, "updated_at": "2026-10-16T02:00:00Z"}
        assert listed(later, pinned, alerted) == [pinned["key"], later["key"], alerted["key"]]


def test_preferences_remain_user_scoped(isolated_portal_case):
    """用户偏好按用户隔离，收藏的 Lineage 去重保存"""
    with isolated_portal_case():
        portal._set_portal_user_preferences("sample_admin", {"preferences": {
            "appearance": {"motion": "full"},
            "lineage": {"favorite_lineage_ids": ["lineage_111111111111", "lineage_111111111111"]},
        }})
        assert portal._portal_user_preferences("sample_admin")["appearance"]["motion"] == "full"
        assert portal._portal_user_preferences("CASE-A")["appearance"]["motion"] == "system"
        assert portal._portal_user_preferences("sample_admin")["lineage"]["favorite_lineage_ids"] == ["lineage_111111111111"]
        assert portal._portal_user_preferences("CASE-A")["lineage"]["favorite_lineage_ids"] == []


def test_article_workspace_preference_survives_read_state_refactor(isolated_portal_case):
    """文章工作区上次打开的条目按用户保存在偏好中"""
    with isolated_portal_case():
        portal._set_portal_user_preferences("sample_admin", {"preferences": {
            "resource_workspaces": {"article": {"last_item_id": "article_111111111111"}},
        }})
        assert portal._portal_user_preferences("sample_admin")["resource_workspaces"]["article"]["last_item_id"] == "article_111111111111"
        assert portal._portal_user_preferences("CASE-A")["resource_workspaces"]["article"]["last_item_id"] == ""


def test_read_state_is_removed_with_actor_data(isolated_portal_case):
    """删除用户数据时一并删除其已读状态"""
    with isolated_portal_case():
        conversation_read_state.alert(["sample_admin"], "resident:resident_111111111111", "reply-1")
        assert conversation_read_state.load("sample_admin")["rows"]
        assert conversation_read_state.delete_actor("sample_admin")
        assert conversation_read_state.load("sample_admin")["rows"] == {}


def test_the_schema_check_takes_no_lock_on_an_existing_read_state_table(isolated_portal_case):
    """每个进程首次使用时检查阅读状态表结构，表已存在时不锁表

    CREATE INDEX IF NOT EXISTS 和 ADD COLUMN IF NOT EXISTS 即使无事可做也要锁表，会排在写过这张表的
    事务后面，后续读写再排在它后面；所以先查索引是否存在，列写在建表语句里。

    :design: decision_15f7df737ed5
    """
    from management_portal import conversation_receipt_store as store

    with isolated_portal_case():
        database = store.database()
        database.ensure()
        table = database.database.schema + ".conversation_states"
        with database.database.transaction() as connection:
            def modes():
                return sorted(row["mode"] for row in connection.execute(
                    "SELECT mode FROM pg_locks WHERE pid=pg_backend_pid() AND relation=to_regclass(%s)", (table,)))

            connection.executescript(store.DDL)
            assert modes() == []
            connection.execute("CREATE INDEX IF NOT EXISTS conversation_states_unread_idx "
                               "ON conversation_states(actor) WHERE alert_position > read_position")
            assert "ShareLock" in modes()


def test_a_mention_alerts_mailbox_once_and_reading_the_post_on_home_reads_it(monkeypatch, isolated_portal_case):
    """公告板 @ 在提及时写入被提及者的 Mailbox 行，在 Home 读完帖子即读到这条 @

    发帖、编辑新增和回复中的 @ 各提醒被提及者一次，作者自己和未被提及的人没有提醒；
    在 Home 只读到较早版本时 @ 保持未读，读完整个帖子后 Mailbox 中的 @ 也变为已读。

    :design: decision_15f7df737ed5
    """
    from management_portal.notice_board_backend import NoticeBoardBackend, mailbox_key

    monkeypatch.setattr(NoticeBoardBackend, "_sync_permissions", staticmethod(lambda *_args, **_kwargs: None))

    def unread(actor, post_id):
        return _row(actor, mailbox_key(post_id))["unread"]

    with isolated_portal_case():
        board = NoticeBoardBackend()
        post_id = board.create_post("alice", "Status", "Body @bob @alice")["post"]["post_id"]
        assert unread("bob", post_id) and not unread("alice", post_id) and not unread("carol", post_id)
        first = int(board.get_post("bob", post_id)["post"]["notification_revision"])
        board.add_reply("alice", post_id, "@bob 再看一下")
        # Reading an earlier revision on Home leaves the newer mention unread.
        board.mark_post_read("bob", post_id, first)
        assert unread("bob", post_id)
        current = int(board.get_post("bob", post_id)["post"]["notification_revision"])
        board.mark_post_read("bob", post_id, current)
        assert not unread("bob", post_id)
        board.update_post("alice", post_id, "Status", "Body @bob @carol")
        assert unread("carol", post_id) and not unread("bob", post_id)
