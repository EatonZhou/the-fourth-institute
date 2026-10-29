from __future__ import annotations

from tests.cases.database_test_support import connect, guard_queries
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest

from management_portal.execution.agent_data_store import (
    AGENT_DATA_SCHEMA,
    AgentDataStore,
)


AT = "2026-08-23T00:00:00Z"


def test_ensure_agent_reads_existing_identity_before_opening_write_transaction(monkeypatch):
    """ensure_agent 对已存在的 Agent 只读不开写事务，缺失 Agent 或新增父链接时才写入"""
    store = AgentDataStore()
    parent_id = "resident_7a90d9ab21f1"
    child_id = "resident_7a90d9ab21f2"
    parent = store.ensure_agent(parent_id)
    child = store.ensure_agent(child_id, sequence_kind="subagent_trace")
    original_write = store.database.write
    writes = []

    def counted_write(operation):
        writes.append(True)
        return original_write(operation)

    monkeypatch.setattr(store.database, "write", counted_write)
    assert store.ensure_agent(parent_id) == parent
    assert store.ensure_agent(child_id, sequence_kind="subagent_trace") == child
    with pytest.raises(ValueError, match="trace purpose or parent"):
        store.ensure_agent(child_id)
    assert writes == []

    # A missing Agent and a newly discovered parent edge still use the
    # transaction's advisory lock and the existing atomic creation/update path.
    missing_id = "resident_7a90d9ab21f3"
    assert store.ensure_agent(missing_id)["agent_id"] == missing_id
    linked = store.ensure_agent(child_id, sequence_kind="subagent_trace", parent_agent_id=parent_id)
    assert linked["parent_agent_id"] == parent_id
    assert len(writes) == 2
    assert store.ensure_agent(child_id, sequence_kind="subagent_trace", parent_agent_id=parent_id) == linked
    assert len(writes) == 2


def test_final_reply_follows_continuation_and_preserves_terminal_notices():
    """最终回复取 final_answer 之后续写的最后一条，失败输入的显示回复保留失败通知"""
    store = AgentDataStore()
    agent_id = "resident_replayed_reply"
    for input_id in ("completed", "failed", "unphased"):
        kinds = ("assistant_message", "assistant_message") if input_id == "unphased" else (
            "commentary", "final_answer", "commentary",
        )
        for index, kind in enumerate(kinds):
            message_id = f"{input_id}-{index}"
            segment_id = store.put_segment(
                segment_type="model", content=message_id, created_by_agent_id=agent_id,
                metadata={"role": "agent", "kind": kind, "message": {
                    "message_id": message_id, "reply_to_message_id": input_id,
                    "role": "agent", "parts": [{"type": "text", "text": message_id}],
                }},
            )
            store.append_agent_segments(agent_id, [segment_id], actor_ref="test-actor")
    store.project_chat_message(agent_id, _chat(
        "failure-notice", "system", "execution failed", reply_to_message_id="failed",
    ))
    assert store.chat_final_replies_many({agent_id: ["completed", "failed", "unphased", "missing"]}) == {
        agent_id: {
            "completed": ("completed-2", "completed-2"),
            "failed": ("failed-2", "failure-notice"),
            "unphased": ("unphased-1", "unphased-1"),
        },
    }


