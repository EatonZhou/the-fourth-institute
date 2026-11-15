"""Input-authorized, opt-in runtime diagnostics never grants execution browsing."""
from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_input_diagnostics as diagnostics
from management_portal.society_service import resident_protocol
from management_portal.society_service.isa import execution as isa


@pytest.fixture
def boundary(monkeypatch):
    from management_portal.society_service import resident_conversation_stream
    calls = []
    state = {"message_id": "input", "work": {"execution_id": "execution-bound"}}
    monkeypatch.setattr(diagnostics.permission_system, "allows",
                        lambda actor, instruction, args: actor == "reader")
    monkeypatch.setattr(resident_protocol, "get_resident_input_state",
                        lambda rid, actor, mid, **_kwargs: calls.append((rid, actor, mid)) or dict(state))
    monkeypatch.setattr(diagnostics.resident_record_domain, "find_resident_for_admission",
                        lambda rid: {"resident_id": rid})
    monkeypatch.setattr(diagnostics.family_facade, "is_facade", lambda record: False)
    monkeypatch.setattr(diagnostics.resident_input_scope, "permits", lambda *args: False)
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: SimpleNamespace(
        input_state=lambda *args: {"work": {"state": "unknown"}}))
    return state, calls


def resolve(**kwargs):
    return diagnostics.resolve_input_execution(actor="reader", resident_id="resident", message_id="input", **kwargs)


def test_only_current_authorized_input_can_resolve_execution(boundary):
    state, calls = boundary
    state["prompt"] = "private content must not cross the identity port"
    assert resolve() == {
        "available": True, "resident_id": "resident", "message_id": "input",
        "execution_input_id": "input", "execution_id": "execution-bound",
    }
    assert calls == [("resident", "reader", "input")]
    with pytest.raises(PermissionError):
        diagnostics.resolve_input_execution(actor="other", resident_id="resident", message_id="input")
    assert len(calls) == 1, "Denied callers must not read the input"
    with pytest.raises(TypeError):
        resolve(execution_id="unrelated-execution")


@pytest.mark.parametrize("error", [PermissionError("isolated scope"), KeyError("unknown input")])
def test_existing_scope_and_visibility_rejections_propagate(boundary, monkeypatch, error):
    def reject(*args):
        raise error
    monkeypatch.setattr(resident_protocol, "get_resident_input_state", reject)
    with pytest.raises(type(error)):
        resolve()


@pytest.mark.parametrize("record", [None, {"deleted_at": "yesterday"}])
def test_deleted_sender_receipt_does_not_expand_to_runtime_audit(boundary, monkeypatch, record):
    monkeypatch.setattr(diagnostics.resident_record_domain, "find_resident_for_admission", lambda rid: record)
    assert resolve() == {"available": False, "reason": "resident_unavailable"}


def test_no_execution_association_is_not_evidence_of_no_execution(boundary):
    state, _ = boundary
    state["work"] = {"state": "unknown"}
    assert resolve() == {"available": False, "reason": "execution_not_associated"}


def test_family_resolves_current_persisted_holder_not_stale_facade_work(boundary, monkeypatch):
    checks = []
    monkeypatch.setattr(diagnostics.family_facade, "is_facade", lambda record: True)
    monkeypatch.setattr(diagnostics.family_facade, "relay_holder",
                        lambda rid, mid: ([rid], ("member", "member-input")))
    monkeypatch.setattr(diagnostics.resident_input_scope, "permits",
                        lambda *args: checks.append(args) or False)
    monkeypatch.setattr(resident_protocol, "_stored_resident_input_state", lambda rid, mid: {
        "grouped_into_message_id": "group", "work": {"execution_id": "execution-member"},
    })
    assert resolve() == {
        "available": True, "resident_id": "member", "message_id": "member-input",
        "execution_input_id": "group", "execution_id": "execution-member",
    }
    assert checks == [("member", "reader", "member-input"), ("member", "reader", "group")]


