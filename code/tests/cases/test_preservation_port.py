"""Execution-owned preservation and optimistic Resident admission.

:module: execution.service
:importance: P1
"""
from contextlib import contextmanager
import copy
import json
from types import SimpleNamespace

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.context_preservation import prepare_context_preservation, validate_context_preservation, commit_context_preservation
from management_portal.society_service import resident_access, resident_record_domain, resident_restart_domain
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_session_restart import _restart_domain


def source(store):
    agent = "resident_aaaaaaaaaaaa"
    store.ensure_agent(agent, actor_ref="alice")
    sid = store.put_segment(segment_type="input", content="keep original")
    store.append_agent_segments(agent, [sid], actor_ref="alice")
    session = store.ensure_provider_session(provider="codex", backend_route_id="zero",
        backend_session_id="thread", agent_id=agent, actor_ref="alice")
    binding = store.bind_provider_session("resident:" + agent, session["provider_session_ref"])
    return agent, binding


def test_native_preparation_does_not_attach_before_admission(tmp_path):
    store = AgentDataStore()
    agent, binding = source(store)
    live = store.agent_context_snapshot(agent)
    archive = tmp_path / "history.jsonl"
    item = {"type": "reasoning", "encrypted_content": "opaque", "summary": []}
    archive.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread"}}) + "\n" +
                       json.dumps({"type": "response_item", "payload": item}) + "\n")
    calls = []
    routes = SimpleNamespace(find_thread_rollout=lambda *a, **k: calls.append(1) or archive)
    args = dict(actor="alice", operation_id="one", source_binding=binding, store=store, routes=routes)
    prepared = prepare_context_preservation(agent, **args)
    assert prepare_context_preservation(agent, **args) == prepared
    assert calls == [1]
    assert store.agent_context_snapshot(agent) == live
    seed = prepared["seed"]
    assert seed["source_agent_id"] == agent
    assert str(archive) not in json.dumps(prepared)
    assert store.fork_context_segments(seed["segments"])[0]["content"] == {"replacement_history": [item]}
    assert commit_context_preservation(prepared, store=store) == seed
    committed = store.agent_context_snapshot(agent)
    assert commit_context_preservation(prepared, store=store) == seed
    assert store.agent_context_snapshot(agent) == committed
    archive.write_text(archive.read_text() + json.dumps({"type":"response_item", "payload":
                       {"type":"message", "role":"user", "content":"later"}}) + "\n")
    with pytest.raises(ValueError, match="native source changed"):
        validate_context_preservation(prepared, store=store)
    target = store.ensure_provider_session(provider="codex", backend_route_id="one", backend_session_id="target",
                                           agent_id=agent, actor_ref="alice")
    store.record_context_received(seed["source_agent_id"], target["provider_session_ref"],
                                  [r["segment_id"] for r in seed["segments"]], actor_ref="alice")
    assert store.provider_context_snapshot(provider_session_ref=target["provider_session_ref"], agent_id=agent)["segments"][0]["content"] == {"replacement_history": [item]}
    assert store.provider_binding("resident:" + agent) == binding
    with pytest.raises(ValueError, match="another source or actor"):
        prepare_context_preservation(agent, **{**args, "actor": "bob"})


def test_canonical_preparation_rechecks_trace_and_binding(monkeypatch):
    store = AgentDataStore()
    agent, binding = source(store)
    routes = SimpleNamespace(find_thread_rollout=lambda *a, **k: None)
    args = dict(actor="alice", operation_id="canonical", source_binding=binding, store=store, routes=routes)
    before = store.agent_context_snapshot(agent)
    prepared = prepare_context_preservation(agent, **args)
    assert store.agent_context_snapshot(agent) == before
    refs = prepared["seed"]["segments"]
    assert [r["segment_id"] for r in refs] == [s["segment_id"] for s in before["segments"]]
    extra = store.put_segment(segment_type="input", content="concurrent input")
    store.append_agent_segments(agent, [extra], actor_ref="alice")
    with pytest.raises(ValueError, match="source history changed"):
        validate_context_preservation(prepared, store=store)
    with pytest.raises(ValueError, match="source history changed"):
        prepare_context_preservation(agent, **args)
    with pytest.raises(ValueError, match="binding changed"):
        prepare_context_preservation(agent, **{**args, "source_binding": {**binding, "backend_session_id": "wrong"}})


