from __future__ import annotations
from management_portal.society_service import resident_conversation_stream


import pytest

from management_portal import agent_conversation_index_backend as backend
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import resident_domain
from tests.cases.test_agent_data_store import _chat


from management_portal.society_service import resident_conversation_projection_domain, resident_directory_domain

def make_history(tmp_path, count=7):
    store = AgentDataStore()
    for number in range(count):
        store.display_chat_message(
            f"resident_{number:012x}",
            _chat(f"message_{number}", "user", "history", author_id="CASE-A"),
        )
    return store


def _pages(store):
    """The Conversation repository Lineage pages an actor's Conversations with."""
    return resident_conversation_stream.ResidentConversationRepository(
        resident_conversation_stream.get_lineage_data_store(), store,
    )


def test_conversation_keyset_pages_survive_newer_insertions(tmp_path):
    """会话键集分页在插入更新活动后仍完整读取原列表"""
    store = make_history(tmp_path)
    pages = _pages(store)
    first = pages.actor_activity_page("CASE-A", limit=2)
    assert len(first["activity"]) == 2
    store.display_chat_message(
        "resident_new", _chat("new", "user", "new", author_id="CASE-A", created_at="2026-09-01T00:00:00Z"),
    )
    collected = list(first["activity"])
    token = first["next_page_token"]
    while token:
        page = pages.actor_activity_page("CASE-A", limit=2, page_token=token)
        assert len(page["activity"]) <= 2
        collected.extend(page["activity"])
        token = page["next_page_token"]
    assert collected == [f"resident_{number:012x}" for number in range(7)]
    assert next(iter(pages.actor_activity_page("CASE-A", limit=2)["activity"])) == "resident_new"


def test_conversation_pages_of_one_listing_find_lagging_traces_once(tmp_path, monkeypatch):
    """同一次会话列表的多个活动页只检查一次投影滞后的 trace

    逐页读取用户的会话活动时，版本标记落后的 trace 只在列表开始时查一次并重建，
    各页共用；分页结果完整且顺序不变。

    :aspects: performance
    """
    from management_portal.execution import chat_position_index

    store = make_history(tmp_path)
    reads = []
    original = chat_position_index.lagging

    def listing(connection, sequence_ids=None):
        reads.append(sequence_ids is None)
        return original(connection, sequence_ids)

    monkeypatch.setattr(chat_position_index, "lagging", listing)
    pages = list(_pages(store).iter_actor_activity_pages("CASE-A", limit=2))
    assert [resident_id for page in pages for resident_id in page["activity"]] == [
        f"resident_{number:012x}" for number in range(7)]
    assert len(pages) == 4
    assert reads == [True]


def test_unread_membership_uses_service_visibility_without_local_domain_ports(monkeypatch):
    """Mailbox 未读汇总经服务接口过滤可见性，不调用 Web 内未配置的领域端口"""
    from management_portal.society_service import resident_access
    from types import SimpleNamespace

    monkeypatch.setattr(resident_access, "visible_resident_records", lambda *args: pytest.fail(
        "Web must not invoke an unconfigured Lineage domain port"))
    calls = []
    def visible(ids, actor, scope):
        calls.append((ids, actor, scope))
        return {"resident_ids": [resident_id for resident_id in ids if resident_id != "resident_000000000003"]}
    monkeypatch.setattr(backend, "get_society_service", lambda: SimpleNamespace(
        resident=SimpleNamespace(filter_visible_resident_ids=visible)))
    state = {"rows": {
        "resident:resident_000000000001": {"unread": True, "read_revision": 4},
        "resident:resident_000000000002": {"unread": False, "read_revision": 5},
        "resident:resident_000000000003": {"unread": True, "read_revision": 6},
    }}
    entries = backend.unread_resident_entries("CASE-A", state)
    assert [(item["resident_id"], item["read_revision"]) for item in entries] == [("resident_000000000001", 4)]
    assert calls == [(["resident_000000000001", "resident_000000000003"], "CASE-A", "portal")]
    assert backend.unread_resident_entries("CASE-A", {"rows": {}}) == []
    assert len(calls) == 1