def test_group_cannot_override_another_callers_input_scope(boundary, monkeypatch):
    boundary[0]["grouped_into_message_id"] = "group"
    def reject(*args):
        raise PermissionError("other caller")
    monkeypatch.setattr(diagnostics.resident_input_scope, "permits", reject)
    with pytest.raises(PermissionError):
        resolve()


def test_input_retaining_own_execution_is_not_rebound_to_later_group(boundary, monkeypatch):
    from management_portal.society_service import resident_conversation_stream
    boundary[0]["grouped_into_message_id"] = "later-group"
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: SimpleNamespace(
        input_state=lambda *args: {"work": {"execution_id": "execution-bound"}}))
    result = resolve()
    assert result["execution_input_id"] == "input"
    assert result["execution_id"] == "execution-bound"


@pytest.mark.parametrize("opt_in", [None, False, True])
def test_isa_opt_in_uses_current_caller_and_no_supplied_execution(boundary, monkeypatch, opt_in):
    calls = []
    values = {"resident_id": "resident", "message_id": "input", "include_execution_diagnostics": opt_in}
    inv = SimpleNamespace(caller="reader", argument=lambda key: values.get(key))
    monkeypatch.setattr(isa, "protocol", lambda: SimpleNamespace(
        get_resident_input_state=lambda *args, **kwargs: {"state": "running"}))
    monkeypatch.setattr(diagnostics, "read_input_diagnostics",
                        lambda **kwargs: calls.append(kwargs) or {"status": "running"})
    result = isa.get_input_state(inv)["input_state"]
    if opt_in is True:
        assert result["execution_diagnostics"] == {"status": "running"}
        assert calls == [{"actor": "reader", "resident_id": "resident", "message_id": "input"}]
    else:
        assert calls == []
        assert result == {"state": "running"}


def test_owner_port_does_not_reuse_original_execution_actor(boundary, monkeypatch):
    from management_portal.society_service import execution_client
    calls = []
    monkeypatch.setattr(execution_client, "service", lambda: SimpleNamespace(
        input_diagnostics=lambda **kwargs: calls.append(kwargs) or {"available": True}))
    diagnostics.read_input_diagnostics(actor="reader", resident_id="resident", message_id="input")
    assert calls == [{"actor": "reader", "resident_id": "resident", "message_id": "input"}]


def test_isa_to_execution_reauthorizes_and_only_returns_associated_facts(boundary, monkeypatch):
    from management_portal.execution.service_rpc import execution_input_diagnostics
    from management_portal.society_service import execution_client
    from unittest.mock import Mock

    server = SimpleNamespace(
        agent_data=SimpleNamespace(execution=Mock(return_value={
            "agent_id": "resident", "actor_ref": "original-execution-actor",
            "inputs": [{"message_id": "input"}], "prompt": "do not disclose",
        })),
        scheduler=SimpleNamespace(input_diagnostics=Mock(return_value={
            "source": "scheduler", "status": "running", "attempts": 1,
        })),
    )
    monkeypatch.setattr(execution_client, "service", lambda: SimpleNamespace(
        input_diagnostics=lambda **params: execution_input_diagnostics(server, params)))
    monkeypatch.setattr(isa, "protocol", lambda: resident_protocol)
    values = {"resident_id": "resident", "message_id": "input", "include_execution_diagnostics": True}
    inv = SimpleNamespace(caller="reader", argument=lambda key: values.get(key))
    result = isa.get_input_state(inv)["input_state"]["execution_diagnostics"]
    assert result == {
        "schema": "tfi.execution-input-diagnostics.v1", "available": True,
        "resident_id": "resident", "message_id": "input", "execution_id": "execution-bound",
        "source": "scheduler", "status": "running", "attempts": 1,
        "created_at": "unknown", "started_at": "unknown", "finished_at": "unknown",
    }
    assert boundary[1] == [("resident", "reader", "input")] * 2
    server.agent_data.execution.assert_called_once_with("execution-bound")
