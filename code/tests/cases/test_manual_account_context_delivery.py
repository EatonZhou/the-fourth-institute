"""Manual account handoff is material, never an extra preparation model turn."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_execution_domain as domain


@pytest.fixture
def delivery(monkeypatch):
    record = {
        "resident_id": "resident_123456789abc", "backend": "claude", "backend_route_id": "claude_one",
        "session_restart": {"restart_id": "restart_one", "pending_handoff": {
            "operation_id": "restart_one", "strategy": "compact", "context_reset_at": "",
            "target": {"provider": "claude", "route": "claude_one"}, "prompt": "frozen bounded history",
        }},
    }
    observation = SimpleNamespace(accepted=False, binding={}, prepared=0)
    monkeypatch.setattr(domain.resident_input_scope, "get", lambda *args: None)
    monkeypatch.setattr(domain, "_lineage_delivery_profile", lambda job: {})
    monkeypatch.setattr(domain, "_requires_initial_context_compaction", lambda record: False)
    monkeypatch.setattr(domain, "_initial_context_compaction_completed", lambda record: False)
    monkeypatch.setattr(domain.resident_backend, "session_record", lambda record: observation.binding)
    monkeypatch.setattr(domain.resident_backend, "execution_backend", lambda record: record["backend"])
    monkeypatch.setattr(domain.execution_port, "sessions", lambda: SimpleNamespace(
        has_context=lambda *args, **kwargs: observation.accepted))

    def prepare(record, job):
        observation.prepared += 1
        return {**job, "actual_sent_prompt": "actual user input", "prompt_builder_version": 1}

    monkeypatch.setattr(domain, "_prepare_resident_prompt", prepare)
    def no_trace(*args, **kwargs):
        raise AssertionError("must not rebuild the full trace or submit a preparation model")
    monkeypatch.setattr(domain.execution_port, "agent_context_snapshot", no_trace)
    monkeypatch.setattr(domain.resident_session_backend_domain, "run_backend_session_operation", no_trace)
    return record, observation


def record_input(resident_id, message_id):
    from management_portal.execution.agent_data_store import get_agent_data_store
    get_agent_data_store().display_chat_message(resident_id, {
        "message_id": message_id, "role": "user", "content_parts": [{"type": "text", "text": "actual user input"}],
    })


@pytest.mark.parametrize("strategy", ["compact", "visible"])
def test_first_input_and_failed_delivery_retain_exact_material(delivery, strategy):
    record, seen = delivery
    record["session_restart"]["pending_handoff"]["strategy"] = strategy
    from management_portal.execution.agent_data_store import get_agent_data_store
    store = get_agent_data_store()
    sid = store.put_segment(segment_type="model", content="retained model response")
    store.append_agent_segments(record["resident_id"], [sid], actor_ref="test")
    original = deepcopy(record)
    job = {"user_message_id": "msg_first", "fork_context_input": {"prompt": "stale"}}
    record_input(record["resident_id"], job["user_message_id"])
    first = domain._portable_context_execution(record, job)
    seed = first["fork_context_input"]
    assert [s["segment_id"] for s in seed["segments"]] == [sid]
    assert seed["prompt"] == "actual user input"
    assert "portable_prompt" not in seed
    # A failed submission with no acceptance retries the identical seed.
    assert domain._portable_context_execution(record, job)["fork_context_input"] == seed
    assert record == original and job["fork_context_input"] == {"prompt": "stale"}


@pytest.mark.parametrize("isolation", ["fresh", "scoped"])
def test_independent_inputs_do_not_consume_session_material(delivery, monkeypatch, isolation):
    record, seen = delivery
    if isolation == "fresh":
        monkeypatch.setattr(domain, "_lineage_delivery_profile", lambda job: {"isolated": True})
    else:
        monkeypatch.setattr(domain.resident_input_scope, "get", lambda *args: object())
    job = {"user_message_id": "independent"}
    assert domain._portable_context_execution(record, job) is job
    assert seen.prepared == 0
    assert record["session_restart"]["pending_handoff"]["prompt"] == "frozen bounded history"


@pytest.mark.parametrize("change", ["operation", "provider", "route", "missing_reset", "binding", "strategy"])
def test_stale_material_is_rejected_even_when_a_binding_has_context(delivery, change):
    record, seen = delivery
    seen.accepted = True
    if change == "operation":
        record["session_restart"]["restart_id"] = "restart_later"
    elif change == "provider":
        record["backend"] = "codex"
    elif change == "route":
        record["backend_route_id"] = "claude_two"
    elif change == "missing_reset":
        record["session_restart"]["pending_handoff"].pop("context_reset_at")
    elif change == "strategy":
        record["session_restart"]["pending_handoff"]["strategy"] = "fresh"
    else:
        seen.binding = {"provider": "codex", "backend_route_id": "one"}
    from management_portal.society_service.resident_pending_session_context import validate_handoff
    with pytest.raises(ValueError, match="Pending session handoff"):
        validate_handoff(record)


def test_explicit_clear_discards_handoff_without_blocking_new_input(delivery):
    record, seen = delivery
    record["context_reset_at"] = "2026-11-18T10:00:00Z"
    record_input(record["resident_id"], "msg_new")
    prepared = domain._portable_context_execution(record, {"user_message_id": "msg_new"})
    assert "fork_context_input" not in prepared
    assert "frozen bounded history" not in prepared["actual_sent_prompt"]


def test_real_binding_acceptance_stops_injection_but_empty_session_does_not(delivery, monkeypatch):
    from management_portal.execution.agent_data_store import AgentDataStore

    record, seen = delivery
    store = AgentDataStore()
    rid = record["resident_id"]
    binding_id = domain.resident_backend.execution_binding_id(record)
    session = store.ensure_provider_session(
        provider="claude", backend_route_id="claude_one", backend_session_id="test-native-session",
        agent_id=rid, actor_ref="test",
    )
    store.bind_provider_session(binding_id, session["provider_session_ref"])
    # Use the actual persisted has_context observer, not a bool fake.
    monkeypatch.setattr(domain.execution_port, "sessions", lambda: SimpleNamespace(
        has_context=lambda binding, **kwargs: store.binding_has_context(binding, **kwargs)))
    seen.binding = store.provider_binding(binding_id)
    sid = store.put_segment(segment_type="model", content="source material")
    store.append_agent_segments(rid, [sid], actor_ref="test")
    record_input(rid, "msg_pg")
    job = {"user_message_id": "msg_pg"}
    assert "fork_context_input" in domain._portable_context_execution(record, job)
    store.begin_execution(execution_id="execution-pending-context-test", agent_id=rid,
                          actor_ref="test", provider="claude", started_at="2026-11-18T10:00:00Z")
    store.bind_execution_provider_session("execution-pending-context-test", provider_session_ref=session["provider_session_ref"])
    store.record_input_acceptance("execution-pending-context-test", agent_id=rid, provider_turn_id="test-turn")
    store.record_context_received(rid, session["provider_session_ref"], [sid], actor_ref="test")
    assert "fork_context_input" not in domain._portable_context_execution(record, job)
    # Another account's empty session cannot inherit acceptance of this one.
    other = store.ensure_provider_session(
        provider="claude", backend_route_id="claude_one", backend_session_id="another-session",
        agent_id=rid, actor_ref="test",
    )
    store.bind_provider_session("session-restart:test-context", other["provider_session_ref"])
    store.replace_provider_binding(binding_id, "session-restart:test-context",
                                   expected_provider="claude", expected_backend_session_id="test-native-session")
    seen.binding = store.provider_binding(binding_id)
    assert "fork_context_input" in domain._portable_context_execution(record, job)


@pytest.mark.parametrize("adapter", ["codex", "claude"])
def test_legacy_text_handoff_requires_database_references(adapter):
    from management_portal.execution.context_seed import prepare_backend_context_seed
    from management_portal.execution.backend_services import AgentSessionService

    invocation = SimpleNamespace(request=SimpleNamespace(metadata={"fork_context_input": {
        "segments": [], "prompt": "new input", "portable_prompt": "legacy history and new input"}}))
    with pytest.raises(ValueError, match="Segment references"):
        prepare_backend_context_seed(AgentSessionService().providers[adapter], invocation)


def test_typed_history_still_uses_canonical_adapter():
    from management_portal.execution.backend_contracts import BackendInvocation
    from management_portal.execution.codex_backend import CodexBackend
    from management_portal.execution.context_seed import prepare_backend_context_seed

    request = SimpleNamespace(metadata={"fork_context_input": {
        "segments": [{"segment_id": "segment_retained"}], "prompt": "user",
        "portable_prompt": "fallback typed history user",
    }})
    invocation = BackendInvocation(request=request, session=None, user_prompt="user", system_context="",
                                   attach_system_context=False, is_new_backend_session=True)
    assert prepare_backend_context_seed(CodexBackend.__new__(CodexBackend), invocation) is invocation
