"""Registration is deterministic; only an accepted user input starts inference.

:design: decision_ccbb1cac24aa
Real isolated PostgreSQL/AgentData and domain owners; native Provider I/O is fake.
"""
from unittest.mock import Mock
import json

import pytest

from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.execution.agent_data_session_capture import persist_provider_compaction
from management_portal.execution.service import ExecutionService
from management_portal.society_service import (
    lineage_resident_nodes,
    resident_backend,
    resident_conversation_stream,
    resident_domain,
    resident_execution_domain,
    resident_execution_work,
    resident_initialization,
    resident_intake_domain,
    resident_record_domain,
    resident_session_backend_domain,
)
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_resident_folder_creation_contract import (
    _activate_created_resident,
    _new_session_payload,
    future_creation_store,  # noqa: F401
)
from tests.cases.test_resident_fork_context_delivery import _record_provider_turn


@pytest.fixture
def native_operations(monkeypatch):
    def create(**options):
        assert options["operation"] == "create"
        execution_port.sessions().bind_session(
            options["binding_id"], provider=options["backend"],
            backend_route_id=options["backend_route_id"],
            backend_session_id="01a00000-0000-7000-8000-000000000907",
        )
        return {"backend_session_id": "01a00000-0000-7000-8000-000000000907"}

    operations = Mock(side_effect=create)
    monkeypatch.setattr(resident_session_backend_domain, "run_backend_session_operation", operations)
    return operations


def assert_unstarted(record):
    rid = record["resident_id"]
    assert not resident_backend.record_session_id(record)
    assert not resident_execution_work.read(record)
    assert get_agent_data_store().read_chat_messages(rid) == []
    assert get_agent_data_store().trace_length(rid) == 0
    assert resident_initialization.view(rid, "sample_admin")["status"] == "not_required"


@pytest.mark.parametrize("backend", ["codex", "claude", "kimi"])
@pytest.mark.parametrize("member", [False, True])
def test_registration_and_retry_do_not_start_a_model_turn(
    future_creation_store, monkeypatch, native_operations, backend, member,
):
    submit = Mock(side_effect=AssertionError("registration submitted a model turn"))
    monkeypatch.setattr(ExecutionService, "submit", submit)
    payload = {**_new_session_payload("No hidden initialization"), "backend": backend}
    if backend == "claude":
        payload["backend_route_id"] = "claude_one"
    if member:
        parent = resident_domain.register_resident(_new_session_payload("Parent"), "sample_admin")
        created = lineage_resident_nodes.create_lineage_resident(parent["lineage_id"], payload, "sample_admin")["resident"]
    else:
        created = resident_domain.register_resident(payload, "sample_admin")
    assert_unstarted(created)
    assert resident_initialization.retry(created["resident_id"], "sample_admin")["status"] == "not_required"
    assert_unstarted(resident_record_domain.find_resident(created["resident_id"]))
    assert native_operations.call_count == submit.call_count == 0


@pytest.mark.parametrize("context_mode", ["full", "5", "10"])
def test_registration_with_source_defers_native_creation_and_keeps_context(
    future_creation_store, native_operations, context_mode,
):
    source = resident_domain.register_resident(_new_session_payload("Source"), "sample_admin")
    source = _activate_created_resident(source["resident_id"])
    store = get_agent_data_store()
    _record_provider_turn(store, source, 0, question="Original facts", answer="Original decision")
    created = resident_domain.register_resident({
        **_new_session_payload("Context registration"),
        "source_resident_id": source["resident_id"],
        "initialize_from_source": True,
        "source_context_mode": context_mode,
    }, "sample_admin")
    assert native_operations.call_count == 0
    assert not resident_backend.record_session_id(created)
    assert created["session_state"] == "pending_create"
    assert not resident_execution_work.read(created)
    assert store.read_chat_messages(created["resident_id"]) == []
    context = store.agent_context_snapshot(created["resident_id"])
    assert [s["content"] for s in context["segments"]] == ["Original facts", "Original decision"]
    assert resident_initialization.view(created["resident_id"], "sample_admin")["status"] == "not_required"


