"""Program composition preserves domain authority and dynamic entry points.

:module: lineage.service
:design: decision_4adcc1ff2f2c
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from management_portal.society_service import (
    lineage_domain as domain,
    lineage_program_backlog,
    lineage_program_runtime,
    lineage_program_services as services,
    lineage_resident_controls,
)
from management_portal.society_service import lineage_aggregate
from tests.cases import society_service_test_support as support
from tests.cases.test_lineage_program_runtime_integration import (  # noqa: F401
    ACTOR, WORK_A, _component, _wire, _worker, runtime_store,
)


def test_constructed_ports_keep_domain_authorization_and_scope_lifetime(monkeypatch):
    """装配后替换领域函数仍生效，权限异常和锁释放按原调用顺序传播。"""
    ports = domain._program_execution_ports()
    calls = []
    denied = PermissionError("not a manager")

    @contextmanager
    def scope(lineage_id, *, operation=""):
        calls.append(("enter", lineage_id, operation))
        try:
            yield
        finally:
            calls.append(("exit", lineage_id, operation))

    def read(lineage_id, *, actor):
        calls.append(("read", lineage_id, actor))
        return {"lineage_id": lineage_id}

    def ensure_manager(lineage, actor):
        calls.append(("authorize", lineage["lineage_id"], actor))
        raise denied

    monkeypatch.setattr(lineage_aggregate, "lineage_scope", scope)
    monkeypatch.setattr(lineage_aggregate, "read_lineage", read)
    monkeypatch.setattr(lineage_aggregate, "ensure_manager", ensure_manager)
    with pytest.raises(PermissionError) as error:
        with ports.state.lineage_scope("lineage_exact", operation="delivery.check"):
            lineage = ports.state.read_lineage("lineage_exact", actor="viewer")
            ports.state.ensure_manager(lineage, "viewer")
    assert error.value is denied
    assert calls == [
        ("enter", "lineage_exact", "delivery.check"),
        ("read", "lineage_exact", "viewer"),
        ("authorize", "lineage_exact", "viewer"),
        ("exit", "lineage_exact", "delivery.check"),
    ]

    receipt = {"message_id": "original-input"}
    options = {"idempotency_key": "original-delivery"}
    monkeypatch.setattr(lineage_resident_controls, "_send_node_message", lambda *args, **kwargs: (
        calls.append((args, kwargs)) or receipt
    ))
    monkeypatch.setattr(domain, "_send_node_message", lambda *args, **kwargs: (
        calls.append((args, kwargs)) or receipt
    ))
    node = {"id": "resident_exact"}
    assert ports.resident.send_node_message(node, "prompt", "sender", **options) is receipt
    assert calls[-1] == ((node, "prompt", "sender"), options)


def test_flow_reuses_late_bound_public_factories_and_dispatch(monkeypatch):
    """已经装配的恢复端口沿用领域公开入口，包括替换过的投递、积压和授权工厂。"""
    ports = domain._program_terminal_ports()
    execution, event, control = object(), object(), object()
    monkeypatch.setattr(domain, "_program_execution_ports", lambda: execution)
    monkeypatch.setattr(domain, "_program_event_authority", lambda: event)
    monkeypatch.setattr(domain, "_program_control_authority", lambda: control)
    assert ports.flow.program_execution_ports() is execution
    assert ports.flow.event_authority() is event
    assert ports.flow.effect_authority() is event
    calls = []
    monkeypatch.setattr(domain, "dispatch_lineage_program_event", lambda *args, **kwargs: (
        calls.append((args, kwargs)) or {"dispatched": True}
    ))
    envelope = {"id": "event_exact"}
    assert ports.flow.dispatch_lineage_program_event(
        "lineage_exact", "source_exact", envelope, "sender", cursor="cursor_exact",
    ) == {"dispatched": True}
    assert calls[-1] == (("lineage_exact", "source_exact", envelope, "sender"), {"cursor": "cursor_exact"})
    monkeypatch.setattr(lineage_program_backlog, "drain_event_backlog", lambda *args, **kwargs: (
        calls.append((args, kwargs)) or {"delivered_count": 1}
    ))
    assert ports.flow.drain_program_event_wire_backlog("lineage_exact", "sender", max_events=7) == {"delivered_count": 1}
    assert calls[-1] == (("lineage_exact", "sender"), {"max_events": 7, "authority": control})


def test_terminal_evidence_and_catalog_remain_in_their_owners(monkeypatch):
    """跨图恢复只通过原目录和原生输入证据接口，构造端口不缓存目录或状态。"""
    ports = domain._program_terminal_ports()
    calls = []
    receipt = {"message_id": "message_exact", "state": "completed"}
    monkeypatch.setattr(domain.resident_protocol, "get_latest_completed_message_reference", lambda resident, actor: (
        calls.append((resident, actor)) or receipt
    ))
    assert ports.resident.latest_completed_message_reference("resident_exact", "reader") is receipt
    assert calls == [("resident_exact", "reader")]
    for lineage_ids in (["lineage_first"], ["lineage_first", "lineage_second"]):
        catalog = SimpleNamespace(lineage_ids_for_resident=lambda resident: lineage_ids)
        monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: catalog)
        assert ports.directory.lineage_catalog_store() is catalog
        assert ports.directory.lineage_ids_for_resident("resident_exact") is lineage_ids
    assert not hasattr(ports.directory, "send_node_message")
    assert not hasattr(ports.resident, "unlisted_domain_operation")


def test_polling_and_observation_capture_callbacks_without_doing_work(monkeypatch):
    """轮询及系统事实装配保留既有快照回调绑定，不在装配阶段读数据库或派发。"""
    calls = []

    def first(*args, **kwargs):
        calls.append((args, kwargs))
        return {"version": 1}

    monkeypatch.setattr(lineage_aggregate, "read_lineage_state", first)
    monkeypatch.setattr(lineage_aggregate, "read_lineage", first)
    polling = domain._program_polling_ports()
    observations = services.event_reconcile_ports(domain)
    assert calls == []
    monkeypatch.setattr(lineage_aggregate, "read_lineage_state", lambda _: {"version": 2})
    monkeypatch.setattr(lineage_aggregate, "read_lineage", lambda *args, **kwargs: {"version": 2})
    assert polling.read_lineage_state("lineage_exact") == {"version": 1}
    assert observations.read_lineage("lineage_exact", actor="reader") == {"version": 1}
    assert domain._program_polling_ports().read_lineage_state("lineage_exact") == {"version": 2}
    assert services.event_reconcile_ports(domain).read_lineage("lineage_exact", actor="reader") == {"version": 2}
    assert observations.dispatch_program_event is domain.dispatch_lineage_program_event
    assert observations.lineage_scope is lineage_aggregate.lineage_scope
    assert polling.ensure_manager is lineage_aggregate.ensure_manager


def test_public_event_and_recovery_wiring_deliver_one_persisted_input(runtime_store, monkeypatch):
    """隔离PG中真实事件入口持久排队，恢复端口经同一领域入口完成投递并保存输入关联。"""
    busy = {"value": True}
    sent = []
    monkeypatch.setattr(support, "NODE_ACTIVE_JOB_RESOLVER", lambda node, actor: busy["value"])
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: (
        sent.append((node["id"], actor)) or {"message_id": "job_composition_delivery"}
    ))
    lineage_id = support.create_lineage_with_resident_members({
        "title": "Program composition", "project_id": "project_test",
        "nodes": [_worker(WORK_A)],
        "program": {
            "components": [_component("event_source_composition", "event_source", event_type="manual.triggered")],
            "wires": [_wire("wire_composition", "route", "event_source_composition", WORK_A,
                            config={"unavailable_policy": "queue"})],
        },
    }, ACTOR)["lineage_id"]
    domain.emit_program_manual_event(lineage_id, {"event_source_id": "event_source_composition"}, ACTOR)
    assert sent == []
    assert lineage_program_runtime.read_runtime(lineage_id)["event_wires"]["wire_composition"]["pending_events"]
    ports = domain._program_terminal_ports()
    busy["value"] = False
    result = ports.flow.drain_program_event_wire_backlog(lineage_id, ACTOR)
    assert result["delivered_count"] == 1
    assert sent == [(WORK_A, ACTOR)]
    wire = lineage_program_runtime.read_runtime(lineage_id)["event_wires"].get("wire_composition", {})
    assert wire.get("pending_events", []) == []
    inputs = lineage_program_runtime.pending_resident_handoffs(lineage_id)
    assert len(inputs) == 1
    assert inputs[0]["resident_id"] == WORK_A
    assert inputs[0]["message_id"] == "job_composition_delivery"
    assert ports.directory.lineage_ids_for_resident(WORK_A) == [lineage_id]
    domain._reconcile_lineage_program_system_events(lineage_id, ACTOR)
    assert sent == [(WORK_A, ACTOR)]
