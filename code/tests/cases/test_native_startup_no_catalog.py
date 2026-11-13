"""Executing saved native settings never depends on model discovery."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.execution.backend_contracts import BackendInvocation, BackendResult
from management_portal.execution.backend_services import AgentSessionService
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.native_options import NativeOptions, prepare_invocation
from management_portal.execution.provider_catalog import AGENT_PROVIDERS, AgentProvider
from management_portal.execution.session_manager import RuntimeSession


def invocation(provider, operation="turn", model="saved-model"):
    request = ExecutionRequest.new(
        actor="test", agent_id="resident:test", prompt="continue", service_type="agent_session",
        provider=provider, operation=operation, permission_profile="owner", cwd="/workspace",
        session=SessionOptions(mode="persistent", binding_id="resident:test", backend_route_id="saved-route"),
        runtime_options={"runtime_settings": {"model": model, "reasoning_effort": "high",
                                             "permission_preset": "full-access"},
                         "authorization_scope": {"role": "owner"}, "is_owner": True})
    session = RuntimeSession("persistent", "resident:test", "native-session", False, "saved-route")
    return BackendInvocation(request, session, "continue", "system", True, False)


@pytest.fixture
def unavailable_catalog(monkeypatch):
    catalog = Mock(side_effect=RuntimeError("native catalog unavailable"))
    monkeypatch.setattr(AgentProvider, "catalog", catalog)
    return catalog


def service(monkeypatch, provider, backend):
    # Isolate tool assembly/seed transport; retain the real service and real
    # prepare_invocation so a catalog gate would stop before backend.execute.
    from management_portal.execution import backend_services, resident_capability_assembly
    monkeypatch.setattr(resident_capability_assembly, "prepare", lambda inv, ctx: inv)
    monkeypatch.setattr(resident_capability_assembly, "observe_delivery", lambda *a, **kw: None)
    monkeypatch.setattr(backend_services, "prepare_backend_context_seed", lambda backend, inv: inv)
    harness = SimpleNamespace(prepare_invocation=lambda inv, provider: inv, should_run=lambda inv: False)
    return AgentSessionService({provider: backend}, harness)


@pytest.mark.parametrize("provider", ["kimi", "antigravity"])
@pytest.mark.parametrize("operation", ["turn", "release"])
@pytest.mark.parametrize("model", ["saved-model", ""])
def test_saved_settings_execute_once_without_catalog(
    monkeypatch, unavailable_catalog, provider, operation, model
):
    original = invocation(provider, operation, model)
    result = BackendResult(output={"message": "ok"})
    backend = SimpleNamespace(execute=Mock(return_value=result))
    context = object()
    executed = service(monkeypatch, provider, backend).execute(original, context)
    assert executed.output["message"] == "ok"
    assert result.output == {"message": "ok"}
    backend.execute.assert_called_once()
    prepared, actual_context = backend.execute.call_args.args
    assert actual_context is context
    assert prepared.session is original.session
    assert prepared.request.actor == original.request.actor
    assert prepared.request.agent_id == original.request.agent_id
    assert prepared.request.permission_profile == "owner"
    assert prepared.request.session is original.request.session
    assert prepared.request.operation == operation
    options = prepared.request.runtime_options
    assert options["model"] == model
    assert options["provider_profile"] == "saved-route"
    assert options["is_owner"] is True
    if provider == "kimi":
        assert options["permission_mode"] == "yolo"
        assert "Write" in options["tools"]
    else:
        assert options["effort"] == "high"
        assert options["native_tools_enabled"] is True
    assert "runtime_settings" in original.request.runtime_options
    assert original.request.runtime_options["authorization_scope"] == {"role": "owner"}
    assert "runtime_settings" not in options
    assert "authorization_scope" not in options
    unavailable_catalog.assert_not_called()


@pytest.mark.parametrize("provider", ["kimi", "antigravity"])
@pytest.mark.parametrize("operation", ["turn", "release"])
def test_native_error_propagates_without_catalog_retry(monkeypatch, unavailable_catalog, provider, operation):
    error = RuntimeError("native authentication or quota failure")
    backend = SimpleNamespace(execute=Mock(side_effect=error))
    with pytest.raises(RuntimeError) as raised:
        service(monkeypatch, provider, backend).execute(invocation(provider, operation), object())
    assert raised.value is error
    backend.execute.assert_called_once()
    unavailable_catalog.assert_not_called()


@pytest.mark.parametrize("provider", ["claude", "dsh"])
def test_inherited_translation_keeps_saved_model_effort_and_permissions(monkeypatch, unavailable_catalog, provider):
    from management_portal.execution import native_options
    monkeypatch.setattr(native_options, "claude_route_config_dir", lambda route: None)
    original = invocation(provider)
    prepared = prepare_invocation(original)
    options = prepared.request.runtime_options
    assert options["model"] == "saved-model"
    assert options["effort"] == "high"
    assert options["provider_profile"] == "saved-route"
    assert options["is_owner"] is True
    assert prepared.session is original.session
    assert prepared.request.permission_profile == "owner"
    if provider == "claude":
        assert options["permission_mode"] == "bypassPermissions"
    unavailable_catalog.assert_not_called()


@pytest.mark.parametrize("model", ["", "saved-model"])
def test_kimi_defaults_do_not_discover_catalog(unavailable_catalog, model):
    definition = AGENT_PROVIDERS["kimi"]
    settings = definition.runtime_adapter().defaults(definition, {"model": model, "backend_route_id": "saved-route"})
    assert settings["model"] == model
    assert settings["permission_preset"] == "full-access"
    unavailable_catalog.assert_not_called()


def test_editing_and_reconciliation_still_validate_catalog(monkeypatch):
    definition = AGENT_PROVIDERS["kimi"]
    catalog = Mock(return_value={"models": ["offered"], "default_model": "offered", "efforts_by_model": {}})
    monkeypatch.setattr(AgentProvider, "catalog", catalog)
    adapter = NativeOptions()
    monkeypatch.setattr(adapter, "validate_requested_fields", lambda *a: {"model"})
    with pytest.raises(ValueError, match="not enabled"):
        adapter.validate_updates(definition, "saved-route", {}, {"model": "invalid"})
    assert adapter.reconcile(definition, "saved-route", {"model": "invalid"})["model"] == "offered"
    assert catalog.call_count == 2
