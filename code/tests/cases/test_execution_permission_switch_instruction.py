"""Permission switching is a distinct operation from execution settings.

:module: isa.instructions
:design: decision_b820faf42218
"""
from types import SimpleNamespace

import pytest

from management_portal import permission_system
from management_portal.execution.service_settings import ExecutionSettings
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.society_service.isa import system_workflows


def invocation(arguments):
    return SimpleNamespace(caller="alice", arguments=arguments, argument=arguments.get)


def test_legacy_permission_field_is_rejected_before_execution():
    from management_common.portal_database import portal_database
    from management_portal.hatg.catalog.environment import DECLARATIONS
    from management_portal.hatg.kernel import InstructionKernel, OwnerBinding
    from management_portal.hatg.store import ExecutionStore

    declaration = next(d for d in DECLARATIONS if d.name == "UpdateExecutionSettings")
    effects = []
    kernel = InstructionKernel(
        OwnerBinding(owner="lineage", handlers={declaration.name: lambda inv: effects.append(inv.arguments) or {}}),
        store=ExecutionStore(portal_database(schema="settings_split_test")),
        catalog={declaration.name: declaration}, authorize=lambda *args: None,
    )
    result = kernel.execute(InstructionCall(declaration.name, {
        "permission_system_enabled": False, "harness_retention_days": 60,
    }), InstructionContext(caller="alice", entry="web"))
    assert result.error["code"] == "instruction.invalid_arguments"
    assert not effects


def test_switch_permission_is_checked_before_any_state_change(monkeypatch):
    observed = []
    monkeypatch.setattr(permission_system, "allows", lambda actor, name, args:
                        observed.append((actor, name, args)) or False)
    monkeypatch.setattr(system_workflows, "_switch_permission_system", lambda *args:
                        pytest.fail("unauthorized switch reached storage"))
    with pytest.raises(InstructionError, match="无权"):
        system_workflows.set_permission_system_enabled(invocation({"enabled": False}))
    assert observed == [("alice", "SetPermissionSystemEnabled", {"enabled": False})]


@pytest.mark.parametrize("enabled", [None, 0, "false"])
def test_switch_rejects_invalid_boolean_without_touching_storage(monkeypatch, enabled):
    monkeypatch.setattr(permission_system, "allows", lambda *args: True)
    monkeypatch.setattr(system_workflows, "_switch_permission_system", lambda *args:
                        pytest.fail("invalid value reached storage"))
    with pytest.raises(InstructionError, match="布尔"):
        system_workflows.set_permission_system_enabled(invocation({"enabled": enabled}))


def test_enabling_reconciliation_failure_keeps_permission_system_disabled(monkeypatch):
    monkeypatch.setattr(permission_system, "allows", lambda *args: True)
    settings = ExecutionSettings()
    settings.update_permission_system(False)
    observed = []

    def reconcile(actor):
        observed.append((actor, settings.permission_system_enabled()))
        raise RuntimeError("instance reconciliation failed")

    monkeypatch.setattr(system_workflows, "reconcile_before_enabling", reconcile)
    with pytest.raises(RuntimeError, match="reconciliation failed"):
        system_workflows.set_permission_system_enabled(invocation({"enabled": True}))
    assert observed == [("alice", False)]
    assert not settings.permission_system_enabled()


def test_switch_returns_original_receipt_and_preserves_other_settings(monkeypatch):
    monkeypatch.setattr(permission_system, "allows", lambda *args: True)
    settings = ExecutionSettings()
    before = settings.get()
    report = {"scheme_id": "default", "added_nodes": 2}
    calls = []
    monkeypatch.setattr(system_workflows, "reconcile_before_enabling", lambda actor:
                        calls.append(actor) or report)
    off = system_workflows.set_permission_system_enabled(invocation({"enabled": False}))
    assert off == {"settings": {**before, "permission_system_enabled": False}}
    on = system_workflows.set_permission_system_enabled(invocation({"enabled": True}))
    assert on == {"settings": before, "reconciled": report}
    assert system_workflows.set_permission_system_enabled(invocation({"enabled": True})) == {"settings": before}
    assert calls == ["alice"]