def test_unread_membership_includes_older_pages_once_without_card_or_state_fanout(monkeypatch):
    """未读汇总一次覆盖旧分页且不扇出读取卡片或状态"""
    from types import SimpleNamespace

    monkeypatch.setattr(backend, "get_society_service", lambda: SimpleNamespace(
        resident=SimpleNamespace(filter_visible_resident_ids=lambda ids, actor, scope: {"resident_ids": ids})))
    state = {"rows": {f"resident:resident_{i:012x}": {"unread": True, "read_revision": i + 1} for i in range(120)}}
    entries = backend.unread_resident_entries("CASE-A", state)
    summary = backend.mailbox_summary(entries + [{"key": "chatroom:muted", "kind": "chatroom",
                                               "unread": True, "attention_unread": False}])
    assert summary["unread_count"] == summary["sections"]["conversation"]["unread_count"] == 120
    assert len(summary["entries"]) == 120


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 101}, {"page_token": "not-a-cursor"},
    {"page_token": "e30="}, {"page_token": "a" * 2049},
])
def test_conversation_page_rejects_invalid_bounds(tmp_path, options):
    """会话分页拒绝无效范围及游标"""
    with pytest.raises(ValueError):
        _pages(make_history(tmp_path, count=0)).actor_activity_page("CASE-A", **options)


def test_permission_filtered_page_hydrates_only_returned_summaries(tmp_path, monkeypatch):
    """权限过滤分页仅补全实际返回条目的摘要"""
    store = make_history(tmp_path)
    repository = _pages(store)
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repository)
    hidden = {"resident_000000000000", "resident_000000000002"}
    monkeypatch.setattr(resident_directory_domain, "_engaged_conversation_records", lambda ids, actor: [
        {"resident_id": resident_id} for resident_id in reversed(list(ids)) if resident_id not in hidden
    ])
    hydrated = []

    def summaries(ids, actor):
        hydrated.extend(ids)
        return {}

    monkeypatch.setattr(resident_conversation_projection_domain, "conversation_family_summaries", summaries)
    result = resident_domain.list_resident_conversation_activity("CASE-A", page_size=2)
    expected = ["resident_000000000001", "resident_000000000003"]
    assert [item["resident_id"] for item in result["residents"]] == expected
    assert hydrated == expected
    activity = store.chat_actor_activity("CASE-A", [expected[-1]])
    assert result["next_page_token"] == store.chat_activity_page_token(expected[-1], activity[expected[-1]])


def test_filtered_conversation_page_scans_activity_once_and_keeps_overflow(tmp_path, monkeypatch):
    """对话分页过滤历史对象时只扫描一次活动索引

    跳过大量已删除或不可见对话时只读取一次活动索引，并确保溢出的可见对象在下一页仍可读取。

    :design: decision_b4e5ea4a47ec
    :design-point:
    """
    store = make_history(tmp_path, count=41)
    repository = _pages(store)
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repository)
    visible = {f"resident_{number:012x}" for number in (27, 30, 33, 34, 39)}
    monkeypatch.setattr(resident_directory_domain, "_engaged_conversation_records", lambda ids, actor: [
        {"resident_id": resident_id} for resident_id in reversed(list(ids)) if resident_id in visible
    ])
    monkeypatch.setattr(resident_conversation_projection_domain, "conversation_family_summaries", lambda ids, actor: {})
    original = store.chat_actor_activity_page
    pages_read = []

    def activity(*args, **kwargs):
        pages_read.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "chat_actor_activity_page", activity)
    monkeypatch.setattr(store, "chat_actor_activity", lambda *args, **kwargs: pytest.fail("full activity read"))
    first = resident_domain.list_resident_conversation_activity("CASE-A", page_size=3)
    assert [item["resident_id"] for item in first["residents"]] == sorted(visible)[:3]
    # Every candidate page groups all activity, so each reads the most a page allows.
    assert pages_read and all(page["limit"] == 101 for page in pages_read)
    previous_reads = len(pages_read)
    second = resident_domain.list_resident_conversation_activity("CASE-A", page_size=3, page_token=first["next_page_token"])
    assert [item["resident_id"] for item in second["residents"]] == sorted(visible)[3:]
    assert second["next_page_token"] == ""
    assert len(pages_read) > previous_reads
    visible.clear()
    empty = resident_domain.list_resident_conversation_activity("CASE-A", page_size=3)
    assert empty == {"residents": [], "next_page_token": ""}
    assert all(page["limit"] == 101 for page in pages_read)


