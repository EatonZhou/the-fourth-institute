"""Automatic sources become ready when the graph has a valid Leader."""
from copy import deepcopy

import pytest

from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.society_service import lineage_domain, lineage_program_runtime
from management_portal.society_service.isa import build_kernel
from tests.cases.test_isa_lineage_lifecycle import future_creation_store, lineage, resident, graph  # noqa: F401

TIMER = "event_source_readiness_clock"
MANUAL = "event_source_readiness_manual"
CONFIG = {"event_type": "time.reached", "schedule_mode": "recurring",
          "recurrence_kind": "interval", "interval_seconds": 300, "jitter_seconds": 20,
          "max_fires": 3}


def call(name, **arguments):
    return build_kernel().execute(InstructionCall.from_payload({
        "instruction": name, "arguments": arguments,
    }), InstructionContext(caller="sample_admin", entry="web")).raise_for_error()


def create(lineage_id):
    definition = {**graph(lineage_id), "program": {"components": [
        {"id": TIMER, "kind": "event_source", "config": CONFIG},
        {"id": MANUAL, "kind": "event_source", "config": {"event_type": "manual.triggered"}},
    ], "wires": []}}
    lineage_domain.update_lineage(lineage_id, definition, "sample_admin")
    return lineage_id


def observe(lineage_id):
    before = deepcopy(lineage_program_runtime.read_runtime(lineage_id))
    view = call("GetLineage", lineage_id=lineage_id)["lineage"]
    components = call("GetComponentRuntime", lineage_id=lineage_id)["components"]
    runtime = next(item for item in components if item["component_id"] == TIMER)
    for key, value in view["program_event_source_runtime"][TIMER].items():
        assert runtime[key] == value
    diagnostics = call("GetLineageDiagnostics", lineage_id=lineage_id)
    errors = [item for item in diagnostics["diagnostics"]
              if item["code"] == "event.automation_identity_unavailable"]
    assert errors == [item for item in view["program_diagnostics"]
                      if item["code"] == "event.automation_identity_unavailable"]
    assert lineage_program_runtime.read_runtime(lineage_id) == before
    assert view["program_event_source_runtime"][MANUAL]["state"] == "listening"
    return view, runtime, errors, diagnostics


def test_empty_lineage_blocks_until_first_registered_leader_arrives(lineage):
    lineage_id = create(lineage)
    frozen = deepcopy(lineage_program_runtime.read_runtime(lineage_id))
    result = lineage_domain.reconcile_lineage_system_events_as_service(lineage_id)
    assert result["status"] == "blocked" and result["event_count"] == 0
    view, blocked, errors, diagnostics = observe(lineage_id)
    assert blocked["state"] == "blocked" and "Leader" in blocked["error"]
    assert errors and not diagnostics["runnable"]
    assert view["program_execution_explanations"]["nodes"][TIMER]["state"] == "blocked"
    assert lineage_program_runtime.read_runtime(lineage_id) == frozen

    first = resident(lineage_id, "First")
    assert graph(lineage_id)["leader_resident_id"] == first
    result = lineage_domain.reconcile_lineage_system_events_as_service(lineage_id)
    assert result["event_count"] == 0 and result.get("status") != "blocked"
    _, ready, errors, diagnostics = observe(lineage_id)
    assert ready["state"] == "listening" and "error" not in ready
    assert ready["schedule_generation"] > 0 and ready["next_due_at"]
    assert not errors and diagnostics["runnable"]


@pytest.mark.parametrize("paused,exhausted,expected", [(True, False, "paused"),
    (False, True, "exhausted"), (True, True, "paused")])
def test_paused_and_exhausted_precede_missing_identity(lineage, paused, exhausted, expected):
    """暂停或耗尽的旧运行状态保留；恢复新观测时才要求 Leader。"""
    lineage_id = create(lineage)
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        runtime["event_sources"][TIMER] = {"paused": paused, "fire_count": 3 if exhausted else 0}
    view, runtime, errors, _ = observe(lineage_id)
    assert runtime["state"] == expected and "error" not in runtime and not errors
    assert view["program_execution_explanations"]["nodes"][TIMER]["state"] == (
        "paused" if paused else "completed")
    if paused:
        resumed = call("PauseComponent", lineage_id=lineage_id, component_id=TIMER, paused=False)
        assert resumed["runtime"]["state"] == ("exhausted" if exhausted else "blocked")


@pytest.mark.parametrize("event_type", ["manual.triggered", "effect.succeeded", "budget.exhausted"])
def test_trusted_derived_and_manual_sources_do_not_acquire_automation_identity(monkeypatch, event_type):
    from management_portal.society_service.lineage_program_runtime_projection import event_source_runtime_view
    monkeypatch.setattr(lineage_program_runtime, "read_runtime", lambda _: {})
    runtime = event_source_runtime_view({"lineage_id": "lineage_readiness"}, {
        MANUAL: {"event_type": event_type},
    })[MANUAL]
    assert runtime["state"] == "listening" and "error" not in runtime


@pytest.mark.parametrize("leader", ["", "portal", "sample_admin", "resident_aaaaaaaaaaaa"])
def test_unusable_leader_projection_does_not_borrow_reader_or_owner(monkeypatch, leader):
    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity
    from management_portal.society_service.lineage_program_runtime_projection import event_source_runtime_view
    monkeypatch.setattr(lineage_program_runtime, "read_runtime", lambda _: {})
    with operation_identity_scope(OperationIdentity.for_actor("sample_admin")):
        runtime = event_source_runtime_view({"lineage_id": "lineage_readiness", "nodes": [],
            "leader_resident_id": leader, "run_as_principal": {"kind": "user", "id": "sample_admin"},
            "created_by": "sample_admin"}, {TIMER: CONFIG})[TIMER]
    assert runtime["state"] == "blocked" and "Leader" in runtime["error"]
