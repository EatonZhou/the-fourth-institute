from __future__ import annotations
from management_portal.society_service import resident_conversation_stream

from pathlib import Path

import psycopg
import pytest

from management_portal.execution.agent_data_store import (
    CHAT_READ_INDEX_SQL,
    CHAT_STATISTICS_SQL,
    AgentDataStore,
    agent_data_schema_matches,
)
from management_portal.society_service import resident_domain
from tests.cases.test_agent_data_store import _chat


from management_portal.society_service import resident_conversation_projection_domain, resident_directory_domain

def test_targeted_actor_activity_includes_fork_history(tmp_path):
    """按 Resident 查询用户活跃时间时，fork 出的 Resident 继承源 Resident 的历史消息"""
    store = AgentDataStore()
    parent, child = "resident_parent", "resident_child"
    store.display_chat_message(parent, _chat("original", "user", "hello", author_id="CASE-A"))
    store.fork_agent(source_agent_id=parent, target_agent_id=child, actor_ref="CASE-A", segment_ids=store.sequence_segment_ids(store.trace_sequence_id(parent)))
    assert store.chat_actor_activity("case-a", [child]) == {
        child: store.chat_actor_activity("case-a", [parent])[parent],
    }
    store.display_chat_message(child, _chat("native", "user", "world", author_id="CASE-A"))
    full = store.chat_actor_activity("CASE-A")
    assert set(full) == {parent, child}
    assert store.chat_actor_activity(" case-a ", (item for item in [child, child, "", "missing"])) == {child: full[child]}
    targets = [f"missing-{number}" for number in range(1000)] + [parent, child]
    assert store.chat_actor_activity("CASE-A", targets) == full


def test_actor_activity_page_orders_and_seeks_in_sql(tmp_path):
    """用户活跃 Resident 分页在 SQL 中按时间倒序排序、按游标续页并排除指定 Resident"""
    store = AgentDataStore()
    for number, at in enumerate((
        "2026-10-01T00:00:00Z", "2026-10-03T00:00:00Z",
        "2026-10-03T00:00:00Z", "2026-10-02T00:00:00Z",
    )):
        store.display_chat_message(
            f"resident_page_{number}",
            _chat(f"page_{number}", "user", "hello", author_id="CASE-A", created_at=at),
        )
    first = store.chat_actor_activity_page("CASE-A", limit=2)
    assert [resident_id for resident_id, _ in first] == ["resident_page_1", "resident_page_2"]
    second = store.chat_actor_activity_page(
        "CASE-A", after=(first[-1][1], first[-1][0]), limit=2,
    )
    assert [resident_id for resident_id, _ in second] == ["resident_page_3", "resident_page_0"]
    assert store.chat_actor_activity_page(
        "CASE-A", limit=5, exclude_agent_ids=["resident_page_2"],
    ) == [first[0], *second]


def test_actor_activity_includes_operational_chat_notices(tmp_path):
    """用户活跃时间计入该用户发出的轮次通知和发给该用户的资源变更通知"""
    store = AgentDataStore()
    sent_at = "2026-10-01T00:00:00Z"
    addressed_at = "2026-10-02T00:00:00Z"
    store.display_chat_message("resident_notice_sent", {
        "message_id": "notice_sent", "role": "user", "author_id": "CASE-A",
        "created_at": sent_at,
        "content_parts": [{"type": "native_turn", "status": "completed"}],
    })
    store.display_chat_message("resident_notice_addressed", {
        "message_id": "notice_addressed", "role": "agent",
        "created_at": addressed_at,
        "recipients": [{"kind": "user", "id": "CASE-A"}],
        "content_parts": [{"type": "resource_changes", "items": []}],
    })

    assert store.chat_actor_activity_page("CASE-A") == [
        ("resident_notice_addressed", addressed_at),
        ("resident_notice_sent", sent_at),
    ]
    assert store.chat_actor_activity("CASE-A", ["resident_notice_sent"]) == {
        "resident_notice_sent": sent_at,
    }


