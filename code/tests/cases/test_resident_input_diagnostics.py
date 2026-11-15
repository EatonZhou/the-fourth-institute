"""Input-authorized, opt-in runtime diagnostics never grants execution browsing."""
from types import SimpleNamespace
from unittest.mock import Mock

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
    from management_portal.society_service import execution_client, state_store
    from unittest.mock import Mock

    server = SimpleNamespace(
        agent_data=SimpleNamespace(input_diagnostics=Mock(return_value={
            "execution_submission": {"recorded": True},
            "provider_link": {"session_ref_recorded": False, "turn_id_recorded": False},
        })),
        scheduler=SimpleNamespace(input_diagnostics=Mock(return_value={
            "source": "scheduler", "status": "running", "attempts": 1,
        })),
    )
    monkeypatch.setattr(execution_client, "service", lambda: SimpleNamespace(
        input_diagnostics=lambda **params: execution_input_diagnostics(server, params)))
    submission = Mock(return_value={"current_job_matches": False})
    monkeypatch.setattr(state_store, "get_lineage_data_store", lambda: SimpleNamespace(
        executions=SimpleNamespace(input_execution_diagnostics=submission)))
    monkeypatch.setattr(isa, "protocol", lambda: resident_protocol)
    values = {"resident_id": "resident", "message_id": "input", "include_execution_diagnostics": True}
    inv = SimpleNamespace(caller="reader", argument=lambda key: values.get(key))
    result = isa.get_input_state(inv)["input_state"]["execution_diagnostics"]
    assert result == {
        "schema": "tfi.execution-input-diagnostics.v1", "available": True,
        "resident_id": "resident", "message_id": "input", "execution_id": "execution-bound",
        "source": "scheduler", "status": "running", "attempts": 1,
        "created_at": "unknown", "started_at": "unknown", "finished_at": "unknown",
        "submission": {"current_job_matches": False},
        "execution_submission": {"recorded": True},
        "provider_link": {"session_ref_recorded": False, "turn_id_recorded": False},
    }
    assert boundary[1] == [("resident", "reader", "input")] * 2
    server.agent_data.input_diagnostics.assert_called_once_with("execution-bound", agent_id="resident", message_id="input")
    submission.assert_called_once_with("resident", "input", "execution-bound")


def test_submission_receipts_are_exact_read_only_and_redacted(tmp_path, monkeypatch):
    from tests.cases.test_conversation_projection_authority import RESIDENT, _repository
    from management_portal.society_service import resident_execution_storage

    store = _repository(tmp_path).state_store
    store.put_resident_execution_record(RESIDENT, {
        "execution_id": "execution-one", "user_message_id": "input-one", "actor": "sample_admin",
        "submitted_at": "2026-11-14T17:49:08Z", "updated_at": "private malformed value",
        "execution_request": {"execution_id": "execution-one", "prompt": "secret prompt"},
        "queue": [{"user_message_id": "next", "message": "secret next input"}],
        "execution_acknowledged": False,
    })
    revision = store.resident_execution_revision(RESIDENT)
    monkeypatch.setattr(resident_execution_storage, "read",
                        lambda *a, **kw: pytest.fail("diagnostics loaded preparation/queue"))
    result = store.executions.input_execution_diagnostics(RESIDENT, "input-one", "execution-one")
    assert result == {
        "current_job_matches": True, "submitted_at": "2026-11-14T17:49:08+00:00",
        "dispatched_at": "unknown", "started_at": "unknown", "finished_at": "unknown",
        "updated_at": "unknown", "completion_applied_at": "unknown", "outcome": "unknown",
        "terminal_status": "unknown", "acknowledged": False, "stop_requested": "unknown",
        "request_persisted": True, "request_matches_execution": True,
        "registration_ready": True, "intake_open": "unknown", "intake_admitted": "unknown",
        "lifecycle_pending": False, "activation_policy": "default", "lifecycle_step": "none",
    }
    assert store.resident_execution_revision(RESIDENT) == revision
    for rid, mid, eid in [(RESIDENT, "other-input", "execution-one"),
                          (RESIDENT, "input-one", "other-execution"),
                          ("other-resident", "input-one", "execution-one")]:
        assert store.executions.input_execution_diagnostics(rid, mid, eid) == {"current_job_matches": False}


def test_submission_missing_request_is_unknown_not_proof_of_no_provider_send(tmp_path):
    from tests.cases.test_conversation_projection_authority import RESIDENT, _repository

    store = _repository(tmp_path).state_store
    store.put_resident_execution_record(RESIDENT, {
        "execution_id": "execution-preparing", "user_message_id": "input-one", "actor": "sample_admin",
    })
    result = store.executions.input_execution_diagnostics(RESIDENT, "input-one", "execution-preparing")
    assert result["current_job_matches"] is True
    assert result["submitted_at"] == result["request_persisted"] == result["request_matches_execution"] == "unknown"


