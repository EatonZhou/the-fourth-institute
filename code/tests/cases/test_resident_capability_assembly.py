"""Resident selection is enforced at the actual tool boundary, not by prose."""
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from management_portal.execution.harness import HarnessTool
from management_portal.execution import resident_capability_assembly as assembly
from tests.cases.test_resident_mcp import mcp, connection, resources  # noqa: F401


@dataclass(frozen=True)
class Request:
    operation: str = "turn"
    provider: str = "claude"
    execution_id: str = "execution_test"
    metadata: dict = field(default_factory=lambda: {"resident_id": "resident_test"})
    runtime_options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Invocation:
    request: Request = field(default_factory=Request)
    harness_tools: tuple = ()
    recovery_checkpoint: dict = field(default_factory=dict)
    available_harness_tool_names: frozenset = frozenset()
    session: object = field(default_factory=lambda: SimpleNamespace(backend_session_id="old", backend_route_id="one"))
    is_new_backend_session: bool = False
    harness_capabilities: frozenset = frozenset({"portal_tools"})


class Context:
    def __init__(self):
        self.plans, self.events = [], []

    def record_capability_assembly(self, value):
        self.plans.append(value)

    def emit(self, name, details):
        self.events.append((name, details))

    def is_cancelled(self):
        return False


def invocation(tools=(), selections=(), **kwargs):
    config = {"schema": "tfi.resident-capabilities.v1", "tools": list(selections), "mcp": [], "skills": []}
    return Invocation(request=Request(runtime_options={"runtime_settings": {"capability_config": config},
                                                     "resident_assembly": {"settings_revision": "r1"}}),
                      harness_tools=tools, **kwargs)


def tool(name="hatg_instruction", handler=lambda args: "ok"):
    return HarnessTool(name, "test tool", {"type": "object"}, handler)


def test_unknown_legacy_registration_keeps_original_session_and_tool(monkeypatch):
    monkeypatch.setattr(assembly, "current_selection", lambda *args: {})
    before = invocation((tool(),))
    context = Context()
    result = assembly.prepare(before, context)
    assert result.session is before.session
    assert result.harness_tools[0].handler({}) == "ok"
    assert context.plans[0]["tools"][0]["delivery"] == "not_sent"
    assert context.plans[0]["provider_managed"] == "unknown"


def test_disabled_tool_cannot_be_called_by_an_old_native_session(monkeypatch):
    calls = []
    monkeypatch.setattr(assembly, "current_selection", lambda *args: {"tools": [{"ref": "portal:hatg_instruction", "enabled": False}]})
    result = assembly.prepare(invocation((tool(handler=lambda args: calls.append(args)),)), Context())
    with pytest.raises(PermissionError, match="disabled"):
        result.harness_tools[0].handler({})
    assert calls == []


def test_disabled_schema_removed_even_when_old_provider_remembers_it():
    result = assembly.prepare(invocation((tool(),), [{"ref": "portal:hatg_instruction", "enabled": False}]), Context())
    assert not result.harness_tools[0].expose_schema
    with pytest.raises(PermissionError):
        result.harness_tools[0].handler({})


def test_required_absent_fails_before_provider_acceptance():
    with pytest.raises(ValueError, match="required"):
        assembly.prepare(invocation(selections=[{"ref": "portal:absent", "enabled": True, "required": True}]), Context())


def test_optional_absent_is_reported_without_global_failure():
    context = Context()
    assembly.prepare(invocation(selections=[{"ref": "portal:absent", "enabled": True}]), context)
    assert context.plans[0]["observations"][0]["observation"] == "missing"


