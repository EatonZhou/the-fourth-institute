"""Actual offered Kimi catalog, required capabilities, and delivery evidence.

The service, assembly, Kimi execute/offer, and native tool serializer are real.
Account/network/catalog publication and the model client stay isolated.
"""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from management_portal.execution import (
    backend_contracts,
    backend_services,
    kimi_backend,
    native_harness_tools,
    native_options,
)
from management_portal.execution import resident_capability_assembly as assembly
from management_portal.execution import resident_mcp
from management_portal.execution.contracts import ExecutionRecoveryDeferred, ExecutionRequest
from management_portal.execution.harness import HarnessTool
from management_portal.execution.session_manager import RuntimeSession
from management_portal.native_agent_transport import NativeAgentDetached


REF = "resource_test"
BUILTIN = HarnessTool("hatg_instruction", "Portal instruction", {"type": "object"}, lambda _: "ok")
MCP = HarnessTool("mcp_lookup", "Lookup", {"type": "object"}, lambda _: "ok",
                  capability_ref=f"mcp:{REF}#lookup", connection_ref=REF, local_name="lookup")


class NoHarnessLoop:
    def prepare_invocation(self, invocation, provider):
        return invocation

    def should_run(self, invocation):
        return False


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    events, model_calls, published = [], [], []
    state = SimpleNamespace(error=None)

    def run_turn(**kwargs):
        model_calls.append(kwargs)
        if state.error is not None:
            raise state.error
        return SimpleNamespace(response="done", session_id="test-session", turn_id="test-turn", usage={})

    client = SimpleNamespace(home=tmp_path, run_turn=run_turn)
    backend = kimi_backend.KimiBackend(client=client)
    # Remove live gateway/account/profile/usage I/O, keeping execute and offer real.
    monkeypatch.setattr(backend, "turn_scope", lambda *_: nullcontext())
    monkeypatch.setattr(backend, "client_turn_options", lambda *_: {})
    monkeypatch.setattr(backend, "record_provider_compaction", lambda **_: None)
    monkeypatch.setattr(backend, "observe_turn_usage", lambda **_: None)
    monkeypatch.setattr(native_options, "prepare_invocation", lambda invocation: invocation)
    monkeypatch.setattr(kimi_backend.kimi_portal_tools, "service_ready", lambda _: True)
    monkeypatch.setattr(kimi_backend, "publish_catalog", lambda catalog, home: published.append(catalog))

    def materialize(selections, **kwargs):
        assert kwargs["caller"] == "resident_test"
        assert selections == [{"ref": REF, "enabled": True, "required": selections[0]["required"], "tools": ["lookup"]}]
        return (MCP,), []

    monkeypatch.setattr(resident_mcp, "materialize_mcp_tools", materialize)
    context = backend_contracts.BackendContext(
        emit_event=lambda name, payload: events.append((name, payload)),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_: None,
    )
    service = backend_services.AgentSessionService(providers={"kimi": backend}, harness=NoHarnessLoop())
    return SimpleNamespace(backend=backend, context=context, service=service, events=events,
                           model_calls=model_calls, published=published, state=state)


def invocation(*, kind="builtin", required=False, operation="turn"):
    config = {"schema": "tfi.resident-capabilities.v1", "tools": [
        {"ref": "portal:hatg_instruction", "enabled": True, "required": required and kind == "builtin"}],
        "mcp": [], "skills": []}
    if kind == "mcp":
        config["mcp"] = [{"ref": REF, "enabled": True, "required": required, "tools": ["lookup"]}]
    request = ExecutionRequest(
        execution_id="execution-delivery-test", actor="resident_test", prompt="do work",
        service_type="agent_session", provider="kimi", operation=operation,
        cwd="/tmp", permission_profile="default",
        runtime_options={"runtime_settings": {"capability_config": config}},
        metadata={"resident_id": "resident_test"},
    )
    return backend_contracts.BackendInvocation(request=request,
        session=RuntimeSession("resume", "resident:test", "test-session", False),
        user_prompt="do work", system_context="", attach_system_context=False,
        is_new_backend_session=False, harness_tools=(BUILTIN,), harness_capabilities=frozenset({"portal_tools"}))


def fail_offer(monkeypatch, reason):
    def failure(*args, **kwargs):
        raise RuntimeError("isolated catalog failure")
    if reason == "service_not_ready":
        monkeypatch.setattr(kimi_backend.kimi_portal_tools, "service_ready", lambda _: False)
    elif reason == "publish_failure":
        monkeypatch.setattr(kimi_backend, "publish_catalog", failure)
    elif reason == "prepare_failure":
        monkeypatch.setattr(native_harness_tools, "prepare_tools", failure)
    elif reason == "empty_catalog":
        monkeypatch.setattr(native_harness_tools, "prepare_tools", lambda *_: ([], None))
    else:
        raise AssertionError(reason)