def test_submission_exposes_only_recovery_gates_not_lifecycle_payload(tmp_path):
    from tests.cases.test_conversation_projection_authority import RESIDENT, _repository
    from management_portal.society_service.resident_lifecycle import ResidentLifecycleOperations

    store = _repository(tmp_path).state_store
    store.put_resident_execution_record(RESIDENT, {
        "execution_id": "execution-one", "user_message_id": "input-one", "actor": "sample_admin",
        "intake_admitted_at": "2026-11-14T17:49:08Z", "activation_policy": "private-invalid-value",
    })
    ResidentLifecycleOperations(store).enqueue(RESIDENT, [{"kind": "session_restart", "private": "secret"}])
    store.database.write(lambda c: c.execute("UPDATE residents SET intake_open=false WHERE resident_id=%s", (RESIDENT,)))
    result = store.executions.input_execution_diagnostics(RESIDENT, "input-one", "execution-one")
    assert result["lifecycle_pending"] is True
    assert result["lifecycle_step"] == "session_restart"
    assert result["intake_open"] is False and result["intake_admitted"] is True
    assert result["activation_policy"] == "unsupported"
    assert "secret" not in str(result) and "private-invalid-value" not in str(result)


@pytest.mark.parametrize("source,expected", [("codex_old", False), ("antigravity_current", True), ("", "unknown")])
def test_reserve_diagnostics_exposes_binding_mismatch_without_migration_body(monkeypatch, source, expected):
    from management_portal.society_service import family_quota, resident_default_backend

    migration = {"status": "unavailable", "epoch": source + ":private-window" if source else "",
                 "pending_restart": {"secret": "private restart"}, "reason": "private reason"}
    monkeypatch.setattr(family_quota, "role_migration", lambda _: migration)
    monkeypatch.setattr(family_quota, "auto_migration_enabled", lambda _: True)
    monkeypatch.setattr(family_quota, "override_enabled", lambda _: False)
    monkeypatch.setattr(resident_default_backend, "selection_mode", lambda _: "fixed")
    monkeypatch.setattr(family_quota.execution_port, "service", lambda: SimpleNamespace(accounts=lambda: {"accounts": []}))
    monkeypatch.setattr(family_quota, "resident_account", lambda *_: {
        "account_id": "antigravity_current", "quota_reserve": {"fresh": False}})
    result = family_quota.admission_diagnostics({})
    assert result == {
        "auto_migration_enabled": True, "selection_mode": "fixed", "migration_status": "unavailable",
        "migration_pending_restart": True, "account_wait": False, "source_matches_current_account": expected,
        "account_found": True, "reserve_remaining": "unknown", "quota_fresh": False, "override_enabled": False,
    }
    assert "private" not in str(result) and "codex_old" not in str(result)
    assert migration["pending_restart"] == {"secret": "private restart"}


def test_reserve_diagnostics_account_read_failure_stays_unknown(monkeypatch):
    from management_portal.society_service import family_quota, resident_default_backend

    monkeypatch.setattr(family_quota, "role_migration", lambda _: {})
    monkeypatch.setattr(family_quota, "auto_migration_enabled", lambda _: True)
    monkeypatch.setattr(family_quota, "override_enabled", lambda _: False)
    monkeypatch.setattr(resident_default_backend, "selection_mode", lambda _: "fixed")
    monkeypatch.setattr(family_quota.execution_port, "service", Mock(side_effect=RuntimeError("private transport")))
    result = family_quota.admission_diagnostics({})
    assert result["account_found"] == result["reserve_remaining"] == "unknown"
    assert result["migration_status"] == "none"
    assert "private" not in str(result)


def test_current_input_diagnostics_reads_reserve_gate_without_starting_work(boundary, monkeypatch):
    from management_portal.society_service import execution_client, family_quota, resident_execution_work, state_store

    monkeypatch.setattr(execution_client, "service", lambda: SimpleNamespace(input_diagnostics=lambda **_: {
        "available": True, "resident_id": "resident", "message_id": "input", "execution_id": "execution-bound"}))
    monkeypatch.setattr(state_store, "get_lineage_data_store", lambda: SimpleNamespace(executions=SimpleNamespace(
        input_execution_diagnostics=lambda *_: {"current_job_matches": True})))
    record = {"resident_id": "resident"}
    monkeypatch.setattr(diagnostics.resident_record_domain, "find_resident", lambda _: record)
    read = Mock(return_value={"migration_status": "waiting"})
    monkeypatch.setattr(family_quota, "admission_diagnostics", read)
    monkeypatch.setattr(family_quota, "ensure_role_reserve", Mock(side_effect=AssertionError("must not advance")))
    monkeypatch.setattr(resident_execution_work, "continuation_running", lambda _: False)
    result = diagnostics.read_input_diagnostics(actor="reader", resident_id="resident", message_id="input")
    assert result["reserve_admission"] == {"migration_status": "waiting"}
    assert result["continuation_running"] is False
    read.assert_called_once_with(record)
