"""Formal settings, durable input and actual tool execution use one intent."""
import pytest

from management_portal.execution import resident_capability_assembly as assembly
from management_portal.execution.harness import HarnessTool
from management_portal.society_service import resident_execution_domain as execution
from management_portal.society_service import resident_execution_work, resident_runtime_domain
from management_portal.society_service.errors import LineageConflictError
from management_portal.society_service.isa import residents
from tests.cases.test_prompt_injection import _invocation
from tests.cases.test_resident_execution_protocol import resident_record  # noqa: F401
from tests.cases.test_resident_capability_assembly import Context, Invocation, Request


def test_isa_settings_cas_and_disable_reaches_an_already_assembled_handler(resident_record, monkeypatch):
    rid = resident_record["resident_id"]
    read = residents.get_resident_settings(_invocation("GetResidentSettings", resident_id=rid))
    calls = []
    native = Invocation(request=Request(metadata={"resident_id": rid}, runtime_options={"runtime_settings": read["settings"]}),
                        harness_tools=(HarnessTool("hatg_instruction", "ISA", {"type": "object"}, lambda args: calls.append(args)),))
    prepared = assembly.prepare(native, Context())
    monkeypatch.setattr(assembly, "current_selection", lambda *_: residents.get_resident_settings(
        _invocation("GetResidentSettings", resident_id=rid))["settings"].get("capability_config", {}))
    change = {"capability_config": {"schema": "tfi.resident-capabilities.v1", "tools": [{"ref": "portal:hatg_instruction", "enabled": False}]}}
    saved = residents.update_resident_settings(_invocation("UpdateResidentSettings", resident_id=rid,
        expected_revision=read["settings_revision"], changes=change))
    assert saved["application"]["settings_revision"] == saved["settings_revision"]
    with pytest.raises(PermissionError):
        prepared.harness_tools[0].handler({})
    with pytest.raises(LineageConflictError):
        residents.update_resident_settings(_invocation("UpdateResidentSettings", resident_id=rid,
            expected_revision=read["settings_revision"], changes=change))
    assert not calls


def test_saved_request_carries_one_revision_and_prompt_provenance_after_settings_change(resident_record):
    rid = resident_record["resident_id"]
    job = {"execution_id": "execution-" + "7" * 24, "actor": "CASE-A", "message": "hello",
           "actual_sent_prompt": "hello", "prompt_cwd": str(resident_record["default_cwd"]), "queue": []}
    resident_execution_work.write(resident_record, job)
    first = execution.durable_resident_execution_request(resident_record, job,
        lambda: execution._build_resident_execution_request(resident_record, job))
    snapshot = first.runtime_options["resident_assembly"]
    current = residents.get_resident_settings(_invocation("GetResidentSettings", resident_id=rid))
    assert snapshot["settings_revision"] == current["settings_revision"]
    assert first.runtime_options["runtime_settings"] == current["settings"]
    assert snapshot["prompt_parts"]
    assert all(part["content_digest"] and part["content_location"]["execution_id"] == first.execution_id
               for part in snapshot["prompt_parts"])
    execution.change_resident_runtime_settings(rid, "CASE-A", {"prompt_injection": {"preset": "pure"}})
    def forbidden():
        pytest.fail("recovery must not reassemble from changed settings")
    recovered = execution.durable_resident_execution_request(resident_record, resident_execution_work.read(resident_record), forbidden)
    assert recovered.to_payload() == first.to_payload()


def test_api_required_tool_is_rejected_before_a_model_request():
    request = Request(operation="generate", provider="provider_api", runtime_options={"runtime_settings": {
        "capability_config": {"tools": [{"ref": "portal:hatg_instruction", "enabled": True, "required": True}]}}})
    with pytest.raises(ValueError, match="required"):
        assembly.prepare(Invocation(request=request, harness_capabilities=frozenset()), Context())


def test_formal_switch_handler_and_query_share_actual_binding(monkeypatch, isolated_portal_case):
    from tests.cases.test_session_restart import _restart_domain
    from management_portal.society_service import resident_domain, resident_restart_domain
    with isolated_portal_case():
        record, operations, saved = _restart_domain(monkeypatch)
        monkeypatch.setattr(resident_domain, "restart_resident_session", resident_restart_domain.restart_resident_session)
        result = residents.restart_session(_invocation("RestartSession", resident_id=record["resident_id"],
            provider="codex", account_id="zero", operation="switch_account", context_strategy="preserve"))
        assert result["no_op"] and result["context_result"] == "unchanged"
        assert result["actual_binding"]["backend_session_id"] == "session_old"
        assert not operations and not saved
        current = {"provider_session_ref": "current-ref", "backend_session_id": "current-thread"}
        monkeypatch.setattr(resident_domain, "get_resident_session_restart", lambda *_: {
            "actual_binding": current, "restart": {"restart_id": "restart-old", "status": "failed",
            "source": {"provider_session_ref": "old-ref"}, "context_result": "unchanged", "operation": "switch_account"}})
        observed = residents.get_session_restart(_invocation("GetSessionRestart", resident_id=record["resident_id"]))
        assert observed["actual_binding"] == current
        assert observed["operation_id"] == "restart-old" and observed["operation"] == "switch_account"


def test_execution_checkpoint_freezes_assembly_before_the_provider_runs(tmp_path):
    from management_portal.execution.runtime import ExecutionRuntime, RuntimeContext
    from management_portal.execution.backends import BackendResult
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    from management_portal.execution.harness import AgentHarness
    checkpoints, observations = [], []
    class Provider:
        provider_id = "codex"
        def execute(self, invocation, context):
            assert checkpoints[-1]["capability_assembly"]["settings_revision"] == "saved-revision"
            observations.append(invocation.request.runtime_options["resident_assembly"])
            context.set_interrupt_target("test-thread", "test-turn", "one")
            return BackendResult(output={"message": "done"}, backend_session_id="test-thread", backend_route_id="one")
        def interrupt(self, **kwargs):
            return True
    runtime = ExecutionRuntime(agent_providers={"codex": Provider()}, agent_harness=AgentHarness(()), state_root=tmp_path)
    request = ExecutionRequest.new(actor="sample_admin", prompt="real input", service_type="agent_session",
        provider="codex", operation="turn", session=SessionOptions(mode="persistent", binding_id="resident:assembly"),
        metadata={"resident_id": "resident_aaaaaaaaaaaa"},
        runtime_options={"resident_assembly": {"settings_revision": "saved-revision"}})
    runtime.execute(request, RuntimeContext(checkpoint_provider=checkpoints.append))
    assert observations[0]["execution_id"] == request.execution_id
    assembly_checkpoints = [c for c in checkpoints if "capability_assembly" in c]
    snapshot = assembly_checkpoints[-1]["capability_assembly"]
    assert all(c["capability_assembly"] == snapshot for c in checkpoints if "capability_assembly" in c)
    assert assembly_checkpoints[-1]["turn_id"] == "test-turn"
