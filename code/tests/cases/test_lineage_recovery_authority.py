"""Recovery evidence must not stall authoring or settle a replaced input.

:importance: P2
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from management_common.principal import OperationIdentity
from management_portal.society_service import lineage_domain, lineage_instruction_context, lineage_program_edits
from management_portal.society_service import lineage_execution_authority as authority
from management_portal.society_service import lineage_program_runtime as runtime
from management_portal.society_service import lineage_program_terminal_reconciliation as terminal
from tests.cases import society_service_test_support as support
from tests.cases.test_lineage_program_runtime_integration import (
    runtime_store, ACTOR, WORK_A, _component, _wire, _worker,
)


def test_completion_evidence_and_response_projection_release_admission(tmp_path, monkeypatch):
    """读取完成证据时不持执行准入锁"""
    support._install_isolated_portal_access(monkeypatch)
    lid, rid = "lineage_111111111111", "resident_111111111111"
    graph = {"lineage_id": lid, "nodes": [{"id": rid}], "program": {}}
    reading, release, edited = Event(), Event(), Event()
    observed = []
    def gate(_):
        return authority.execution_authority(lid, root=tmp_path)
    def completion(*_):
        reading.set()
        assert release.wait(5)
        return {"message_id": "old", "completed_at": "2026-10-15T00:00:00Z"}
    def edit():
        with gate(lid):
            graph["program"] = {"revision": "changed during read"}
            edited.set()
    monkeypatch.setattr(runtime, "pending_resident_handoffs", lambda _: [])
    monkeypatch.setattr(terminal, "_compiled_program", lambda _: {})
    monkeypatch.setattr(terminal.lineage_program, "resident_completion_subscriptions",
                        lambda *_: [("source", [rid], False, ["target"])])
    monkeypatch.setattr(runtime, "observe_resident_completion", lambda *a, **k: observed.append(k) or {"status": "unchanged"})
    monkeypatch.setattr(terminal, "reconcile_terminal_absorption_edges", lambda *a, **k: 0)
    ports = terminal.ProgramTerminalPorts(
        state=SimpleNamespace(read_lineage=lambda *a, **k: deepcopy(graph), ensure_manager=lambda *_: None,
                              lineage_scope=lambda _: nullcontext()),
        resident=SimpleNamespace(node_has_active_execution=lambda *_: False,
                                 latest_completed_message_reference=completion, node_resident_id=lambda n: n["id"],
                                 handle_resident_message_completion=lambda *a, **k: {}),
        flow=SimpleNamespace(drain_program_event_wire_backlog=lambda *_: {}),
        directory=SimpleNamespace(lineage_execution_authority=gate),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(terminal.reconcile_runtime_with_authority, lid, "sample_admin", ports=ports)
        try:
            assert reading.wait(5), future.result() if future.done() else "read never began"
            changing = pool.submit(edit)
            assert edited.wait(5), "Evidence IO held the execution admission lock"
        finally:
            release.set()
        changing.result()
        future.result()
    # The definition changed while the evidence was read, so none of it is consumed.
    assert observed == []


def test_component_edit_finishes_while_recovery_waits_for_resident_evidence(runtime_store, monkeypatch):
    """恢复等待 Resident 输入证据期间，组件编辑不被阻塞即可完成"""
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", lambda *_: {"message_id": "msg_slow_recovery"})
    graph = support.create_lineage_with_resident_members({
        "title": "Recovery admission",
        "nodes": [_worker(WORK_A)],
        "program": {
            "components": [_component("event_source_manual", "event_source", event_type="manual.triggered")],
            "wires": [_wire("wire_manual_worker", "route", "event_source_manual", WORK_A)],
        },
    }, ACTOR)
    lid = graph["lineage_id"]
    lineage_domain.emit_program_manual_event(lid, {"event_source_id": "event_source_manual"}, ACTOR)
    reading, release, edited = Event(), Event(), Event()

    def read_evidence(*_):
        reading.set()
        assert release.wait(5)
        return {"status": "running"}

    def edit():
        result = lineage_program_edits.save_lineage_program_component(lid, {
            "component_id": "event_source_manual", "config": {"event_type": "manual.triggered", "max_fires": 2},
        }, ACTOR)
        edited.set()
        return result

    monkeypatch.setattr(support, "RESIDENT_INPUT_STATE_RESOLVER", read_evidence)
    with ThreadPoolExecutor(max_workers=2) as pool:
        recovery = pool.submit(lineage_domain.reconcile_lineage_runtime, lid, ACTOR, recovery_only=True)
        try:
            assert reading.wait(5), recovery.result() if recovery.done() else "Evidence lookup never started"
            editing = pool.submit(edit)
            assert edited.wait(5), "Definition edits must not wait for Resident evidence IO"
        finally:
            release.set()
        editing.result()
        assert recovery.result()["recovered_handoff_count"] == 0
    assert len(runtime.pending_resident_handoffs(lid)) == 1


@pytest.mark.parametrize("change", ["edit", "delete", "replace", "execution", "settle", "membership"])
def test_recovery_revalidates_handoff_under_admission_lock(tmp_path, monkeypatch, change):
    """恢复在准入锁下复核 handoff，读证据期间被删除、替换、结算或移出成员时不再结算"""
    support._install_isolated_portal_access(monkeypatch)
    lid, rid = "lineage_111111111111", "resident_111111111111"
    saved_identity = OperationIdentity.for_actor("sample_admin")
    handoff = {
        "step_id": "trigger_step_111111111111", "resident_id": rid, "message_id": "msg_recover",
        "status": "queued", "event": {
            "id": "event_recover", "type": "manual.triggered", "payload": {},
            "occurred_at": "2026-10-15T00:00:00Z", "identity": saved_identity.to_dict(),
        },
    }
    current = [deepcopy(handoff)]
    graph = {"lineage_id": lid, "nodes": [{"id": rid}], "program": {}}
    reading, release, edited = Event(), Event(), Event()
    settled = []

    def gate(_):
        return authority.execution_authority(lid, root=tmp_path)

    def read_evidence(*_):
        reading.set()
        assert release.wait(5)
        return {"status": "completed"}

    def settle(*args, **kwargs):
        assert authority._OWNERS[lid] == get_ident()
        assert lineage_instruction_context.current_event_identity() == saved_identity
        settled.append((args, kwargs))
        return {}

    def edit():
        with gate(lid):
            if change == "delete":
                current.clear()
            elif change == "replace":
                current[0]["step_id"] = "trigger_step_222222222222"
            elif change == "execution":
                current[0]["execution_id"] = "execution_replacement"
            elif change == "settle":
                current[0]["status"] = "completed"
            elif change == "membership":
                graph["nodes"] = []
            else:
                graph["title"] = "Edited during evidence read"
            edited.set()

    def drain(*_):
        assert lid not in authority._OWNERS
        return {}

    ports = terminal.ProgramTerminalPorts(
        state=SimpleNamespace(read_lineage=lambda *a, **k: deepcopy(graph), ensure_manager=lambda *_: None,
                              lineage_scope=lambda _: nullcontext()),
        resident=SimpleNamespace(resident_input_reconciliation_state=read_evidence,
                                 handle_resident_message_terminal=settle,
                                 node_has_active_execution=lambda *_: False,
                                 latest_completed_message_reference=lambda *_: {}, node_resident_id=lambda n: n["id"],
                                 handle_resident_message_completion=lambda *_: {}),
        flow=SimpleNamespace(drain_program_event_wire_backlog=drain),
        directory=SimpleNamespace(lineage_execution_authority=gate),
    )
    monkeypatch.setattr(runtime, "pending_resident_handoffs", lambda _: deepcopy(current))
    monkeypatch.setattr(terminal.trigger_paths, "resident_inputs", lambda *a, **k: deepcopy(current))
    monkeypatch.setattr(terminal, "_compiled_program", lambda _: {})
    with ThreadPoolExecutor(max_workers=2) as pool:
        recovery = pool.submit(terminal.reconcile_runtime_with_authority, lid, "sample_admin", ports=ports, recovery_only=True)
        try:
            assert reading.wait(5), recovery.result() if recovery.done() else "Evidence lookup never started"
            editing = pool.submit(edit)
            assert edited.wait(5), "Read-only evidence collection held the admission lock"
        finally:
            release.set()
        editing.result()
        result = recovery.result()
    assert len(settled) == (1 if change == "edit" else 0)
    assert result["recovered_handoff_count"] == len(settled)


def test_recovery_with_nothing_to_settle_only_drains(monkeypatch):
    """没有可结算的 handoff 时，恢复不读定义、不取准入锁、不生成完整视图，只排空积压；不排空时什么都不做"""
    lid = "lineage_111111111111"
    graph = {"lineage_id": lid, "nodes": [], "program": {}}
    reads, gates, drains = [], [], []
    ports = terminal.ProgramTerminalPorts(
        state=SimpleNamespace(read_lineage=lambda *a, **k: reads.append(1) or deepcopy(graph),
                              ensure_manager=lambda *_: None, lineage_scope=lambda _: nullcontext(),
                              get_lineage=lambda *_: pytest.fail("recovery has no reader for a full Lineage view")),
        resident=SimpleNamespace(node_resident_id=lambda n: n["id"], handle_resident_message_completion=lambda *_: {}),
        flow=SimpleNamespace(drain_program_event_wire_backlog=lambda *a, **k: drains.append(1) or {}),
        directory=SimpleNamespace(lineage_execution_authority=lambda _: gates.append(1) or nullcontext()),
    )
    monkeypatch.setattr(runtime, "pending_resident_handoffs", lambda _: [])
    monkeypatch.setattr(terminal, "_compiled_program", lambda _: pytest.fail("recovery compiles nothing"))
    result = terminal.reconcile_runtime_with_authority(lid, "sample_admin", ports=ports, recovery_only=True)
    assert (len(reads), len(gates), len(drains)) == (0, 0, 1)
    assert "lineage" not in result
    terminal.reconcile_runtime_with_authority(lid, "sample_admin", ports=ports, recovery_only=True, drain=False)
    assert (len(reads), len(gates), len(drains)) == (0, 0, 1)
