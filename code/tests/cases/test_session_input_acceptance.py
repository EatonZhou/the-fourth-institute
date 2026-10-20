"""Session readiness survives loss of operational Execution history."""
from tests.cases.agent_data_test_support import session_transcript_ids

from tests.cases.database_test_support import guard_queries
from contextlib import contextmanager

import pytest

from management_portal.execution.agent_data_store import AgentDataStore


def _session(store, agent="resident_one", route="zero", session="thread-one", binding="resident:one"):
    result = store.ensure_provider_session(
        provider="codex", backend_route_id=route, backend_session_id=session,
        agent_id=agent, actor_ref="test",
    )
    store.bind_provider_session(binding, result["provider_session_ref"])
    return result["provider_session_ref"]


def _execution(store, session_ref, execution="execution-one", agent="resident_one"):
    store.begin_execution(
        execution_id=execution, agent_id=agent, actor_ref="test", provider="codex",
        started_at="2026-09-25T00:00:00Z",
    )
    if session_ref:
        store.bind_execution_provider_session(execution, provider_session_ref=session_ref)


@pytest.mark.parametrize("acceptance", ["event", "terminal", "late_binding"])
def test_receipt_survives_execution_history_loss_without_replaying_context(tmp_path, acceptance, monkeypatch):
    """执行历史丢失后接收回执仍有效且不回放上下文"""
    store = AgentDataStore()
    ref = _session(store)
    _execution(store, ref if acceptance != "late_binding" else "")
    assert not store.binding_has_accepted_input("resident:one", agent_id="resident_one")
    if acceptance == "terminal":
        store.finish_execution("execution-one", status="completed", provider_turn_id="turn-one")
    else:
        store.record_input_acceptance("execution-one", agent_id="resident_one", provider_turn_id="turn-one")
    if acceptance == "late_binding":
        store.bind_execution_provider_session("execution-one", provider_session_ref=ref)
    ids = store.sequence_segment_ids(store.trace_sequence_id('resident_one'))
    assert ids == []
    with store.database.read() as connection:
        receipt = store._session_input_acceptance(connection, ref, "resident_one")
    assert receipt["evidence_execution_id"] == "execution-one"
    assert store.session_context_ids(ref) == []
    assert session_transcript_ids(store, ref) == []
    store.database.write(lambda connection: connection.execute("DELETE FROM executions"))
    reopened = AgentDataStore()
    original_read = reopened.database.read_transaction

    @contextmanager
    def no_execution_reads():
        with original_read() as connection:
            yield guard_queries(connection, forbidden=(" from executions",))

    monkeypatch.setattr(reopened.database, "read_transaction", no_execution_reads)
    assert reopened.binding_has_accepted_input("resident:one", agent_id="resident_one")
    assert not reopened.binding_has_accepted_input("resident:one", agent_id="resident_other")


def test_receipt_is_scoped_to_session_route_and_agent_and_is_immutable(tmp_path):
    """接收回执按会话路由与 agent 隔离且首次记录不可变"""
    store = AgentDataStore()
    ref = _session(store)
    _execution(store, ref)
    store.record_input_acceptance("execution-one", agent_id="resident_one", provider_turn_id="turn-one")
    with store.database.read() as connection:
        before = store._session_input_acceptance(connection, ref, "resident_one")
    store.record_input_acceptance("execution-one", agent_id="resident_one", provider_turn_id="turn-one")
    store.finish_execution("execution-one", status="failed")
    _execution(store, ref, execution="execution-two")
    store.record_input_acceptance("execution-two", agent_id="resident_one", provider_turn_id="turn-two")
    with store.database.read() as connection:
        assert store._session_input_acceptance(connection, ref, "resident_one") == before
    for number, route in enumerate(("zero", "one")):
        target = f"empty-{number}"
        _session(store, route=route, session="thread-new" if route == "zero" else "thread-one", binding=target)
        assert not store.binding_has_accepted_input(target, agent_id="resident_one")
    replacement = store.replace_provider_binding("resident:one", "empty-1")
    assert not store.binding_has_accepted_input("resident:one", agent_id="resident_one")
    store.restore_provider_binding(
        "resident:one", expected_provider_session_ref=replacement["current_binding"]["provider_session_ref"],
        binding=replacement["previous_binding"],
    )
    assert store.binding_has_accepted_input("resident:one", agent_id="resident_one")


def test_acceptance_and_receipt_rollback_together(tmp_path, monkeypatch):
    """输入接收状态与会话回执在写入失败时一同回滚"""
    store = AgentDataStore()
    ref = _session(store)
    _execution(store, ref)
    def broken(*args, **kwargs):
        raise RuntimeError("receipt write failed")
    monkeypatch.setattr(store, "_record_session_input_acceptance", broken)
    with pytest.raises(RuntimeError, match="receipt write failed"):
        store.record_input_acceptance("execution-one", agent_id="resident_one", provider_turn_id="turn-one")
    assert store.execution("execution-one")["provider_turn_id"] == ""
    assert not store.binding_has_accepted_input("resident:one", agent_id="resident_one")
    with pytest.raises(RuntimeError, match="receipt write failed"):
        store.finish_execution("execution-one", status="completed", provider_turn_id="turn-one")
    assert store.execution("execution-one")["status"] == "running"


def test_invalid_turn_does_not_create_a_receipt(tmp_path):
    """无效轮次或执行者不匹配时不创建接收回执"""
    store = AgentDataStore()
    ref = _session(store)
    _execution(store, ref)
    with pytest.raises(ValueError, match="turn identity"):
        store.record_input_acceptance("execution-one", agent_id="resident_one", provider_turn_id=" ")
    with pytest.raises(KeyError):
        store.record_input_acceptance("execution-one", agent_id="resident_other", provider_turn_id="turn")
    store.finish_execution("execution-one", status="cancelled")
    assert not store.binding_has_accepted_input("resident:one", agent_id="resident_one")


def test_first_binding_promotion_is_conditional_and_rollback_restores_absence(tmp_path):
    """首次绑定提升要求目标为空且回滚恢复无绑定状态"""
    from management_portal.execution.session_manager import SessionManager
    store = AgentDataStore()
    sessions = SessionManager(agent_data=store)
    _session(store, binding="prepared")
    replacement = sessions.replace_session("resident:one", "prepared", expected_binding={})
    assert replacement["previous_binding"] == {}
    assert sessions.provider_binding("resident:one")
    assert sessions.restore_session("resident:one", replacement) == {}
    assert not sessions.provider_binding("resident:one")
    _session(store, binding="prepared-again", session="new-prepared")
    _session(store, binding="resident:one", session="concurrently-created")
    with pytest.raises(RuntimeError, match="changed"):
        sessions.replace_session("resident:one", "prepared-again", expected_binding={})
    with pytest.raises(RuntimeError, match="changed"):
        sessions.restore_session("resident:one", replacement)
    assert sessions.provider_binding("resident:one")["backend_session_id"] == "concurrently-created"
    assert sessions.provider_binding("prepared-again")
    with pytest.raises(ValueError, match="absent"):
        store.replace_provider_binding(
            "missing", "prepared-again", expected_absent=True,
            expected_provider="codex",
        )
