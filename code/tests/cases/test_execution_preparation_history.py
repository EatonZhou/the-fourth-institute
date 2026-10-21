"""
:importance: P1
:design: decision_71aa039e3211
"""
import pytest

from management_portal.execution.agent_data_store import AgentDataStore


def test_preparation_queued_start_keep_one_execution():
    """执行从准备、排队到开始始终是同一条记录，保留创建时间且来源不可更改"""
    store = AgentDataStore()
    args = dict(execution_id="execution-reserved", agent_id="resident_aaaaaaaaaaaa",
                actor_ref="sample_admin", provider="codex")
    prepared = store.reserve_execution(**args, created_at="2026-10-14T00:00:00Z")
    assert prepared["status"] == "preparing" and prepared["started_at"] == ""
    store.queue_execution(args["execution_id"])
    assert store.execution(args["execution_id"])["status"] == "queued"
    started = store.begin_execution(**args, started_at="2026-10-14T00:00:02Z")
    assert started["created_at"] == "2026-10-14T00:00:00Z"
    assert started["started_at"] == "2026-10-14T00:00:02Z"
    store.reserve_execution(**args)
    store.queue_execution(args["execution_id"])
    assert store.execution(args["execution_id"])["status"] == "running"
    with pytest.raises(ValueError, match="immutable provenance"):
        store.reserve_execution(**(args | {"agent_id": "resident_bbbbbbbbbbbb"}))


def test_cancel_before_provider_start_is_retained():
    """Provider 启动前取消的执行保留创建时间和 cancelled 状态，不能再开始"""
    store = AgentDataStore()
    args = dict(execution_id="execution-cancelled", agent_id="resident_aaaaaaaaaaaa",
                actor_ref="sample_admin", provider="codex")
    store.reserve_execution(**args, created_at="2026-10-14T00:00:00Z")
    store.finish_execution(args["execution_id"], status="cancelled", finished_at="2026-10-14T00:00:01Z")
    result = store.execution(args["execution_id"])
    assert result["status"] == "cancelled" and result["started_at"] == ""
    assert result["created_at"] == "2026-10-14T00:00:00Z"
    with pytest.raises(ValueError):
        store.begin_execution(**args, started_at="2026-10-14T00:00:02Z")
