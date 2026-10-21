""":importance: P2"""
from __future__ import annotations
from tests.cases.agent_data_test_support import register_session

import ast
from tests.cases.database_test_support import OperationalError
from pathlib import Path

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.session_manager import SessionManager
from management_portal.execution.transport import ExecutionServiceClient
from management_portal.society_service import changes, execution_client, resident_backend


@pytest.fixture
def session_boundary(tmp_path):
    # SocietyService runs these session transactions in its own process (execution_client.sessions()).
    store = AgentDataStore()
    return store, SessionManager(agent_data=store)


def bind(manager, binding_id, session_id, *, provider="codex", route="zero"):
    return manager.bind_session(
        binding_id, provider=provider, backend_session_id=session_id,
        backend_route_id=route,
    )


def test_session_lifecycle_contract(session_boundary):
    """会话对应关系的完整生命周期

    SessionManager 依次建立、重放、补充上下文摘要、批量读取、替换、按回执恢复并释放会话对应关系；释放只删除对应关系，Provider 会话记录保留。
    """
    store, manager = session_boundary
    original = bind(manager, "current", "session-original")
    assert bind(manager, "current", "session-original")["provider_session_ref"] == original["provider_session_ref"]
    manager.bind_session(
        "current", provider="codex", backend_session_id="session-original",
        backend_route_id="zero",
        context_fingerprint="fingerprint", context_injected_at="2026-09-08T00:00:00Z",
        context_injection_count=3, harness_tool_names=["read", "write"],
    )
    assert manager.provider_binding("current")["context_injection_count"] == 3
    original = manager.provider_binding("current")
    bind(manager, "prepared", "session-new", provider="claude", route="")
    replacement = manager.replace_session("current", "prepared", expected_binding=original)
    assert manager.provider_binding("prepared") == {}
    assert replacement["current_binding"]["provider"] == "claude"
    assert replacement["previous_binding"]["context_injection_count"] == 3
    restored = manager.restore_session("current", replacement)
    assert restored["provider_session_ref"] == original["provider_session_ref"]
    assert list(restored["harness_tool_names"]) == ["read", "write"]
    manager.release_session("current")
    manager.release_session("current")
    assert manager.provider_binding("current") == {}
    with store.database.read() as connection:
        assert connection.execute(
            'SELECT 1 FROM provider_sessions WHERE provider_session_ref = %s',
            (original["provider_session_ref"],),
        ).fetchone()


def test_session_binding_conflict_rolls_back_session_creation(session_boundary):
    """绑定到不同后端会话的冲突被拒绝，并回滚新建的 Provider 会话记录"""
    store, manager = session_boundary
    original = bind(manager, "current", "session-original")
    with store.database.read() as connection:
        before = connection.execute("SELECT COUNT(*) FROM provider_sessions").fetchone()[0]
    with pytest.raises(ValueError, match="immutable"):
        bind(manager, "current", "session-other")
    with store.database.read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM provider_sessions").fetchone()[0] == before
    assert manager.provider_binding("current") == original


def test_session_replace_and_restore_fence_stale_snapshots(session_boundary):
    """会话替换和恢复拒绝基于过期快照或其他绑定的操作"""
    store, manager = session_boundary
    original = bind(manager, "current", "same-native-id")
    bind(manager, "prepared", "same-native-id", route="one")
    changed = manager.replace_session("current", "prepared", expected_binding=original)
    bind(manager, "next", "session-next")
    with pytest.raises(RuntimeError, match="changed before replacement"):
        manager.replace_session("current", "next", expected_binding=original)
    assert manager.provider_binding("next")
    manager.replace_session("current", "next", expected_binding=changed["current_binding"])
    with pytest.raises(RuntimeError, match="changed after replacement"):
        manager.restore_session("current", changed)
    with pytest.raises(ValueError, match="another identity"):
        manager.restore_session("other", changed)
    assert manager.provider_binding("current")["backend_session_id"] == "session-next"


