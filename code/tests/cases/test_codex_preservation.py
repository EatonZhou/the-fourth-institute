"""Recover persisted account-switch receipts against the effective database trace.

:module: execution.service
:importance: P1
"""
import pytest

from management_portal.execution import context_preservation as owner
from management_portal.execution.agent_data_store import AgentDataStore, stable_segment_id, utc_now
from management_portal.execution.agent_operations import put_operation
from tests.cases.test_preservation_port import source


def legacy_receipt(store, agent, binding, refs, *, native):
    key = stable_segment_id("session-preservation", agent, "switch")
    receipt = {
        "schema": owner.SCHEMA, "operation_id": "switch", "agent_id": agent, "actor": "alice",
        "receipt_id": key, "source_binding": owner._identity(binding),
        "source": owner._source(store, agent, binding), "native_boundary": native,
        "seed": {"source_agent_id": agent, "schema": "tfi.provider-context-seed.v1",
                 "segments": [{"segment_id": sid} for sid in refs], "empty_context": not refs},
    }
    metadata = {"kind": "session_preservation", "source": "execution", "prepared_sha256": owner._digest(receipt)}
    if native:
        # The original archive was on a worker that no longer exists.
        metadata["native_evidence"] = {"path": "/retired/worker/rollout.jsonl", "fingerprint": [1, 2, 3, 4]}
    store.put_operation_record(segment_id=key, segment_type="harness", created_by_agent_id=agent,
                               content={"prepared": receipt}, metadata=metadata)
    return receipt


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("text_column", [False, True])
def test_old_native_boundary_requires_database_commit(committed, text_column):
    store = AgentDataStore()
    agent, binding = source(store)
    if text_column:
        with store.database.transaction() as connection:
            connection.execute("ALTER TABLE agent_operations ALTER COLUMN data_json TYPE TEXT USING data_json::text")
    item = {"type": "reasoning", "encrypted_content": "opaque", "summary": []}
    sid = store.put_segment(segment_type="compaction", content={"replacement_history": [item]},
                            content_type="application/json")
    prepared = legacy_receipt(store, agent, binding, [sid], native=True)
    if committed:
        store.append_agent_segments(agent, [sid], actor_ref="alice")
        committed_source = owner._source(store, agent, binding)
        with store.database.transaction() as connection:
            put_operation(connection, operation_id=stable_segment_id("session-preservation-commit", prepared["receipt_id"]),
                          kind="session_preserved", agent_id=agent, created_at=utc_now(),
                          data={"source": committed_source, "receipt_id": prepared["receipt_id"]})
    before = store.agent_context_snapshot(agent)
    if committed:
        assert owner.prepare_context_preservation(agent, actor="alice", operation_id="switch",
                                                 source_binding=binding, store=store) == prepared
        assert owner.commit_context_preservation(prepared, store=store) == prepared["seed"]
        assert store.fork_context_segments(prepared["seed"]["segments"])[0]["content"]["replacement_history"] == [item]
        store.append_agent_segments(agent, [store.put_segment(segment_type="input", content="later")], actor_ref="alice")
        with pytest.raises(ValueError, match="source history changed"):
            owner.commit_context_preservation(prepared, store=store)
    else:
        with pytest.raises(ValueError, match="never committed"):
            owner.commit_context_preservation(prepared, store=store)
        assert store.agent_context_snapshot(agent) == before


@pytest.mark.parametrize("window", ["complete", "partial", "reversed", "empty-native"])
def test_old_receipt_must_match_full_trace_membership_and_order(window):
    store = AgentDataStore()
    agent, binding = source(store)
    first = store.agent_context_snapshot(agent)["segments"][0]["segment_id"]
    second = store.put_segment(segment_type="model", content="later answer")
    store.append_agent_segments(agent, [second], actor_ref="alice")
    refs = {"complete": [first, second], "partial": [first], "reversed": [second, first], "empty-native": []}[window]
    prepared = legacy_receipt(store, agent, binding, refs, native=window == "empty-native")
    if window == "complete":
        assert owner.commit_context_preservation(prepared, store=store) == prepared["seed"]
    else:
        with pytest.raises(ValueError, match="differs from the effective Agent trace"):
            owner.commit_context_preservation(prepared, store=store)
    assert [s["segment_id"] for s in store.agent_context_snapshot(agent)["segments"]] == [first, second]


def test_empty_legacy_native_receipt_after_database_reset():
    store = AgentDataStore()
    agent, binding = source(store)
    store.reset_provider_context(agent, reset_at=utc_now(), actor_ref="alice")
    prepared = legacy_receipt(store, agent, binding, [], native=True)
    assert owner.commit_context_preservation(prepared, store=store) == prepared["seed"]
    assert store.agent_context_snapshot(agent)["empty_context"] is True
