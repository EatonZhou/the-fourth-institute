"""Multi-Resident search reads Chat projections in bounded database batches.

:module: resident.conversation
:aspects: functional, performance
:design: decision_c524c775e0be
"""
from contextlib import contextmanager

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository
from management_portal.society_service import resident_protocol
from tests.cases.database_test_support import guard_queries


def say(store, resident, mid, text, *, role="user", at="2026-10-09T00:00:00Z"):
    return store.display_chat_message(resident, {
        "message_id": mid, "role": role, "author_id": "CASE-A" if role == "user" else "portal",
        "created_at": at, "content_parts": [{"type": "text", "text": text}],
    })


def test_batch_chat_preserves_reset_fork_notice_payload_and_occurrence_order():
    """批量读取多个 Resident 的 Chat 与逐个读取一致，保留重置、fork、通知和请求顺序

    :design-point:"""
    store = AgentDataStore()
    a, b, fork, missing = [f"resident_{i:012x}" for i in range(4)]
    say(store, a, "archived", "old")
    store.record_context_reset(a, reset_at="2026-10-09T00:00:01Z", actor_ref="CASE-A", trace_position=store.trace_length(a))
    say(store, a, "body", "<user-message>literal</user-message>\n" + "数据库" * 10000)
    say(store, a, "notice", "failure", role="system", at="2026-10-08T00:00:00Z")
    say(store, b, "other", "Straße")
    store.fork_agent(source_agent_id=a, target_agent_id=fork, actor_ref="CASE-A", segment_ids=store.sequence_segment_ids(store.trace_sequence_id(a)))
    wanted = [a, b, fork, missing, a]
    expected = {rid: store.read_chat_messages(rid) for rid in wanted}
    assert store.read_chat_messages_many(wanted) == expected
    assert [m["message_id"] for m in expected[a]] == ["body", "notice"]
    assert expected[missing] == []
    assert store.read_chat_messages_many([]) == {}


@pytest.mark.parametrize("size", [1, 53])
def test_search_batches_reads_with_exact_casefold_pagination_and_neighbors(monkeypatch, size):
    """跨 Resident 搜索按批读取，大小写折叠匹配、分页和前后相邻消息准确

    :design-point:"""
    store = AgentDataStore()
    repository = ResidentConversationRepository(None, store)
    ids = [f"resident_{i:012x}" for i in range(size)]
    for index, rid in enumerate(ids):
        say(store, rid, f"before_{index:03d}", "before")
        say(store, rid, f"hit_{index:03d}", "Straße 数据库")
        say(store, rid, f"after_{index:03d}", "after", role="system")
    monkeypatch.setattr(store, "read_chat_messages", lambda *_a, **_k: pytest.fail("per-Resident history read"))
    statements = []
    original_read = store.database.read
    @contextmanager
    def measured():
        with original_read() as connection:
            yield guard_queries(connection, statements=statements)
    monkeypatch.setattr(store.database, "read", measured)
    result = repository.search_messages(ids, "STRASSE", limit=1)
    hit = result["results"][0]
    assert hit["resident_id"] == ids[-1]
    assert hit["message"]["message_id"] == f"hit_{size - 1:03d}"
    assert hit["previous_message_id"] == f"before_{size - 1:03d}"
    assert hit["next_message_id"] == f"after_{size - 1:03d}"
    assert result["next_page_token"] == ("1" if size > 1 else "")
    # Trace/reset and payload/usage reads grow by batch, not by Resident.
    batches = (size + 49) // 50
    assert len(statements) <= 10 * batches
    rest = repository.search_messages(ids, "数据库", page_token="1", limit=200)
    assert [hit["message"]["message_id"] for hit in rest["results"]] == [
        f"hit_{i:03d}" for i in reversed(range(size - 1))
    ]
    assert rest["next_page_token"] == ""


def test_search_authorizes_one_batch_before_reading_any_message(monkeypatch):
    """搜索消息前先对 Resident 列表一次性批量鉴权，只读取可见 Resident 的消息

    :design-point:"""
    visible, hidden = "resident_111111111111", "resident_222222222222"
    calls = []
    def authorized(ids, actor, service_actor):
        calls.append((ids, actor, service_actor))
        return [{"resident_id": visible}]
    class Repository:
        def search_messages(self, ids, query, **options):
            assert calls == [([visible, hidden], "CASE-A", "portal")]
            assert ids == [visible]
            assert query == "body" and options == {"page_token": "2", "limit": 3}
            return {"results": []}
    monkeypatch.setattr(resident_protocol.resident_access, "visible_resident_records", authorized)
    monkeypatch.setattr(resident_protocol.resident_conversation_stream, "get_repository", Repository)
    monkeypatch.setattr(resident_protocol, "_target", lambda *_a: pytest.fail("per-Resident resolution"))
    assert resident_protocol.search_conversation_messages([visible, hidden, visible], "CASE-A", "body", page_token="2", limit=3) == {"results": []}
