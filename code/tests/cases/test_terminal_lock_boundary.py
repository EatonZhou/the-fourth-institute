"""Terminal follow-up must not block independent durable admission in the same Lineage."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from management_portal.society_service import (
    lineage_domain, lineage_execution_authority as locks,
    lineage_program_terminal_reconciliation as terminal,
    resident_intake_admission, resident_intake_domain, invariant_locks,
)
from tests.cases.resident_execution_test_support import resident_record, input_delivery  # noqa: F401
from tests.cases.test_lineage_program_runtime_integration import (  # noqa: F401
    runtime_store, ACTOR, WORK_A, _component, _wire, _worker,
)


@pytest.mark.parametrize("status,phase", [
    ("completed", "evidence"), ("completed", "dispatch"), ("completed", "backlog"),
    ("failed", "notify"), ("cancelled", "notify"), ("failed", "dispatch"),
    ("completed", "deferred"),
])
def test_terminal_io_allows_another_real_input(input_delivery, monkeypatch, tmp_path, status, phase):
    lid, source = "lineage_111111111111", "resident_111111111111"
    blocked, release, admitted = Event(), Event(), Event()
    invocations = [{"trigger_path_id": "path_test", "step_id": "step_original"}]

    def authority(_):
        return locks.execution_authority(lid, root=tmp_path)

    def slow(which):
        assert lid not in locks._OWNERS, f"{which} retained Lineage authority"
        if phase == which:
            blocked.set()
            assert release.wait(5)

    def settle(*args, **kw):
        assert locks._OWNERS[lid] == get_ident()
        return invocations

    def agent(*args, **kw):
        assert args[6] == invocations
        slow("evidence")
        return {"invocations": invocations}

    def notify(*args, **kw):
        slow("notify")

    def dispatch(*args, **kw):
        slow("dispatch")
        return []

    def backlog(*args):
        slow("backlog")
        return {}

    ports = terminal.ProgramTerminalPorts(
        state=SimpleNamespace(lineage_scope=invariant_locks.lineage_scope, now_iso=lambda: "2026-11-05T10:00:00Z"),
        resident=SimpleNamespace(notify_program_resident_invocation_terminal=notify),
        flow=SimpleNamespace(program_execution_ports=lambda: None, drain_program_event_wire_backlog=backlog),
        directory=SimpleNamespace(lineage_execution_authority=authority, lineage_ids_for_resident=lambda _: [lid]),
    )
    monkeypatch.setattr(lineage_domain, "_program_terminal_ports", lambda: ports)
    def mark(*args, **kwargs):
        invariant_locks.defer_until_state_unlock(lambda: slow("deferred"))
        return [(lid, source, {"id": source})], 1, 0

    monkeypatch.setattr(terminal, "_mark_sources_idle", mark)
    monkeypatch.setattr(terminal, "_match_terminal_nodes", lambda *a, **kw: terminal._TerminalMatch(
        matched_node_count=1, terminal_nodes={(lid, source)}))
    monkeypatch.setattr(terminal.lineage_program_execution, "prepare_operator_completion", lambda *a, **kw: None)
    monkeypatch.setattr(terminal.lineage_program_execution, "finish_agent_completion", agent)
    monkeypatch.setattr(terminal.lineage_program_resident_inputs, "settle_resident_handoffs", settle)
    monkeypatch.setattr(terminal, "dispatch_program_resident_work_finished", dispatch)
    monkeypatch.setattr(terminal, "dispatch_program_resident_terminal_event", dispatch)
    monkeypatch.setattr(terminal, "_settle_paths_by_lineage", lambda _: [])

    def send():
        rid, mid = input_delivery["resident_id"], "msg_" + "7" * 32
        with resident_intake_admission.bind(rid, mid, lambda: authority(lid), lambda _: None):
            result = resident_intake_domain.deliver_resident_message_content(
                rid, "CASE-A", "independent input", message_id=mid)
        admitted.set()
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        completing = pool.submit(lineage_domain._handle_resident_message_terminal,
                                 source, "sample_admin", "msg_original", lineage_id=lid, terminal_status=status)
        try:
            assert blocked.wait(3), completing.result() if completing.done() else "no terminal follow-up"
            sending = pool.submit(send)
            assert admitted.wait(3), "unrelated input blocked on terminal IO"
            assert sending.result()["accepted"]
        finally:
            release.set()
        completing.result()


def test_real_recovery_releases_authority_before_follow_up(runtime_store, monkeypatch):
    from management_portal.society_service import lineage_aggregate
    from tests.cases import society_service_test_support as support

    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", lambda *a: {"message_id": "job_terminal_recovery"})
    graph = support.create_lineage_with_resident_members({
        "title": "Recovery without terminal IO lock", "nodes": [_worker(WORK_A)],
        "program": {"components": [_component("event_source_manual", "event_source", event_type="manual.triggered")],
                    "wires": [_wire("wire_manual_worker", "route", "event_source_manual", WORK_A)]},
    }, ACTOR)
    lid = graph["lineage_id"]
    lineage_domain.emit_program_manual_event(lid, {"event_source_id": "event_source_manual"}, ACTOR)
    support.record_terminal_execution(WORK_A, "job_terminal_recovery", status="completed")
    monkeypatch.setattr(support, "RESIDENT_INPUT_STATE_RESOLVER", lambda *a: {"status": "completed"})
    blocked, release, entered = Event(), Event(), Event()
    finish = terminal.lineage_program_execution.finish_agent_completion

    def slow(*args, **kwargs):
        assert lid not in locks._OWNERS
        blocked.set()
        assert release.wait(5)
        return finish(*args, **kwargs)

    monkeypatch.setattr(terminal.lineage_program_execution, "finish_agent_completion", slow)

    def independent():
        with lineage_aggregate.execution_authority(lid):
            entered.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        recovering = pool.submit(lineage_domain.reconcile_lineage_runtime, lid, ACTOR, recovery_only=True)
        try:
            assert blocked.wait(3), recovering.result() if recovering.done() else "no recovery finish"
            other = pool.submit(independent)
            assert entered.wait(3), "recovery retained its outer authority"
            other.result()
        finally:
            release.set()
        assert recovering.result()["recovered_handoff_count"] == 1
    assert lineage_domain.reconcile_lineage_runtime(lid, ACTOR, recovery_only=True)["recovered_handoff_count"] == 0
