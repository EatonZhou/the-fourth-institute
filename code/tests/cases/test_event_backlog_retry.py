"""A queued Event that fails to deliver waits alone (decision_4adcc1ff2f2c).

Each wire keeps its own order, so one wire whose head keeps failing must not
hold back the other wires of its Lineage, and its retry waits out the delay
however often the Lineage is looked at again.

:module: lineage.service
:design: decision_4adcc1ff2f2c
"""

from __future__ import annotations

import pytest

from management_portal.society_service import (
    component_runtime, lineage_domain, lineage_program_backlog, lineage_program_runtime,
    lineage_program_wire_queue, trigger_paths,
)
from tests.cases import society_service_test_support as support
from tests.cases.test_lineage_program_runtime_integration import (  # noqa: F401
    ACTOR, PRODUCER_A, WORK_A, WORK_B, _component, _wire, _worker, runtime_store,
)

SOURCE = "event_source_backlog"


@pytest.mark.parametrize("wake", ["next_packet", "scheduled_retry"])
@pytest.mark.parametrize("source", ["loop", "resident"])
def test_old_packet_is_delivered_when_resident_becomes_idle(runtime_store, monkeypatch, wake, source):
    """The next Packet cannot overtake a waiting Packet, and retry needs no new Event."""
    busy = True
    sent = []
    monkeypatch.setattr(support, "NODE_ACTIVE_JOB_RESOLVER", lambda node, actor: node["id"] == WORK_A and busy)
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: (
        sent.append(prompt) or {"message_id": f"job_fifo_{len(sent)}"}
    ))
    monkeypatch.setattr(support, "RESIDENT_MESSAGE_EVIDENCE_RESOLVER", lambda resident, message, actor: (
        {"reply": message} if resident == PRODUCER_A else {}
    ))
    wire = "wire_packet_fifo"
    config = {"unavailable_policy": "queue", "queue_limit": 2}
    components, wires = [], [_wire(wire, "route", PRODUCER_A, WORK_A, config=config)]
    if source == "loop":
        components.append({"id": "loop_round", "kind": "loop", "config": {"max_rounds": 2}})
        wires = [
            _wire("wire_loop_in", "route", PRODUCER_A, "loop_round"),
            {**_wire(wire, "route", "loop_round", WORK_A, config=config), "source_port": "continue"},
        ]
    lineage_id = support.create_lineage_with_resident_members({
        "title": "Packet FIFO", "nodes": [_worker(PRODUCER_A), _worker(WORK_A)],
        "program": {"components": components, "wires": wires},
    }, ACTOR)["lineage_id"]
    first = support.complete_resident_message(PRODUCER_A, ACTOR, "job_packet_first", lineage_id=lineage_id)
    assert sent == []
    assert wire in lineage_program_runtime.read_runtime(lineage_id)["event_wires"], first
    backlog = lineage_program_backlog.drain_event_backlog(
        lineage_id, ACTOR, authority=lineage_domain._program_control_authority(),
    )
    # Do not claim a live Resident's queue head just to probe availability.
    assert backlog["attempted_count"] == 0
    busy_retry = backlog["retry_after_seconds"]
    busy = False
    if wake == "next_packet":
        second = support.complete_resident_message(PRODUCER_A, ACTOR, "job_packet_second", lineage_id=lineage_id)
        assert sent == ["job_packet_first", "job_packet_second"], second["program_event_backlog"]
    else:
        if source == "loop":
            component_runtime.set_paused(lineage_id, "loop_round", True, ACTOR)
            lineage_domain.reconcile_lineage_system_events_as_service(lineage_id)
            assert sent == []
            component_runtime.set_paused(lineage_id, "loop_round", False, ACTOR)
        retry = lineage_domain.reconcile_lineage_system_events_as_service(lineage_id)
        assert sent == ["job_packet_first"], retry
    pending = lineage_program_runtime.read_runtime(lineage_id)["event_wires"].get(wire, {})
    assert pending.get("pending_events", []) == []
    assert busy_retry > 0


