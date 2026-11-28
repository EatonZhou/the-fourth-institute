"""Execution-owned preservation and optimistic Resident admission.

:module: execution.service
:importance: P1
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import copy

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.context_preservation import prepare_context_preservation, validate_context_preservation, commit_context_preservation
from management_portal.society_service import resident_restart_domain
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


def test_preparation_preserves_the_full_trace_across_a_bound_session():
    store = AgentDataStore()
    agent, binding = source(store)
    first = store.agent_context_snapshot(agent)["segments"][0]["segment_id"]
    store.record_context_received(agent, binding["provider_session_ref"], [first], actor_ref="alice")
    pending = store.put_segment(segment_type="input", content="not yet delivered")
    store.append_agent_segments(agent, [pending], actor_ref="alice")
    live = store.agent_context_snapshot(agent)
    args = dict(actor="alice", operation_id="one", source_binding=binding, store=store)
    prepared = prepare_context_preservation(agent, **args)
    seed = commit_context_preservation(prepared, store=store)
    assert [r["segment_id"] for r in seed["segments"]] == [first, pending]
    assert prepare_context_preservation(agent, **args) == prepared
    assert commit_context_preservation(prepared, store=store) == seed
    assert store.agent_context_snapshot(agent) == live
    assert store.provider_binding("resident:" + agent) == binding
    with pytest.raises(ValueError, match="another source or actor"):
        prepare_context_preservation(agent, **{**args, "actor": "bob"})


def test_canonical_preparation_rechecks_trace_and_binding(monkeypatch):
    store = AgentDataStore()
    agent, binding = source(store)
    args = dict(actor="alice", operation_id="canonical", source_binding=binding, store=store)
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
                                     store=store)


def test_concurrent_preparation_returns_one_immutable_receipt(monkeypatch):
    store = AgentDataStore()
    agent, binding = source(store)
    barrier = Barrier(2)
    read_snapshot = store.agent_context_snapshot
    def synchronized_snapshot(agent_id):
        result = read_snapshot(agent_id)
        barrier.wait(timeout=5)
        return result
    monkeypatch.setattr(store, "agent_context_snapshot", synchronized_snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(prepare_context_preservation, agent, actor="alice",
                               operation_id="concurrent", source_binding=binding, store=store)
                   for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1]
    validate_context_preservation(results[0], store=store)


def test_validation_only_reads_source_token_and_receipt_metadata(monkeypatch):
    store = AgentDataStore()
    agent, binding = source(store)
    prepared = prepare_context_preservation(agent, actor="alice", operation_id="budget", source_binding=binding,
        store=store)
    monkeypatch.setattr(store, "agent_context_snapshot", lambda *a, **k: pytest.fail("no history read at commit"))
    monkeypatch.setattr(store, "operation_record", lambda *a, **k: pytest.fail("no full receipt read at commit"))
    validate_context_preservation(prepared, store=store)
    tampered = copy.deepcopy(prepared)
    tampered["seed"]["segments"] = []
    with pytest.raises(ValueError, match="receipt identity"):
        validate_context_preservation(tampered, store=store)


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


def test_resident_restart_leaves_history_delivery_to_first_input(monkeypatch):
    record, operations, _ = _restart_domain(monkeypatch)
    store = AgentDataStore()
    before = store.agent_context_snapshot(record["resident_id"])
    def unexpected(*args, **kwargs):
        pytest.fail("configuration-only restart attempted a separate context import")
    monkeypatch.setattr(execution_port, "prepare_context_preservation", unexpected)
    monkeypatch.setattr(execution_port, "commit_context_preservation", unexpected)
    result = resident_restart_domain.restart_resident_session(
        record["resident_id"], "alice", {"target_account": "one", "idempotency_key": "preserve-one"})
    assert result["configuration_only"]
    assert operations == []
    assert store.agent_context_snapshot(record["resident_id"]) == before