@pytest.mark.parametrize("with_source", [False, True])
def test_first_real_input_is_the_only_model_request_and_remains_auditable(
    future_creation_store, monkeypatch, native_operations, with_source,
):
    payload = _new_session_payload("First real input")
    if with_source:
        source = resident_domain.register_resident(_new_session_payload("Source"), "sample_admin")
        source = _activate_created_resident(source["resident_id"])
        _record_provider_turn(get_agent_data_store(), source, 0)
        payload.update(source_resident_id=source["resident_id"], initialize_from_source=True)
    record = resident_domain.register_resident(payload, "sample_admin")
    rid = record["resident_id"]
    assert not resident_backend.record_session_id(record)
    assert native_operations.call_count == 0
    mid = "msg_" + "c8" * 16
    resident_intake_domain.persist_resident_input(rid, "sample_admin", "Implement the visible task", message_id=mid)
    job = resident_execution_work.read(record)
    assert job["user_message_id"] == mid
    assert job["raw_message"] == "Implement the visible task"
    assert job.get("operation") != "context_fork_create"
    assert not resident_backend.record_session_id(record)
    # The real delivery projector moves an accepted Mailbox input into trace
    # before a turn builds its context boundary; admission alone does not.
    resident_execution_work.materialize_conversation_input(record, job)
    calls = []

    def submit(_service, request):
        calls.append(request)
        assert "Implement the visible task" in request.prompt
        assert not resident_backend.record_session_id(record)
        execution_port.sessions().bind_session(
            request.session.binding_id, provider=request.provider, backend_route_id="zero",
            backend_session_id="01a00000-0000-7000-8000-000000000909",
        )
        store = get_agent_data_store()
        store.begin_execution(execution_id=request.execution_id, agent_id=rid,
                              actor_ref=request.actor, provider=request.provider,
                              started_at="2026-10-27T14:59:00Z")
        store.bind_execution_provider_session(request.execution_id,
            provider_session_ref=resident_backend.session_record(record)["provider_session_ref"])
        store.finish_execution(request.execution_id, status="completed")
        return {"execution_id": request.execution_id}

    def wait(_service, execution_id, **options):
        assert execution_id == calls[0].execution_id
        return {"execution_id": execution_id, "status": "completed",
                "scheduler": {"updated_at": "2026-10-27T15:00:00Z"},
                "result": {"status": "completed", "backend_route_id": "zero",
                           "output": {"message": "The visible task is complete."}}}

    monkeypatch.setattr(ExecutionService, "submit", submit)
    monkeypatch.setattr(ExecutionService, "wait", wait)
    assistant, updated = resident_execution_domain.send_to_resident_backend(record, job)
    assert len(calls) == 1
    assert calls[0].operation == "turn"
    assert updated["session_state"] == "active"
    assert assistant["reply_to_message_id"] == mid
    assert resident_backend.record_session_id(updated) == "01a00000-0000-7000-8000-000000000909"
    repository = resident_conversation_stream.get_repository()
    assert repository.message(rid, mid)["content_parts"] == [{"type": "text", "text": "Implement the visible task"}]
    assert repository.input_state(rid, mid)["state"] == "completed"
    assert [m["message_id"] for m in repository.read_messages(rid) if m["role"] == "user"] == [mid]
    assert bool(calls[0].metadata.get("fork_context_input")) == with_source


@pytest.mark.parametrize("boundary", ["clear", "compact"])
def test_source_registration_freezes_effective_context_and_preserves_source_history(
    future_creation_store, native_operations, boundary,
):
    source = resident_domain.register_resident(_new_session_payload("Boundary source"), "sample_admin")
    source = _activate_created_resident(source["resident_id"])
    rid = source["resident_id"]
    store = get_agent_data_store()
    _record_provider_turn(store, source, 0, question="obsolete question", answer="obsolete answer")
    compact_content = {"replacement_history": [{
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": "preserved summary"}],
    }]}
    if boundary == "clear":
        store.record_context_reset(rid, reset_at="2026-09-08T00:00:30Z", actor_ref="sample_admin",
                                   trace_position=store.trace_length(rid))
    else:
        eid = "execution-registration-compaction"
        session = resident_backend.session_record(source)
        store.begin_execution(execution_id=eid, agent_id=rid, actor_ref="sample_admin", provider="codex",
                              started_at="2026-09-08T00:00:30Z")
        store.bind_execution_provider_session(eid, provider_session_ref=session["provider_session_ref"])
        persist_provider_compaction(store, provider_session_ref=session["provider_session_ref"],
            agent_id=rid, operation_id=eid, actor_ref="sample_admin", provider="codex",
            content=compact_content, created_at="2026-09-08T00:00:30Z")
        store.finish_execution(eid, status="completed")
    _record_provider_turn(store, source, 1, question="current question", answer="current answer")
    source_count = store.trace_length(rid)
    source_context = store.agent_context_snapshot(rid)
    source_session = resident_backend.session_record(source)
    child = resident_domain.register_resident({
        **_new_session_payload("Boundary child"), "source_resident_id": rid, "initialize_from_source": True,
    }, "sample_admin")
    context = store.agent_context_snapshot(child["resident_id"])
    expected = ([compact_content] if boundary == "compact" else []) + ["current question", "current answer"]
    assert [s["content"] for s in context["segments"]] == expected
    assert store.trace_length(rid) == source_count
    assert store.agent_context_snapshot(rid) == source_context
    assert resident_backend.session_record(source) == source_session
    _record_provider_turn(store, source, 2, question="later source question", answer="later source answer")
    assert store.agent_context_snapshot(child["resident_id"]) == context
    prompt = resident_execution_domain._portable_context_execution(child, {"actor": "sample_admin", "message": "Real next task"})
    delivered = json.dumps(prompt["fork_context_input"], ensure_ascii=False)
    assert "current question" in delivered and "current answer" in delivered
    assert "Real next task" in delivered
    assert "obsolete" not in delivered and "later source" not in delivered
    assert ("preserved summary" in delivered) == (boundary == "compact")
    assert native_operations.call_count == 0