def test_recovery_keeps_snapshot_and_fails_only_on_configuration_change():
    """恢复执行沿用已固定的能力快照，只有 Resident 配置变化才失败

    A release that restarts the Worker mid-turn can change Portal tool
    definitions: the HATG tool lists every instruction, so adding one
    (ListProjects on 10-29) failed every running turn. The turn now continues
    on the new definitions, keeps the frozen snapshot and reports the drift.
    """
    context = Context()
    assembly.prepare(invocation((tool(),)), context)
    previous = context.plans[0]
    recovered = Context()
    assembly.prepare(invocation((tool(),), recovery_checkpoint={"capability_assembly": previous}), recovered)
    assert recovered.plans == [previous]
    assert not [name for name, _ in recovered.events if name == "resident_capability_contract_changed"]
    changed = HarnessTool("hatg_instruction", "changed definition", {"type": "object"}, lambda args: "ok")
    released = Context()
    result = assembly.prepare(invocation((changed, tool("added_tool")),
                                         recovery_checkpoint={"capability_assembly": previous}), released)
    assert released.plans == [previous]
    assert result.harness_tools[0].description == "changed definition"
    assert dict(released.events)["resident_capability_contract_changed"] == {"reason": "execution_recovery", "changes": [
        {"ref": "portal:added_tool", "alias": "added_tool", "change": "added"},
        {"ref": "portal:hatg_instruction", "alias": "hatg_instruction", "change": "changed"}]}
    reconfigured = invocation((tool(),), [{"ref": "portal:hatg_instruction", "enabled": False}],
                              recovery_checkpoint={"capability_assembly": previous})
    with pytest.raises(ValueError, match="configuration changed"):
        assembly.prepare(reconfigured, Context())


def test_revocation_lookup_failure_does_not_execute_tool(monkeypatch):
    def unavailable(*args):
        raise RuntimeError("settings unavailable")
    monkeypatch.setattr(assembly, "current_selection", unavailable)
    calls = []
    result = assembly.prepare(invocation((tool(handler=lambda args: calls.append(args)),)), Context())
    with pytest.raises(RuntimeError):
        result.harness_tools[0].handler({})
    assert calls == []


def test_create_assembles_disabled_tools_without_changing_operation():
    before = Invocation(request=Request(operation="create", runtime_options={"runtime_settings": {
        "capability_config": {"tools": [{"ref": "portal:hatg_instruction", "enabled": False}]}}}), harness_tools=(tool(),))
    context = Context()
    prepared = assembly.prepare(before, context)
    assert prepared.request.operation == "create" and prepared.session is before.session
    assert not prepared.harness_tools[0].expose_schema
    assert context.plans[0]["tools"][0]["application"] == "disabled"


def test_required_new_tool_on_a_fixed_session_fails_without_rebuilding():
    before = invocation((tool(),), [{"ref": "portal:hatg_instruction", "required": True}])
    from dataclasses import replace
    before = replace(before, request=replace(before.request, provider="codex"))
    with pytest.raises(ValueError, match="pending.*session"):
        assembly.prepare(before, Context())
    assert before.session.backend_session_id == "old"


def test_optional_new_tool_on_fixed_session_is_pending_and_legacy_unknown_runs():
    from dataclasses import replace
    for selections, expected in (([{"ref": "portal:hatg_instruction"}], "pending_session"), ([], "unknown")):
        before = invocation((tool(),), selections)
        context = Context()
        assembly.prepare(replace(before, request=replace(before.request, provider="codex")), context)
        assert context.plans[0]["tools"][0]["application"] == expected


def test_mcp_assembly_passes_cancel_and_enforces_exact_selection(monkeypatch):
    from management_portal.execution import resident_mcp
    from dataclasses import replace
    calls = []
    mcp_tool = replace(tool("mcp_search", lambda args: calls.append(args)),
                       capability_ref="mcp:resource_test#search", connection_ref="resource_test", local_name="search")
    context = Context()
    def materialize(selections, **kwargs):
        assert kwargs["is_cancelled"] == context.is_cancelled
        return (mcp_tool,), []
    monkeypatch.setattr(resident_mcp, "materialize_mcp_tools", materialize)
    config = {"mcp": [{"ref": "resource_test", "tools": ["search"]}]}
    before = Invocation(request=Request(runtime_options={"runtime_settings": {"capability_config": config}}))
    result = assembly.prepare(before, context)
    monkeypatch.setattr(assembly, "current_selection", lambda *_: {"mcp": [{"ref": "resource_test", "tools": []}]})
    with pytest.raises(PermissionError):
        result.harness_tools[0].handler({})
    assert not calls