def test_release_contract_fences_replaced_bindings(session_boundary):
    """会话对应关系被替换后，基于旧绑定的 release 被拒绝"""
    store, manager = session_boundary
    original = bind(manager, "current", "session-original")
    request = ExecutionRequest.new(
        actor="CASE-A", prompt="release", provider="codex", operation="release",
        service_type="agent_session", session=SessionOptions(mode="persistent", binding_id="current"),
        runtime_options={"delete_binding_on_release": True},
    )
    session = manager.prepare(request)
    bind(manager, "prepared", "session-next")
    manager.replace_session("current", "prepared", expected_binding=original)
    with pytest.raises(RuntimeError, match="binding changed"):
        manager.provider_binding("current", expected_binding=original)
    with pytest.raises(RuntimeError, match="changed before release"):
        manager.release_session("current", expected_provider_session_ref=original["provider_session_ref"])
    with pytest.raises(RuntimeError, match="changed before release"):
        manager.complete(request, session, "")
    assert manager.provider_binding("current")["backend_session_id"] == "session-next"






@pytest.mark.parametrize("accepted", [False, True])
def test_session_acceptance_uses_current_binding_and_exact_agent(session_boundary, accepted):
    """是否已接受输入只按当前绑定和同一 agent 判断，替换绑定后重新计算"""
    store, manager = session_boundary
    agent_id = "resident_aaaaaaaaaaaa"
    original = bind(manager, "current", "session-original")
    store.begin_execution(execution_id="execution-current", agent_id=agent_id,
                          actor_ref="CASE-A", provider="codex", started_at="2026-09-09T00:00:00Z")
    store.bind_execution_provider_session("execution-current", provider_session_ref=original["provider_session_ref"])
    if accepted:
        store.record_input_acceptance("execution-current", agent_id=agent_id, provider_turn_id="turn-accepted")
    assert manager.has_accepted_input("current", agent_id=agent_id) is accepted
    assert not manager.has_accepted_input("current", agent_id="resident_bbbbbbbbbbbb")
    assert not manager.has_accepted_input("missing", agent_id=agent_id)
    bind(manager, "prepared", "session-empty")
    manager.replace_session("current", "prepared", expected_binding=original)
    assert not manager.has_accepted_input("current", agent_id=agent_id)




def test_lineage_session_operations_do_not_wait_for_service_contracts(monkeypatch, subtests, isolated_portal_case):
    """SocietyService 的会话替换、恢复和删除在本地事务完成，不等执行服务 RPC"""
    with subtests.test(contract='test_lineage_session_operations_do_not_wait_for_service'), isolated_portal_case():
        with monkeypatch.context() as monkeypatch:
            store = AgentDataStore()
            manager = SessionManager(agent_data=store)
            monkeypatch.setattr(execution_client, "sessions", lambda: manager)

            def unexpected_request(*args, **kwargs):
                raise AssertionError("Local session transaction must not wait for RPC")

            monkeypatch.setattr(ExecutionServiceClient, "request", unexpected_request)
            record = {"resident_id": "resident_aaaaaaaaaaaa", "backend": "codex"}
            register_session(record, "session-original", backend_route_id="zero")
            current = resident_backend.session_record(record)
            manager.bind_session(
                "prepared", provider="codex", backend_session_id="session-new",
                backend_route_id="zero",
            )
            replacement = resident_backend.replace_with_prepared_session(record, "prepared", expected_binding=current)
            resident_backend.restore_replaced_session(record, replacement)
            assert resident_backend.record_session_id(record) == "session-original"
            with changes.collect_changes() as rejected_notifications:
                with pytest.raises(RuntimeError, match="binding changed"):
                    resident_backend.delete_session_identity(record, expected_provider_session_ref="stale-ref")
            assert rejected_notifications == []
            assert resident_backend.record_session_id(record) == "session-original"
            with changes.collect_changes() as notifications:
                resident_backend.delete_session_identity(record)
            assert resident_backend.session_record(record) == {}
            # Reset commits before the asynchronous release. Open Chat headers
            # must discard the previous binding after that release completes.
            assert notifications == [("resident", record["resident_id"], "identity", {"kind": "changed"})]


def test_lineage_uses_execution_session_and_acceptance_authorities():
    """Lineage 代码不直接调用 Provider 会话绑定的底层写入接口"""
    source_root = Path(__file__).resolve().parents[2] / "management_portal" / "society_service"
    forbidden = {
        "ensure_provider_session", "bind_provider_session", "delete_provider_binding",
        "replace_provider_binding", "restore_provider_binding",
    }
    for path in source_root.glob("*.py"):
        tree = ast.parse(path.read_text())
        assert not [
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in forbidden
        ], str(path)