@pytest.mark.parametrize("targeted", [False, True])
def test_activity_uses_all_effective_messages_in_time_order(tmp_path, targeted):
    """用户活跃时间取所有有效消息中最晚的一条，清空 Chat 后只计清空之后的消息"""
    store = AgentDataStore()
    parent, child = "resident_parent", "resident_child"
    inherited_at = "2026-08-23T05:00:00Z"
    native_at = "2026-08-23T03:00:00Z"
    older_at = "2026-08-23T01:00:00Z"
    store.display_chat_message(parent, _chat("inherited", "user", "hello", author_id="CASE-A", created_at=inherited_at))
    store.fork_agent(source_agent_id=parent, target_agent_id=child, actor_ref="CASE-A", segment_ids=store.sequence_segment_ids(store.trace_sequence_id(parent)))
    store.display_chat_message(child, _chat("native", "user", "hello", author_id="CASE-A", created_at=native_at))
    store.display_chat_message(child, _chat("older", "user", "hello", author_id="CASE-A", created_at=older_at))
    store.display_chat_message(child, _chat("assistant", "assistant", "hello", author_id="CASE-A", created_at=inherited_at))
    store.display_chat_message(child, _chat("other_actor", "user", "hello", author_id="other", created_at=inherited_at))
    expected = {child: inherited_at} if targeted else {parent: inherited_at, child: inherited_at}
    assert store.chat_actor_activity("case-a", [child] if targeted else None) == expected
    # A clear ends what counts: only messages after it do.
    store.clear_chat(child, actor_ref="CASE-A")
    expected.pop(child)
    assert store.chat_actor_activity("case-a", [child] if targeted else None) == expected
    store.display_chat_message(child, _chat("after_clear", "user", "hello", author_id="CASE-A", created_at=older_at))
    expected[child] = older_at
    assert store.chat_actor_activity("case-a", [child] if targeted else None) == expected


def test_empty_activity_scope_never_opens_database(tmp_path, monkeypatch):
    """Resident 范围或用户为空时，活跃时间查询不访问数据库"""
    store = AgentDataStore()

    def unexpected_read():
        pytest.fail("empty activity query accessed database")

    monkeypatch.setattr(store.database, "read", unexpected_read)
    assert store.chat_actor_activity("CASE-A", []) == {}
    assert store.chat_actor_activity("CASE-A", ["", " ", None]) == {}
    assert store.chat_actor_activity("", None) == {}


@pytest.mark.parametrize("targeted", [False, True])
def test_actor_activity_seeks_index_without_reading_message_bodies(tmp_path, monkeypatch, targeted):
    """查询用户活跃时间走 Chat 读索引，不读取大段消息正文"""
    store = AgentDataStore()
    for number in range(48):
        store.display_chat_message(f"resident_other_{number}", _chat(f"other_{number}", "user", "z" * 131072, author_id="other"))
    store.display_chat_message("resident_target", _chat("target", "user", "hello", author_id="CASE-A"))
    class Explain:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=()):
            return self.connection.execute("EXPLAIN (COSTS OFF) " + statement, parameters)

    with store.database.read_transaction() as connection:
        connection.execute("SET LOCAL enable_seqscan=off")
        traces = list(store._trace_ids(connection, ["resident_target"]).values()) if targeted else None
        plans = [str(row[0]) for row in AgentDataStore._activity_rows(Explain(connection), "CASE-A", traces)]
    # The activity kept per trace and person is found by its index; no message is read.
    assert any("Index" in plan and "chat_actor_activity" in plan for plan in plans)
    assert not any("segments" in plan or "chat_message_positions" in plan for plan in plans)
    io_path = Path("/proc/thread-self/io")
    def logical_bytes():
        if not io_path.exists():
            return 0
        return int(next(line.split(":", 1)[1] for line in io_path.read_text().splitlines() if line.startswith("rchar:")))
    before = logical_bytes()
    assert set(store.chat_actor_activity("CASE-A", ["resident_target"] if targeted else None)) == {"resident_target"}
    assert logical_bytes() - before < 256 * 1024


@pytest.mark.parametrize("requested", [[], [""], [" ", None]])
def test_empty_resident_filter_skips_all_conversation_projections(monkeypatch, requested):
    """Resident 过滤列表为空或只有空值时，会话活跃列表直接返回空且不打开存储"""
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: pytest.fail("empty filter opened repository"))
    assert resident_domain.list_resident_conversation_activity("CASE-A", requested) == {"residents": []}