def test_source_mutation_during_preparation_never_publishes_stale_receipt(monkeypatch):
    store = AgentDataStore()
    agent, binding = source(store)
    original = store.agent_context_snapshot
    def snapshot(*a, **k):
        result = original(*a, **k)
        store.append_agent_segments(agent, [store.put_segment(segment_type="input", content="new")], actor_ref="alice")
        return result
    monkeypatch.setattr(store, "agent_context_snapshot", snapshot)
    with pytest.raises(ValueError, match="source history changed"):
        prepare_context_preservation(agent, actor="alice", operation_id="racy", source_binding=binding,
                                     store=store, routes=SimpleNamespace(find_thread_rollout=lambda *a, **k: None))


def test_concurrent_preparation_returns_one_immutable_receipt(tmp_path):
    import concurrent.futures
    import threading
    store = AgentDataStore()
    agent, binding = source(store)
    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "thread"}}) + "\n" +
                    json.dumps({"type": "response_item", "payload": {"type":"message", "role":"user", "content":"same"}}) + "\n")
    barrier = threading.Barrier(2)
    def find(*a, **k):
        barrier.wait(timeout=5)
        return path
    def prepare():
        return prepare_context_preservation(agent, actor="alice", operation_id="concurrent", source_binding=binding,
                                            store=store, routes=SimpleNamespace(find_thread_rollout=find))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(prepare) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1]
    validate_context_preservation(results[0], store=store)


def test_validation_only_reads_source_token_and_receipt_metadata(monkeypatch):
    store = AgentDataStore()
    agent, binding = source(store)
    prepared = prepare_context_preservation(agent, actor="alice", operation_id="budget", source_binding=binding,
        store=store, routes=SimpleNamespace(find_thread_rollout=lambda *a, **k: None))
    monkeypatch.setattr(store, "agent_context_snapshot", lambda *a, **k: pytest.fail("no history read at commit"))
    monkeypatch.setattr(store, "operation_record", lambda *a, **k: pytest.fail("no full receipt read at commit"))
    validate_context_preservation(prepared, store=store)
    tampered = copy.deepcopy(prepared)
    tampered["seed"]["segments"] = []
    with pytest.raises(ValueError, match="receipt identity"):
        validate_context_preservation(tampered, store=store)


def test_native_attachment_and_commit_receipt_roll_back_together(tmp_path, monkeypatch):
    from management_portal.execution import context_preservation as owner
    store = AgentDataStore()
    agent, binding = source(store)
    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"type":"session_meta", "payload":{"id":"thread"}}) + "\n" +
                    json.dumps({"type":"response_item", "payload":{"type":"message", "role":"user", "content":"valid"}}) + "\n")
    prepared = prepare_context_preservation(agent, actor="alice", operation_id="rollback", source_binding=binding,
        store=store, routes=SimpleNamespace(find_thread_rollout=lambda *a, **k: path))
    before = store.agent_context_snapshot(agent)
    with monkeypatch.context() as patch:
        patch.setattr(owner, "put_operation", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("commit unavailable")))
        with pytest.raises(RuntimeError, match="commit unavailable"):
            commit_context_preservation(prepared, store=store)
    assert store.agent_context_snapshot(agent) == before
    validate_context_preservation(prepared, store=store)
    commit_context_preservation(prepared, store=store)


