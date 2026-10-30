"""
:module: execution.persistence
:importance: P1
:aspects: functional, performance
"""
from management_portal.society_service import business_fact_views
import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import lineage_human_notifications, lineage_reads
from management_portal.society_service.read_snapshot import read_snapshot_scope, request_cached
from management_portal.society_service.state_store import LineageDataStore
from management_portal.society_service import lineage_aggregate
from tests.cases.test_agent_data_store import _chat
from tests.cases.test_lineage_catalogs import _lineage_definition


def test_provider_binding_uses_covering_session_index_even_without_turn(tmp_path):
    """无 Turn 的 Provider Session 绑定仍正确选择首个执行所属 Agent

    验证共享 Provider Session 绑定返回按执行开始时间排序的首个 Agent；无 Turn 时查询仍使用覆盖索引，不全表扫描或临时排序。
    """
    store = AgentDataStore()
    session = store.ensure_provider_session(provider="codex", backend_route_id="one", backend_session_id="thread", agent_id="first", actor_ref="CASE-A")
    reference = session["provider_session_ref"]
    for execution_id, agent_id, started in (("first", "owner", "2026-01-01T00:00:00Z"), ("second", "other", "2026-01-02T00:00:00Z")):
        store.begin_execution(execution_id=execution_id, agent_id=agent_id, actor_ref="CASE-A", provider="codex", started_at=started)
        store.bind_execution_provider_session(execution_id, provider_session_ref=reference)
    store.bind_provider_session("test-binding", reference)
    assert store.provider_binding("test-binding")["agent_id"] == "owner"
    with store.database.read_transaction() as connection:
        connection.execute("SET LOCAL enable_seqscan=off")
        plans = [str(row[0]) for row in connection.execute(
            'EXPLAIN (COSTS OFF) SELECT agent_id FROM executions WHERE provider_session_ref=%s ORDER BY started_at, execution_id LIMIT 1', (reference,),
        )]
    assert any("executions_session_turn_idx" in plan for plan in plans)


def test_read_snapshot_is_request_local_and_returns_independent_values():
    """读快照限定请求作用域并隔离返回值修改

    验证嵌套读取复用同一快照，调用方修改返回值不污染缓存；异常退出后释放快照，新请求与作用域外读取重新获取数据。

    :module: lineage.service
    :aspects: functional, behavior
    """
    class Store:
        calls = 0

        @request_cached
        def get(self, identifier):
            self.calls += 1
            return {"ids": [identifier], "revision": self.calls}

    store = Store()
    with pytest.raises(ValueError), read_snapshot_scope():
        store.get("first")["ids"].append("mutated")
        with read_snapshot_scope():
            assert store.get("first") == {"ids": ["first"], "revision": 1}
        raise ValueError("exit")
    with read_snapshot_scope():
        assert store.get("first")["revision"] == 2
    assert store.get("first")["revision"] == 3


def test_notification_query_skips_graphs_and_empty_lineages(tmp_path, monkeypatch):
    """通知列表按 Actor 与权限筛选且跳过无通知的 Lineage

    验证通知查询只加载存在候选通知的 Lineage 权限，正确过滤 Actor 与管理权限，不加载协作图或逐个读取空 Lineage；显式空筛选返回空结果。

    :module: workbench.notifications
    """
    store = LineageDataStore()
    identifiers = [f"lineage_{number:012x}" for number in range(12)]
    for lineage_id in identifiers:
        store.put_lineage_definition(lineage_id, _lineage_definition(lineage_id=lineage_id), {})
    monkeypatch.setattr(lineage_human_notifications, "get_lineage_data_store", lambda: store)
    for lineage_id, actor in ((identifiers[0], "CASE-A"), (identifiers[1], "other"), (identifiers[2], "case-a")):
        lineage_human_notifications.create_notification(lineage_id, title="Notice", message="Message", recipients=[actor])
    def forbidden(*args, **kwargs):
        pytest.fail("notification listing loaded a graph or performed an empty point lookup")
    monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", forbidden)
    monkeypatch.setattr(store.lineage_human_notifications, "get", forbidden)
    hydrated = []
    def hydrate(items, **kwargs):
        hydrated.extend(item["lineage_id"] for item in items)
        return items
    monkeypatch.setattr(business_fact_views, "canonical_views", hydrate)
    result = lineage_reads.list_lineage_human_notifications("CASE-A")
    # Recipients match case-insensitively; a recipient needs no manage permission.
    assert [item["lineage_id"] for item in result["lineages"]] == [identifiers[0], identifiers[2]]
    assert hydrated == [identifiers[0], identifiers[2]]
    assert lineage_reads.list_lineage_human_notifications("CASE-A", []) == {"lineages": []}
    assert lineage_reads.list_lineage_human_notifications("CASE-A", [identifiers[1]]) == {"lineages": []}


def test_conversation_facts_follow_external_writes_equal_timestamps_and_forks():
    """对话统计反映外部写入、相同时间戳与 Fork

    :importance: P2
    """
    reader = AgentDataStore()
    writer = AgentDataStore()
    writer.display_chat_message("parent", _chat("first", "user", "body", author_id="CASE-A"))
    assert set(reader.chat_actor_activity("CASE-A")) == {"parent"}
    writer.fork_agent(source_agent_id="parent", target_agent_id="child", actor_ref="CASE-A", segment_ids=writer.sequence_segment_ids(writer.trace_sequence_id("parent")))
    assert set(reader.chat_actor_activity("CASE-A")) == {"parent", "child"}
    writer.clear_chat("child", actor_ref="CASE-A")
    assert set(reader.chat_actor_activity("CASE-A")) == {"parent"}
    writer.display_chat_message("child", _chat("second", "user", "again", author_id="CASE-A"))
    assert set(reader.chat_actor_activity("CASE-A")) == {"parent", "child"}
    # Writes within one timestamp still change what the reader finds.
    writer.clear_chat("child", actor_ref="CASE-A")
    assert set(reader.chat_actor_activity("CASE-A")) == {"parent"}
    assert reader.chat_conversation_summaries(["parent", "child"], "CASE-A")["child"]["user_message_count"] == 0
