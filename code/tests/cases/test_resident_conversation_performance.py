"""Real PostgreSQL work budgets for long-lived Resident conversation queries.

:design: decision_1b2bdf5c33d6, decision_c524c775e0be
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import invariant_locks
from management_portal.society_service.resident_conversation_stream import (
    ResidentConversationRepository,
)
from management_portal.society_service.state_store import LineageDataStore
from tests.cases.database_test_support import guard_queries
from tests.cases.society_service_test_support import publish_registered_lineage_members


A = "resident_111111111111"
B = "resident_222222222222"
EMPTY = "resident_333333333333"
MISSING = "resident_444444444444"


@pytest.fixture
def history_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
    repository = ResidentConversationRepository(
        LineageDataStore(),
        AgentDataStore(),
    )
    lineage_id = "lineage_111111111111"
    repository.state_store.put_lineage_definition(
        lineage_id,
        {"lineage_id": lineage_id, "nodes": [], "edges": []},
        {},
    )
    publish_registered_lineage_members({
        "lineage_id": lineage_id,
        "nodes": [{"id": resident_id, "title": resident_id} for resident_id in (A, B, EMPTY)],
    })
    with repository.state_store.database.transaction() as connection:
        for resident_id in (A, B):
            repository._ensure_conversation(connection, resident_id)
        connection.executemany(
            'INSERT INTO conversation_input_states VALUES (%s, %s, %s, %s, %s)',
            (
                (A, f"old_{index:06d}", ("completed", "failed", "cancelled")[index % 3],
                 '{}', '2026-09-01T00:00:00Z')
                for index in range(40000)
            ),
        )
        connection.executemany(
            'INSERT INTO conversation_input_states VALUES (%s, %s, %s, %s, %s)',
            [
                (A, 'queued', 'queued', '{}', '2026-09-06T00:00:00Z'),
                (A, 'running', 'running', '{}', '2026-09-06T00:00:00Z'),
                (A, 'visible', 'failed', '{"message":"test failure"}', '2026-09-06T00:00:01Z'),
                (B, 'visible', 'completed', '{}', '2026-09-06T00:00:02Z'),
            ],
        )
    # A finished input's reply is the trace's message answering it.
    for message in (
        {"message_id": "reply_active", "role": "agent", "reply_to_message_id": "running",
         "content_parts": [{"type": "text", "text": "working"}]},
        {"message_id": "reply_final", "role": "system", "reply_to_message_id": "visible",
         "content_parts": [{"type": "text", "text": "failed"}]},
    ):
        repository.append_message(A, message)
    yield repository
    repository.state_store.database.close()
    repository.agent_data.database.close()


@contextmanager
def query_work(repository, monkeypatch):
    original_read = repository.state_store.database.read
    work = {"query_count": 0, "conversation_input_queries": []}
    statements = []

    @contextmanager
    def measured_read():
        with original_read() as connection:
            yield guard_queries(connection, statements=statements)

    monkeypatch.setattr(repository.state_store.database, "read", measured_read)
    start = time.perf_counter()
    try:
        yield work
    finally:
        work["query_count"] = len(statements)
        work["conversation_input_queries"] = [
            statement for statement in statements
            if "conversation_input_states" in statement
        ]
        work["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)
        print(json.dumps({
            "query_count": work["query_count"],
            "conversation_input_query_count": len(work["conversation_input_queries"]),
            "elapsed_ms": work["elapsed_ms"],
        }, sort_keys=True))
        monkeypatch.setattr(repository.state_store.database, "read", original_read)


@pytest.mark.parametrize("visible_ids", ([], ["visible", "old_000003", "running", "visible"]))
def test_snapshot_input_query_visits_page_and_active_inputs_only(history_repository, monkeypatch, visible_ids):
    """快照只查询本页可见与仍在排队或运行的输入，不扫描已结束的历史

    :design-point:"""
    repository = history_repository
    with query_work(repository, monkeypatch) as work:
        with repository.state_store.database.read() as connection:
            inputs = repository._input_states_for_snapshot(connection, A, visible_ids)
    expected_ids = {"queued", "running"} | set(visible_ids)
    assert {item["message_id"] for item in inputs} == expected_ids
    assert len(inputs) == len(expected_ids)
    assert inputs == sorted(inputs, key=lambda item: (item["updated_at"], item["message_id"]))
    if "visible" in visible_ids:
        visible = next(item for item in inputs if item["message_id"] == "visible")
        assert visible["state"] == "failed"
        assert visible["error"] == {"message": "test failure"}
        assert visible["reply_message_ids"] == ["reply_final"]
    input_queries = work["conversation_input_queries"]
    observations = [query for query in input_queries if "from conversation_input_states where" in query]
    outcomes = [query for query in input_queries if "join conversation_input_states input" in query]
    assert len(observations) <= 2
    assert all("resident_id = %s" in query and "state in (" in query for query in observations)
    # Terminal reconciliation visits only the requested pending input identities.
    assert len(outcomes) == 1
    assert "unnest(%s::text[],%s::text[])" in outcomes[0].replace(" ", "")
    assert "using(resident_id,message_id)" in outcomes[0]
    assert "input.state in ('queued','running')" in outcomes[0]
    assert len(input_queries) == len(observations) + len(outcomes)


def test_batch_state_query_seeks_latest_input_without_scanning_history(history_repository, monkeypatch):
    """批量读取状态时用一次查询定位各 Resident 最新输入，不扫描输入历史

    :design-point:"""
    repository = history_repository
    with query_work(repository, monkeypatch) as work:
        states = repository.state_snapshots([A, B, EMPTY, MISSING, A])
    assert set(states) == {A, B, EMPTY}
    assert states[A]["latest_input_state"] == {
        "message_id": "visible", "state": "failed", "updated_at": "2026-09-06T00:00:01Z",
    }
    assert states[B]["latest_input_state"]["state"] == "completed"
    assert states[EMPTY]["latest_input_state"] == {}
    assert len(work["conversation_input_queries"]) == 1
    assert "candidate.resident_id = requested.resident_id" in work["conversation_input_queries"][0]
    assert "candidate.state = kinds.state" in work["conversation_input_queries"][0]


def test_state_only_batch_ignores_input_history(history_repository, monkeypatch):
    """只读工作状态的批量查询不访问输入历史表

    :design-point:"""
    repository = history_repository
    with query_work(repository, monkeypatch) as work:
        states = repository.states([A, B, EMPTY, MISSING, A])
    assert list(states) == [A, B, EMPTY]
    assert all("latest_input_state" not in state for state in states.values())
    assert states[A]["intake_state"] == "open"
    assert work["conversation_input_queries"] == []


def test_latest_input_tie_break_and_snapshot_large_page_preserve_protocol(history_repository):
    """最新输入按时间与消息 ID 决定先后，大页快照返回全部请求的输入

    :design-point:"""
    repository = history_repository
    with repository.state_store.database.transaction() as connection:
        connection.execute(
            "UPDATE conversation_input_states SET updated_at='2026-09-06T00:00:01Z' WHERE resident_id=%s AND message_id='running'", (A,),
        )
        connection.execute(
            "INSERT INTO conversation_input_states VALUES (%s, 'zzz', 'cancelled', '{}', '2026-09-06T00:00:01Z')", (A,),
        )
    assert repository.state_snapshots([A])[A]["latest_input_state"]["message_id"] == "zzz"
    with repository.state_store.database.read() as connection:
        inputs = repository._input_states_for_snapshot(connection, A, [f"old_{index:06d}" for index in range(1000)])
    assert len(inputs) == 1002
    assert len({item["message_id"] for item in inputs}) == 1002


def test_state_query_crosses_batch_boundary_with_bounded_work(history_repository, monkeypatch):
    """超过一批上限的状态查询分两次完成，每批只查请求的 Resident

    :design-point:"""
    repository = history_repository
    absent = [f"resident_{index:012x}" for index in range(501)]
    with query_work(repository, monkeypatch) as work:
        states = repository.state_snapshots([A, *absent, B, A])
    assert list(states) == [A, B]
    assert states[A]["latest_input_state"]["message_id"] == "visible"
    assert states[B]["latest_input_state"]["state"] == "completed"
    assert len(work["conversation_input_queries"]) == 2
    assert all(
        "candidate.resident_id = requested.resident_id" in query
        for query in work["conversation_input_queries"]
    )


@pytest.mark.parametrize(
    "projection",
    ["state", "queue", "activities", "input_state", "terminal_input"],
)
def test_unchanged_projection_does_not_wait_for_another_database_writer(
    history_repository, projection,
):
    """未变化的状态、队列、活动或输入投影不等待其他数据库写事务，也不推进游标

    :design-point:"""
    repository = history_repository
    actions = {
        "state": lambda: repository.set_state(A, {
            "work_state": "running", "active_message_id": "running",
            "queued_count": 0, "intake_state": "open",
        }),
        "queue": lambda: repository.publish_queue(A),
        "activities": lambda: repository.set_activities(A, [{
            "activity_id": "tool-active", "resident_id": A,
            "kind": "command", "status": "running",
        }]),
        "input_state": lambda: repository.set_input_state(
            A, "running", "running", updated_at="2026-09-06T00:00:00Z",
        ),
        "terminal_input": lambda: repository.set_input_state(A, "visible", "running"),
    }
    action = actions[projection]
    action()
    with repository.state_store.database.read() as connection:
        cursor = connection.execute(
            'SELECT cursor FROM conversation_states WHERE resident_id=%s', (A,),
        ).fetchone()[0]
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    errors = []
    timing = {}

    def hold_writer():
        try:
            with repository.state_store.database.transaction() as connection:
                connection.execute(
                    'UPDATE conversation_states SET cursor=cursor WHERE resident_id=%s', (B,),
                )
                entered.set()
                assert release.wait(3)
        except BaseException as exc:
            errors.append(exc)

    def repeat_projection():
        start = time.perf_counter()
        try:
            action()
        except BaseException as exc:
            errors.append(exc)
        finally:
            timing["elapsed_ms"] = (time.perf_counter() - start) * 1000
            completed.set()

    writer = threading.Thread(target=hold_writer)
    reader = threading.Thread(target=repeat_projection)
    writer.start()
    try:
        assert entered.wait(1)
        reader.start()
        assert completed.wait(1), "unchanged projection waited for the PostgreSQL writer"
    finally:
        release.set()
        writer.join(3)
        if reader.ident is not None:
            reader.join(3)
    assert not errors
    assert not writer.is_alive() and not reader.is_alive()
    with repository.state_store.database.read() as connection:
        assert connection.execute(
            'SELECT cursor FROM conversation_states WHERE resident_id=%s', (A,),
        ).fetchone()[0] == cursor
    assert repository.input_state(A, "visible")["state"] == "failed"
    print(json.dumps({"projection": projection, **timing}, sort_keys=True))


def test_projection_fast_path_observes_uncommitted_changes_in_its_transaction(
    history_repository,
):
    """投影快速路径能看到同一事务中未提交的改动，并据此重新写入

    :design-point:"""
    repository = history_repository
    running = {
        "work_state": "running", "active_message_id": "running",
        "queued_count": 0, "intake_state": "open",
    }
    repository.set_state(A, running)
    with repository.state_store.database.transaction() as connection:
        cursor = connection.execute(
            'SELECT cursor FROM conversation_states WHERE resident_id=%s', (A,),
        ).fetchone()[0]
        connection.execute(
            'UPDATE conversation_states SET state_json=%s WHERE resident_id=%s',
            (json.dumps({**running, "work_state": "queued"}), A),
        )
        connection.execute(
            "UPDATE conversation_input_states SET state='queued' WHERE resident_id=%s AND message_id='running'", (A,),
        )
        assert repository.set_state(A, running) is not None
        assert repository.set_input_state(A, "running", "running")["state"] == "running"
        assert connection.execute(
            'SELECT cursor FROM conversation_states WHERE resident_id=%s', (A,),
        ).fetchone()[0] == cursor + 2
    assert repository.current_projection(A)["state"] == running
    assert repository.input_state(A, "running")["state"] == "running"