def test_resident_filter_is_applied_before_activity_discovery(monkeypatch):
    """Resident 过滤条件去重后先传给活跃查询，再筛选可见会话"""
    resident_id = "resident_0123456789ab"

    class Repository:
        def actor_activity(self, actor, resident_ids=None):
            assert actor == "CASE-A"
            assert resident_ids == [resident_id]
            return {resident_id: "2026-09-14T01:00:00Z"}

    def visible(candidates, actor):
        assert actor == "CASE-A"
        assert candidates == [resident_id]
        return []

    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: Repository())
    monkeypatch.setattr(resident_directory_domain, "_engaged_conversation_records", visible)
    monkeypatch.setattr(resident_conversation_projection_domain, "_conversation_family_summaries", lambda ids, actor: {})
    assert resident_domain.list_resident_conversation_activity("CASE-A", iter([resident_id, "", resident_id])) == {"residents": []}


def test_conversation_facts_are_read_without_writing(monkeypatch):
    """读取对话统计不打开写事务"""
    store = AgentDataStore()
    store.display_chat_message("resident_target", _chat("first", "user", "hello", author_id="CASE-A"))
    store.display_chat_message("resident_target", _chat("second", "user", "again", author_id="other"))
    monkeypatch.setattr(store.database, "write", lambda *_args, **_kwargs: pytest.fail("a read opened a write transaction"))
    assert set(store.chat_actor_activity("CASE-A")) == {"resident_target"}
    summary = store.chat_conversation_summaries(["resident_target"], "@CASE-A")["resident_target"]
    assert summary["user_message_count"] == 2
    assert summary["last_user_actor"] == "other"
    assert summary["actor_request_ids"] == ["first"]


def test_a_store_missing_a_read_index_does_not_open():
    """缺少 Chat 读索引的 AgentDataStore 拒绝打开，补回索引后可以重新打开"""
    store = AgentDataStore()
    store.database.write(lambda connection: connection.execute("DROP INDEX segments_message_idx"))
    try:
        with pytest.raises(RuntimeError, match="does not match its schema"):
            AgentDataStore()
    finally:
        store.database.write(lambda connection: connection.execute(CHAT_READ_INDEX_SQL["segments_message_idx"]))
    reopened = AgentDataStore()
    with reopened.database.read() as connection:
        assert agent_data_schema_matches(connection)
        statistics = connection.execute(
            "SELECT stxname FROM pg_statistic_ext WHERE stxnamespace = current_schema()::regnamespace"
        ).fetchall()
    assert sorted(row["stxname"] for row in statistics) == sorted(CHAT_STATISTICS_SQL)


def test_message_headers_find_notice_positions_by_the_notice(monkeypatch):
    """按消息身份读取多个 Chat 的消息头时，消息和运行通知都按身份在消息投影里查找，不在每条轨迹里各查一遍"""
    store = AgentDataStore()
    agents = [f"resident_header_{number}" for number in range(8)]
    for agent in agents:
        # Chats long enough that reading each of them costs more than the lookup.
        for number in range(40):
            store.display_chat_message(agent, _chat(f"{agent}_question_{number}", "user", "hello", author_id="CASE-A"))
    store.display_chat_message(agents[0], {
        "message_id": "header_notice", "role": "user", "author_id": "CASE-A",
        "content_parts": [{"type": "native_turn", "status": "completed"}],
    })
    store.database.write(lambda connection: connection.execute("ANALYZE chat_message_positions"))
    statements = []
    execute = psycopg.Cursor.execute

    def record(cursor, query, params=None, **kwargs):
        statements.append((query, params))
        return execute(cursor, query, params, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", record)
    store.chat_message_headers(agents, message_ids=[f"{agents[1]}_question_3", "header_notice"])
    monkeypatch.undo()
    query, parameters = next(
        (query, parameters) for query, parameters in statements
        if "position.message_id = ANY" in str(query)
    )
    with store.database.read_transaction() as connection:
        connection.execute("SET LOCAL enable_seqscan=off")
        plan = "\n".join(str(row[0]) for row in connection.execute("EXPLAIN (COSTS OFF) " + query, parameters))
    assert "chat_message_positions_identity_idx" in plan
