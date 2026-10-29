"""Metadata-only evidence from real projection/transform transactions, without a Provider."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import json
import time

import pytest

from management_portal.execution import agent_data_contexts, agent_data_trace_edits
from management_portal.execution import agent_data_store as data_module
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.contracts import ExecutionRequest, ExecutionResult
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.session_manager import RuntimeSession
from management_portal.society_service import resident_context_evidence as evidence
from management_portal.society_service import resident_record_domain


def observe(store, agent):
    return evidence.project_context_evidence(store.context_evidence_snapshot(agent))


def test_actual_provider_rpc_receipt_is_durable_private_and_not_inferred():
    """实际 RPC 回执留在操作记录中；轮次接纳和重放不补造方法证明"""
    store, agent = AgentDataStore(), "resident_rpc_receipt"
    req = ExecutionRequest.new(actor="test-actor", agent_id=agent, provider="codex",
        service_type="agent_session", operation="turn", prompt="private-input")
    projector = AgentDataProjector(store)
    projector.begin(req, RuntimeSession(mode="stateless", binding_id="", backend_session_id="thread-rpc",
                                       is_new_backend_session=False))
    kwargs = dict(provider="codex", backend_route_id="one", backend_session_id="thread-rpc",
                  execution_id=req.execution_id)
    projector.observe(**kwargs, event={"method": "turn/started", "params": {"turn": {"id": "turn-rpc"}}})
    from management_portal.execution.agent_data_projection import stable_segment_id
    receipt_id = stable_segment_id("provider-request", req.execution_id, "thread-rpc", "turn-rpc", "review/start")
    with pytest.raises(KeyError):
        store.operation_record(receipt_id)
    before = store.sequence_segment_ids(store.trace_sequence_id(agent))
    event = {"method": "portal/provider_request_accepted", "params": {
        "threadId": "thread-rpc", "turnId": "turn-rpc", "rpcMethod": "review/start",
        "target": "private-diff", "authorization": "private-token"}}
    projector.observe(**kwargs, event=event)
    original = AgentDataStore().operation_record(receipt_id)
    assert original["execution_id"] == req.execution_id
    assert original["created_by_agent_id"] == agent
    assert original["content"] == {"type": "provider_request_receipt", "provider": "codex",
        "method": "review/start", "backend_session_id": "thread-rpc", "provider_turn_id": "turn-rpc"}
    assert "private-" not in json.dumps(original)
    projector.observe(**kwargs, event=event)
    assert store.operation_record(receipt_id) == original
    assert store.sequence_segment_ids(store.trace_sequence_id(agent)) == before


def transform(store, agent, sources, target="segment_prepared"):
    return store.transform_agent_segments(agent, sources, segment_id=target, content="private-prepared",
        actor_ref="test-actor", operation_id="execution_prepare", operation_type="agent_harness.prepare_invocation")


def test_immediate_input_preparation_is_recorded_atomically_and_read_through_conversation(monkeypatch):
    """输入组装与准备的变换回执原子记录，GetConversation 返回的上下文证据不含正文"""
    from management_portal.society_service import execution_client, resident_workspace_domain
    from management_portal.society_service import resident_conversation_projection_domain
    from management_portal.society_service.isa import conversation

    store, agent = AgentDataStore(), "resident_position_receipt"
    # The pre-click sample cannot contain an input which has not been admitted.
    assert observe(store, agent)["trace"]["positions"]["entries"] == []
    old = store.display_chat_message(agent, {"message_id": "msg_actual_input", "role": "user",
        "content_parts": [{"type": "text", "text": "private-original"}]})["segment_id"]
    req = ExecutionRequest.new(actor="test-actor", agent_id=agent, provider="codex",
        service_type="agent_session", operation="turn", prompt="private-original",
        metadata={"user_message_id": "msg_actual_input"})
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    scheduler.submit(req)
    scheduler.register_worker(worker_id="evidence-worker", build_id="fixture", capabilities=[req.capability], state="standby")
    scheduler.activate_worker("evidence-worker")
    claim = scheduler.claim(worker_id="evidence-worker", capabilities=[req.capability])
    assert claim["claimed"] and claim["execution"]["execution_id"] == req.execution_id
    scheduler.start(req.execution_id, worker_id="evidence-worker", claim_token=claim["claim_token"])
    projector = AgentDataProjector(store)
    projector.begin(req, RuntimeSession(mode="stateless", binding_id="", backend_session_id="",
                                       is_new_backend_session=True))
    projector.observe_invocation(req.execution_id, SimpleNamespace(user_prompt="private-prepared", request=req))
    # There was no polling between either transformation and input admission.
    monkeypatch.setattr(data_module, "get_agent_data_store", lambda: store)
    seen = []
    monkeypatch.setattr(resident_conversation_projection_domain, "can_view_actual_sent_prompts",
                        lambda actor, target: actor == "resident_reader" and target == agent)
    def resolve(target, actor, *, require_visible):
        seen.append((target, actor, require_visible))
        return SimpleNamespace(resident_id=target)
    monkeypatch.setattr(resident_record_domain, "resolve_resident_runtime_target", resolve)
    monkeypatch.setattr(conversation, "protocol", lambda: SimpleNamespace(
        get_resident_conversation_snapshot=lambda *a, **kw: {"messages": []}))
    args = {"resident_id": agent, "include_context_evidence": True}
    result = conversation.get_conversation(SimpleNamespace(caller="resident_reader", argument=args.get))["context_evidence"]
    assert seen == [(agent, "resident_reader", True)]
    receipts = {item["operation_type"]: item for item in result["transformations"]}
    assert set(receipts) == {"provider_input.compose", "agent_harness.prepare_invocation"}
    compose, prepared = receipts["provider_input.compose"], receipts["agent_harness.prepare_invocation"]
    assert compose["sources"][0]["segment_id"] == old
    assert compose["sources"][0]["message_id"] == "msg_actual_input"
    assert compose["sources"][0]["ordinal"] == prepared["sources"][0]["ordinal"] == 1
    assert compose["target_segment_id"] == prepared["sources"][0]["segment_id"]
    assert prepared["sources"][0]["message_id"] == "msg_actual_input"
    assert compose["execution_id"] == prepared["operation_id"] == req.execution_id
    assert result["trace"]["positions"]["entries"][0]["segment_id"] == prepared["target_segment_id"]
    assert "private-original" not in json.dumps(result) and "private-prepared" not in json.dumps(result)
    scheduler.finish(ExecutionResult(execution_id=req.execution_id, status="completed", output=""),
                     worker_id="evidence-worker", claim_token=claim["claim_token"])
    scheduler.acknowledge(req.execution_id, actor=req.actor)
    assert scheduler.snapshot(req.execution_id)["request"] == {}
    assert scheduler.prune_terminal(older_than_epoch=time.time() + 86401) == 1
    assert observe(AgentDataStore(), agent)["transformations"] == result["transformations"]


def test_snapshot_does_not_mix_positions_and_receipts_from_a_concurrent_transform(monkeypatch):
    """上下文证据快照不混入并发变换提交的位置与回执"""
    store, agent = AgentDataStore(), "resident_concurrent_positions"
    ids = [store.put_segment(segment_type="input", content=f"private-{i}") for i in range(3)]
    store.append_agent_segments(agent, ids, actor_ref="test-actor")
    original = agent_data_contexts.agent_trace
    with ThreadPoolExecutor(max_workers=1) as executor:
        def read_then_commit(connection, target):
            trace = original(connection, target)
            executor.submit(transform, store, agent, ids[:2]).result(timeout=10)
            return trace
        with monkeypatch.context() as patch:
            patch.setattr(agent_data_contexts, "agent_trace", read_then_commit)
            before = observe(store, agent)
    after = observe(store, agent)
    assert [(p["ordinal"], p["segment_id"]) for p in before["trace"]["positions"]["entries"]] == list(enumerate(ids, 1))
    assert before["trace"]["segment_count"] == 3 and before["transformations"] == []
    assert [p["ordinal"] for p in after["trace"]["positions"]["entries"]] == [1, 3]
    assert after["trace"]["segment_count"] == 2 and len(after["transformations"]) == 1
    assert before["trace"]["positions"]["sampled_at"] <= after["trace"]["positions"]["sampled_at"]
    assert before["trace"]["positions"]["trace_updated_at"] != after["trace"]["positions"]["trace_updated_at"]


def test_receipt_failure_rolls_back_and_replay_never_rewrites_consumed_positions(monkeypatch):
    """变换回执写入失败时整体回滚，重放不改写已消费位置也不补造旧回执"""
    store, agent = AgentDataStore(), "resident_atomic_positions"
    old = store.put_segment(segment_type="input", content="private-body")
    store.append_agent_segments(agent, [old], actor_ref="test-actor")
    original = agent_data_trace_edits.put_operation
    def fail(connection, **kwargs):
        if kwargs["kind"] == "trace_transformation":
            raise RuntimeError("receipt unavailable")
        return original(connection, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(agent_data_trace_edits, "put_operation", fail)
        with pytest.raises(RuntimeError, match="receipt unavailable"):
            transform(store, agent, [old])
    assert store.sequence_segment_ids(store.trace_sequence_id(agent)) == [old]
    assert observe(store, agent)["transformations"] == []
    with pytest.raises(KeyError):
        store.segment("segment_prepared")
    transform(store, agent, [old])
    receipts = observe(store, agent)["transformations"]
    transform(store, agent, [old])
    assert observe(AgentDataStore(), agent)["transformations"] == receipts
    assert observe(store, "resident_unrelated")["transformations"] == []
    # Simulate a historical transformation predating this feature: do not
    # reconstruct its missing position receipt by replaying its current target.
    with store.database.transaction() as c:
        c.execute("DELETE FROM agent_operations WHERE kind='trace_transformation' AND agent_id=%s", (agent,))
    transform(store, agent, [old])
    assert observe(store, agent)["transformations"] == []


def test_native_identity_is_joined_only_from_the_segments_actual_execution():
    """上下文证据只从片段所属执行关联原生会话与轮次标识，不带出正文"""
    store, agent = AgentDataStore(), "resident_native_evidence"
    session = store.ensure_provider_session(provider="codex", backend_route_id="one",
        backend_session_id="native-session-observed", agent_id=agent, actor_ref="test-actor")
    store.begin_execution(execution_id="execution_native", agent_id=agent, actor_ref="test-actor",
                          provider="codex", started_at="2026-10-16T01:00:00Z")
    store.bind_execution_provider_session("execution_native", provider_session_ref=session["provider_session_ref"])
    store.record_input_acceptance("execution_native", agent_id=agent, provider_turn_id="native-turn-observed")
    known = store.put_segment(segment_type="model", content="private-model", execution_id="execution_native",
        started_at="2026-10-16T01:01:00Z", finished_at="2026-10-16T01:01:02Z", metadata={"private": "private-metadata"})
    unknown = store.put_segment(segment_type="model", content="private-old-model")
    store.append_agent_segments(agent, [known, unknown], actor_ref="test-actor")
    result = observe(store, agent)
    one, two = result["trace"]["positions"]["entries"]
    assert (one["provider"], one["backend_route_id"], one["backend_session_id"], one["provider_turn_id"]) == (
        "codex", "one", "native-session-observed", "native-turn-observed")
    assert one["segment_started_at"] == "2026-10-16T01:01:00Z"
    assert one["segment_finished_at"] == "2026-10-16T01:01:02Z"
    assert two["segment_started_at"] is None and two["segment_finished_at"] is None
    assert two["provider_turn_id"] == two["backend_session_id"] == two["execution_id"] == ""
    assert not any(key in one for key in ("request_id", "metadata", "content"))
    assert "private-" not in json.dumps(result)


def test_evidence_refuses_a_weaker_enclosing_transaction():
    """上下文证据快照拒绝在外层非只读可重复读事务中读取"""
    store = AgentDataStore()
    with store.database.transaction():
        with pytest.raises(RuntimeError, match="read-only repeatable-read"):
            store.context_evidence_snapshot("resident_missing")


def test_bounds_old_server_and_field_allowlists_remain_explicit(monkeypatch):
    """上下文证据按上限截断、按字段白名单输出，旧服务端快照标为不可用"""
    store, agent = AgentDataStore(), "resident_bounded_positions"
    ids = [store.put_segment(segment_type="input", content="private-content") for _ in range(3)]
    store.append_agent_segments(agent, ids, actor_ref="test-actor")
    transform(store, agent, [ids[0]], "segment_first")
    transform(store, agent, [ids[1]], "segment_second")
    monkeypatch.setattr(agent_data_contexts, "CONTEXT_EVIDENCE_POSITION_LIMIT", 1)
    snapshot = store.context_evidence_snapshot(agent)
    snapshot["trace_positions"]["entries"][0]["private_extra"] = "private-extra"
    snapshot["transformations"][0]["private_extra"] = "private-extra"
    result = evidence.project_context_evidence(snapshot)
    assert result["trace"]["positions"]["truncated"] and result["trace"]["segment_count"] == 3
    assert len(result["trace"]["positions"]["entries"]) == len(result["transformations"]) == 1
    assert result["transformations_available"] and result["transformations_truncated"]
    assert "private-" not in json.dumps(result)
    for key in ("trace_positions", "transformations", "transformations_truncated"):
        snapshot.pop(key)
    old_server = evidence.project_context_evidence(snapshot)
    assert old_server["trace"]["positions"] is None and not old_server["transformations_available"]