def test_delivery_observation_never_infers_acceptance_or_business_success():
    context = Context()
    prepared = assembly.prepare(invocation((tool(),)), context)
    assembly.observe_delivery(prepared, context, completed=True, available_names={"hatg_instruction"})
    delivery = context.events[-1][1]
    assert delivery["delivery"] == "sent" and delivery["acceptance"] == "unknown"
    assembly.observe_delivery(prepared, context, completed=False)
    assert context.events[-1][1]["delivery"] == "unknown"


def test_adapter_completion_without_tool_receipt_is_not_delivery_evidence():
    context = Context()
    prepared = assembly.prepare(invocation((tool(),)), context)
    assembly.observe_delivery(prepared, context, completed=True)
    assert context.events[-1][1]["delivery"] == "unknown"


def test_required_kimi_tool_cannot_fall_back_to_an_empty_catalog(monkeypatch, tmp_path):
    from management_portal.execution.kimi_backend import KimiBackend, kimi_portal_tools
    before = invocation((tool(),), [{"ref": "portal:hatg_instruction", "required": True}])
    monkeypatch.setattr(kimi_portal_tools, "service_ready", lambda home: False)
    with pytest.raises(ValueError, match="[Rr]equired.*(tool|capability)"):
        KimiBackend.offer_portal_tools(before, Context(), home=tmp_path)


def test_native_provider_without_attached_capabilities_uses_its_declared_contract():
    context = Context()
    before = invocation((tool(),), harness_capabilities=None)
    prepared = assembly.prepare(before, context)
    assembly.observe_delivery(prepared, context, completed=True, available_names={"hatg_instruction"})
    assert context.events[-1][1]["delivery"] == "sent"


def test_required_mcp_uses_native_capabilities_without_an_attached_override(monkeypatch):
    from dataclasses import replace
    from management_portal.execution import resident_mcp
    native = replace(tool("mcp_lookup"), capability_ref="mcp:resource_test#lookup",
                     connection_ref="resource_test", local_name="lookup")
    calls = []
    def materialize(selected, **kwargs):
        calls.append(selected)
        return (native,), []
    monkeypatch.setattr(resident_mcp, "materialize_mcp_tools", materialize)
    before = Invocation(harness_capabilities=None, request=Request(runtime_options={
        "runtime_settings": {"capability_config": {"mcp": [
            {"ref": "resource_test", "tools": ["lookup"], "required": True}]}}}))
    result = assembly.prepare(before, Context())
    assert len(calls) == 1 and result.harness_tools[0].name == "mcp_lookup"


@pytest.mark.parametrize("fails", [False, True])
def test_delivery_observation_failure_keeps_the_provider_outcome(monkeypatch, fails):
    from management_portal.execution import backend_services, native_options
    reply = SimpleNamespace(available_harness_tool_names=frozenset({"hatg_instruction"}))
    class Provider:
        def execute(self, invocation, context):
            if fails:
                raise RuntimeError("provider failed")
            return reply
    class Harness:
        def prepare_invocation(self, invocation, provider):
            return invocation
        def should_run(self, invocation):
            return False
    def broken(*args, **kwargs):
        raise TypeError("argument of type 'NoneType' is not iterable")
    monkeypatch.setattr(native_options, "prepare_invocation", lambda invocation: invocation)
    monkeypatch.setattr(assembly, "prepare", lambda invocation, context: invocation)
    monkeypatch.setattr(assembly, "observe_delivery", broken)
    monkeypatch.setattr(backend_services, "prepare_backend_context_seed", lambda provider, invocation: invocation)
    service = backend_services.AgentSessionService(providers={"claude": Provider()}, harness=Harness())
    if fails:
        with pytest.raises(RuntimeError, match="provider failed"):
            service.execute(Invocation(), Context())
    else:
        assert service.execute(Invocation(), Context()) is reply