def test_final_replies_look_up_each_input_in_its_own_trace(monkeypatch):
    """最终回复只在每条输入自己的 trace 中查找

    批量读取多个 Agent 的最终回复时，每条输入只在所属 Agent 的 trace 中查一次，不把每个 trace 与每条输入两两组合；结果与逐个读取一致。

    :module: platform.core
    :aspects: performance
    """
    store = AgentDataStore()
    agents = [f"resident_paired_reply_{index}" for index in range(3)]
    inputs = {agent_id: [f"{agent_id}-first", f"{agent_id}-second"] for agent_id in agents}
    for agent_id, input_ids in inputs.items():
        for input_id in input_ids:
            segment_id = store.put_segment(
                segment_type="model", content=input_id, created_by_agent_id=agent_id,
                metadata={"role": "agent", "kind": "assistant_message", "message": {
                    "message_id": f"{input_id}-answer", "reply_to_message_id": input_id,
                    "role": "agent", "parts": [{"type": "text", "text": input_id}],
                }},
            )
            store.append_agent_segments(agent_id, [segment_id], actor_ref="test-actor")
    original_read = store.database.read
    lookups = []

    class Recorder:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, *args, **kwargs):
            if "reply_candidate" in statement and "LIMIT 1" in statement:
                lookups.append(args[0])
            return self.connection.execute(statement, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    @contextmanager
    def recorded_read():
        with original_read() as connection:
            yield Recorder(connection)

    monkeypatch.setattr(store.database, "read", recorded_read)
    assert store.chat_final_replies_many(inputs) == {
        agent_id: {input_id: (f"{input_id}-answer", f"{input_id}-answer") for input_id in input_ids}
        for agent_id, input_ids in inputs.items()
    }
    # One lookup per input, not one per input and requested trace.
    assert len(lookups) == 1
    traces, input_ids = lookups[0]
    assert len(traces) == len(input_ids) == 6
    assert sorted(input_ids) == sorted(item for values in inputs.values() for item in values)


def test_agent_data_health_reports_its_schema_without_reading_table_sizes(monkeypatch):
    """AgentData 健康信息只报告 schema 与数据库

    健康信息只含 enabled、schema 版本和数据库名，不报告任何表的行数；读取时只查询 agent_store_meta 一条语句。

    :module: execution.persistence
    """
    store = AgentDataStore()
    original_read = store.database.read
    statements = []

    @contextmanager
    def traced_read():
        with original_read() as connection:
            yield guard_queries(connection, statements=statements)

    monkeypatch.setattr(store.database, "read", traced_read)
    assert store.health() == {"enabled": True, "schema": AGENT_DATA_SCHEMA, "database": store.database.display_name}
    assert len(statements) == 1 and "agent_store_meta" in statements[0]


def test_session_compaction_lookup_seeks_the_execution_index(monkeypatch):
    """按会话查找压缩记录时使用 segments_execution_idx 索引，不扫描全部片段"""
    store = AgentDataStore()
    original_read = store.database.read
    statements = []

    @contextmanager
    def traced_read():
        with original_read() as connection:
            yield guard_queries(connection, statements=statements)

    monkeypatch.setattr(store.database, "read", traced_read)
    assert store.session_compaction("provider_session_missing", "compaction-missing") == ""
    lookup = next(statement for statement in statements if "provider_compaction_ref" in statement)
    with store.database.read_transaction() as connection:
        connection.execute("SET LOCAL enable_seqscan=off")
        plan = " ".join(str(row[0]) for row in connection.execute(
            "EXPLAIN (COSTS OFF) " + lookup, ("provider_session_missing", "compaction-missing"),
        ))
    # Without it the lookup scanned every Segment: 5-13 s on the live store.
    assert "segments_execution_idx" in plan


def test_provider_binding_routes_never_reads_execution_history(tmp_path, monkeypatch):
    """按 binding 读取 Provider 会话路由不查询执行、片段或 payload 表，空请求不访问数据库"""
    store = AgentDataStore()
    expected = {}
    for number, route in enumerate(("one", "four")):
        binding_id = f"binding-{number}"
        session = store.ensure_provider_session(
            provider="codex", backend_route_id=route,
            backend_session_id=f"session-{number}", agent_id="resident_test", actor_ref="test",
        )
        store.bind_provider_session(binding_id, session["provider_session_ref"])
        expected[binding_id] = {
            "binding_id": binding_id,
            **{key: session[key] for key in (
                "provider_session_ref", "provider", "backend_route_id", "backend_session_id",
            )},
        }
    original_read = store.database.read
    statements = []

    @contextmanager
    def bounded_read():
        with original_read() as connection:
            yield guard_queries(
                connection,
                forbidden=(
                    " from executions",
                    " from segments",
                    " from payloads",
                    "harness_tool_names_json",
                ),
                statements=statements,
            )

    monkeypatch.setattr(store.database, "read", bounded_read)
    requested = [" binding-0 ", "binding-1", "binding-0", "", None]
    requested.extend(f"missing-{number}" for number in range(1000))
    assert store.provider_binding_routes(iter(requested)) == expected
    assert len(statements) >= 2
    statements.clear()
    assert store.provider_binding_routes([]) == {}
    assert statements == []


def test_equal_content_in_distinct_occurrences_is_not_deduplicated(isolated_portal_case):
    """内容相同但出现位置不同的输入片段在会话上下文中各自保留"""
    with isolated_portal_case():
        store = AgentDataStore()
        agent_id = "resident_recovery"
        session = store.ensure_provider_session(
            provider="codex", backend_route_id="zero", backend_session_id="thread-recovery",
            agent_id=agent_id, actor_ref="test-actor",
        )
        session_ref = session["provider_session_ref"]
        for execution_id in ("earlier-execution", "current-execution"):
            store.begin_execution(
                execution_id=execution_id, agent_id=agent_id, actor_ref="test-actor",
                provider="codex", started_at=AT,
            )
            store.bind_execution_provider_session(execution_id, provider_session_ref=session_ref)
        for segment_id, execution_id, content in (
            ("earlier-input", "earlier-execution", "same input"),
            ("old-input", "current-execution", "same input"),
            ("changed-input", "current-execution", "different input"),
            ("new-input", "current-execution", "same input"),
        ):
            store.put_segment(
                segment_id=segment_id, segment_type="harness", content=content,
                execution_id=execution_id, created_by_agent_id=agent_id,
                metadata={"role": "user", "kind": "effective_provider_input", "features": [segment_id]},
            )
        store.put_segment(
            segment_id="output", segment_type="model", content="working",
            execution_id="current-execution", created_by_agent_id=agent_id,
        )
        old_segment = store.segment("old-input")
        store.append_agent_segments(agent_id, ['earlier-input', 'old-input', 'output', 'changed-input', 'new-input'], actor_ref='test-actor')
        # A recovered attempt prepared the same input again: the session holds
        # it once, where it first received it; another execution's identical
        # input and a changed input are inputs of their own.
        assert store.session_context_ids(session_ref) == [
            "earlier-input", "old-input", "output", "changed-input", "new-input",
        ]
        assert store.segment("old-input") == old_segment


def _chat(
    message_id: str,
    role: str,
    text: str,
    *,
    author_id: str = "",
    created_at: str = AT,
    reply_to_message_id: str = "",
) -> dict[str, object]:
    message: dict[str, object] = {
        "message_id": message_id,
        "role": role,
        "content_parts": [{"type": "text", "text": text}],
        "created_at": created_at,
    }
    if author_id:
        message["author_id"] = author_id
    if reply_to_message_id:
        message["reply_to_message_id"] = reply_to_message_id
    return message


def _segment(
    store: AgentDataStore,
    segment_id: str,
    content: object,
    *,
    segment_type: str = "input",
    agent_id: str = "resident_source",
) -> str:
    return store.put_segment(
        segment_id=segment_id,
        segment_type=segment_type,
        content=content,
        content_type=(
            "application/json"
            if isinstance(content, dict)
            else "text/plain; charset=utf-8"
        ),
        started_at=AT,
        finished_at=AT,
        created_by_actor_ref="sample_admin",
        performed_by_actor_ref="sample_admin",
        created_by_agent_id=agent_id,
    )


def test_segment_identity_is_immutable_and_payloads_are_content_addressed_contracts(tmp_path, subtests, isolated_portal_case, monkeypatch):
    """Segment 不可变且内容去重存储，Chat 按 trace 序号分页，Fork 复制所选轮次前的消息"""
    with subtests.test(contract='test_segment_identity_is_immutable_and_payloads_are_content_addressed'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()

        _segment(store, "segment_one", "same payload")
        _segment(store, "segment_two", "same payload")
        _segment(store, "segment_one", "same payload")

        with pytest.raises(ValueError, match="immutable content"):
            _segment(store, "segment_one", "different payload")

        with connect(store.database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 2
            assert connection.execute("SELECT COUNT(*) FROM payloads").fetchone()[0] == 1
    with subtests.test(contract='test_chat_message_pages_use_trace_ordinals_across_work_between_messages'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        agent_id = "resident_paged_chat"
        for index in range(1, 7):
            if index == 3:
                # Work between messages holds trace positions the Chat skips.
                _segment(store, "segment_tool_work", {"tool": "shell"}, segment_type="tool", agent_id=agent_id)
                store.append_agent_segments(agent_id, ['segment_tool_work'], actor_ref='sample_admin')
                continue
            store.display_chat_message(
                agent_id,
                _chat(f"message_{index}", "user", f"question {index}"),
            )

        newest = store.read_chat_message_page(agent_id, limit=2)
        assert [item["message_id"] for item in newest["messages"]] == [
            "message_5",
            "message_6",
        ]
        assert newest["next_before_ordinal"] == 5

        middle = store.read_chat_message_page(
            agent_id,
            before_ordinal=newest["next_before_ordinal"],
            limit=2,
        )
        assert [item["message_id"] for item in middle["messages"]] == [
            "message_2",
            "message_4",
        ]
        assert middle["next_before_ordinal"] == 2

        oldest = store.read_chat_message_page(
            agent_id,
            before_ordinal=middle["next_before_ordinal"],
            limit=2,
        )
        assert [item["message_id"] for item in oldest["messages"]] == ["message_1"]
        assert oldest["next_before_ordinal"] is None
        assert (newest["ordinals"], middle["ordinals"], oldest["ordinals"]) == ([5, 6], [2, 4], [1])

        later = store.read_chat_message_page(agent_id, after_ordinal=4, limit=10)
        assert [item["message_id"] for item in later["messages"]] == ["message_5", "message_6"]
        assert later["next_before_ordinal"] is None
        window = store.read_chat_message_page(agent_id, before_ordinal=6, after_ordinal=1, limit=10)
        assert window["ordinals"] == [2, 4, 5]
        assert store.chat_message_ordinal(agent_id, "message_4") == 4
        assert store.chat_message_ordinal(agent_id, "message_3") is None
    with subtests.test(contract='test_chat_messages_through_turn_anchor_the_window_to_an_old_message'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        agent_id = "resident_old_turn"
        store.display_chat_message(
            agent_id,
            _chat("selected_user", "user", "selected question"),
        )
        store.display_chat_message(
            agent_id,
            _chat(
                "selected_reply",
                "agent",
                "selected answer",
                reply_to_message_id="selected_user",
            ),
        )
        for index in range(130):
            store.display_chat_message(
                agent_id,
                _chat(f"newer_user_{index}", "user", f"newer {index}"),
            )

        assert "selected_user" not in {
            item["message_id"] for item in store.read_chat_messages(agent_id, limit=120)
        }
        assert [
            item["message_id"]
            for item in store.read_chat_messages_through_turn(
                agent_id,
                ["selected_user"],
                limit=120,
            )
        ] == ["selected_user", "selected_reply"]
        assert store.read_chat_messages_through_turn(
            agent_id,
            ["missing_message"],
            limit=120,
        ) == []
    with subtests.test(contract='test_agent_fork_copies_chat_through_selected_turn'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        source_id = "resident_source_chat"
        messages = [
            _chat("user_one", "user", "one", created_at="2026-08-23T00:00:00Z"),
            _chat("answer_one", "agent", "one answer", created_at="2026-08-23T00:00:01Z", reply_to_message_id="user_one"),
            _chat("user_two", "user", "two", created_at="2026-08-23T00:00:02Z"),
            _chat("answer_two", "agent", "two answer", created_at="2026-08-23T00:00:03Z", reply_to_message_id="user_two"),
            _chat("user_three", "user", "three", created_at="2026-08-23T00:00:04Z"),
        ]
        for message in messages:
            store.display_chat_message(source_id, message)

        result = store.fork_agent(
            source_agent_id=source_id,
            target_agent_id="resident_child_chat",
            actor_ref="sample_admin",
                     segment_ids=store.sequence_segment_ids(store.trace_sequence_id(source_id))[:4],
        )

        assert result["segment_count"] == 4
        assert [
            item["message_id"] for item in store.read_chat_messages("resident_child_chat")
        ] == ["user_one", "answer_one", "user_two", "answer_two"]
    with subtests.test(contract='test_actor_activity_includes_copied_history_and_accepted_messages'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        source_id = "resident_source_chat"
        child_id = "resident_child_chat"
        store.display_chat_message(
            source_id,
            _chat(
                "message_source",
                "user",
                "source chat",
                author_id="CASE-A",
                created_at="2026-08-23T00:00:00Z",
            ),
        )
        store.fork_agent(
            source_agent_id=source_id,
            target_agent_id=child_id,
            actor_ref="CASE-A",
            segment_ids=store.sequence_segment_ids(store.trace_sequence_id(source_id)),
        )

        assert store.chat_actor_activity("CASE-A") == {
            source_id: "2026-08-23T00:00:00Z",
            child_id: "2026-08-23T00:00:00Z",
        }
        headers = store.chat_message_headers([source_id, child_id])
        messages_by_agent = {
            item["agent_id"]: item["message_id"]
            for item in headers
            if item["role"] == "user"
        }
        assert messages_by_agent == {source_id: "message_source", child_id: "message_source"}
        assert all("is_native" not in item for item in headers)
        # A named message is found in every Chat that holds it.
        assert store.chat_message_headers(
            [source_id, child_id], message_ids=["message_source"],
        ) == [item for item in headers if item["message_id"] == "message_source"]

        store.display_chat_message(
            child_id,
            _chat(
                "message_child",
                "user",
                "new child chat",
                author_id="CASE-A",
                created_at="2026-08-23T01:00:00Z",
            ),
        )

        assert store.chat_actor_activity("CASE-A") == {
            source_id: "2026-08-23T00:00:00Z",
            child_id: "2026-08-23T01:00:00Z",
        }


def test_agent_sequences_replace_locally_and_fork_current_segment_references(
    tmp_path: Path,
) -> None:
    """替换片段只改源 Agent 自己的序列，先前的 Fork 保留原片段，之后的 Fork 引用新片段"""
    store = AgentDataStore()
    for segment_id in ("segment_1", "segment_2", "segment_3"):
        _segment(store, segment_id, segment_id)
    store.append_agent_segments('resident_source', ['segment_1', 'segment_2', 'segment_3'], actor_ref='sample_admin')
    store.fork_agent(
        source_agent_id="resident_source",
        target_agent_id="resident_branch",
        actor_ref="sample_admin",
        segment_ids=store.sequence_segment_ids(store.trace_sequence_id("resident_source")),
    )

    store.transform_agent_segments(
        'resident_source', ['segment_2'], segment_id='segment_2_prime', content='replacement',
        actor_ref='agent_harness', operation_id='harness_operation', operation_type='agent_harness.transform',
    )
    fork = store.fork_agent(
        source_agent_id="resident_source",
        target_agent_id="resident_cutoff",
        actor_ref="sample_admin",
               segment_ids=store.sequence_segment_ids(store.trace_sequence_id("resident_source"))[:2],
    )

    assert store.sequence_segment_ids(store.trace_sequence_id('resident_source')) == [
        "segment_1",
        "segment_2_prime",
        "segment_3",
    ]
    assert store.sequence_segment_ids(store.trace_sequence_id('resident_branch')) == [
        "segment_1",
        "segment_2",
        "segment_3",
    ]
    assert store.sequence_segment_ids(store.trace_sequence_id('resident_cutoff')) == [
        "segment_1",
        "segment_2_prime",
    ]
    branch = store.read_sequence(store.trace_sequence_id('resident_branch'))
    assert branch["total"] == 3
    assert "T" in branch["updated_at"] and branch["updated_at"].endswith("Z")
    assert fork["segment_count"] == 2
    with store.database.read() as connection:
        relation = connection.execute(
            "SELECT * FROM segment_relations"
        ).fetchone()
        assert relation["source_segment_id"] == "segment_2"
        assert relation["target_segment_id"] == "segment_2_prime"


def test_a_merging_transform_leaves_a_gap_that_appends_and_cutoffs_respect(
    tmp_path: Path,
) -> None:
    """合并变换留下的序号空位不影响追加与截断

    两个 Segment 合并成一个时，新 Segment 占第一个的位置，第二个的序号空出；之后的追加接在最大序号后，按位置截断的 Fork 按实际顺序计数。
    """
    store = AgentDataStore()
    for segment_id in ("segment_1", "segment_2", "segment_3", "segment_4"):
        _segment(store, segment_id, segment_id)
    store.append_agent_segments('resident_source', ['segment_1', 'segment_2', 'segment_3'], actor_ref='sample_admin')
    # Two occurrences become one at the first one's position; the second's ordinal stays free.
    store.transform_agent_segments(
        'resident_source', ['segment_1', 'segment_2'], segment_id='segment_12', content='merged',
        actor_ref='agent_harness', operation_id='harness_merge', operation_type='agent_harness.transform',
    )
    store.append_agent_segments('resident_source', ['segment_4'], actor_ref='sample_admin')

    with store.database.read() as connection:
        ordinals = [
            int(row[0])
            for row in connection.execute(
                "SELECT member.ordinal FROM sequence_segments AS member "
                "JOIN agents AS agent "
                "ON agent.trace_sequence_id = member.sequence_id "
                "WHERE agent.sequence_kind = 'agent_trace' "
                "AND agent.agent_id = 'resident_source' "
                "ORDER BY member.ordinal"
            ).fetchall()
        ]
    assert ordinals == [1, 3, 4]
    assert store.sequence_segment_ids(store.trace_sequence_id('resident_source')) == [
        "segment_12",
        "segment_3",
        "segment_4",
    ]
    fork = store.fork_agent(
        source_agent_id="resident_source",
        target_agent_id="resident_child",
        actor_ref="sample_admin",
               segment_ids=store.sequence_segment_ids(store.trace_sequence_id("resident_source"))[:2],
    )
    assert fork["segment_count"] == 2
    assert store.sequence_segment_ids(store.trace_sequence_id('resident_child')) == [
        "segment_12",
        "segment_3",
    ]


def test_agent_fork_replays_into_precreated_empty_target_with_chat(
    tmp_path: Path,
) -> None:
    """向预先创建的空目标重复 Fork 结果不变，也不覆盖子代之后的新消息"""
    store = AgentDataStore()
    source_id = "resident_replay_source"
    target_id = "resident_replay_target"
    for message in [
        _chat("source_user", "user", "question", created_at="2026-09-23T00:00:00Z"),
        _chat("source_agent", "agent", "answer", created_at="2026-09-23T00:00:01Z"),
    ]:
        store.display_chat_message(source_id, message)
    store.ensure_agent(target_id)

    first = store.fork_agent(
        source_agent_id=source_id,
        target_agent_id=target_id,
        actor_ref="sample_admin",
                segment_ids=store.sequence_segment_ids(store.trace_sequence_id(source_id)),
    )
    replay = store.fork_agent(
        source_agent_id=source_id,
        target_agent_id=target_id,
        actor_ref="sample_admin",
                 segment_ids=store.sequence_segment_ids(store.trace_sequence_id(source_id)),
    )
    store.display_chat_message(
        target_id,
        _chat("child_user", "user", "continue", created_at="2026-09-23T00:00:02Z"),
    )
    replay_after_child_turn = store.fork_agent(
        source_agent_id=source_id,
        target_agent_id=target_id,
        actor_ref="sample_admin",
                                  segment_ids=store.sequence_segment_ids(store.trace_sequence_id(source_id)),
    )

    assert replay == first
    assert replay_after_child_turn == first
    assert [
        item["message_id"] for item in store.read_chat_messages(target_id)
    ] == ["source_user", "source_agent", "child_user"]


def test_fork_trace_runs_through_the_last_message_it_inherits(tmp_path: Path) -> None:
    """Fork 的 trace 截到它继承的最后一条消息为止

    截断点落在 Provider 这一轮的最后一个 Segment 时，Fork 的 trace 仍然带上这一轮的回复（它 Chat 里的第一条消息就在自己的 trace 里）；指定不存在的截断消息时报错。

    A Fork's Chat is its trace: a cutoff at the Provider's last Segment still takes the turn's reply.

    :module: execution.persistence
    :importance: P1
    """

    store = AgentDataStore()
    source_id = "resident_fork_reply_source"
    store.display_chat_message(source_id, _chat("user_one", "user", "one", author_id="sample_admin"))
    _segment(store, "segment_model_work", {"text": "thinking"}, segment_type="model", agent_id=source_id)
    store.append_agent_segments(source_id, ['segment_model_work'], actor_ref='sample_admin')
    store.display_chat_message(
        source_id, _chat("answer_one", "agent", "one answer", reply_to_message_id="user_one"),
    )
    store.display_chat_message(source_id, _chat("user_two", "user", "two", author_id="sample_admin"))

    result = store.fork_agent(
        source_agent_id=source_id,
        target_agent_id="resident_fork_reply_child",
        actor_ref="sample_admin",
                 segment_ids=store.sequence_segment_ids(store.trace_sequence_id(source_id))[:3],
    )

    assert result["segment_count"] == 3
    assert [
        item["message_id"] for item in store.read_chat_messages("resident_fork_reply_child")
    ] == ["user_one", "answer_one"]
    with pytest.raises(ValueError, match="unknown Segments"):
        store.fork_agent(
            source_agent_id=source_id,
            target_agent_id="resident_fork_reply_other",
            actor_ref="sample_admin",
            segment_ids=["missing_segment"],
        )


def test_agent_fork_materializes_empty_source_and_resolves_segment_cutoff(
    tmp_path: Path,
) -> None:
    """Fork 空的源 Agent 得到空 trace，执行结束后源 trace 仍按输入、输出排列"""
    store = AgentDataStore()
    store.ensure_agent("resident_empty_source")
    empty = store.fork_agent(
        source_agent_id="resident_empty_source",
        target_agent_id="resident_empty_child",
        actor_ref="sample_admin",
                segment_ids=store.sequence_segment_ids(store.trace_sequence_id("resident_empty_source")),
    )
    assert empty["segment_count"] == 0
    assert store.sequence_segment_ids(store.trace_sequence_id('resident_empty_child')) == []

    store.begin_execution(
        execution_id="execution_turn_one",
        agent_id="resident_event_source",
        actor_ref="sample_admin",
        provider="codex",
        started_at="2026-08-23T00:00:00Z",
    )
    store.put_segment(
        segment_id="segment_turn_input",
        segment_type="input",
        content="question",
        started_at="2026-08-23T00:00:00Z",
        created_by_actor_ref="sample_admin",
        performed_by_actor_ref="sample_admin",
        created_by_agent_id="resident_event_source",
        execution_id="execution_turn_one",
    )
    store.put_segment(
        segment_id="segment_turn_output",
        segment_type="model",
        content="answer",
        started_at="2026-08-23T00:00:01Z",
        created_by_actor_ref="sample_admin",
        performed_by_actor_ref="codex",
        created_by_agent_id="resident_event_source",
        execution_id="execution_turn_one",
    )
    store.append_agent_segments('resident_event_source', ['segment_turn_input', 'segment_turn_output'], actor_ref='sample_admin')
    session = store.ensure_provider_session(
        provider="codex",
        backend_route_id="zero",
        backend_session_id="thread_turn_one",
        agent_id="resident_event_source",
        actor_ref="sample_admin",
    )
    store.bind_execution_provider_session(
        "execution_turn_one",
        provider_session_ref=session["provider_session_ref"],
    )
    store.finish_execution(
        "execution_turn_one",
        status="completed",
        finished_at="2026-08-23T00:00:01Z",
        provider_turn_id="turn_one",
    )
    assert store.sequence_segment_ids(store.trace_sequence_id('resident_event_source')) == [
        'segment_turn_input', 'segment_turn_output',
    ]


def test_chat_projection_order_updates_and_archive_are_current_state(
    tmp_path: Path,
) -> None:
    """Chat 按 trace 顺序显示，消息可原位修订，清空 Chat 后 trace 仍保留全部片段"""
    store = AgentDataStore()
    agent_id = "resident_chat"
    user = _chat("message_user", "user", "question", author_id="sample_admin")
    final = _chat(
        "message_final",
        "agent",
        "done",
        created_at="2026-08-23T00:00:03Z",
        reply_to_message_id="message_user",
    )
    commentary = _chat(
        "message_commentary",
        "agent",
        "working",
        created_at="2026-08-23T00:00:02Z",
        reply_to_message_id="message_user",
    )
    store.display_chat_message(agent_id, user)
    store.display_chat_message(agent_id, final)
    store.display_chat_message(agent_id, commentary)

    assert [
        item["message_id"] for item in store.read_chat_messages(agent_id)
    ] == ["message_user", "message_final", "message_commentary"]
    assert store.latest_chat_message(agent_id)["message_id"] == "message_commentary"
    assert store.latest_chat_message(agent_id, role="user")["message_id"] == "message_user"
    assert store.latest_chat_message(agent_id, role="portal") is None

    revised = dict(user, content_parts=[{"type": "text", "text": "revised question"}])
    updated = store.update_chat_message(agent_id, "message_user", revised)
    assert updated["content_parts"] == [{"type": "text", "text": "revised question"}]
    assert store.read_chat_messages(agent_id)[0]["content_parts"] == [
        {"type": "text", "text": "revised question"}
    ]

    assert store.displayed_chat_message_ids(agent_id) == [
        "message_user",
        "message_final",
        "message_commentary",
    ]
    store.clear_chat(agent_id, actor_ref="sample_admin")
    # A clear ends the Chat where it stands in the trace; the trace keeps all of it.
    assert store.read_chat_messages(agent_id) == []
    assert store.displayed_chat_message_ids(agent_id) == []
    assert store.chat_message(agent_id, "message_user") is None
    assert len(store.sequence_segment_ids(store.trace_sequence_id(agent_id))) == 3
    with store.database.read() as connection:
        relation = connection.execute(
            "SELECT relation_type FROM segment_relations "
            "WHERE operation_type = 'chat.message.update'"
        ).fetchone()
        assert relation["relation_type"] == "transformed_from"


@pytest.mark.parametrize("boundary", ["compaction", "clear", "restart"])
def test_fork_message_cutoff_stops_before_later_context_boundaries(boundary):
    """按消息截取的上下文不越过之后的压缩、清空或重置边界，当前上下文不含边界前片段"""
    store = AgentDataStore()
    agent_id = "resident_trace_cutoff"
    store.display_chat_message(agent_id, _chat("input-one", "user", "question"))
    store.display_chat_message(agent_id, _chat("reply-one", "agent", "answer"))
    expected = store.sequence_segment_ids(store.trace_sequence_id(agent_id))
    if boundary == "compaction":
        compact = store.put_segment(segment_type="compaction", content={"replacement_history": ["later"]})
        store.append_agent_segments(agent_id, [compact], actor_ref="sample_admin")
    elif boundary == "clear":
        store.clear_chat(agent_id)
    else:
        store.reset_provider_context(agent_id, reset_at="2026-10-14T00:00:00Z", actor_ref="sample_admin")
    store.display_chat_message(agent_id, _chat("input-two", "user", "future question"))
    selected = store.agent_context_snapshot(agent_id, through_message_id="input-one")
    assert [s["segment_id"] for s in selected["segments"]] == expected
    assert not set(expected) & {s["segment_id"] for s in store.agent_context_snapshot(agent_id)["segments"]}


def test_fork_snapshot_and_materialization_share_the_effective_conversation_boundary(
    tmp_path: Path,
) -> None:
    """源 Chat 清空后上下文快照为空，据此 Fork 的子代也不含被清空的消息"""
    store = AgentDataStore()
    source_agent_id = "resident_fork_source"
    target_agent_id = "resident_fork_target"
    hidden_message = _chat(
        "message_hidden",
        "user",
        "hide this",
        author_id="sample_admin",
    )
    store.display_chat_message(source_agent_id, hidden_message)
    store.clear_chat(source_agent_id, actor_ref="sample_admin")
    session = store.ensure_provider_session(
        provider="codex",
        backend_route_id="zero",
        backend_session_id="thread-fork-source",
        agent_id=source_agent_id,
        actor_ref="sample_admin",
    )

    snapshot = store.provider_context_snapshot(
        provider_session_ref=session["provider_session_ref"],
        agent_id=source_agent_id,
    )

    assert snapshot is not None
    assert snapshot["empty_context"] and not snapshot["segments"]
    store.fork_agent(
        source_agent_id=source_agent_id,
        target_agent_id=target_agent_id,
        actor_ref="sample_admin",
        segment_ids=[item["segment_id"] for item in snapshot["segments"]],
    )
    assert [
        message["message_id"]
        for message in store.read_chat_messages(target_agent_id)
    ] == []


def test_distinct_chat_message_ids_never_share_a_segment_contracts(tmp_path, subtests, isolated_portal_case, monkeypatch):
    """不同 Chat 消息不共用片段，消息身份跨 Fork 保持，已结束的执行不能重启，并发追加串行"""
    with subtests.test(contract='test_distinct_chat_message_ids_never_share_a_segment'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        first = _chat("message_first", "agent", "same answer")
        second = {**first, "message_id": "message_second"}

        store.display_chat_message("resident_chat", first)
        store.display_chat_message("resident_chat", second)

        messages = store.read_chat_messages("resident_chat")
        assert [item["message_id"] for item in messages] == [
            "message_first",
            "message_second",
        ]
        assert messages[0]["segment_id"] != messages[1]["segment_id"]
    with subtests.test(contract='test_chat_message_headers_batch_only_projects_summary_candidates'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        store.display_chat_message(
            "resident_one",
            _chat("message_user", "user", "question", author_id="sample_admin"),
        )
        store.display_chat_message(
            "resident_one",
            _chat(
                "message_commentary",
                "agent",
                "working",
                created_at="2026-08-23T00:00:02Z",
            ),
        )
        store.display_chat_message(
            "resident_one",
            _chat(
                "message_final",
                "agent",
                "done",
                created_at="2026-08-23T00:00:03Z",
                reply_to_message_id="message_user",
            ),
        )

        headers = store.chat_message_headers(["resident_one", "resident_empty"])

        assert [item["message_id"] for item in headers] == [
            "message_user",
            "message_final",
        ]
        assert headers[-1]["has_content"] is True
        assert headers[-1]["reply_to_message_id"] == "message_user"
        assert store.chat_message_headers(
            ["resident_one"], message_ids=["message_final", "missing"],
        ) == [headers[-1]]
        assert store.chat_message_headers(["resident_one"], message_ids=[]) == []
        assert store.chat_message_headers(
            ["resident_empty"], message_ids=["message_final"],
        ) == []
    with subtests.test(contract='test_chat_identity_survives_fork'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        message = _chat(
            "message_stable",
            "user",
            "run this",
            author_id="sample_admin",
        )
        projected = store.display_chat_message("resident_source", message)
        segment_id = str(projected["segment_id"])
        assert store.display_chat_message("resident_source", message)["segment_id"] == segment_id

        store.fork_agent(
            source_agent_id="resident_source",
            target_agent_id="resident_child",
            actor_ref="sample_admin",
            segment_ids=store.sequence_segment_ids(store.trace_sequence_id("resident_source")),
        )

        assert store.chat_message("resident_child", "message_stable")["segment_id"] == segment_id
        assert store.chat_message("resident_source", "message_stable")["segment_id"] == segment_id
        assert store.sequence_segment_ids(store.trace_sequence_id('resident_source')) == [segment_id]
        assert store.sequence_segment_ids(store.trace_sequence_id('resident_child')) == [segment_id]
    with subtests.test(contract='test_terminal_execution_cannot_restart'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        store.begin_execution(
            execution_id="execution-recovery",
            agent_id="resident_test",
            actor_ref="sample_peer",
            provider="codex",
            started_at="2026-08-26T00:00:00Z",
        )
        store.finish_execution(
            "execution-recovery",
            status="cancelled",
            finished_at="2026-08-26T00:01:00Z",
            provider_turn_id="turn-existing",
        )

        store.begin_execution(execution_id="execution-recovery", agent_id="resident_test",
                              actor_ref="sample_peer", provider="codex", started_at="2026-08-26T00:00:00Z")
        execution = store.execution("execution-recovery")
        assert execution["status"] == "cancelled"
        assert execution["finished_at"] == "2026-08-26T00:01:00Z"
        assert execution["provider_turn_id"] == "turn-existing"
    with subtests.test(contract='test_multiple_store_instances_serialize_concurrent_sequence_appends'), isolated_portal_case() as case_path:
        tmp_path = case_path
        AgentDataStore()
        barrier = threading.Barrier(5)
        errors: list[BaseException] = []

        def writer(worker: int) -> None:
            try:
                store = AgentDataStore()
                barrier.wait(timeout=3)
                for index in range(25):
                    segment_id = f"segment_{worker}_{index}"
                    _segment(store, segment_id, segment_id)
                    store.append_agent_segments('resident_concurrent', [segment_id], actor_ref=f'worker_{worker}')
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=3)
        for thread in threads:
            thread.join(timeout=10)

        assert not errors
        assert not any(thread.is_alive() for thread in threads)
        store = AgentDataStore()
        assert len(
            store.sequence_segment_ids(store.trace_sequence_id('resident_concurrent'))
        ) == 100
    with subtests.test(contract='test_agent_database_requires_the_exact_schema_contract'), isolated_portal_case() as case_path:
        tmp_path = case_path
        with monkeypatch.context() as isolated:
            isolated.setenv(
                "PORTAL_DATABASE_NAMESPACE",
                f"{os.environ['PORTAL_DATABASE_NAMESPACE'][:32]}_ac",
            )
            store = AgentDataStore()
            with connect(store.database) as connection:
                connection.execute("DROP INDEX executions_session_turn_idx")
            store.database.close()

            with pytest.raises(RuntimeError, match="does not match its schema"):
                AgentDataStore()


def test_postgres_sequence_append_locks_parent_before_allocating_ordinal():
    """PostgreSQL 追加序列成员时先锁定父序列再分配序号"""
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return self.rows

    class Connection:
        backend = "postgresql"

        def __init__(self):
            self.statements = []
            self.inserted = []

        def execute(self, statement, parameters=()):
            normalized = " ".join(statement.split())
            self.statements.append(normalized)
            if normalized.startswith("SELECT segment_id FROM segments"):
                return Result([{"segment_id": "segment_new"}])
            if normalized.endswith("FOR UPDATE"):
                return Result([{"sequence_id": "sequence_test"}])
            if normalized.startswith("SELECT segment_id FROM sequence_segments"):
                return Result([])
            if "MAX(ordinal)" in normalized:
                return Result([{"ordinal": 8}])
            return Result([])

        def executemany(self, statement, parameters):
            self.statements.append(" ".join(statement.split()))
            self.inserted.extend(parameters)

    connection = Connection()
    additions = AgentDataStore._append_sequence_ids(
        connection,
        sequence={"sequence_id": "sequence_test"},
        segment_ids=["segment_new"],
        actor_ref="test",
    )

    lock_index = next(
        index
        for index, statement in enumerate(connection.statements)
        if statement.endswith("FOR UPDATE")
    )
    allocation_index = next(
        index
        for index, statement in enumerate(connection.statements)
        if "MAX(ordinal)" in statement
    )
    assert lock_index < allocation_index
    assert additions == ["segment_new"]
    assert connection.inserted == [("sequence_test", 8, "segment_new")]


def test_conversation_summary_reuses_only_current_committed_trace_facts(monkeypatch):
    """对话摘要只复用当前已提交的消息事实

    按消息序列的已提交版本复用对话摘要；同时间替换、并发快照和事务回滚保持正确隔离。

    :design: decision_b1ab45c4b3ec
    :design-point:
    """
    from concurrent.futures import ThreadPoolExecutor
    from management_portal.execution import agent_data_store

    monkeypatch.setattr(agent_data_store, "utc_now", lambda: AT)
    store, writer = AgentDataStore(), AgentDataStore()
    original_snapshot = store.database.snapshot
    statements = []

    @contextmanager
    def observed_snapshot():
        with original_snapshot() as connection:
            yield guard_queries(connection, statements=statements)

    monkeypatch.setattr(store.database, "snapshot", observed_snapshot)
    first = _chat("message_first", "user", "first", author_id="sample_admin", created_at=AT)
    writer.display_chat_message("resident_one", first)
    writer.database.write(lambda connection: writer._locked_trace(connection, "resident_child", "sample_admin"))
    assert store.chat_conversation_summaries(["resident_child"], "sample_admin")["resident_child"]["user_message_count"] == 0
    writer.fork_agent(source_agent_id="resident_one", target_agent_id="resident_child",
                      segment_ids=writer.sequence_segment_ids(writer.trace_sequence_id("resident_one")), actor_ref="sample_admin")
    assert store.chat_conversation_summaries(["resident_child"], "sample_admin")["resident_child"]["user_message_count"] == 1

    def read(actor="sample_admin", excluded=""):
        return store.chat_conversation_summaries(
            ["resident_one"], actor, excluded_business_ref=excluded,
        )["resident_one"]

    assert read()["actor_request_ids"] == ["message_first"]
    statements.clear()
    value = read()
    value["actor_request_ids"].clear()
    assert read()["actor_request_ids"] == ["message_first"]
    assert not any("count(*) as count, max(segment.created_at)" in sql for sql in statements)

    # Another store replaces a message without changing its timestamp or the
    # trace length. The former sender's cached engagement must disappear.
    writer.update_chat_message("resident_one", "message_first", {**first, "author_id": "CASE-A"})
    assert read()["actor_request_ids"] == []
    assert read("CASE-A")["actor_request_ids"] == ["message_first"]
    second = _chat("message_second", "user", "second", author_id="sample_admin", created_at=AT)
    writer.display_chat_message("resident_one", second)
    assert read()["user_message_count"] == 2
    writer.update_chat_message("resident_one", "message_second", {**second, "business_ref": "family-relay:test"})
    assert read(excluded=r"^family-relay:")["user_message_count"] == 1
    assert read()["user_message_count"] == 2

    # A concurrent reader can populate a newer version while an older
    # repeatable-read snapshot is still open. Neither may reuse the other's.
    with original_snapshot():
        assert read()["user_message_count"] == 2
        with ThreadPoolExecutor(max_workers=1) as pool:
            third = _chat("message_third", "user", "third", author_id="sample_admin", created_at=AT)
            pool.submit(writer.display_chat_message, "resident_one", third).result(timeout=5)
            assert pool.submit(read).result(timeout=5)["user_message_count"] == 3
        assert read()["user_message_count"] == 2
    assert read()["user_message_count"] == 3

    with pytest.raises(RuntimeError, match="rollback"):
        with store.database.transaction():
            store.clear_chat("resident_one", actor_ref="sample_admin")
            assert read()["user_message_count"] == 0
            raise RuntimeError("rollback")
    assert read()["user_message_count"] == 3
    writer.clear_chat("resident_one", actor_ref="sample_admin")
    assert read()["user_message_count"] == 0


def test_chat_conversation_summaries_project_directory_facts_without_bodies(
    tmp_path: Path,
) -> None:
    """对话摘要只给出消息数、最后发言者与时间和本人请求，不含正文，空 Agent 得到空摘要"""
    store = AgentDataStore()
    store.display_chat_message(
        "resident_one",
        _chat(
            "message_first",
            "user",
            "first",
            author_id="@sample_admin",
            created_at="2026-08-23T00:00:01Z",
        ),
    )
    store.display_chat_message(
        "resident_one",
        _chat(
            "message_second",
            "user",
            "second",
            author_id="CASE-A",
            created_at="2026-08-23T00:00:02Z",
        ),
    )
    store.display_chat_message(
        "resident_one",
        _chat(
            "message_reply",
            "agent",
            "done",
            reply_to_message_id="message_second",
            created_at="2026-08-23T00:00:03Z",
        ),
    )

    summaries = store.chat_conversation_summaries(
        ["resident_one", "resident_empty"],
        "sample_admin",
    )

    assert summaries["resident_one"]["user_message_count"] == 2
    assert summaries["resident_one"]["last_user_actor"] == "CASE-A"
    assert summaries["resident_one"]["last_user_message_at"] == (
        "2026-08-23T00:00:02Z"
    )
    assert summaries["resident_one"]["actor_last_user_message_at"] == (
        "2026-08-23T00:00:01Z"
    )
    assert summaries["resident_one"]["actor_request_ids"] == ["message_first"]
    assert summaries["resident_one"]["conversation_signature"]
    assert summaries["resident_empty"] == {
        "user_message_count": 0,
        "last_user_actor": "",
        "last_user_message_at": "",
        "actor_last_user_message_at": "",
        "actor_request_ids": [],
        "actor_addressed_message_id": "",
        "actor_addressed_at": "",
        "conversation_signature": "",
    }


def test_chat_provenance_reads_current_input_without_walking_history(tmp_path, monkeypatch):
    """读取消息出处直接得到当前输入片段、Provider 会话与轮次，不递归遍历变换历史"""
    store = AgentDataStore()
    agent_id = "resident_current_provenance"
    initial = store.display_chat_message(agent_id, _chat("message_selected", "user", "selected"))
    session = store.ensure_provider_session(provider="codex", backend_route_id="zero", backend_session_id="thread-provenance", agent_id=agent_id, actor_ref="sample_admin")
    store.begin_execution(execution_id="execution_selected", agent_id=agent_id, actor_ref="sample_admin", provider="codex", started_at="2026-10-08T01:00:00Z")
    store.bind_execution_provider_session("execution_selected", provider_session_ref=session["provider_session_ref"])
    store.record_input_acceptance("execution_selected", agent_id=agent_id, provider_turn_id="turn_selected")
    current = store.transform_agent_segments(agent_id, [initial["segment_id"]], segment_id="segment_current",
        content="prepared", execution_id="execution_selected", actor_ref="agent_harness",
        operation_id="execution_selected", operation_type="agent_harness.prepare_invocation")
    original_read = store.database.read
    @contextmanager
    def bounded_read():
        with original_read() as connection:
            yield guard_queries(connection, forbidden=(" from segment_relations", "recursive"))
    monkeypatch.setattr(store.database, "read", bounded_read)
    assert store.chat_message_provenance(agent_id, "message_selected") == {
        "segment_id": current, "provider_session_ref": session["provider_session_ref"], "provider_turn_id": "turn_selected"}


def _taken(store: AgentDataStore, agent_id: str, message_id: str, session_ref: str, started_at: str) -> None:
    """A Chat message one Execution of ``session_ref`` delivered to the Provider."""

    shown = store.display_chat_message(agent_id, _chat(message_id, "user", message_id))
    execution_id = "execution_" + message_id
    store.begin_execution(execution_id=execution_id, agent_id=agent_id, actor_ref="sample_admin", provider="codex",
                          started_at=started_at)
    store.bind_execution_provider_session(execution_id, provider_session_ref=session_ref)
    store.record_input_acceptance(execution_id, agent_id=agent_id, provider_turn_id="turn_" + message_id)
    store.transform_agent_segments(agent_id, [shown["segment_id"]], segment_id="segment_" + message_id,
        content=message_id, execution_id=execution_id, actor_ref="agent_harness",
        operation_id=execution_id, operation_type="agent_harness.prepare_invocation")


def test_a_message_stays_in_the_newest_context_until_a_compaction_a_clear_or_a_new_session():
    """消息在压缩、清空上下文或换新会话之前一直在 Resident 最新的上下文中

    Provider 接收的消息在最新会话的上下文中；被压缩、清空上下文或换新会话之后不在；没有被 Provider 接收、没有显示过的消息不在。

    The Family Router keeps its brief while the message that brought it is in context (CASE-A 10-12).

    :module: execution.persistence
    """

    from management_portal.execution.agent_data_projection import persist_provider_compaction

    store = AgentDataStore()
    agent_id = "resident_message_in_context"

    def session(thread: str) -> str:
        return str(store.ensure_provider_session(provider="codex", backend_route_id="zero", backend_session_id=thread,
                                                 agent_id=agent_id, actor_ref="sample_admin")["provider_session_ref"])

    first = session("thread-in-context")
    _taken(store, agent_id, "message_brief", first, "2026-10-12T01:00:00Z")
    assert store.message_in_context(agent_id, "message_brief")
    # Shown but never taken by a Provider, or never shown at all.
    store.display_chat_message(agent_id, _chat("message_waiting", "user", "waiting"))
    assert not store.message_in_context(agent_id, "message_waiting")
    assert not store.message_in_context(agent_id, "message_unknown")

    # A compaction summarises it; what comes after is in the compacted context.
    persist_provider_compaction(
        store, provider_session_ref=first, agent_id=agent_id, operation_id="execution_message_brief",
        actor_ref="sample_admin", provider="codex", created_at="2026-10-12T01:00:05Z",
        content={"replacement_history": [{"type": "message", "role": "user",
                                          "content": [{"type": "input_text", "text": "摘要"}]}]},
    )
    _taken(store, agent_id, "message_after", first, "2026-10-12T01:00:10Z")
    assert not store.message_in_context(agent_id, "message_brief")
    assert store.message_in_context(agent_id, "message_after")

    # A clear after it, or a newer session, leaves it out.
    assert not store.message_in_context(agent_id, "message_after", since="2026-10-12T01:00:20Z")
    _taken(store, agent_id, "message_new_session", session("thread-in-context-2"), "2026-10-12T01:00:30Z")
    assert not store.message_in_context(agent_id, "message_after")
    assert store.message_in_context(agent_id, "message_new_session")


def test_fresh_reset_excludes_old_input_before_next_provider_session():
    """Fresh must report old inputs out of context before its lazy first turn."""
    store = AgentDataStore()
    agent_id = "resident_fresh_context_visibility"
    first = store.ensure_provider_session(provider="codex", backend_route_id="zero",
        backend_session_id="thread-before-fresh", agent_id=agent_id, actor_ref="sample_admin")
    _taken(store, agent_id, "message_before_fresh", first["provider_session_ref"], "2026-10-12T01:00:00Z")
    provenance = store.chat_message_provenance(agent_id, "message_before_fresh")
    original = store.segment(provenance["segment_id"])
    assert store.message_in_context(agent_id, "message_before_fresh")

    store.reset_provider_context(agent_id, reset_at="2026-10-12T01:01:00Z", actor_ref="sample_admin")
    assert store.agent_context_snapshot(agent_id)["segments"] == []
    assert store.latest_provider_session_ref(agent_id) == first["provider_session_ref"]
    assert not store.message_in_context(agent_id, "message_before_fresh")
    assert store.segment(provenance["segment_id"]) == original

    second = store.ensure_provider_session(provider="codex", backend_route_id="zero",
        backend_session_id="thread-after-fresh", agent_id=agent_id, actor_ref="sample_admin")
    _taken(store, agent_id, "message_after_fresh", second["provider_session_ref"], "2026-10-12T01:02:00Z")
    assert store.message_in_context(agent_id, "message_after_fresh")
    assert not store.message_in_context(agent_id, "message_before_fresh")


def test_execution_preserves_durable_provider_outcome_without_copying_context(
    tmp_path: Path,
) -> None:
    """执行结束时记录 Provider 会话、轮次与用量，重复相同结束可接受，改写终态被拒绝"""
    store = AgentDataStore()
    agent_id = "resident_model"
    _segment(store, "segment_input", "prompt", agent_id=agent_id)
    _segment(
        store,
        "segment_output",
        "answer",
        segment_type="model",
        agent_id=agent_id,
    )
    session = store.ensure_provider_session(
        provider="codex",
        backend_route_id="zero",
        backend_session_id="thread_model",
        agent_id=agent_id,
        actor_ref="sample_admin",
    )
    session_ref = session["provider_session_ref"]
    execution = store.begin_execution(
        execution_id="execution-model",
        agent_id=agent_id,
        actor_ref="sample_admin",
        provider="codex",
        started_at=AT,
    )
    assert execution["status"] == "running"
    store.bind_execution_provider_session(
        "execution-model",
        provider_session_ref=session_ref,
    )
    store.finish_execution(
        "execution-model",
        status="completed",
        finished_at="2026-08-23T00:00:01Z",
        usage={"input_tokens": 3, "output_tokens": 1},
        provider_turn_id="turn-model",
    )

    with store.database.read() as connection:
        row = connection.execute(
            "SELECT provider_session_ref, provider_turn_id, status, usage_json "
            "FROM executions "
            "WHERE execution_id = 'execution-model'"
        ).fetchone()
    assert row["provider_session_ref"] == session_ref
    assert row["provider_turn_id"] == "turn-model"
    assert row["status"] == "completed"
    assert row["usage_json"]["input_tokens"] == 3
    assert store.execution("execution-model")["usage"]["output_tokens"] == 1
    store.finish_execution(
        "execution-model",
        status="completed",
        finished_at="2026-08-23T00:00:01Z",
        usage={"input_tokens": 3, "output_tokens": 1},
        provider_turn_id="turn-model",
    )
    with pytest.raises(ValueError, match="terminal fact"):
        store.finish_execution(
            "execution-model",
            status="failed",
            finished_at="2026-08-23T00:00:02Z",
        )


def test_concurrent_first_records_of_one_provider_session_both_succeed():
    """同一 Provider 会话的并发首次记录都成功

    Worker 的事件投影与执行运行时可能同时第一次记录同一个新会话。后写入的一方等先写入的提交，
    然后沿用已有记录，不因主键冲突丢掉这次写入。
    """
    store = AgentDataStore()
    identity = dict(provider="codex", backend_route_id="zero", backend_session_id="thread_race",
                    agent_id="resident_race", actor_ref="sample_admin")
    second: dict[str, object] = {}

    def record_second():
        try:
            second["session"] = store.ensure_provider_session(**identity)
        except Exception as error:  # noqa: BLE001 - asserted below
            second["error"] = error

    # The observer has its own connection: the store's pool is held by the two writers.
    with store.database.transaction(), psycopg.connect(store.database.dsn, autocommit=True) as observer:
        first = store.ensure_provider_session(**identity)
        racer = threading.Thread(target=record_second)
        racer.start()
        deadline = time.monotonic() + 10
        while not observer.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
            "AND position('INSERT INTO provider_sessions' in query) = 1"
        ).fetchone()[0]:
            assert time.monotonic() < deadline, "the second record did not wait for the first"
            time.sleep(0.02)
    racer.join(10)
    assert "error" not in second, repr(second.get("error"))
    assert second["session"]["provider_session_ref"] == first["provider_session_ref"]