def test_dismissed_conversations_leave_the_page_before_their_summaries(tmp_path, monkeypatch):
    """“清除全部”隐藏的对话在分页时直接跳过

    隐藏的对话不读取身份与摘要，也不占页面名额，每页用仍显示的对话填满；
    对话有了晚于清除时间的活动后重新出现。

    :aspects: performance
    """
    from management_portal import conversation_read_state

    store = make_history(tmp_path)
    repository = _pages(store)
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repository)
    read = []

    def records(ids, actor):
        read.extend(ids)
        return [{"resident_id": resident_id} for resident_id in ids]

    monkeypatch.setattr(resident_directory_domain, "_engaged_conversation_records", records)
    monkeypatch.setattr(resident_conversation_projection_domain, "conversation_family_summaries", lambda ids, actor: {})
    activity = store.chat_actor_activity("CASE-A")
    hidden = [f"resident_{number:012x}" for number in (0, 1, 3)]
    conversation_read_state.dismiss("CASE-A", {f"resident:{resident_id}": activity[resident_id] for resident_id in hidden})
    first = resident_domain.list_resident_conversation_activity("CASE-A", page_size=2)
    second = resident_domain.list_resident_conversation_activity("CASE-A", page_size=2, page_token=first["next_page_token"])
    shown = [f"resident_{number:012x}" for number in (2, 4, 5, 6)]
    assert [item["resident_id"] for item in first["residents"] + second["residents"]] == shown
    assert second["next_page_token"] == ""
    assert not set(read) & set(hidden)
    store.display_chat_message(hidden[1], _chat("again", "user", "again", author_id="CASE-A", created_at="2030-01-01T00:00:00Z"))
    again = resident_domain.list_resident_conversation_activity("CASE-A", page_size=2)
    assert [item["resident_id"] for item in again["residents"]] == [hidden[1], shown[0]]


def test_mailbox_activity_does_not_repeat_older_trace_on_next_page(tmp_path, monkeypatch):
    """Mailbox 活动提前排序后下页不重复旧 trace"""
    store = make_history(tmp_path, count=3)
    repository = _pages(store)
    monkeypatch.setattr(repository.state_store, "mailbox_actor_activity", lambda actor, **kwargs: {
        "resident_000000000000": "2030-01-01T00:00:00Z",
    })
    first = repository.actor_activity_page("CASE-A", limit=1)
    assert list(first["activity"]) == ["resident_000000000000"]
    second = repository.actor_activity_page("CASE-A", limit=1, page_token=first["next_page_token"])
    assert list(second["activity"]) == ["resident_000000000001"]


def test_http_payload_uses_bounded_page_instead_of_full_discovery(monkeypatch):
    """会话 HTTP 响应使用有界分页读取"""
    from management_portal import serve_management_portal as portal

    calls = []

    def page(actor, **options):
        calls.append((actor, options))
        return {"entries": [], "paged": True, "page_size": 3, "next_page_token": "next"}

    def unexpected_full_read(*args, **kwargs):
        pytest.fail("paginated HTTP request used full conversation discovery")

    monkeypatch.setattr(portal.agent_conversation_index_backend, "build_entries_page", page)
    monkeypatch.setattr(portal, "_agent_conversation_entries", unexpected_full_read)
    result = portal._agent_conversation_payload("CASE-A", page_size=3, page_token="previous")
    assert calls == [("CASE-A", {"page_size": 3, "page_token": "previous"})]
    assert result["next_page_token"] == "next"
    assert result["paged"] is True


def test_first_index_page_carries_notice_board_mentions(monkeypatch):
    """会话索引首页附带公告提及且后续页不重复"""
    from management_portal import notice_board_backend, serve_management_portal as portal

    mention = {
        "event_id": "post_1:post:2026-09-30T00:00:00Z", "post_id": "post_1",
        "title": "Release plan", "author": "sample_admin", "context": "post",
        "occurred_at": "2026-09-30T00:00:00Z",
    }
    requested = []

    def mention_events(actor):
        requested.append(actor)
        return [mention]

    monkeypatch.setattr(notice_board_backend.notice_board, "mention_events", mention_events)
    monkeypatch.setattr(portal.agent_conversation_index_backend, "build_entries_page", lambda actor, **options: {
        "entries": [], "paged": True, "page_size": 3, "next_page_token": "",
    })
    first = portal._agent_conversation_payload("CASE-A", page_size=3)
    assert [entry["key"] for entry in first["entries"]] == ["notice-board:post_1"]
    assert first["entries"][0]["kind"] == "notice-board-mention"
    later = portal._agent_conversation_payload("CASE-A", page_size=3, page_token="next")
    assert later["entries"] == []
    assert requested == ["CASE-A"]