def test_prepare_racing_its_own_commit_reuses_original_receipt(tmp_path):
    import concurrent.futures
    import threading
    store = AgentDataStore()
    agent, binding = source(store)
    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"type":"session_meta", "payload":{"id":"thread"}}) + "\n" +
                    json.dumps({"type":"response_item", "payload":{"type":"message", "role":"user", "content":"same"}}) + "\n")
    second_scanning, committed = threading.Event(), threading.Event()
    def find(first, *a, **k):
        if first:
            assert second_scanning.wait(5)
        else:
            second_scanning.set()
            assert committed.wait(5)
        return path
    def prepare(first):
        return prepare_context_preservation(agent, actor="alice", operation_id="commit-race", source_binding=binding,
            store=store, routes=SimpleNamespace(find_thread_rollout=lambda *a, **k: find(first, *a, **k)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(prepare, True)
        second = pool.submit(prepare, False)
        try:
            prepared = first.result(timeout=10)
            commit_context_preservation(prepared, store=store)
        finally:
            committed.set()
        assert second.result(timeout=10) == prepared


def test_legacy_text_operation_records_use_real_postgres_casts(tmp_path, isolated_portal_case):
    """Existing installations may retain TEXT while new stores use JSONB."""
    with isolated_portal_case():
        store = AgentDataStore()
        agent, binding = source(store)
        with store.database.transaction() as connection:
            connection.execute("ALTER TABLE agent_operations ALTER COLUMN data_json TYPE TEXT USING data_json::text")
            row = connection.execute("SELECT data_type FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='agent_operations' AND column_name='data_json'").fetchone()
            assert row["data_type"] == "text"
        path = tmp_path / "native.jsonl"
        path.write_text(json.dumps({"type":"session_meta", "payload":{"id":"thread"}}) + "\n" +
                        json.dumps({"type":"response_item", "payload":{"type":"message", "role":"user", "content":"legacy"}}) + "\n")
        args = dict(actor="alice", operation_id="legacy-text", source_binding=binding, store=store,
                    routes=SimpleNamespace(find_thread_rollout=lambda *a, **k: path))
        prepared = prepare_context_preservation(agent, **args)
        validate_context_preservation(prepared, store=store)
        commit_context_preservation(prepared, store=store)
        assert prepare_context_preservation(agent, **args) == prepared
        assert commit_context_preservation(prepared, store=store) == prepared["seed"]


def test_empty_unbound_source_is_explicit_and_unknown_is_rejected(monkeypatch):
    store = AgentDataStore()
    prepared = prepare_context_preservation("resident_aaaaaaaaaaaa", actor="alice", operation_id="empty",
                                             source_binding={}, store=store)
    assert prepared["seed"]["empty_context"] is True
    assert prepared["seed"]["segments"] == []
    monkeypatch.setattr(store, "agent_context_snapshot", lambda *a, **k: {"segments": []})
    with pytest.raises(ValueError, match="no explicit boundary"):
        prepare_context_preservation("resident_bbbbbbbbbbbb", actor="alice", operation_id="unknown",
                                     source_binding={}, store=store)


def test_empty_native_source_keeps_pending_trace_without_provider_import(tmp_path):
    from management_portal.execution.codex_history_delivery import CodexHistoryDelivery
    store = AgentDataStore()
    agent, binding = source(store)
    before = store.read_chat_messages(agent)
    path = tmp_path / "empty.jsonl"
    path.write_text(json.dumps({"type":"session_meta", "payload":{"id":"thread"}}) + "\n")
    prepared = prepare_context_preservation(agent, actor="alice", operation_id="empty-native", source_binding=binding,
        store=store, routes=SimpleNamespace(find_thread_rollout=lambda *a, **k: path))
    seed = commit_context_preservation(prepared, store=store)
    assert seed["empty_context"] is True
    assert seed["segments"] == []
    CodexHistoryDelivery(references=seed["segments"], route_id="one", agent_id=agent, actor="alice",
        execution_id="switch", store=store, send=lambda *a: pytest.fail("empty context requires no Provider call")).initialize("empty-target")
    target = store.ensure_provider_session(provider="codex", backend_route_id="one", backend_session_id="empty-target",
        agent_id=agent, actor_ref="alice")
    store.record_context_received(agent, target["provider_session_ref"], [r["segment_id"] for r in seed["segments"]], actor_ref="alice")
    window = store.provider_context_snapshot(provider_session_ref=target["provider_session_ref"], agent_id=agent)
    assert window == {"segments": [], "empty_context": True}
    assert store.read_chat_messages(agent) == before


def observe_admission_lock(monkeypatch):
    depth = [0]
    @contextmanager
    def lock(*a, **k):
        depth[0] += 1
        try:
            yield
        finally:
            depth[0] -= 1
    monkeypatch.setattr(resident_record_domain, "state_lock", lock)
    return depth


def test_resident_scans_outside_lock_and_rechecks_inside(monkeypatch, isolated_portal_case):
    with isolated_portal_case():
        record, operations, _ = _restart_domain(monkeypatch)
        depth = observe_admission_lock(monkeypatch)
        original_prepare = execution_port.prepare_context_preservation
        original_commit = execution_port.commit_context_preservation
        def prepare(*a, **k):
            assert depth[0] == 0
            return original_prepare(*a, **k)
        def commit(prepared):
            assert depth[0] == 1
            return original_commit(prepared)
        monkeypatch.setattr(execution_port, "prepare_context_preservation", prepare)
        monkeypatch.setattr(execution_port, "commit_context_preservation", commit)
        result = resident_restart_domain.restart_resident_session(record["resident_id"], "alice",
                    {"target_account": "one", "idempotency_key": "preserve-one"})
        assert result["status"] == "accepted"
        assert operations[0]["step"]["request"]["metadata"]["fork_context_input"]["source_agent_id"] == record["resident_id"]


@pytest.mark.parametrize("change", ["configuration", "cancel", "permission", "history", "failure"])
def test_recheck_refuses_stale_or_failed_preparation(monkeypatch, isolated_portal_case, change):
    with isolated_portal_case():
        record, operations, saved = _restart_domain(monkeypatch)
        before = copy.deepcopy(record)
        prepare_owner = execution_port.prepare_context_preservation
        def prepare(*a, **k):
            if change == "failure":
                raise RuntimeError("native archive unavailable")
            prepared = prepare_owner(*a, **k)
            if change == "configuration": record["backend_route_id"] = "other"
            if change == "cancel": record["session_restart"] = {"status": "cancelled", "restart_id": "other"}
            if change == "permission": monkeypatch.setattr(resident_access, "can_manage", lambda *a: False)
            if change == "history":
                store = AgentDataStore()
                store.append_agent_segments(record["resident_id"], [store.put_segment(segment_type="input", content="new")], actor_ref="alice")
            return prepared
        monkeypatch.setattr(execution_port, "prepare_context_preservation", prepare)
        with pytest.raises((ValueError, PermissionError, RuntimeError)):
            resident_restart_domain.restart_resident_session(record["resident_id"], "alice", {"target_account": "one"})
        assert not saved
        assert not any("step" in row for row in operations)
        assert record.get("intake_open") == before.get("intake_open")


def test_slow_prepare_does_not_exclude_same_resident_state_transition(monkeypatch, isolated_portal_case):
    import threading
    from management_portal.society_service import invariant_locks
    with isolated_portal_case():
        record, operations, _ = _restart_domain(monkeypatch)
        monkeypatch.setattr(resident_record_domain, "state_lock", invariant_locks.resident_lock)
        owner = execution_port.prepare_context_preservation
        def prepare(*a, **k):
            acquired = threading.Event()
            def transition():
                with resident_record_domain.state_lock(record["resident_id"]): acquired.set()
            thread = threading.Thread(target=transition)
            thread.start()
            try:
                assert acquired.wait(2), "native preparation retained the Resident lock"
            finally:
                thread.join(timeout=3)
            return owner(*a, **k)
        monkeypatch.setattr(execution_port, "prepare_context_preservation", prepare)
        assert resident_restart_domain.restart_resident_session(record["resident_id"], "alice", {"target_account": "one"})["status"] == "accepted"