def deliveries(runtime):
    return [data for name, data in runtime.events if name == "resident_capability_delivery"]


@pytest.mark.parametrize("reason", ["service_not_ready", "publish_failure", "prepare_failure", "empty_catalog"])
def test_optional_empty_kimi_catalog_never_reports_sent(runtime, monkeypatch, reason):
    fail_offer(monkeypatch, reason)
    result = runtime.service.execute(invocation(), runtime.context)
    assert result.output["message"] == "done"
    assert len(runtime.model_calls) == 1 and runtime.model_calls[0]["tools"] == []
    assert result.available_harness_tool_names == frozenset()
    observed = deliveries(runtime)
    assert len(observed) == 1
    assert observed[0]["delivery"] == "unknown" and observed[0]["acceptance"] == "unknown"


@pytest.mark.parametrize("kind", ["builtin", "mcp"])
@pytest.mark.parametrize("reason", ["service_not_ready", "publish_failure", "prepare_failure", "empty_catalog"])
def test_required_kimi_tools_missing_from_offer_fail_before_model(runtime, monkeypatch, kind, reason):
    fail_offer(monkeypatch, reason)
    with pytest.raises((ValueError, RuntimeError), match="(?i)required"):
        runtime.service.execute(invocation(kind=kind, required=True), runtime.context)
    assert runtime.model_calls == []
    assert all(data["delivery"] != "sent" for data in deliveries(runtime))


def test_successful_delivery_uses_only_names_returned_by_real_kimi_adapter(runtime, monkeypatch):
    prepare = native_harness_tools.prepare_tools

    def subset(invocation, context):
        catalog, dispatch = prepare(invocation, context)
        return [entry for entry in catalog if entry["name"] == "hatg_instruction"], dispatch

    monkeypatch.setattr(native_harness_tools, "prepare_tools", subset)
    result = runtime.service.execute(invocation(kind="mcp"), runtime.context)
    assert result.available_harness_tool_names == frozenset({"hatg_instruction"})
    assert [entry["name"] for entry in runtime.model_calls[0]["tools"]] == ["hatg_instruction"]
    observed = {data["alias"]: data for data in deliveries(runtime)}
    assert observed["hatg_instruction"]["delivery"] == "sent"
    assert observed["mcp_lookup"]["delivery"] == "unknown"
    assert all(data["acceptance"] == "unknown" for data in observed.values())


def test_completed_adapter_without_delivery_receipt_remains_unknown(runtime):
    prepared = assembly.prepare(invocation(), runtime.context)
    assembly.observe_delivery(prepared, runtime.context, completed=True)
    assert deliveries(runtime)[-1]["delivery"] == "unknown"


@pytest.mark.parametrize("error", [ValueError("isolated turn error"), InterruptedError("isolated cancellation")])
def test_error_or_cancelled_turn_never_reports_sent(runtime, error):
    runtime.state.error = error
    with pytest.raises(type(error), match="isolated"):
        runtime.service.execute(invocation(), runtime.context)
    observed = deliveries(runtime)
    assert len(observed) == 1
    assert observed[0]["delivery"] == "unknown" and observed[0]["acceptance"] == "unknown"


def test_turn_left_to_the_next_worker_reports_no_delivery(runtime):
    """交给下一个 Worker 的轮次不报告交付证据

    Worker 交接时轮次仍在运行，接手的 Worker 在轮次结束后报告交付；退役 Worker 不写失败证据。
    """
    runtime.state.error = NativeAgentDetached("left")
    with pytest.raises(ExecutionRecoveryDeferred):
        runtime.service.execute(invocation(), runtime.context)
    assert deliveries(runtime) == []


@pytest.mark.parametrize("operation", ["create", "fork"])
def test_create_or_fork_never_claims_tool_delivery_from_names_alone(runtime, operation):
    prepared = assembly.prepare(invocation(operation=operation), runtime.context)
    assembly.observe_delivery(prepared, runtime.context, completed=True,
                              available_names=frozenset({"hatg_instruction"}))
    assert deliveries(runtime)[-1]["delivery"] == "unknown"


def test_required_mcp_without_selected_tools_does_not_block_optional_kimi_turn(runtime, monkeypatch):
    before = invocation()
    config = before.request.runtime_options["runtime_settings"]["capability_config"]
    config["mcp"] = [{"ref": REF, "enabled": True, "required": True, "tools": []}]
    fail_offer(monkeypatch, "service_not_ready")
    result = runtime.service.execute(before, runtime.context)
    assert result.output["message"] == "done"
    assert runtime.model_calls[0]["tools"] == []
    assert deliveries(runtime)[-1]["delivery"] == "unknown"