@pytest.mark.parametrize("operation", ["create", "fork"])
def test_empty_session_operations_do_not_claim_they_sent_tools(operation):
    from dataclasses import replace
    context = Context()
    before = invocation((tool(),))
    prepared = assembly.prepare(replace(before, request=replace(before.request, operation=operation)), context)
    assembly.observe_delivery(prepared, context, completed=True)
    assert context.events[-1][1]["delivery"] == "unknown"


def test_recovery_passes_frozen_mcp_dependencies_before_discovery(monkeypatch):
    from management_portal.execution import resident_mcp
    from dataclasses import replace
    native = replace(tool("mcp_search"), capability_ref="mcp:resource_test#search",
                     connection_ref="resource_test", local_name="search")
    observed = [{"ref": "resource_test", "local_name": "search", "observation": "found",
                 "connection_digest": "a" * 64, "tool_digest": "b" * 64}]
    received = []
    def materialize(selections, **kwargs):
        received.append(kwargs.get("expected_dependencies"))
        return (native,), observed
    monkeypatch.setattr(resident_mcp, "materialize_mcp_tools", materialize)
    before = Invocation(request=Request(runtime_options={"runtime_settings": {"capability_config": {
        "mcp": [{"ref": "resource_test", "tools": ["search"]}]}}}))
    context = Context()
    assembly.prepare(before, context)
    snapshot = context.plans[-1]
    assembly.prepare(replace(before, recovery_checkpoint={"capability_assembly": snapshot}), Context())
    assert received == [None, {"resource_test": {"connection_digest": "a" * 64, "tools": {"search": "b" * 64}}}]
    observed[0]["tool_digest"] = "c" * 64
    with pytest.raises(ValueError, match="changed"):
        assembly.prepare(replace(before, recovery_checkpoint={"capability_assembly": snapshot}), Context())


@pytest.mark.parametrize("selection", [{"ref": "resource_test", "required": True},
                                      {"ref": "resource_test", "required": True, "tools": []}])
def test_unselected_mcp_is_not_a_required_provider_capability(selection):
    before = Invocation(request=Request(provider="provider_api", operation="generate", runtime_options={
        "runtime_settings": {"capability_config": {"mcp": [selection]}}}), harness_capabilities=frozenset())
    assert assembly.prepare(before, Context()).harness_tools == ()


def test_actual_mcp_assembly_recovers_same_definition_and_refuses_new_endpoint(mcp, connection, resources):
    from copy import deepcopy
    from dataclasses import replace
    from tests.cases.test_resident_mcp import REF, messages, select
    docs, reads = resources
    doc, log = connection
    docs[REF] = doc
    before = Invocation(request=Request(runtime_options={"runtime_settings": {"capability_config": {"mcp": [select()]}}}))
    context = Context()
    assembly.prepare(before, context)
    snapshot = context.plans[-1]
    assert len(snapshot["mcp_dependencies"][REF]["tools"]["echo"]) == 64
    recovering = replace(before, recovery_checkpoint={"capability_assembly": snapshot})
    assert assembly.prepare(recovering, Context()).harness_tools
    docs[REF] = deepcopy(doc)
    docs[REF]["transport"]["args"][1] = str(log.parent / "new-endpoint.jsonl")
    with pytest.raises(mcp.MCPError, match="recovery_connection_changed"):
        assembly.prepare(recovering, Context())
    assert not (log.parent / "new-endpoint.jsonl.pid").exists()
    assert all(message.get("method") != "tools/call" for message in messages(log))
    assert all(actor == "resident_test" for _, actor in reads)
