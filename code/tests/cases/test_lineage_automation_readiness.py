"""Automatic sources accept an optional User, independently of the reader."""
from copy import deepcopy
import json

import pytest

from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.society_service import lineage_domain, lineage_program_runtime
from management_portal.society_service.isa import build_kernel
from tests.cases.test_lineage_generic_primitives import primitive_store  # noqa: F401
from tests.cases.test_lineage_automation_identity import accounts, USER  # noqa: F401


TIMER = "event_source_readiness_clock"
MANUAL = "event_source_readiness_manual"
CONFIG = {"event_type": "time.reached", "schedule_mode": "recurring",
          "recurrence_kind": "interval", "interval_seconds": 300, "jitter_seconds": 20,
          "max_fires": 3}


def call(name, **arguments):
    return build_kernel().execute(InstructionCall.from_payload({
        "instruction": name, "arguments": arguments,
    }), InstructionContext(caller="sample_admin", entry="web")).raise_for_error()


def create(*, configured=False):
    definition = {"program": {"components": [
        {"id": TIMER, "kind": "event_source", "config": CONFIG},
        {"id": MANUAL, "kind": "event_source", "config": {"event_type": "manual.triggered"}},
    ], "wires": []}}
    if configured:
        definition["run_as_principal"] = USER
    return call("CreateLineage", title="Automation readiness", project_id="project_test", definition=definition)["lineage_id"]


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


def test_optional_user_schedule_and_explicit_disabled_user_blocking(primitive_store, accounts):
    """未配置运行用户的定时器照常排期，所配用户停用时阻断，启用或清除后恢复监听"""
    lineage_id = create()
    result = lineage_domain.reconcile_lineage_system_events_as_service(lineage_id)
    assert result['event_count'] == 0 and result.get('status') != 'blocked'
    view, runtime, errors, diagnostics = observe(lineage_id)
    assert runtime["state"] == "listening" and "error" not in runtime
    assert runtime["schedule_generation"] > 0 and runtime["next_due_at"]
    assert not errors and diagnostics["runnable"]
    explanation = view["program_execution_explanations"]["nodes"][TIMER]
    assert explanation["state"] != "blocked"

    call("UpdateLineage", lineage_id=lineage_id, changes={"run_as_principal": USER})
    assert call("GetLineage", lineage_id=lineage_id)["lineage"]["run_as_principal"] == USER
    result = lineage_domain.reconcile_lineage_system_events_as_service(lineage_id)
    assert result["event_count"] == 0
    persisted = lineage_program_runtime.read_runtime(lineage_id)["event_sources"][TIMER]
    view, ready, errors, diagnostics = observe(lineage_id)
    assert ready["state"] == "listening" and "error" not in ready
    assert ready["schedule_generation"] == persisted["generation"] > 0
    assert ready["next_scheduled_at"] == persisted["next_scheduled_at"]
    assert ready["next_due_at"] == persisted["next_due_at"] != ""
    assert not errors and diagnostics["runnable"]
    print(json.dumps({"missing": runtime, "configured_user": view["run_as_principal"],
                      "planned": ready}, ensure_ascii=False))

    # Live authority must refresh even with the same cached graph revision.
    accounts["sample_admin"] = False
    frozen = deepcopy(lineage_program_runtime.read_runtime(lineage_id))
    result = lineage_domain.reconcile_lineage_system_events_as_service(lineage_id)
    assert result['status'] == 'blocked' and 'inactive' in result['error']
    disabled_view, blocked, _, _ = observe(lineage_id)
    assert disabled_view["run_as_principal"] == USER
    assert blocked["state"] == "blocked" and "sample_admin" in blocked["error"]
    assert lineage_program_runtime.read_runtime(lineage_id) == frozen
    accounts["sample_admin"] = True
    assert observe(lineage_id)[1]["state"] == "listening"
    call("UpdateLineage", lineage_id=lineage_id, changes={"run_as_principal": None})
    assert observe(lineage_id)[1]["state"] == "listening"


@pytest.mark.parametrize("paused,exhausted,expected", [(True, False, "paused"),
    (False, True, "exhausted"), (True, True, "paused")])
def test_paused_and_exhausted_precede_missing_identity(primitive_store, accounts, paused, exhausted, expected):
    """定时器暂停或触发次数用尽时显示对应状态，缺少运行身份不报错"""
    lineage_id = create()
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        runtime["event_sources"][TIMER] = {"paused": paused, "fire_count": 3 if exhausted else 0}
    view, runtime, errors, _ = observe(lineage_id)
    assert runtime["state"] == expected and "error" not in runtime and not errors
    assert view["program_execution_explanations"]["nodes"][TIMER]["state"] == (
        "paused" if paused else "completed")
    if paused:
        resumed = call("PauseComponent", lineage_id=lineage_id, component_id=TIMER, paused=False)
        assert resumed["runtime"]["state"] == ("exhausted" if exhausted else "listening")


def test_create_roundtrips_explicit_user_and_deleted_user_blocks(primitive_store, accounts):
    """创建时指定的运行用户可以读回，该用户被删除后事件源进入 blocked"""
    lineage_id = create(configured=True)
    assert call("GetLineage", lineage_id=lineage_id)["lineage"]["run_as_principal"] == USER
    del accounts["sample_admin"]
    _, runtime, errors, _ = observe(lineage_id)
    assert runtime["state"] == "blocked" and "sample_admin" in runtime["error"] and errors


@pytest.mark.parametrize("event_type", ["manual.triggered", "effect.succeeded", "budget.exhausted"])
def test_trusted_derived_and_manual_sources_do_not_acquire_automation_identity(monkeypatch, event_type):
    """手动、Effect 结果和预算耗尽事件源不需要自动运行身份，直接处于监听状态"""
    from management_portal.society_service.lineage_program_runtime_projection import event_source_runtime_view
    monkeypatch.setattr(lineage_program_runtime, "read_runtime", lambda _: {})
    runtime = event_source_runtime_view({"lineage_id": "lineage_readiness"}, {
        MANUAL: {"event_type": event_type},
    })[MANUAL]
    assert runtime["state"] == "listening" and "error" not in runtime


@pytest.mark.parametrize("principal", [{"kind": "resident", "id": "resident_aaaaaaaaaaaa"},
                                     {"kind": "service", "id": "portal"}, "sample_admin"])
def test_unusable_identity_projection_does_not_borrow_reader_or_owner(monkeypatch, principal):
    """运行身份是 Resident、服务或字符串时事件源显示 blocked，不借用读者或 owner"""
    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity
    from management_portal.society_service.lineage_program_runtime_projection import event_source_runtime_view
    monkeypatch.setattr(lineage_program_runtime, "read_runtime", lambda _: {})
    with operation_identity_scope(OperationIdentity.for_actor("sample_admin")):
        runtime = event_source_runtime_view({"lineage_id": "lineage_readiness",
            "run_as_principal": principal, "created_by": "sample_admin"}, {TIMER: CONFIG})[TIMER]
    assert runtime["state"] == "blocked" and "User" in runtime["error"]