def test_queue_full_trigger_path_retains_rejected_wire_and_drop_status(runtime_store, monkeypatch):
    """A bounded queue rejection is a visible drop, not a failed delivery."""
    monkeypatch.setattr(support, "NODE_ACTIVE_JOB_RESOLVER", lambda node, actor: True)
    wire = "wire_backlog_full"
    lineage_id = support.create_lineage_with_resident_members({
        "title": "Full queue", "nodes": [_worker(WORK_A)],
        "program": {
            "components": [_component(SOURCE, "event_source", event_type="manual.triggered")],
            "wires": [_wire(wire, "route", SOURCE, WORK_A, config={"unavailable_policy": "queue", "queue_limit": 1})],
        },
    }, ACTOR)["lineage_id"]
    first, second = [
        lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
        for _ in range(2)
    ]

    assert trigger_paths.get(lineage_id, first["trigger_path_id"])["status"] == "active"
    assert second["results"][0]["status"] == "queue_full"
    path = trigger_paths.get(lineage_id, second["trigger_path_id"])
    assert path["status"] == "completed"
    step, = path["steps"]
    assert step["status"] == "skipped"
    outcome, = step["result"]["deliveries"]
    assert (outcome["wire_id"], outcome["status"]) == (wire, "queue_full")


def test_a_failing_wire_waits_alone_and_is_not_retried_before_its_delay(runtime_store, monkeypatch):
    """投递一直失败的连线单独等待，到重试时间前不再尝试

    两条连线各排着一次投递，排在前面的那条投递失败。同一次处理照常投递另一条；随即再处理一次时，
    失败的那条不会提前重试，返回的重试等待时间就是它剩下的间隔。
    """
    busy = {"value": True}
    sent: list[str] = []
    monkeypatch.setattr(support, "NODE_ACTIVE_JOB_RESOLVER", lambda node, actor: busy["value"])
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: (
        sent.append(node["id"]) or {"message_id": f"job_backlog_{len(sent)}"}
    ))
    queue = {"unavailable_policy": "queue"}
    lineage_id = support.create_lineage_with_resident_members({
        "title": "Event backlog retry", "project_id": "project_test",
        "nodes": [_worker(WORK_A), _worker(WORK_B)],
        "program": {
            "components": [_component(SOURCE, "event_source", event_type="manual.triggered")],
            "wires": [_wire("wire_backlog_a", "route", SOURCE, WORK_A, config=queue),
                      _wire("wire_backlog_b", "route", SOURCE, WORK_B, config=queue)],
        },
    }, ACTOR)["lineage_id"]
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
    assert sent == []
    busy["value"] = False

    deliver = lineage_program_backlog.deliver_claimed
    attempts: list[str] = []

    def failing_a(wire_id, *args):
        attempts.append(wire_id)
        if wire_id == "wire_backlog_a":
            raise RuntimeError("target unavailable")
        return deliver(wire_id, *args)

    monkeypatch.setattr(lineage_program_backlog, "deliver_claimed", failing_a)
    authority = lineage_domain._program_control_authority()
    first = lineage_program_backlog.drain_event_backlog(lineage_id, ACTOR, authority=authority)
    assert attempts == ["wire_backlog_a", "wire_backlog_b"]
    assert sent == [WORK_B]
    assert 0 < first["retry_after_seconds"] <= 5

    second = lineage_program_backlog.drain_event_backlog(lineage_id, ACTOR, authority=authority)
    assert attempts == ["wire_backlog_a", "wire_backlog_b"]
    assert 0 < second["retry_after_seconds"] <= first["retry_after_seconds"]
    pending = lineage_program_runtime.read_runtime(lineage_id)["event_wires"]["wire_backlog_a"]["pending_events"]
    assert [item["attempt_count"] for item in pending] == [1]
    assert pending[0]["last_error"] == "target unavailable"


