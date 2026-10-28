"""Live Conversation controls exclude deleted identities; history remains readable."""
import json
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository
from management_portal.society_service.state_store import LineageDataStore
from tests.cases.database_test_support import register_owned_workspace
from tests.cases.test_resident_deleted_workspace_v31_integration import RID, sparse_history


@pytest.fixture
def active_repository():
    # This tests the database admission boundary, without ResourceService's
    # independently owned publication pipeline or filesystem projections.
    store = LineageDataStore()
    agents = AgentDataStore()
    for rid in ("resident_111111111111", "resident_222222222222"):
        store.database.write(lambda connection, rid=rid: connection.execute(
            "INSERT INTO residents(resident_id,title,owned_workspace_id) VALUES (%s,%s,%s)",
            (rid, rid, register_owned_workspace(connection, rid)),
        ))
        agents.ensure_agent(rid, actor_ref="fixture")
    return ResidentConversationRepository(store, agents)


def test_unused_resident_event_reader_stays_empty_until_first_event(active_repository):
    """未使用过的 Resident 在首个事件前读到空事件页且不建状态行，未知 Resident 报错"""
    repo = active_repository
    rid = "resident_111111111111"
    assert repo.events_after(rid, 0) == {
        "resident_id": rid, "after_cursor": 0, "cursor": 0, "events": [],
    }
    with repo.state_store.database.read() as connection:
        assert connection.execute(
            "SELECT 1 FROM conversation_states WHERE resident_id=%s", (rid,),
        ).fetchone() is None
    with pytest.raises(KeyError):
        repo.events_after("resident_444444444444", 0)

    repo.set_state(rid, {"work_state": "running", "intake_state": "open"})
    page = repo.events_after(rid, 0)
    assert page["cursor"] > 0
    assert page["events"][0]["event_type"] == "resident.state.changed"
    assert repo.events_after(rid, page["cursor"])["events"] == []


@pytest.fixture(params=[False, True], ids=["no-state", "residual-state"])
def deleted_repository(sparse_history, request):
    fixture = sparse_history
    repo = ResidentConversationRepository(fixture.store, fixture.agents)
    if request.param:
        fixture.store.database.write(lambda connection: connection.execute(
            "INSERT INTO conversation_states(resident_id,state_json) VALUES (%s,%s)",
            (RID, json.dumps({"work_state": "queued", "intake_state": "closed"})),
        ))
    return repo


def _stored_state(repository):
    with repository.state_store.database.read() as connection:
        row = connection.execute(
            "SELECT state_json,cursor FROM conversation_states WHERE resident_id=%s", (RID,),
        ).fetchone()
    return dict(row) if row else None


def test_deleted_identity_is_absent_from_live_reads_but_history_is_read_only(deleted_repository):
    """已删除的 Resident 不出现在实时状态读取中，历史对话仍可只读访问

    :design: decision_d19b9909a674, decision_b1ab45c4b3ec
    :design-point:
    """
    repo = deleted_repository
    before = _stored_state(repo)
    with pytest.raises(KeyError):
        repo.current_state(RID)
    for read in (repo.states, repo.state_snapshots, repo.current_projections):
        assert read([RID]) == {}
    assert repo.current_projection(RID)["resident_id"] == RID
    assert "state" in repo.projection._snapshot_projection(RID, [])
    assert _stored_state(repo) == before


def test_deleted_identity_cannot_create_or_update_live_state(deleted_repository):
    """已删除的 Resident 不能新建或更新对话实时状态，也不产生事件

    :design: decision_d19b9909a674, decision_b1ab45c4b3ec
    :design-point:
    """
    repo = deleted_repository
    before = _stored_state(repo)
    with pytest.raises(KeyError):
        repo.state_store.database.write(lambda connection: repo.events.ensure_conversation(connection, RID))
    with pytest.raises(KeyError):
        repo.set_state(RID, {"work_state": "running", "intake_state": "open"})
    assert _stored_state(repo) == before
    with repo.state_store.database.read() as connection:
        assert connection.execute(
            "SELECT COUNT(*) AS count FROM conversation_events WHERE resident_id=%s", (RID,),
        ).fetchone()["count"] == 0
    assert repo.agent_data.trace_length(RID) == 1


def test_live_reads_derive_controls_while_stream_reads_keep_published_snapshot(active_repository):
    """实时状态读取按当前控制推导工作状态，对话流投影保留已发布的状态"""
    repo = active_repository
    running, idle = "resident_111111111111", "resident_222222222222"
    repo.set_state(running, {"work_state": "running", "intake_state": "closed"})
    assert repo.current_state(idle)["work_state"] == "idle"
    assert repo.current_state(idle)["intake_state"] == "open"
    assert repo.current_state(running)["work_state"] == "idle"
    for read in (repo.states, repo.state_snapshots, repo.current_projections):
        result = read([running, idle, "resident_444444444444"])
        assert set(result) == {running, idle}
        running_state = result[running].get("state", result[running])
        idle_state = result[idle].get("state", result[idle])
        if read == repo.current_projections:
            assert running_state["work_state"] == "running"
            assert running_state["intake_state"] == "closed"
        else:
            assert running_state["work_state"] == "idle"
            assert running_state["intake_state"] == "open"
        assert idle_state["work_state"] == "idle"


def test_unchanged_state_cannot_bypass_deleted_identity_admission(active_repository):
    """Resident 删除后，写入与原来相同的状态也被拒绝

    :design: decision_d19b9909a674, decision_b1ab45c4b3ec
    :design-point:
    """
    repo = active_repository
    rid = "resident_111111111111"
    state = {"work_state": "queued", "queued_count": 9, "intake_state": "closed"}
    repo.set_state(rid, state)
    assert repo.set_state(rid, state) is None
    repo.state_store.database.write(lambda connection: connection.execute(
        "UPDATE residents SET deleted_at=%s,deleted_by=%s WHERE resident_id=%s",
        ("2026-10-08T00:00:00Z", "sample_admin", rid),
    ))
    before = repo.current_projection(rid)
    with pytest.raises(KeyError):
        repo.set_state(rid, state)
    assert repo.current_projection(rid) == before
    assert repo.current_projection(rid)["state"]["queued_count"] == 9


def test_conversation_admission_holds_identity_lock_until_writer_commits(active_repository):
    """对话写入事务提交前持有身份锁，期间删除该 Resident 会等待锁

    :design: decision_d19b9909a674, decision_b1ab45c4b3ec
    :design-point:
    """
    repo = active_repository
    rid = "resident_111111111111"

    def delete_with_timeout():
        with repo.state_store.database.transaction() as connection:
            connection.execute("SET LOCAL lock_timeout = '100ms'")
            connection.execute(
                "UPDATE residents SET deleted_at=%s,deleted_by=%s WHERE resident_id=%s",
                ("2026-10-08T00:00:00Z", "sample_admin", rid),
            )

    with ThreadPoolExecutor(max_workers=1) as workers:
        with repo.state_store.database.transaction() as connection:
            repo.events.ensure_conversation(connection, rid)
            with pytest.raises(psycopg.errors.LockNotAvailable):
                workers.submit(delete_with_timeout).result(timeout=3)
        # Commit releases the identity lock; a subsequent deletion succeeds.
        delete_with_timeout()
    with pytest.raises(KeyError):
        repo.set_state(rid, {"work_state": "running", "intake_state": "open"})