class _CountingAuthority:
    def __init__(self, inner):
        self.inner, self.reads, self.deleted = inner, 0, 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def read_lineage(self, *args, **kwargs):
        self.reads += 1
        return self.inner.read_lineage(*args, **kwargs)

    def delete_exhausted_sources(self, *args):
        self.deleted += 1
        return self.inner.delete_exhausted_sources(*args)


def test_a_drain_reads_the_lineage_again_only_after_a_delivery_attempt(runtime_store, monkeypatch):
    """处理积压只在尝试过投递后重读 Lineage，没有耗尽即删的事件源时不做收尾检查

    两条连线各排着一次投递，其中一条的排头已被别处认领。认领落空不改变任何状态，剩下的连线沿用同一次读取挑选；
    投递一次要重读一次挑选下一条，再加上投递前按事件保存的身份授权读一次。
    """
    busy = {"value": True}
    sent: list[str] = []
    monkeypatch.setattr(support, "NODE_ACTIVE_JOB_RESOLVER", lambda node, actor: busy["value"])
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: (
        sent.append(node["id"]) or {"message_id": f"job_reads_{len(sent)}"}
    ))
    queue = {"unavailable_policy": "queue"}
    lineage_id = support.create_lineage_with_resident_members({
        "title": "Drain reads", "project_id": "project_test",
        "nodes": [_worker(WORK_A), _worker(WORK_B)],
        "program": {
            "components": [_component(SOURCE, "event_source", event_type="manual.triggered")],
            "wires": [_wire("wire_reads_a", "route", SOURCE, WORK_A, config=queue),
                      _wire("wire_reads_b", "route", SOURCE, WORK_B, config=queue)],
        },
    }, ACTOR)["lineage_id"]
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
    busy["value"] = False
    claim = lineage_program_wire_queue.claim_pending_event
    monkeypatch.setattr(lineage_program_wire_queue, "claim_pending_event", lambda lid, wire_id, **kwargs: (
        None if wire_id == "wire_reads_a" else claim(lid, wire_id, **kwargs)
    ))

    authority = _CountingAuthority(lineage_domain._program_control_authority())
    result = lineage_program_backlog.drain_event_backlog(lineage_id, ACTOR, authority=authority)
    assert sent == [WORK_B]
    assert result["complete"] is True
    assert (authority.reads, authority.deleted) == (3, 0)


def test_events_queued_behind_a_closed_source_are_not_claimed_until_it_opens(runtime_store, monkeypatch):
    """事件源关闭期间不认领它排队的投递，打开后立即投递

    事件源关闭时，处理积压不认领也不尝试它排队的 Event，尝试次数不增加；打开事件源后，下一次处理就投递出去。
    """
    busy = {"value": True}
    sent: list[str] = []
    monkeypatch.setattr(support, "NODE_ACTIVE_JOB_RESOLVER", lambda node, actor: busy["value"])
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: (
        sent.append(node["id"]) or {"message_id": f"job_closed_{len(sent)}"}
    ))
    lineage_id = support.create_lineage_with_resident_members({
        "title": "Closed source backlog", "project_id": "project_test", "nodes": [_worker(WORK_A)],
        "program": {
            "components": [_component(SOURCE, "event_source", event_type="manual.triggered")],
            "wires": [_wire("wire_closed", "route", SOURCE, WORK_A, config={"unavailable_policy": "queue"})],
        },
    }, ACTOR)["lineage_id"]
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)
    component_runtime.set_paused(lineage_id, SOURCE, True, ACTOR)
    busy["value"] = False
    authority = lineage_domain._program_control_authority()

    for _ in range(3):
        result = lineage_program_backlog.drain_event_backlog(lineage_id, ACTOR, authority=authority)
        assert result["attempted_count"] == 0
    pending = lineage_program_runtime.read_runtime(lineage_id)["event_wires"]["wire_closed"]["pending_events"]
    assert [item["attempt_count"] for item in pending] == [0]
    assert sent == []

    component_runtime.set_paused(lineage_id, SOURCE, False, ACTOR)
    lineage_program_backlog.drain_event_backlog(lineage_id, ACTOR, authority=authority)
    assert sent == [WORK_A]
