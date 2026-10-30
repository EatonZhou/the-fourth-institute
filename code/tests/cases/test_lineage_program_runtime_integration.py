from __future__ import annotations

from tests.cases import society_service_test_support as lineage_test_support
from tests.cases.society_service_test_support import install_resource_boundary

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from management_portal.hatg.contracts import InstructionError

from management_portal.society_service import lineage_program_backlog
from management_portal.society_service import (
    lineage_domain,
    lineage_program_edits,
    lineage_program_identity,
    lineage_reads,
    lineage_state,
)
from management_portal.society_service import lineage_operator_runtime
from management_portal.society_service import resident_message_contract
from management_portal.society_service import (
    lineage_program_occurrences, lineage_program_resident_inputs, lineage_program_runtime,
    lineage_program_schedules,
)
from management_portal.society_service import lineage_recurring_schedule
from management_portal.society_service import (
    lineage_program_compilation, lineage_program_contracts, state_store,
)
from tests.cases.isa_test_support import ok, reset_kernels
from management_portal.society_service.runtime.backend_adapter import (
    LineageBackendRuntimeAdapter,
)
from management_portal.society_service.runtime.contracts import ResidentTerminalFact
from management_portal.society_service import lineage_program_execution
from management_portal.society_service import lineage_aggregate
from tests.cases.effect_script_support import effect_config


ACTOR = "sample_admin"
PRODUCER_A = "resident_aaaaaaaaaaaa"
PRODUCER_B = "resident_bbbbbbbbbbbb"
REDUCER = "reducer_cccccccccccc"
REDUCER_EXECUTOR = "resident_cccccccccccc"
WORK_A = "resident_dddddddddddd"
WORK_B = "resident_eeeeeeeeeeee"


def _packet_reply(prompt: str) -> str:
    """A Resident reply carrying one structured message Packet."""
    payload = {'key': 'default', 'priority': 0, 'message': {'text': prompt, 'data': {}}}
    return "<packet>" + json.dumps(payload, ensure_ascii=False) + "</packet>"


def _reducer_reply(prompt: str) -> str:
    return _packet_reply(prompt).replace("packet>", "reducer-result>")


@pytest.fixture()
def runtime_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    root = tmp_path / "lineages"
    monkeypatch.setattr(lineage_aggregate, "DATA_ROOT", root)
    monkeypatch.setattr(lineage_state, "LINEAGE_FOLDER_ROOT", root / "lineages")
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(root / "lineages"))
    install_resource_boundary(monkeypatch)
    lineage_test_support.install_registered_lineage_members(monkeypatch)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda _node, _actor: False,
    )
    monkeypatch.setattr(
        lineage_test_support,
        "LATEST_COMPLETED_MESSAGE_RESOLVER",
        lambda _resident_id, _actor: {},
    )
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_INPUT_STATE_RESOLVER",
        None,
    )
    return root


def _worker(resident_id: str) -> dict:
    return {
        "id": resident_id,
        "title": resident_id,
    }


PACKETS: dict[str, str] = {}


def _producer(resident_id: str, packet: str) -> dict:
    """An ordinary Resident whose completed reply emits ``packet``."""

    PACKETS[resident_id] = packet
    return _worker(resident_id)


def _reducer_executor() -> dict:
    return {
        **_worker(REDUCER_EXECUTOR),
        "operator_executor": {
            "component_id": REDUCER,
            "kind": "reducer",
        },
    }


def _reducer_component() -> dict:
    return {
        "id": REDUCER,
        "kind": "reducer",
        "title": "Reducer",
        "config": {
            "operator_implementation": "resident",
            "resident_executor_id": REDUCER_EXECUTOR,
            "prompt": "Reduce.",
            "reduce_strategy": "join",
            "reduce_separator": "\n\n",
            "output_key": "reduced",
        },
    }


def _component(
    component_id: str,
    kind: str,
    **config: str,
) -> dict:
    return {"id": component_id, "kind": kind, "config": config}


DELIVER = effect_config("DeliverMessage", {"resident_id": {"from": "endpoint.target"}}, ["target"])


def _deliver(component_id: str) -> dict:
    """A DeliverMessage Effect sending to the Resident on its ``target`` endpoint wire."""

    return {"id": component_id, "kind": "effect", "config": json.loads(json.dumps(DELIVER))}


def _wire(
    wire_id: str,
    kind: str,
    source: str,
    target: str,
    *,
    config: dict | None = None,
) -> dict:
    wire = {
        "id": wire_id,
        "kind": kind,
        "source": source,
        "target": target,
    }
    if config is not None:
        wire["config"] = config
    return wire


def _watch(
    wire_id: str,
    source: str,
    event_source_id: str,
) -> dict:
    return {
        "id": wire_id,
        "kind": "watch",
        "source": event_source_id,
        "source_port": "watch",
        "target": source,
    }


def _emit_output(lineage: dict, resident_id: str) -> dict:
    """Fire the Event wired to a producer and complete its turn.

    The producer's own delivery does not reach the test's sender, so a test
    observes only what its Packet causes downstream.
    """

    lineage_id = lineage["lineage_id"]
    current = lineage_reads.get_lineage(lineage_id, ACTOR)
    source_id = next(
        wire["source"] for wire in current["program"]["wires"]
        if wire["kind"] == "route" and wire["target"] == resident_id
    )
    job_id = f"job_producer_{resident_id}"
    sender = lineage_test_support.NODE_MESSAGE_SENDER
    resolver = lineage_test_support.RESIDENT_MESSAGE_EVIDENCE_RESOLVER

    def send(node, prompt, actor):
        if str(node.get("id") or "") == resident_id:
            return {"message_id": job_id}
        return sender(node, prompt, actor)

    def evidence(target, message_id, actor):
        if message_id == job_id:
            return {"job_id": job_id, "reply": _packet_reply(PACKETS[resident_id])}
        return resolver(target, message_id, actor) if resolver else {}

    lineage_test_support.NODE_MESSAGE_SENDER = send
    lineage_test_support.RESIDENT_MESSAGE_EVIDENCE_RESOLVER = evidence
    try:
        lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": source_id}, ACTOR)
        return lineage_test_support.complete_resident_message(
            resident_id, ACTOR, job_id, lineage_id=lineage_id,
        )
    finally:
        lineage_test_support.NODE_MESSAGE_SENDER = sender
        lineage_test_support.RESIDENT_MESSAGE_EVIDENCE_RESOLVER = resolver


@pytest.mark.parametrize("structured", [False, True], ids=["plain-reply", "packet-reply"])
def test_chat_turn_routes_output_without_an_incoming_program_invocation(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    structured: bool,
) -> None:
    """普通聊天完成后沿 Resident Output 投递一次

    只有 Resident Output 到 Invoke 的连线也可传递普通回复和结构化 Packet；无需上游 Event 或调用记录，连续回合分别投递，重复完成通知不重复投递。

    :module: lineage.hatg
    :importance: P1
    :design: decision_4adcc1ff2f2c
    :design-point:
    """
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, prompt, _actor: (
            sent.append((node["id"], prompt))
            or {"message_id": f"job_receiver_{len(sent)}"}
        ),
    )
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_MESSAGE_EVIDENCE_RESOLVER",
        lambda _resident_id, message_id, _actor: {
            "reply": _packet_reply(message_id) if structured else message_id,
        },
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Chat replies flow through Resident Output",
            "nodes": [_worker(PRODUCER_A), _worker(WORK_A)],
            "program": {
                "components": [],
                "wires": [
                    _wire("wire_chat_output", "route", PRODUCER_A, WORK_A,
                          config={"include_envelope": False}),
                ],
            },
        },
        ACTOR,
    )
    lineage_id = created["lineage_id"]
    for message_id in ("job_chat_first", "job_chat_second"):
        assert not lineage_program_resident_inputs.resident_handoff(
            lineage_id, PRODUCER_A, message_id,
        )
        lineage_test_support.complete_resident_message(
            PRODUCER_A, ACTOR, message_id, lineage_id=lineage_id,
        )
        # Re-observing the same terminal fact must not deliver it again.
        lineage_domain.handle_resident_message_completion(
            PRODUCER_A, ACTOR, message_id, lineage_id=lineage_id,
        )
    assert sent == [(WORK_A, "job_chat_first"), (WORK_A, "job_chat_second")]


def test_start_requeues_completed_turns_whose_output_a_stop_dropped(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """启动后补上停止前丢失的 Resident Output 转交

    上一进程完成回合后、消费终态前停止，回复没有开启 Trigger Path；新进程启动后补回这类回合，沿 Output 投递一次。已转交、仍由 Program 交接等待、超出回看窗口、由新进程完成或没有 Output 连线的回合不补。

    :module: lineage.hatg
    :importance: P1
    :design: decision_4adcc1ff2f2c
    :design-point:
    """
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.society_service import lost_terminal_facts

    sent: list[tuple[str, str]] = []

    def send(node, prompt, _actor):
        if node["id"] == PRODUCER_A:
            return {"message_id": "job_handoff"}
        sent.append((node["id"], prompt))
        return {"message_id": f"job_receiver_{len(sent)}"}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_MESSAGE_EVIDENCE_RESOLVER",
        lambda _resident_id, message_id, _actor: {"reply": message_id},
    )
    lineage_id = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Dropped terminal facts",
            "nodes": [_worker(PRODUCER_A), _worker(WORK_A)],
            "program": {
                "components": [
                    _component("event_source_manual_producer", "event_source", event_type="manual.triggered"),
                ],
                "wires": [
                    _wire("wire_manual_producer", "route", "event_source_manual_producer", PRODUCER_A),
                    _wire("wire_producer_output", "route", PRODUCER_A, WORK_A,
                          config={"include_envelope": False}),
                ],
            },
        },
        ACTOR,
    )["lineage_id"]
    started_at = "2026-10-14T01:00:00Z"
    finished = {
        (PRODUCER_A, "job_routed"): "2026-10-14T00:50:00Z",
        (PRODUCER_A, "job_dropped"): "2026-10-14T00:55:00Z",
        (PRODUCER_A, "job_handoff"): "2026-10-14T00:56:00Z",
        (PRODUCER_A, "job_too_old"): "2026-10-14T00:20:00Z",
        (PRODUCER_A, "job_this_process"): "2026-10-14T00:59:00Z",
        (WORK_A, "job_no_output_wire"): "2026-10-14T00:58:00Z",
    }
    lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": "event_source_manual_producer"}, ACTOR)
    assert lineage_program_resident_inputs.resident_handoff(lineage_id, PRODUCER_A, "job_handoff")
    for (resident_id, message_id), at in finished.items():
        lineage_test_support.record_terminal_execution(resident_id, message_id, ACTOR, at=at)
    lineage_test_support.complete_resident_message(PRODUCER_A, ACTOR, "job_routed", lineage_id=lineage_id)
    get_agent_data_store().database.write(lambda connection: connection.execute(
        "UPDATE executions SET final_message_id='msg_final_' || execution_id",
    ))

    class Inputs:
        """The previous process finalized every input except the one this process did."""

        def input_state(self, resident_id, message_id):
            if message_id == "job_this_process":
                return {"state": "completed", "updated_at": "2026-10-14T01:00:05Z"}
            return {"state": "completed", "updated_at": finished[(resident_id, message_id)]}

        def message_business_ref(self, _resident_id, _message_id):
            return ""

    monkeypatch.setattr(lost_terminal_facts, "_repository", Inputs)
    facts = lost_terminal_facts.find(started_at)
    assert [(fact["resident_id"], fact["message_id"]) for fact in facts] == [(PRODUCER_A, "job_dropped")]
    assert facts[0]["actor"] == ACTOR and facts[0]["terminal_status"] == "completed"

    LineageBackendRuntimeAdapter().consume_terminal(ResidentTerminalFact.from_payload(facts[0]))
    assert sent == [(WORK_A, "job_routed"), (WORK_A, "job_dropped")]
    # The routed reply named its Execution, so a later start finds nothing.
    assert lost_terminal_facts.find(started_at) == []


@pytest.mark.parametrize("maximum", [None, 0, 1, 2])
def test_output_route_message_limit_and_reset(runtime_store: Path, monkeypatch, maximum) -> None:
    """Resident 输出边限额与幂等重置

    覆盖无限次、0 次与有限次数；成功消息计数、重复完成通知去重，重置不改变图版本且允许后续新消息。

    :module: lineage.hatg
    :importance: P1
    :design: decision_4adcc1ff2f2c
    :design-point:
    """
    sent = []
    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: (
        sent.append(prompt) or {"message_id": f"job_limit_{len(sent)}"}
    ))
    monkeypatch.setattr(lineage_test_support, "RESIDENT_MESSAGE_EVIDENCE_RESOLVER",
                        lambda resident, message, actor: {"reply": message})
    lineage = lineage_test_support.create_lineage_with_resident_members({
        "title": "Output delivery budget", "nodes": [_worker(PRODUCER_A), _worker(WORK_A)],
        "program": {"components": [], "wires": [
            _wire("wire_message_limit", "route", PRODUCER_A, WORK_A,
                  config={"include_envelope": False, "max_messages": maximum}),
        ]},
    }, ACTOR)
    lineage_id = lineage["lineage_id"]

    def output(message):
        return lineage_test_support.complete_resident_message(PRODUCER_A, ACTOR, message, lineage_id=lineage_id)

    def view():
        return lineage_reads.get_lineage(lineage_id, ACTOR)["program_invoke_wire_runtime"]["wire_message_limit"]

    for message in ("job_first", "job_second", "job_third"):
        output(message)
        lineage_domain.handle_resident_message_completion(PRODUCER_A, ACTOR, message, lineage_id=lineage_id)
    expected = 3 if maximum is None else maximum
    assert len(sent) == expected
    assert view()["sent_count"] == expected
    assert view()["remaining_messages"] == (None if maximum is None else 0)
    reset_kernels()
    for edited_limit in (0, maximum):
        ok("UpdateWire", {"lineage_id": lineage_id, "wire_id": "wire_message_limit",
                          "changes": {"config": {"max_messages": edited_limit, "include_envelope": False}}})
        assert view()["sent_count"] == expected
    before = lineage_reads.get_lineage(lineage_id, ACTOR)["revision"]
    reset_kernels()
    reset = ok("ResetComponentRuntime", {"lineage_id": lineage_id, "wire_id": "wire_message_limit"}, key="reset-delivery-count")
    assert reset["runtime"]["sent_count"] == 0
    assert reset["runtime"]["generation"] == 1
    assert lineage_reads.get_lineage(lineage_id, ACTOR)["revision"] == before
    ok("ResetComponentRuntime", {"lineage_id": lineage_id, "wire_id": "wire_message_limit"}, key="reset-delivery-count")
    assert view()["generation"] == 1
    lineage_domain.handle_resident_message_completion(PRODUCER_A, ACTOR, "job_first", lineage_id=lineage_id)
    assert len(sent) == expected
    output("job_after_reset")
    assert len(sent) == expected + (maximum != 0)
    assert view()["sent_count"] == int(maximum != 0)


def test_route_budget_reservations_are_atomic_and_survive_reset(runtime_store: Path) -> None:
    """Route 并发占位与重置后去重

    并发投递不超限，明确未提交可释放占位，确认只计一次；重置保留旧消息去重和未确认占位。

    :module: lineage.hatg
    :importance: P1
    :design: decision_4adcc1ff2f2c
    :design-point:
    """
    from concurrent.futures import ThreadPoolExecutor
    from management_portal.society_service import wire_delivery_budget as budget

    lineage = lineage_test_support.create_lineage_with_resident_members({
        "title": "Concurrent admission", "nodes": [_worker(PRODUCER_A), _worker(WORK_A)],
        "program": {"components": [], "wires": [_wire("wire_admission", "route", PRODUCER_A, WORK_A, config={"max_messages": 1})]},
    }, ACTOR)
    lineage_id, wire_id = lineage["lineage_id"], "wire_admission"
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda index: budget.reserve(lineage_id, wire_id, f"message_{index}", 1), range(4)))
    assert [r["status"] for r in receipts].count("reserved") == 1
    admitted = f"message_{next(i for i, r in enumerate(receipts) if r['status'] == 'reserved')}"
    assert budget.reserve(lineage_id, wire_id, admitted, 1)["status"] == "reserved"
    budget.release(lineage_id, wire_id, admitted)
    assert budget.reserve(lineage_id, wire_id, "message_retry", 1)["status"] == "reserved"
    budget.accept(lineage_id, wire_id, "message_retry", "accepted_retry")
    budget.accept(lineage_id, wire_id, "message_retry", "accepted_retry")
    assert budget.views(lineage_id, {wire_id: {"max_messages": 1}})[wire_id]["sent_count"] == 1
    budget.reset(lineage_id, wire_id, ACTOR)
    assert budget.reserve(lineage_id, wire_id, "message_retry", 1)["status"] == "sent"
    assert budget.views(lineage_id, {wire_id: {"max_messages": 1}})[wire_id]["sent_count"] == 0
    assert budget.reserve(lineage_id, wire_id, "message_next", 1)["status"] == "reserved"
    # Reset counts accepted messages only; it cannot erase an uncertain send.
    budget.reset(lineage_id, wire_id, ACTOR)
    assert budget.reserve(lineage_id, wire_id, "message_other", 1)["status"] == "limited"
    budget.accept(lineage_id, wire_id, "message_next", "accepted_next")
    assert budget.views(lineage_id, {wire_id: {"max_messages": 1}})[wire_id]["sent_count"] == 1


def test_queued_route_deliveries_share_the_message_budget(runtime_store: Path, monkeypatch) -> None:
    """Route 排队投递遵守发送上限

    尚未发送的等待消息不计数，重放队列在成功投递后耗尽额度，剩余消息丢弃。

    :module: lineage.hatg
    :importance: P1
    :design: decision_4adcc1ff2f2c
    :design-point:
    """
    busy, sent = {"value": True}, []
    attempts = []
    def send(node, prompt, actor):
        attempts.append(prompt)
        if len(attempts) == 1:
            busy["value"] = True
            raise InstructionError("instruction.failed", "Known uncommitted delivery", commit_state="not_committed")
        sent.append(prompt)
        return {"message_id": f"job_queued_{len(sent)}"}
    monkeypatch.setattr(lineage_test_support, "NODE_ACTIVE_JOB_RESOLVER", lambda node, actor: busy["value"])
    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(lineage_test_support, "RESIDENT_MESSAGE_EVIDENCE_RESOLVER", lambda resident, message, actor: {"reply": message})
    lineage = lineage_test_support.create_lineage_with_resident_members({
        "title": "Queued delivery budget",
        "nodes": [_worker(PRODUCER_A), _worker(WORK_A)],
        "program": {"components": [], "wires": [
            _wire("wire_queued_limit", "route", PRODUCER_A, WORK_A,
                  config={"include_envelope": False, "unavailable_policy": "queue", "max_messages": 1}),
        ]},
    }, ACTOR)
    lineage_id = lineage["lineage_id"]
    for message in ("job_queue_one", "job_queue_two", "job_queue_three"):
        lineage_test_support.complete_resident_message(PRODUCER_A, ACTOR, message, lineage_id=lineage_id)
    before = lineage_reads.get_lineage(lineage_id, ACTOR)["program_invoke_wire_runtime"]["wire_queued_limit"]
    assert (before["pending_count"], before["sent_count"]) == (3, 0)
    busy["value"] = False
    lineage_program_backlog.drain_event_backlog(lineage_id, ACTOR, authority=lineage_domain._program_control_authority())
    failed = lineage_reads.get_lineage(lineage_id, ACTOR)["program_invoke_wire_runtime"]["wire_queued_limit"]
    assert (failed["pending_count"], failed["sent_count"], failed["reserved_count"]) == (2, 0, 0)
    busy["value"] = False
    for _ in range(2):
        lineage_program_backlog.drain_event_backlog(lineage_id, ACTOR, authority=lineage_domain._program_control_authority())
    after = lineage_reads.get_lineage(lineage_id, ACTOR)["program_invoke_wire_runtime"]["wire_queued_limit"]
    assert attempts == ["job_queue_one", "job_queue_two"]
    assert sent == ["job_queue_two"]
    assert (after["pending_count"], after["sent_count"], after["message_limit_reached"]) == (0, 1, True)


def test_terminal_fact_reaches_observer_in_another_lineage(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 终态通知跨 Lineage 观察者触发投递"""
    sent: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_observer_{len(sent)}"}
        ),
    )
    source = lineage_test_support.create_lineage_with_resident_members(
        { "title": "Source Lineage", "nodes": [_worker(WORK_A)]},
        ACTOR,
    )
    observer = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "External observer Lineage",
            "nodes": [_worker(WORK_B)],
            "program": {
                "components": [
                    _component(
                        "event_source_external_done",
                        "event_source",
                        event_type="resident.work_finished",
                    )
                ],
                "wires": [
                    _watch(
                        "wire_watch_external_done",
                        WORK_A,
                        "event_source_external_done",
                    ),
                    _wire(
                        "wire_external_done_target",
                        'route',
                        "event_source_external_done",
                        WORK_B,
                    ),
                ],
            },
        },
        ACTOR,
    )

    lineage_test_support.record_terminal_execution(WORK_A, "job_external_source_done", ACTOR)
    result = LineageBackendRuntimeAdapter().consume_terminal(
        ResidentTerminalFact(
            resident_id=WORK_A,
            actor=ACTOR,
            message_id="job_external_source_done",
            lineage_id=source["lineage_id"],
        )
    )
    duplicate = LineageBackendRuntimeAdapter().consume_terminal(
        ResidentTerminalFact(
            resident_id=WORK_A,
            actor=ACTOR,
            message_id="job_external_source_done",
            lineage_id=source["lineage_id"],
        )
    )

    assert sent == [WORK_B]
    assert result["resident_observers"]["observer_lineage_count"] == 1
    assert result["resident_observers"]["dispatches"][0][
        "lineage_id"
    ] == observer["lineage_id"]
    assert result["resident_observers"]["event_source_count"] == 1
    assert result["resident_observers"]["failures"] == []
    assert duplicate["resident_observers"]["observer_lineage_count"] == 1


def test_effect_executes_against_external_residents_owned_lineage(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Effect 向外部 Lineage 的 Resident 投递并记录完整路径"""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, prompt, _actor: (
            sent.append((str(node["id"]), prompt))
            or {"message_id": "job_external_effect"}
        ),
    )
    lineage_test_support.create_lineage_with_resident_members(
        { "title": "Effect target Lineage", "nodes": [_worker(WORK_B)]},
        ACTOR,
    )
    authoring = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Effect authoring Lineage",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_external_effect",
                        "event_source",
                        event_type="manual.triggered",
                    ),
                    _deliver("effect_external_message"),
                ],
                "wires": [
                    _wire(
                        "wire_external_effect_invoke",
                        'route',
                        "event_source_external_effect",
                        "effect_external_message",
                    ),
                    _wire(
                        "wire_external_effect_target",
                        "target",
                        "effect_external_message",
                        WORK_B,
                    ),
                ],
            },
        },
        ACTOR,
    )
    event = lineage_domain.lineage_primitives.make_event(
        "manual.triggered",
        payload={"prompt": "Continue external work."},
        correlation="external-effect",
    )

    result = lineage_domain.dispatch_lineage_program_event(
        authoring["lineage_id"],
        "event_source_external_effect",
        event,
        ACTOR,
        cursor="manual:external-effect",
    )

    assert sent == [(WORK_B, "Continue external work.")]
    assert result["results"][0]["status"] == "executed"
    outputs = result["results"][0]["typed_outputs"]
    assert outputs["message_id"] == "job_external_effect"
    assert outputs["resident_id"] == WORK_B
    definition = lineage_reads.get_lineage(
        authoring["lineage_id"],
        ACTOR,
    )
    assert json.loads(json.dumps(definition)) == definition
    effect_runtime = definition["program_effect_runtime"]["effect_external_message"]
    assert effect_runtime["status"] == "executed"
    assert effect_runtime["outputs"] == outputs

    # The source Path belongs to the authoring Lineage, while the Resident
    # execution belongs to its recipient. Membership must not hide that link.
    from management_portal.society_service import resident_execution_links, trigger_paths
    execution_id = lineage_test_support.record_terminal_execution(WORK_B, outputs["message_id"], ACTOR)
    with state_store.get_lineage_data_store().database.transaction() as connection:
        assert resident_execution_links.link_inputs(WORK_B, {
            "execution_id": execution_id, "user_message_id": outputs["message_id"],
            "started_at": "2026-10-14T00:00:00Z",
        }, connection=connection)
    path_id = lineage_program_identity.trigger_path_id(authoring["lineage_id"], event.id)
    path = trigger_paths.get(authoring["lineage_id"], path_id)
    assert path["status"] == "completed"
    assert any(step["kind"] == "resident" and step["node_id"] == WORK_B for step in path["steps"])


@pytest.mark.parametrize("explicit_effect", [False, True], ids=["resident-invoke", "deliver-effect"])
def test_resident_invoke_and_effect_share_isa_delivery_and_durable_path(runtime_store, monkeypatch, explicit_effect):
    """Resident 调用与 Effect 共用 ISA 幂等投递及持久路径"""
    from management_portal.hatg import routing
    from management_portal.society_service import resident_protocol, resident_execution_links, trigger_paths

    delivered = []
    calls = []
    execute = routing.execute

    def dispatch(call, context, instruction_id=""):
        result = execute(call, context, instruction_id=instruction_id)
        if call.get("instruction") == "DeliverMessage":
            calls.append((call, context, result))
        return result

    def receive(payload, actor, **options):
        delivered.append(payload)
        return {"resident_id": payload["resident_id"], "message_id": payload["message"]["message_id"]}

    monkeypatch.setattr(routing, "execute", dispatch)
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", receive)
    source = "event_source_delivery"
    components = [_component(source, "event_source", event_type="manual.triggered")]
    wires = [_wire("wire_delivery", 'route', source, "effect_delivery" if explicit_effect else WORK_B,
                   config={"include_envelope": False})]
    if explicit_effect:
        components.append(_deliver("effect_delivery"))
        wires.append(_wire("wire_target", "target", "effect_delivery", WORK_B))
    lineage = lineage_test_support.create_lineage_with_resident_members({
        "title": "Canonical Resident delivery", "nodes": [_worker(WORK_B)],
        "program": {"components": components, "wires": wires},
    }, ACTOR)
    lineage_id = lineage["lineage_id"]
    event = lineage_domain.lineage_primitives.make_event("manual.triggered", payload={"prompt": "Verify delivery."})
    first = lineage_domain.dispatch_lineage_program_event(lineage_id, source, event, ACTOR, cursor=event.id)
    assert first["results"][0]["status"] == ("executed" if explicit_effect else "sent")
    assert len(calls) == len(delivered) == 1
    call, context, receipt = calls[0]
    path_id = lineage_program_identity.trigger_path_id(lineage_id, event.id)
    assert receipt.status == "committed" and receipt.instruction_id
    assert context.entry == "lineage" and context.trigger_path_id == path_id and context.caller == ACTOR
    assert call["idempotency_key"]
    assert delivered[0]["message"]["content_parts"] == [{"type": "text", "text": "Verify delivery."}]
    # ISA replay and Event replay each preserve one delivery and one admitted input.
    assert routing.execute(call, context).replayed
    lineage_domain.dispatch_lineage_program_event(lineage_id, source, event, ACTOR, cursor=event.id)
    assert len(delivered) == 1
    message_id = delivered[0]["message"]["message_id"]
    path = trigger_paths.get(lineage_id, path_id)
    assert path["status"] == "active"
    leaf = next(step for step in path["steps"] if step["kind"] == "resident")
    parent = next(step for step in path["steps"] if step["kind"] == ("effect" if explicit_effect else "event"))
    assert any(edge["source_step_id"] == parent["step_id"] and edge["target_step_id"] == leaf["step_id"] for edge in path["edges"])
    execution_id = lineage_test_support.record_terminal_execution(WORK_B, message_id, ACTOR)
    with state_store.get_lineage_data_store().database.transaction() as connection:
        assert resident_execution_links.link_inputs(WORK_B, {
            "execution_id": execution_id, "user_message_id": message_id,
            "started_at": "2026-10-14T00:00:00Z",
        }, connection=connection)
        assert connection.execute("SELECT count(*) AS n FROM trigger_path_execution_links WHERE step_id=%s AND execution_id=%s",
                                  (leaf["step_id"], execution_id)).fetchone()["n"] == 1
    assert trigger_paths.get(lineage_id, path_id)["status"] == "completed"


def test_send_prompt_accepts_canonical_resident_delivery_acknowledgement_contracts(subtests, isolated_portal_case):
    """发送提示接受标准 Resident 回执且周期日历使用下次时点"""
    with subtests.test(contract='test_send_prompt_accepts_canonical_resident_delivery_acknowledgement'), isolated_portal_case() as case_path:
        message_id = "msg_0123456789abcdef0123456789abcdef"

        assert resident_message_contract.delivered_message_id(
            {"resident_id": WORK_B, "message_id": message_id}
        ) == message_id
        assert resident_message_contract.delivered_message_id(
            {"message": {"message_id": message_id}}
        ) == message_id
    with subtests.test(contract='test_recurring_calendar_plan_uses_iana_timezone'), isolated_portal_case() as case_path:
        next_at = lineage_recurring_schedule.first_after(
            {
                "recurrence_kind": "calendar",
                "timezone": "Asia/Hong_Kong",
                "calendar_frequency": "weekly",
                "calendar_time": "09:30",
                "calendar_weekdays": "mon,wed",
                "misfire_policy": "coalesce_latest",
                "overlap_policy": "drop",
            },
            datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc),
        )

        assert next_at == datetime(2026, 8, 19, 1, 30, tzinfo=timezone.utc)


def _fixed_work_finished_program() -> dict:
    return lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Work-finished source contract",
            "nodes": [
                _producer(PRODUCER_A, "Continue under the matched contract."),
                _worker(WORK_A),
                _worker(WORK_B),
            ],
            "program": {
                "components": [
                    _component(
                        "event_source_work_done",
                        "event_source",
                        event_type="resident.work_finished",
                    ),
                    _deliver("effect_send_work_b"),
                ],
                "wires": [
                    _watch(
                        "wire_watch_work_done",
                        WORK_A,
                        "event_source_work_done",
                    ),
                    _wire(
                        "wire_done_role",
                        'route',
                        "event_source_work_done",
                        PRODUCER_A,
                    ),
                    _wire(
                        "wire_role_effect",
                        'route',
                        PRODUCER_A,
                        "effect_send_work_b",
                    ),
                    _wire(
                        "wire_effect_target",
                        "target",
                        "effect_send_work_b",
                        WORK_B,
                    ),
                ],
            },
        },
        ACTOR,
    )


def _direct_work_finished_program(
    *,
    target_wire_config: dict | None = None,
) -> dict:
    return lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Durable work-finished delivery",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    _component(
                        "event_source_direct_work_done",
                        "event_source",
                        event_type="resident.work_finished",
                    )
                ],
                "wires": [
                    _watch(
                        "wire_watch_direct_work_done",
                        WORK_A,
                        "event_source_direct_work_done",
                    ),
                    _wire(
                        "wire_direct_work_done_target",
                        'route',
                        "event_source_direct_work_done",
                        WORK_B,
                        config=target_wire_config,
                    ),
                ],
            },
        },
        ACTOR,
    )


def test_work_finished_event_exposes_message_and_job_identity(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完成事件暴露原消息与执行任务身份"""
    del runtime_store
    prompts: list[str] = []

    def send(_node: dict, prompt: str, _actor: str) -> dict:
        prompts.append(prompt)
        return {"message_id": "msg_work_finished_contract_target"}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    created = _direct_work_finished_program(
        target_wire_config={
            "invoke_profiles": {
                "agent": {
                    "arguments": {
                        "prompt": {
                            "concat": [
                                {"from": "event.payload.message_id"},
                                {"value": "|"},
                                {"from": "event.payload.execution_id"},
                            ],
                            "separator": "",
                        }
                    }
                }
            }
        }
    )

    lineage_test_support.emit_resident_completion(
        str(created["lineage_id"]),
        WORK_A,
        "msg_source_work_finished_contract",
        ACTOR,
        ports=lineage_domain._program_terminal_ports(),
    )

    assert len(prompts) == 1
    assert (
        "msg_source_work_finished_contract|" + lineage_test_support.record_terminal_execution(WORK_A, "msg_source_work_finished_contract")
        in prompts[0]
    )


def _make_work_finished_retry_due(lineage_id: str) -> None:
    """Advance the stored retry deadline without sleeping or replaying delivery."""
    with lineage_program_runtime._runtime_transaction(lineage_id) as state:
        pending = state["event_wires"]["wire_direct_work_done_target"]["pending_events"]
        assert len(pending) == 1 and pending[0].get("retry_at")
        pending[0]["retry_at"] = "2000-01-01T00:00:00Z"


def test_work_finished_delivery_failure_remains_pending_until_persisted(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完成事件投递失败保持待重试直到输入持久化"""
    del runtime_store
    from management_portal.hatg import routing
    from management_portal.hatg.contracts import failed_result

    attempts = 0
    execute = routing.execute

    def dispatch(call, context, instruction_id=""):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # Transport failure before reaching the instruction owner leaves no stored receipt.
            return failed_result("inst_lin_unavailable", "DeliverMessage", InstructionError(
                "instruction.owner_unavailable", "Resident input store unavailable", retryable=True))
        return execute(call, context, instruction_id=instruction_id)

    monkeypatch.setattr(routing, "execute", dispatch)
    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", lambda *_: {"message_id": "msg_durable_work_finished"})
    created = _direct_work_finished_program()
    lineage_id = str(created["lineage_id"])

    failed = lineage_test_support.emit_resident_completion(
        lineage_id,
        WORK_A,
        "msg_source_work_finished",
        ACTOR,
        ports=lineage_domain._program_terminal_ports(),
    )[0]

    assert failed["results"][0]["status"] == "failed"
    assert failed["results"][0]["retry_pending"] is True
    pending = lineage_program_runtime.read_runtime(lineage_id)[
        "event_wires"
    ]["wire_direct_work_done_target"]["pending_events"]
    assert len(pending) == 1
    assert pending[0]["attempt_count"] == 1
    assert pending[0]["last_error"] == "Resident input store unavailable"

    _make_work_finished_retry_due(lineage_id)
    drained = lineage_program_backlog.drain_event_backlog(
        lineage_id,
        ACTOR,
     authority=lineage_domain._program_control_authority())

    assert drained["delivered_count"] == 1, json.dumps(drained)
    assert attempts == 2
    runtime = lineage_program_runtime.read_runtime(lineage_id)
    assert "wire_direct_work_done_target" not in runtime["event_wires"]
    assert [
        item["message_id"] for item in lineage_program_resident_inputs.pending_resident_handoffs(lineage_id)
    ] == ["msg_durable_work_finished"]

    duplicate = lineage_test_support.emit_resident_completion(
        lineage_id,
        WORK_A,
        "msg_source_work_finished",
        ACTOR,
        ports=lineage_domain._program_terminal_ports(),
    )[0]
    assert duplicate["duplicate"] is True
    assert attempts == 2


@pytest.mark.parametrize("queued,retryable", [(False, False), (True, False), (False, True)],
                         ids=["immediate", "backlog", "stored-failure-replay"])
def test_resident_delivery_terminal_failure_settles_path(runtime_store, monkeypatch, queued, retryable):
    """Resident 投递终止失败时结束路径并清空积压"""
    from management_portal.society_service import trigger_paths

    attempts = []

    def send(*args):
        attempts.append(args)
        raise InstructionError("instruction.forbidden", "Delivery denied", retryable=retryable)

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(lineage_test_support, "NODE_ACTIVE_JOB_RESOLVER",
                        lambda node, actor: queued and node["id"] == WORK_B)
    lineage_id = _direct_work_finished_program(target_wire_config={"unavailable_policy": "queue"})["lineage_id"]
    result = lineage_test_support.emit_resident_completion(
        lineage_id, WORK_A, "msg_permanent_failure", ACTOR,
        ports=lineage_domain._program_terminal_ports(),
    )[0]
    if queued:
        assert not attempts
        monkeypatch.setattr(lineage_test_support, "NODE_ACTIVE_JOB_RESOLVER", lambda *_: False)
    if retryable:
        _make_work_finished_retry_due(lineage_id)
    if queued or retryable:
        lineage_program_backlog.drain_event_backlog(
            lineage_id, ACTOR, authority=lineage_domain._program_control_authority())
    assert len(attempts) == 1
    assert lineage_program_runtime.read_runtime(lineage_id)["event_wires"] == {}
    path = trigger_paths.get(lineage_id, result["trigger_path_id"])
    assert path["status"] == "failed"
    delivery = next(step for step in path["steps"] if step["kind"] == "operator")
    assert delivery["status"] == "failed" and delivery["result"]["error"] == "Delivery denied"


def test_work_finished_retry_is_idempotent_after_input_persisted_before_ack(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """输入已持久化但回执失败时重试不重复发送"""
    del runtime_store
    persisted_messages: set[str] = set()
    send_attempts = 0
    register_attempts = 0

    def send(_node: dict, _prompt: str, _actor: str) -> dict:
        nonlocal send_attempts
        send_attempts += 1
        persisted_messages.add("msg_idempotent_work_finished")
        return {"message_id": "msg_idempotent_work_finished"}

    real_register = lineage_domain._register_program_agent_invocation

    def register(*args: object, **kwargs: object) -> dict:
        nonlocal register_attempts
        register_attempts += 1
        if register_attempts == 1:
            raise RuntimeError("crash after Resident input commit")
        return real_register(*args, **kwargs)

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(
        lineage_domain,
        "_register_program_agent_invocation",
        register,
    )
    created = _direct_work_finished_program()
    lineage_id = str(created["lineage_id"])

    failed = lineage_test_support.emit_resident_completion(
        lineage_id,
        WORK_A,
        "msg_source_ack_race",
        ACTOR,
        ports=lineage_domain._program_terminal_ports(),
    )[0]
    assert failed["results"][0]["retry_pending"] is True
    assert persisted_messages == {"msg_idempotent_work_finished"}

    _make_work_finished_retry_due(lineage_id)
    drained = lineage_program_backlog.drain_event_backlog(
        lineage_id,
        ACTOR,
     authority=lineage_domain._program_control_authority())

    assert drained["delivered_count"] == 1, json.dumps(drained)
    assert send_attempts == 1  # The committed ISA receipt completes handoff without redelivery.
    assert register_attempts == 2
    assert persisted_messages == {"msg_idempotent_work_finished"}
    runtime = lineage_program_runtime.read_runtime(lineage_id)
    assert runtime["event_wires"] == {}
    assert len(lineage_program_resident_inputs.pending_resident_handoffs(lineage_id)) == 1


def test_completion_starts_a_new_path_and_does_not_wait_for_descendant_execution(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 完成开启新路径且不等待下游执行结束"""
    del runtime_store
    sent: list[tuple[str, str, str]] = []

    def send(node: dict, prompt: str, _actor: str) -> dict:
        job_id = f"job_agent_{len(sent) + 1}"
        sent.append((str(node["id"]), job_id, prompt))
        return {"message_id": job_id}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_MESSAGE_EVIDENCE_RESOLVER",
        lambda resident_id, job_id, _actor: {
            "job_id": job_id,
            "reply": f"completed output from {resident_id}",
        },
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Ordinary Agent Packet run",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    _component(
                        "event_source_manual_agent",
                        "event_source",
                        event_type="manual.triggered",
                    ),
                    {
                        "id": "event_source_team_finished",
                        "kind": "event_source",
                        "config": {
                            "event_type": "residents.all_finished",
                            "resident_ids": [WORK_A, WORK_B],
                            "trigger_path_id": "$trigger_path.id",
                        },
                    },
                ],
                "wires": [
                    _wire(
                        "wire_manual_agent_a",
                        'route',
                        "event_source_manual_agent",
                        WORK_A,
                    ),
                    _wire("wire_manual_agent_b", 'route', "event_source_manual_agent", WORK_B),
                    _wire(
                        "wire_agent_a_packet_b",
                        'route',
                        WORK_A,
                        WORK_B,
                    ),
                    _wire(
                        "wire_team_finished_a",
                        'route',
                        "event_source_team_finished",
                        WORK_A,
                    ),
                ],
            },
        },
        ACTOR,
    )
    started = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {
            "event_source_id": "event_source_manual_agent",
            "expected_revision": created["revision"],
        },
        ACTOR,
    )
    assert started["matched_count"] == 2
    assert sent[0][0] == WORK_A
    completed_a = lineage_test_support.complete_resident_message(
        WORK_A,
        ACTOR,
        sent[0][1],
        lineage_id=created["lineage_id"],
    )
    assert completed_a["agent_result_count"] == 1
    assert sent[2][0] == WORK_B
    assert "completed output" in sent[2][2]
    parent = completed_a["agent_results"][0]["invocations"][0]["trigger_path_id"]
    descendants = completed_a["agent_results"][0]["packet_results"]
    assert descendants
    assert completed_a["agent_results"][0]["all_finished_results"] == []

    completed_b = lineage_test_support.complete_resident_message(
        WORK_B,
        ACTOR,
        sent[1][1],
        lineage_id=created["lineage_id"],
    )
    assert completed_b["agent_result_count"] == 1
    assert sent[3][0] == WORK_A
    finished = completed_b["agent_results"][0]["all_finished_results"]
    assert len(finished) == 1
    assert finished[0]["event"]["payload"]["trigger_path_id"] != parent
    assert finished[0]["event"]["payload"]["cause"]["kind"] == "execution"
    # The child input at sent[2] has not completed; it cannot delay its parent.
    from management_portal.society_service import trigger_paths
    pending = trigger_paths.resident_inputs(created["lineage_id"])
    assert any(row["message_id"] == sent[2][1] and row["status"] != "completed" for row in pending)

    current = lineage_reads.get_lineage(created["lineage_id"], ACTOR)
    node_b = next(node for node in current["nodes"] if node["id"] == WORK_B)
    assert node_b["last_execution"]["status"] == "completed"


@pytest.mark.parametrize("prompt_override", [None, "Use this edited packet only."])
def test_packet_to_agent_uses_invoke_queue_and_route_envelope_policy(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt_override: str | None,
) -> None:
    """发往 agent 的包遵循调用队列与连线封装策略"""
    busy = {"value": True}
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda node, _actor: (
            busy["value"] and str(node.get("id") or "") == WORK_A
        ),
    )
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, prompt, _actor: (
            sent.append((str(node["id"]), prompt))
            or {"message_id": "job_packet_agent"}
        ),
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Packet direct Invoke queue",
            "nodes": [
                _producer(PRODUCER_A, "Apply the exact queued packet."),
                _worker(WORK_A),
            ],
            "program": {
                "components": [
                    _component(
                        "event_source_manual_packet",
                        "event_source",
                        event_type="manual.triggered",
                    )
                ],
                "wires": [
                    _wire(
                        "wire_manual_packet",
                        'route',
                        "event_source_manual_packet",
                        PRODUCER_A,
                    ),
                    _wire(
                        "wire_packet_agent",
                        'route',
                        PRODUCER_A,
                        WORK_A,
                        config={
                            "include_envelope": False,
                            "unavailable_policy": "queue",
                            "queue_limit": 3,
                            "expires_after_seconds": 60,
                        },
                    ),
                ],
            },
        },
        ACTOR,
    )

    started = _emit_output(created, PRODUCER_A)
    assert sent == []
    queued = lineage_reads.get_lineage(created["lineage_id"], ACTOR)
    runtime = queued["program_invoke_wire_runtime"][
        "wire_packet_agent"
    ]
    assert runtime["unavailable_policy"] == "queue"
    assert runtime["pending_count"] == 1
    assert started["agent_result_count"] == 1
    delivery = started["agent_results"][0]["packet_results"][0]["deliveries"][0]
    assert (delivery["status"], delivery["reason"]) == ("queued", "resident_running")

    if prompt_override is not None:
        pending = lineage_domain.get_pending_deliveries(
            created["lineage_id"], {"wire_id": "wire_packet_agent"}, ACTOR,
        )["deliveries"][0]
        lineage_domain.update_pending_delivery(created["lineage_id"], {
            "wire_id": "wire_packet_agent",
            "delivery_id": pending["delivery_id"],
            "expected_delivery_revision": pending["revision"],
            "prompt": prompt_override,
        }, ACTOR)

    busy["value"] = False
    drained = lineage_program_backlog.drain_event_backlog(
        created["lineage_id"],
        ACTOR,
     authority=lineage_domain._program_control_authority())

    assert drained["delivered_count"] == 1, json.dumps(drained)
    assert sent == [(WORK_A, prompt_override or "Apply the exact queued packet.")]
    current = lineage_reads.get_lineage(created["lineage_id"], ACTOR)
    assert current["program_invoke_wire_runtime"][
        "wire_packet_agent"
    ]["pending_count"] == 0


def test_backlog_consumes_pending_event_with_existing_resident_invocation(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """积压事件已有 Resident 调用时直接消费且不重复发送"""
    busy = {"value": True}
    sent: list[str] = []
    wire_id = "wire_packet_agent"
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda node, _actor: (
            busy["value"] and str(node.get("id") or "") == WORK_A
        ),
    )
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": "job_unexpected_duplicate"}
        ),
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Recover delivered packet pending",
            "nodes": [_producer(PRODUCER_A, "Apply once."), _worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_manual_packet",
                        "event_source",
                        event_type="manual.triggered",
                    )
                ],
                "wires": [
                    _wire(
                        "wire_manual_packet",
                        'route',
                        "event_source_manual_packet",
                        PRODUCER_A,
                    ),
                    _wire(
                        wire_id,
                        'route',
                        PRODUCER_A,
                        WORK_A,
                        config={"unavailable_policy": "queue"},
                    ),
                ],
            },
        },
        ACTOR,
    )
    _emit_output(created, PRODUCER_A)
    lineage_id = created["lineage_id"]
    runtime = lineage_program_runtime.read_runtime(lineage_id)
    pending = runtime["event_wires"][wire_id]["pending_events"][0]
    event = pending["event"]
    lineage_program_resident_inputs.register_resident_handoff(
        lineage_id,
        message_id="msg_1234567890abcdef1234567890abcdef",
        resident_id=WORK_A,
        trigger_path_id=event["payload"]["trigger_path_id"],
        event=event,
        source_wire_id=wire_id,
    )

    busy["value"] = False
    drained = lineage_program_backlog.drain_event_backlog(
        created["lineage_id"],
        ACTOR,
     authority=lineage_domain._program_control_authority())

    assert drained["delivered_count"] == 1, json.dumps(drained)
    assert drained["results"][0]["dispatch"]["status"] == "already_delivered"
    assert sent == []
    assert wire_id not in lineage_program_runtime.read_runtime(lineage_id)["event_wires"]


def test_selector_route_preserves_origin_role_in_agent_envelope(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """经选择器投递的 agent 封装保留来源 Resident"""
    selector_id = "selector_ffffffffffff"
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, prompt, _actor: (
            sent.append((str(node["id"]), prompt))
            or {"message_id": "job_selector_origin"}
        ),
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Selector preserves Packet producer",
            "nodes": [
                _producer(PRODUCER_A, "ROUTE_ALPHA"),
                _worker(WORK_A),
                _worker(WORK_B),
            ],
            "program": {
                "components": [
                    _component(
                        "event_source_selector_origin",
                        "event_source",
                        event_type="manual.triggered",
                    ),
                    _component(
                        selector_id,
                        "selector",
                        operator_implementation="rules",
                        prompt="Route by marker.",
                        branches=[
                            {
                                "id": "alpha",
                                "label": "Alpha",
                                "description": "",
                                "match": {
                                    "mode": "all",
                                    "conditions": [
                                        {
                                            "from": "packet.message.text",
                                            "operator": "contains",
                                            "value": "ROUTE_ALPHA",
                                        }
                                    ],
                                    "order_by": "input",
                                    "limit": None,
                                },
                            },
                            {
                                "id": "beta",
                                "label": "Beta",
                                "description": "",
                                "match": {
                                    "mode": "all",
                                    "conditions": [
                                        {
                                            "from": "packet.message.text",
                                            "operator": "contains",
                                            "value": "ROUTE_BETA",
                                        }
                                    ],
                                    "order_by": "input",
                                    "limit": None,
                                },
                            },
                        ],
                    ),
                ],
                "wires": [
                    _wire(
                        "wire_manual_selector_origin",
                        'route',
                        "event_source_selector_origin",
                        PRODUCER_A,
                    ),
                    _wire(
                        "wire_role_selector_origin",
                        'route',
                        PRODUCER_A,
                        selector_id,
                    ),
                    {
                        **_wire(
                            "wire_selector_alpha_origin",
                            'route',
                            selector_id,
                            WORK_A,
                        ),
                        "source_port": "branch:alpha",
                    },
                    {
                        **_wire(
                            "wire_selector_beta_origin",
                            'route',
                            selector_id,
                            WORK_B,
                        ),
                        "source_port": "branch:beta",
                    },
                ],
            },
        },
        ACTOR,
    )

    started = _emit_output(created, PRODUCER_A)

    assert len(sent) == 1, json.dumps(
        started.get("activation"),
        ensure_ascii=False,
        indent=2,
    )
    assert sent[0][0] == WORK_A
    delivered_prompt = sent[0][1]
    assert "ROUTE_ALPHA" in delivered_prompt
    assert f"来源 Resident：{PRODUCER_A}" in delivered_prompt
    assert f"经由组件：{selector_id}" in delivered_prompt
    assert f"来源 Resident：{selector_id}" not in delivered_prompt


@pytest.mark.parametrize(
    ("include_envelope", "expects_envelope"),
    [(True, True), (False, False)],
)
def test_packet_to_reducer_honors_invoke_edge_envelope_policy(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    include_envelope: bool,
    expects_envelope: bool,
) -> None:
    """发往归约器的包遵循调用连线封装设置"""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, prompt, _actor: (
            sent.append((str(node["id"]), prompt))
            or {"message_id": "job_reducer_envelope"}
        ),
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Packet Reducer Invoke envelope",
            "nodes": [
                _producer(PRODUCER_A, "One exact packet."),
                _reducer_executor(),
                _worker(WORK_A),
            ],
            "program": {
                "components": [
                    _reducer_component(),
                    _component(
                        "event_source_manual_reducer_envelope",
                        "event_source",
                        event_type="manual.triggered",
                    ),
                    _deliver("effect_reducer_envelope"),
                ],
                "wires": [
                    _wire(
                        "wire_manual_role_reducer_envelope",
                        'route',
                        "event_source_manual_reducer_envelope",
                        PRODUCER_A,
                    ),
                    _wire(
                        "wire_role_reducer_envelope",
                        'route',
                        PRODUCER_A,
                        REDUCER,
                        config={"include_envelope": include_envelope},
                    ),
                    _wire(
                        "wire_reducer_effect_envelope",
                        'route',
                        REDUCER,
                        "effect_reducer_envelope",
                    ),
                    _wire(
                        "wire_effect_target_envelope",
                        "target",
                        "effect_reducer_envelope",
                        WORK_A,
                    ),
                ],
            },
        },
        ACTOR,
    )

    _emit_output(created, PRODUCER_A)

    assert sent and sent[0][0] == REDUCER_EXECUTOR
    assert "One exact packet." in sent[0][1]
    assert ("[HATG Packet]" in sent[0][1]) is expects_envelope


def test_reconcile_recovers_exact_program_reducer_job_once(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对账仅恢复指定归约器任务且重复完成不再投递"""
    sent: list[tuple[str, str]] = []
    queries: list[tuple[str, str]] = []

    def send(node: dict, _prompt: str, _actor: str) -> dict:
        job_id = f"job_{len(sent) + 1}"
        sent.append((str(node["id"]), job_id))
        return {"message_id": job_id}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_MESSAGE_EVIDENCE_RESOLVER",
        lambda resident_id, job_id, _actor: {
            "job_id": job_id,
            "reply": _reducer_reply("Merged restart result.")
            if resident_id == REDUCER_EXECUTOR
            else _packet_reply(PACKETS[resident_id]),
        }
        if resident_id in {REDUCER_EXECUTOR, PRODUCER_A, PRODUCER_B}
        else {},
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Program reducer restart recovery",
            "nodes": [
                _producer(PRODUCER_A, "Critique."),
                _producer(PRODUCER_B, "Question."),
                _reducer_executor(),
                _worker(WORK_B),
            ],
            "program": {
                "components": [
                    _reducer_component(),
                    _component(
                        "event_source_manual",
                        "event_source",
                        event_type="manual.triggered",
                    ),
                    _deliver("effect_send_work_b"),
                ],
                "wires": [
                    _wire(
                        "wire_event_a",
                        'route',
                        "event_source_manual",
                        PRODUCER_A,
                    ),
                    _wire(
                        "wire_event_b",
                        'route',
                        "event_source_manual",
                        PRODUCER_B,
                    ),
                    _wire(
                        "wire_a_reducer",
                        'route',
                        PRODUCER_A,
                        REDUCER,
                    ),
                    _wire(
                        "wire_b_reducer",
                        'route',
                        PRODUCER_B,
                        REDUCER,
                    ),
                    _wire(
                        "wire_reducer_effect",
                        'route',
                        REDUCER,
                        "effect_send_work_b",
                    ),
                    _wire(
                        "wire_effect_target",
                        "target",
                        "effect_send_work_b",
                        WORK_B,
                    ),
                ],
            },
        },
        ACTOR,
    )
    lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_manual"},
        ACTOR,
    )
    for resident_id, job_id in list(sent):
        lineage_test_support.complete_resident_message(
            resident_id, ACTOR, job_id, lineage_id=created["lineage_id"],
        )
    producer_jobs = sent[:2]
    assert sorted(resident_id for resident_id, _ in producer_jobs) == [PRODUCER_A, PRODUCER_B]
    reducer_inputs = list(sent[2:])
    assert len(reducer_inputs) == 2
    reducer_job_id = reducer_inputs[0][1]

    def resolve_job(
        node: dict,
        job_id: str,
        _actor: str,
    ) -> dict:
        queries.append((str(node["id"]), job_id))
        exact = (
            (str(node["id"]), job_id) in reducer_inputs
        )
        completed = exact and queries.count((REDUCER_EXECUTOR, job_id)) > 1
        if completed:
            lineage_test_support.record_terminal_execution(REDUCER_EXECUTOR, job_id)
        return {
            "status": "completed" if completed else "unknown",
            "job_id": job_id,
            "evidence": {"job_id": job_id},
        }

    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_INPUT_STATE_RESOLVER",
        resolve_job,
    )

    first = lineage_domain.reconcile_lineage_runtime(
        created["lineage_id"],
        ACTOR,
    )
    second = lineage_domain.reconcile_lineage_runtime(
        created["lineage_id"],
        ACTOR,
    )
    third = lineage_domain.reconcile_lineage_runtime(
        created["lineage_id"],
        ACTOR,
    )

    assert first["recovered_handoff_count"] == 0
    assert second["recovered_handoff_count"] == 2
    assert third["recovered_handoff_count"] == 0
    assert all(queries.count(item) == 2 for item in reducer_inputs)
    assert [rid for rid, _ in sent[2:]] == [REDUCER_EXECUTOR, REDUCER_EXECUTOR, WORK_B, WORK_B]
    duplicate = lineage_test_support.complete_resident_message(
        REDUCER_EXECUTOR,
        ACTOR,
        reducer_job_id,
        lineage_id=created["lineage_id"],
    )
    assert duplicate["operator_result_count"] == 0
    assert [rid for rid, _ in sent[2:]] == [REDUCER_EXECUTOR, REDUCER_EXECUTOR, WORK_B, WORK_B]
    runtime = lineage_operator_runtime.read_operator_runtime(
        created["lineage_id"],
        REDUCER,
    )
    assert runtime["pending_deliveries"] == {}


@pytest.mark.parametrize(
    "terminal_status",
    ["completed", "failed", "cancelled"],
)
def test_reconcile_recovers_ordinary_program_resident_terminal_once(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    """对账单次恢复普通 Resident 程序调用的终态"""
    sent: list[tuple[str, str]] = []
    queries: list[tuple[str, str]] = []

    def send(node: dict, _prompt: str, _actor: str) -> dict:
        job_id = "job_program_resident"
        sent.append((str(node["id"]), job_id))
        return {"message_id": job_id}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_MESSAGE_EVIDENCE_RESOLVER",
        lambda _resident_id, job_id, _actor: {
            "job_id": job_id,
            "reply": "Ordinary Program Resident result.",
        },
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": f"Program Resident {terminal_status} recovery",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_manual",
                        "event_source",
                        event_type="manual.triggered",
                    )
                ],
                "wires": [
                    _wire(
                        "wire_manual_worker",
                        'route',
                        "event_source_manual",
                        WORK_A,
                    )
                ],
            },
        },
        ACTOR,
    )
    lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_manual"},
        ACTOR,
    )
    assert sent == [(WORK_A, "job_program_resident")]

    def resolve_job(node: dict, job_id: str, _actor: str) -> dict:
        queries.append((str(node["id"]), job_id))
        return {
            "status": terminal_status,
            "job_id": job_id,
            "evidence": {"completed_at": "2026-08-11T10:00:00Z"},
        }

    monkeypatch.setattr(
        lineage_test_support,
        "RESIDENT_INPUT_STATE_RESOLVER",
        resolve_job,
    )
    lineage_test_support.record_terminal_execution(WORK_A, "job_program_resident", status=terminal_status)
    recovered = lineage_domain.reconcile_lineage_runtime(
        created["lineage_id"],
        ACTOR,
    )
    replay = lineage_domain.reconcile_lineage_runtime(
        created["lineage_id"],
        ACTOR,
    )

    assert recovered["recovered_handoff_count"] == 1
    assert replay["recovered_handoff_count"] == 0
    assert queries == [(WORK_A, "job_program_resident")]
    invocation = lineage_program_resident_inputs.resident_handoff(
        created["lineage_id"], WORK_A, "job_program_resident",
    )
    assert invocation["status"] == terminal_status
    current = lineage_reads.get_lineage(created["lineage_id"], ACTOR)
    worker = next(node for node in current["nodes"] if node["id"] == WORK_A)
    assert worker["status"] == "idle"
    assert worker["last_execution"]["status"] == terminal_status


def test_reconcile_baselines_program_subject_then_recovers_next_completion_once(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对账识别被观察对象的新完成并仅恢复一次"""
    work_is_busy = {"value": True}
    latest_queries: list[str] = []
    sent: list[tuple[str, str]] = []
    latest_completion = {
        "message_id": "job_before_watch",
        "completed_at": "2026-07-29T23:00:00Z",
    }

    def send(node: dict, _prompt: str, _actor: str) -> dict:
        job_id = f"job_{len(sent) + 1}"
        sent.append((str(node["id"]), job_id))
        return {"message_id": job_id}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda node, _actor: (
            work_is_busy["value"] if str(node["id"]) == WORK_A else False
        ),
    )

    def latest(
        resident_id: str,
        _actor: str,
    ) -> dict:
        latest_queries.append(resident_id)
        return dict(latest_completion)

    monkeypatch.setattr(
        lineage_test_support,
        "LATEST_COMPLETED_MESSAGE_RESOLVER",
        latest,
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Program-only source recovery",
            "nodes": [
                _worker(WORK_A),
                _worker(WORK_B),
            ],
            "program": {
                "components": [
                    _component(
                        "event_source_work_done",
                        "event_source",
                        event_type="resident.work_finished",
                    ),
                    _deliver("effect_send_work_b"),
                ],
                "wires": [
                    _watch(
                        "wire_watch_program_only_work_done",
                        WORK_A,
                        "event_source_work_done",
                    ),
                    _wire(
                        "wire_done_effect",
                        'route',
                        "event_source_work_done",
                        "effect_send_work_b",
                        config={"invoke_profiles": {"effect": {"arguments": {
                            "prompt": {"value": "Continue from recovered work."},
                        }}}},
                    ),
                    _wire(
                        "wire_effect_target",
                        "target",
                        "effect_send_work_b",
                        WORK_B,
                    ),
                ],
            },
        },
        ACTOR,
    )
    assert sent == []
    baseline = lineage_domain.reconcile_lineage_runtime(
        created["lineage_id"],
        ACTOR,
    )
    assert baseline["checked_node_count"] == 0
    assert baseline["program_completion_baseline_count"] == 0
    latest_completion.update(
        {
                "message_id": "job_work_a_exact",
            "completed_at": "2026-07-30T00:00:00Z",
        }
    )
    lineage_test_support.record_terminal_execution(WORK_A, "job_work_a_exact")
    work_is_busy["value"] = False

    first = lineage_domain.reconcile_lineage_runtime(
        created["lineage_id"],
        ACTOR,
    )
    second = lineage_domain.reconcile_lineage_runtime(
        created["lineage_id"],
        ACTOR,
    )

    assert first["checked_node_count"] == 1
    assert first["created_count"] == 1
    assert second["checked_node_count"] == 0
    assert second["created_count"] == 0
    assert latest_queries == [
        WORK_A,
        WORK_A,
        WORK_A,
    ]
    assert [resident_id for resident_id, _job_id in sent] == [WORK_B]


def test_connecting_completion_wire_baselines_before_reconciler_can_replay(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连接完成连线时原子建立基线以阻止历史重放"""
    sent: list[str] = []
    baseline_entered = threading.Event()
    release_baseline = threading.Event()
    reconcile_done = threading.Event()
    errors: list[BaseException] = []
    writer_result: dict[str, object] = {}
    reconcile_result: dict[str, object] = {}

    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: sent.append(str(node["id"]))
        or {"message_id": f"job_target_{len(sent)}"},
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Atomic completion subscription",
            "nodes": [_worker(WORK_A), _worker(WORK_B)],
            "program": {
                "components": [
                    _component(
                        "event_source_atomic_work_done",
                        "event_source",
                        event_type="resident.work_finished",
                    )
                ],
                "wires": [
                    _watch(
                        "wire_watch_atomic_work_done",
                        WORK_A,
                        "event_source_atomic_work_done",
                    )
                ],
            },
        },
        ACTOR,
    )

    lineage_test_support.record_terminal_execution(
        WORK_A, "job_before_atomic_subscription", at="2026-08-11T09:06:03Z",
    )

    def latest_completion(
        _resident_id: str,
        _actor: str,
    ) -> dict[str, str]:
        baseline_entered.set()
        assert release_baseline.wait(timeout=3)
        return {
                "message_id": "job_before_atomic_subscription",
            "completed_at": "2026-08-11T09:06:03Z",
        }

    monkeypatch.setattr(
        lineage_test_support,
        "LATEST_COMPLETED_MESSAGE_RESOLVER",
        latest_completion,
    )

    def connect() -> None:
        try:
            writer_result.update(
                lineage_program_edits.save_lineage_program_wire(
                    created["lineage_id"],
                    {'expected_revision': created['revision'], 'id': 'wire_atomic_work_target', 'kind': 'route', 'source': 'event_source_atomic_work_done', 'source_port': 'output', 'target': WORK_B, 'target_port': 'invoke'},
                    ACTOR,
                    operation="create",
                )
            )
        except BaseException as exc:  # surfaced after both threads join
            errors.append(exc)

    def reconcile() -> None:
        try:
            reconcile_result.update(
                lineage_domain.reconcile_lineage_runtime(
                    created["lineage_id"],
                    ACTOR,
                )
            )
        except BaseException as exc:  # surfaced after both threads join
            errors.append(exc)
        finally:
            reconcile_done.set()

    writer = threading.Thread(target=connect)
    reconciler: threading.Thread | None = None
    writer.start()
    try:
        assert baseline_entered.wait(timeout=2)
        reconciler = threading.Thread(target=reconcile)
        reconciler.start()
        assert not reconcile_done.wait(timeout=0.15)
    finally:
        release_baseline.set()
    writer.join(timeout=3)
    if reconciler is not None:
        reconciler.join(timeout=3)

    assert not writer.is_alive()
    assert reconciler is not None and not reconciler.is_alive()
    assert errors == []
    assert writer_result["wire"]["id"] == "wire_atomic_work_target"
    assert reconcile_result["created_count"] == 0
    assert sent == []

    runtime = lineage_program_runtime.read_runtime(created["lineage_id"])
    source = runtime["event_sources"][
        "event_source_atomic_work_done"
    ]
    assert source["fire_count"] == 0
    assert source["fired_cursors"] == [
        f"message:{WORK_A}:job_before_atomic_subscription"
    ]
    assert len(source["consumed_cursors"]) == 1


def test_default_drop_discards_event_for_busy_ordinary_resident(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """普通 Resident 忙碌时默认丢弃事件且不积压"""
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda node, _actor: str(node["id"]) == WORK_A,
    )
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda *_args, **_kwargs: pytest.fail(
            "busy Resident must not receive a dropped Event"
        ),
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Default drop for busy Resident",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_manual",
                        "event_source",
                        event_type="manual.triggered",
                    ),
                ],
                "wires": [
                    _wire(
                        "wire_manual_worker",
                        'route',
                        "event_source_manual",
                        WORK_A,
                    ),
                ],
            },
        },
        ACTOR,
    )

    emitted = lineage_domain.emit_program_manual_event(
        created["lineage_id"],
        {"event_source_id": "event_source_manual"},
        ACTOR,
    )

    assert emitted["matched_count"] == 0
    assert emitted["consumed_count"] == 1
    assert emitted["results"][0]["status"] == "dropped"
    assert emitted["results"][0]["reason"] == "resident_running"
    assert emitted["lineage"]["program_invoke_wire_runtime"][
        "wire_manual_worker"
    ]["pending_count"] == 0


@pytest.mark.parametrize("target_kind", ["agent", "effect"])
def test_pending_delivery_commands_edit_delete_and_deliver_independently(
    runtime_store: Path, monkeypatch: pytest.MonkeyPatch, target_kind: str,
) -> None:
    """待投递指令独立编辑删除与投递且不修改图版本"""
    busy = {"value": True}
    sent = []
    original_availability = lineage_program_execution.program_event_route_can_consume
    monkeypatch.setattr(
        lineage_program_execution, "program_event_route_can_consume",
        lambda *args, **kwargs: (False, "resident_running") if busy["value"] else original_availability(*args, **kwargs),
    )
    monkeypatch.setattr(
        lineage_test_support, "NODE_MESSAGE_SENDER",
        lambda node, prompt, _actor: (
            sent.append(prompt) or {"message_id": f"job_pending_{len(sent)}"}
        ),
    )
    components = [_component("event_source_pending", "event_source", event_type="manual.triggered")]
    wires = []
    target_id = WORK_A
    profile = "agent"
    if target_kind == "effect":
        target_id = "effect_pending"
        profile = "effect"
        components.append(_deliver(target_id))
        wires.append(_wire("wire_target", "target", target_id, WORK_A))
    wires.append(_wire("wire_pending", 'route', "event_source_pending", target_id, config={
        "unavailable_policy": "queue", "queue_limit": 5, "include_envelope": False,
        "invoke_profiles": {profile: {"arguments": {"prompt": {"value": "DEFAULT"}}}},
    }))
    created = lineage_test_support.create_lineage_with_resident_members({
        "title": "Pending Invocation controls", "nodes": [_worker(WORK_A)],
        "program": {"components": components, "wires": wires},
    }, ACTOR)
    lineage_id = created["lineage_id"]
    for _index in range(4):
        lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": "event_source_pending"}, ACTOR)
    before = lineage_reads.get_lineage(lineage_id, ACTOR)

    def command(action, *, revision=None, **arguments):
        instruction = {
            "list": "ListPendingDeliveries",
            "update": "UpdatePendingDelivery",
            "delete": "DeletePendingDelivery",
        }[action]
        version = {arguments["delivery_id"]: revision} if revision is not None else None
        return ok(instruction, {"lineage_id": lineage_id, "wire_id": "wire_pending", **arguments},
                  actor=ACTOR, version=version)

    reset_kernels()
    waiting = command("list")["deliveries"]
    assert len(waiting) == 4
    for item, prompt in zip(waiting, ["FIRST", "DELETE", "THIRD", "CLEAR"]):
        command("update", delivery_id=item["delivery_id"], revision=0, prompt=prompt)
    command("update", delivery_id=waiting[3]["delivery_id"], revision=1, prompt="")
    deleted = command("delete", delivery_id=waiting[1]["delivery_id"], revision=1)
    assert len(deleted["deliveries"]) == 3
    assert lineage_reads.get_lineage(lineage_id, ACTOR)["revision"] == before["revision"]
    busy["value"] = False
    drained = lineage_program_backlog.drain_event_backlog(lineage_id, ACTOR, authority=lineage_domain._program_control_authority())
    assert drained["delivered_count"] == 3
    assert sent == ["FIRST", "THIRD", "DEFAULT"]
    assert command("list")["deliveries"] == []
    source = lineage_program_runtime.read_runtime(lineage_id)["event_sources"]["event_source_pending"]
    assert source["fire_count"] == 4
    after = lineage_reads.get_lineage(lineage_id, ACTOR)
    assert after["program"] == before["program"]


def test_a_waiting_event_goes_when_the_target_turn_fails(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目标 Resident 的一轮失败后，等它空闲的 Event 立即投递

    latest 策略的 Event 连线在目标忙碌时排队；目标这一轮以失败结束后，与完成时一样立即投递等待的 Event，不等下一次对账。

    A failed or cancelled turn frees the Resident as a completed one does (a Family steward's Events).

    :module: lineage.hatg
    :design: decision_4adcc1ff2f2c, decision_44cf587c1721
    """

    del runtime_store
    worker_busy = {"value": True}
    sent: list[str] = []

    def send(node: dict, prompt: str, _actor: str) -> dict:
        sent.append(str(node["id"]))
        return {"message_id": f"job_failed_target_{len(sent)}"}

    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda node, _actor: worker_busy["value"] if str(node["id"]) == WORK_A else False,
    )
    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Event waits for a failing Resident",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [_component("event_source_manual", "event_source", event_type="manual.triggered")],
                "wires": [_wire("wire_manual_worker", 'route', "event_source_manual", WORK_A,
                                config={"unavailable_policy": "latest", "expires_after_seconds": 600})],
            },
        },
        ACTOR,
    )
    lineage_id = str(created["lineage_id"])
    queued = lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": "event_source_manual"}, ACTOR)
    assert queued["results"][0]["status"] == "queued" and sent == []

    worker_busy["value"] = False
    result = lineage_domain.handle_resident_message_terminal(
        WORK_A, ACTOR, "msg_" + "f" * 32, lineage_id=lineage_id, terminal_status="failed",
    )

    assert result["program_event_backlog"][lineage_id]["delivered_count"] == 1
    assert sent == [WORK_A]


def test_latest_wire_delivers_only_newest_event_when_worker_becomes_idle(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最新策略在工作者空闲后只投递最新事件"""
    worker_busy = {"value": True}
    sent: list[tuple[str, str]] = []

    def send(node: dict, prompt: str, _actor: str) -> dict:
        sent.append((str(node["id"]), prompt))
        worker_busy["value"] = True
        return {"message_id": f"job_worker_{len(sent)}"}

    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda node, _actor: (
            worker_busy["value"] if str(node["id"]) == WORK_A else False
        ),
    )
    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Latest event wire",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_manual",
                        "event_source",
                        event_type="manual.triggered",
                    ),
                ],
                "wires": [
                    _wire(
                        "wire_manual_worker",
                        'route',
                        "event_source_manual",
                        WORK_A,
                        config={
                            "unavailable_policy": "latest",
                            "expires_after_seconds": 60,
                        },
                    ),
                ],
            },
        },
        ACTOR,
    )

    emitted = [
        lineage_domain.emit_program_manual_event(
            created["lineage_id"],
            {"event_source_id": "event_source_manual"},
            ACTOR,
        )
        for _index in range(3)
    ]
    assert sent == []
    assert [
        result["results"][0]["status"]
        for result in emitted
    ] == ["queued", "replaced", "replaced"]
    latest_event_id = emitted[-1]["event"]["id"]
    queued_view = lineage_reads.get_lineage(
        created["lineage_id"],
        ACTOR,
    )
    assert queued_view["program_invoke_wire_runtime"][
        "wire_manual_worker"
    ]["pending_count"] == 1

    worker_busy["value"] = False
    reconciled = lineage_domain.reconcile_lineage_runtime(
        created["lineage_id"],
        ACTOR,
    )

    assert reconciled["program_event_backlog"]["delivered_count"] == 1
    assert len(sent) == 1
    assert sent[0][0] == WORK_A
    assert latest_event_id in sent[0][1]
    assert all(
        result["event"]["id"] not in sent[0][1]
        for result in emitted[:-1]
    )
    drained_view = lineage_reads.get_lineage(
        created["lineage_id"],
        ACTOR,
    )
    assert drained_view["program_invoke_wire_runtime"][
        "wire_manual_worker"
    ]["pending_count"] == 0




def test_work_finished_dispatch_rejects_contract_changed_after_match(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完成匹配后配置变更会阻止按旧约定投递"""
    sent: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: sent.append(str(node["id"]))
        or {"message_id": f"job_{len(sent)}"},
    )
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda node, _actor: str(node.get("id") or "") == WORK_A,
    )
    created = _fixed_work_finished_program()
    assert sent == []

    real_dispatch = lineage_domain.dispatch_lineage_program_event
    captured_contracts: list[str] = []

    def reconfigure_before_dispatch(*args: object, **kwargs: object) -> dict:
        captured_contracts.append(
            str(kwargs.get("expected_event_source_contract") or "")
        )
        current = lineage_reads.get_lineage(
            created["lineage_id"],
            ACTOR,
        )
        lineage_program_edits.save_lineage_program_wire(
            created["lineage_id"],
            {
                "expected_revision": current["revision"],
                "wire_id": "wire_watch_work_done",
                "wire": {"target": WORK_B},
            },
            ACTOR,
            operation="update",
        )
        return real_dispatch(*args, **kwargs)

    monkeypatch.setattr(
        lineage_domain,
        "dispatch_lineage_program_event",
        reconfigure_before_dispatch,
    )
    results = lineage_test_support.emit_resident_completion(
        created["lineage_id"],
        WORK_A,
        "job_work_a_race",
        ACTOR,
        ports=lineage_domain._program_terminal_ports(),
    )

    assert captured_contracts and all(captured_contracts)
    assert len(results) == 1
    assert results[0]["stale_contract"] is True
    assert results[0]["matched_count"] == 0
    assert sent == []
    runtime = lineage_program_runtime.read_runtime(
        created["lineage_id"]
    )
    rebound = runtime["event_sources"]["event_source_work_done"]
    assert rebound["completion_locator"] == f"resident:{WORK_B}"
    assert rebound["completion_message_id"] == ""
    assert rebound.get("fire_count", 0) == 0


def test_file_event_signature_detects_same_size_content_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文件大小与修改时间不变时签名仍识别内容变化"""
    report = tmp_path / "report.md"
    report.write_bytes(b"first")
    initial_stat = report.stat()
    monkeypatch.setattr(
        lineage_state,
        "_node_workspace_path",
        lambda _lineage_id, _target_node: tmp_path,
    )

    first = lineage_domain._role_file_signature(
        "lineage_aaaaaaaaaaaa",
        {},
        "report.md",
    )
    report.write_bytes(b"other")
    os.utime(
        report,
        ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns),
    )
    second = lineage_domain._role_file_signature(
        "lineage_aaaaaaaaaaaa",
        {},
        "report.md",
    )

    assert first.startswith("sha256:")
    assert second.startswith("sha256:")
    assert first != second


def test_file_and_time_program_sources_reconcile_without_replay(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文件与时间事件对账只触发新变化且不重放旧事件"""
    sent: list[str] = []

    def send(node: dict, _prompt: str, _actor: str) -> dict:
        sent.append(str(node["id"]))
        return {"message_id": f"job_{len(sent)}"}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Program file event",
            "nodes": [
                _worker(WORK_A),
                _worker(WORK_B),
            ],
            "program": {
                "components": [
                    _component(
                        "event_source_report",
                        "event_source",
                        event_type="file.changed",
                        path="report.md",
                    ),
                    _deliver("effect_send_work_b"),
                ],
                "wires": [
                    _watch(
                        "wire_watch_report",
                        WORK_A,
                        "event_source_report",
                    ),
                    _wire(
                        "wire_report_effect",
                        'route',
                        "event_source_report",
                        "effect_send_work_b",
                        config={"invoke_profiles": {"effect": {"arguments": {
                            "prompt": {"value": "Review report."},
                        }}}},
                    ),
                    _wire(
                        "wire_effect_target",
                        "target",
                        "effect_send_work_b",
                        WORK_B,
                    ),
                ],
            },
        },
        ACTOR,
    )
    report = (
        runtime_store
        / "lineages"
        / created["lineage_id"]
        / "residents"
        / WORK_A
        / "workspace"
        / "report.md"
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("first", encoding="utf-8")
    baseline = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    report.write_text("second version", encoding="utf-8")
    changed = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    unchanged = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    report.write_text("first", encoding="utf-8")
    changed_back = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )
    report.write_text("second version", encoding="utf-8")
    changed_again = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"],
        ACTOR,
    )

    assert baseline["baseline_count"] == 1
    assert baseline["event_count"] == 0
    assert changed["event_count"] == 1
    assert unchanged["event_count"] == 0
    assert changed_back["event_count"] == 1
    assert changed_again["event_count"] == 1


    assert sent == [WORK_B, WORK_B, WORK_B]
    runtime = lineage_program_runtime.read_runtime(created["lineage_id"])
    source_runtime = runtime["event_sources"]["event_source_report"]
    assert set(source_runtime) == {
        "consumed_cursors",
        "fanout_settled_generation",
        "fire_count",
        "fire_generation",
        "fired_cursors",
        "file_generation",
        "file_locator",
        "file_signature",
        "last_fired_at",
        "last_delivery",
    }
    assert source_runtime["fanout_settled_generation"] == source_runtime["fire_generation"]
    assert source_runtime["last_delivery"]["total_count"] == 1
    assert source_runtime["last_delivery"]["delivered_count"] == 1
    assert "first" not in repr(runtime)
    assert "second version" not in repr(runtime)

    time_target = "resident_123456abcdef"
    due = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Program time event",
            "nodes": [
                _worker(time_target),
            ],
            "program": {
                "components": [
                    _component(
                        "event_source_due",
                        "event_source",
                        event_type="time.reached",
                        scheduled_at="2000-01-01T00:00:00Z",
                    ),
                    _deliver("effect_send_due"),
                ],
                "wires": [
                    _wire(
                        "wire_due_effect",
                        'route',
                        "event_source_due",
                        "effect_send_due",
                        config={"invoke_profiles": {"effect": {"arguments": {
                            "prompt": {"value": "Deadline reached."},
                        }}}},
                    ),
                    _wire(
                        "wire_due_target",
                        "target",
                        "effect_send_due",
                        time_target,
                    ),
                ],
            },
        },
        ACTOR,
    )
    first_due = lineage_domain.reconcile_lineage_system_events(
        due["lineage_id"],
        ACTOR,
    )
    second_due = lineage_domain.reconcile_lineage_system_events(
        due["lineage_id"],
        ACTOR,
    )

    assert first_due["event_count"] == 1
    assert second_due["event_count"] == 0
    assert sent == [WORK_B, WORK_B, WORK_B, time_target]

    # Resetting the one-shot source's runtime does not replay a consumed time.
    reset_kernels()
    ok("ResetComponentRuntime", {"lineage_id": due["lineage_id"], "component_id": "event_source_due"}, actor=ACTOR)
    after_reset = lineage_domain.reconcile_lineage_system_events(
        due["lineage_id"],
        ACTOR,
    )
    assert after_reset["event_count"] == 0
    assert sent == [WORK_B, WORK_B, WORK_B, time_target]




def test_countdown_timer_is_reconciled_when_due_by_runtime_repair(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行修复在倒计时到期后触发一次且不重放"""
    class Clock(datetime):
        current = datetime(
            2026,
            8,
            18,
            17,
            1,
            29,
            tzinfo=timezone.utc,
        )

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            current = cls.current
            return current if tz is None else current.astimezone(tz)

    sent: list[str] = []
    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    monkeypatch.setattr(lineage_state, "datetime", Clock)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_timer_{len(sent)}"}
        ),
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Countdown runtime repair",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_countdown",
                        "event_source",
                        event_type="time.reached",
                        schedule_mode="countdown",
                        delay_seconds=180,
                    ),
                ],
                "wires": [
                    _wire(
                        "wire_countdown_worker",
                        'route',
                        "event_source_countdown",
                        WORK_A,
                    ),
                ],
            },
        },
        ACTOR,
    )
    lineage_id = created["lineage_id"]
    anchored = lineage_domain.reconcile_lineage_system_events(
        lineage_id,
        ACTOR,
    )
    runtime = lineage_program_runtime.read_runtime(
        lineage_id
    )
    timer = runtime["event_sources"]["event_source_countdown"]

    assert anchored["event_count"] == 0
    assert timer["timer_started_at"] == "2026-08-18T17:01:29Z"
    assert timer["timer_due_at"] == "2026-08-18T17:04:29Z"

    Clock.current += timedelta(seconds=180)
    repaired = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    replay = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)

    assert repaired["event_count"] == 1
    assert replay["event_count"] == 0
    assert replay["events"] == []
    assert sent == [WORK_A]
    source_runtime = lineage_program_runtime.read_runtime(
        lineage_id
    )["event_sources"]["event_source_countdown"]
    assert source_runtime["fire_count"] == 1


def test_recurring_schedule_recovers_pending_after_restart_and_reconcile_is_idempotent(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """周期待触发事件重开存储后恢复且对账保持幂等"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    sent: list[str] = []
    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    monkeypatch.setattr(lineage_state, "datetime", Clock)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_recurring_{len(sent)}"}
        ),
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Recurring restart recovery",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_recurring",
                        "event_source",
                        event_type="time.reached",
                        schedule_mode="recurring",
                        recurrence_kind="interval",
                        interval_seconds=60,
                        misfire_policy="coalesce_latest",
                        overlap_policy="drop",
                    )
                ],
                "wires": [
                    _wire(
                        "wire_recurring_worker",
                        'route',
                        "event_source_recurring",
                        WORK_A,
                    )
                ],
            },
        },
        ACTOR,
    )
    lineage_id = created["lineage_id"]
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    Clock.current += timedelta(seconds=60)

    # Persist the source occurrence, then reconstruct execution through the
    # public reconcile path as a service restart would.
    pending = lineage_program_schedules.observe_recurring_schedule(
        lineage_id,
        "event_source_recurring",
        config={
            "recurrence_kind": "interval",
            "interval_seconds": 60,
            "misfire_policy": "coalesce_latest",
            "overlap_policy": "drop",
        },
    )
    assert pending["status"] == "due"
    # Simulate a crash after the source occurrence was claimed but before its
    # subscriber route was delivered or the schedule cursor was advanced.
    lineage_program_occurrences.claim_event_occurrence(
        lineage_id,
        "event_source_recurring",
        (
            f"recurring:{pending['generation']}:"
            f"{pending['scheduled_at']}"
        ),
    )

    first = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    replay = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)

    assert first["event_count"] == 1
    assert replay["event_count"] == 0
    assert sent == [WORK_A]
    source = lineage_program_runtime.read_runtime(
        lineage_id
    )["event_sources"]["event_source_recurring"]
    assert source["last_scheduled_at"] == "2026-08-20T00:01:00Z"
    assert source["next_scheduled_at"] == "2026-08-20T00:02:00Z"
    assert "pending_scheduled_at" not in source


def test_recurring_jitter_due_time_survives_reconcile_and_runtime_reload(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """周期抖动到期时间在对账与重读后保持不变"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    monkeypatch.setattr(lineage_state, "datetime", Clock)
    sent: list[str] = []
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"]))
            or {"message_id": f"job_jitter_{len(sent)}"}
        ),
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Recurring jitter persistence",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_recurring",
                        "event_source",
                        event_type="time.reached",
                        schedule_mode="recurring",
                        recurrence_kind="interval",
                        interval_seconds=60,
                        jitter_seconds=10,
                    )
                ],
                "wires": [
                    _wire("wire_recurring", 'route', "event_source_recurring", WORK_A)
                ],
            },
        },
        ACTOR,
    )
    draws = iter([7, 4, 9])
    monkeypatch.setattr(
        lineage_recurring_schedule.secrets,
        "randbelow",
        lambda upper: next(draws),
    )
    lineage_id = created["lineage_id"]
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    source = lineage_program_runtime.read_runtime(lineage_id)["event_sources"]["event_source_recurring"]
    assert source["next_scheduled_at"] == "2026-08-20T00:01:00Z"
    assert source["next_due_at"] == "2026-08-20T00:01:07Z"

    Clock.current += timedelta(seconds=66)
    assert lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)["event_count"] == 0
    assert lineage_program_schedules.observe_recurring_schedule(
        lineage_id,
        "event_source_recurring",
        config=created["program"]["components"][0]["config"],
    )["next_due_at"] == "2026-08-20T00:01:07Z"
    Clock.current += timedelta(seconds=1)
    fired = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    assert fired["event_count"] == 1
    assert fired["events"][0]["event"]["payload"]["scheduled_at"] == "2026-08-20T00:01:00Z"
    source = lineage_program_runtime.read_runtime(lineage_id)["event_sources"]["event_source_recurring"]
    assert source["next_due_at"] == "2026-08-20T00:02:04Z"
    Clock.current += timedelta(seconds=57)
    assert lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)["event_count"] == 1
    assert sent == [WORK_A, WORK_A]


def test_created_recurring_jitter_survives_store_restart_and_revision_change(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新建周期抖动在存储重开后保留且配置变更隔离旧代次"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    lineage_id = lineage_test_support.create_lineage_with_resident_members(
        {"nodes": [_worker(WORK_A)], "title": "Recurring jitter revision fence"}, ACTOR
    )["lineage_id"]
    draws = iter([7, 4, 2])
    monkeypatch.setattr(
        lineage_recurring_schedule.secrets,
        "randbelow",
        lambda upper: next(draws),
    )
    reset_kernels()
    created = ok("CreateComponent", {
        "lineage_id": lineage_id,
        "kind": "event_source",
        "title": "Jittered timer",
        "client_key": "audit_recurring_jitter_revision",
        "config": {
            "event_type": "time.reached",
            "schedule_mode": "recurring",
            "recurrence_kind": "interval",
            "interval_seconds": 60,
            "jitter_seconds": 10,
        },
    }, actor=ACTOR, key="audit_recurring_jitter_revision")
    source_id = created["component"]["id"]
    current = lineage_reads.get_lineage(lineage_id, ACTOR)
    before_plan = lineage_program_compilation.compile_program(
        current["program"], current["nodes"]
    )
    before_source = before_plan["event_sources"][source_id]
    assert created["component"]["config"]["jitter_seconds"] == 10
    assert before_source["jitter_seconds"] == 10

    initial = lineage_program_schedules.observe_recurring_schedule(
        lineage_id, source_id, config=before_source
    )
    assert initial["next_due_at"] == "2026-08-20T00:01:07Z"
    old_store = state_store.get_lineage_data_store()
    state_store.reset_lineage_data_store_for_tests()
    assert state_store.get_lineage_data_store() is not old_store
    assert lineage_program_runtime.read_runtime(lineage_id)["event_sources"][source_id][
        "next_due_at"
    ] == "2026-08-20T00:01:07Z"
    projected = lineage_program_occurrences.event_source_runtime_view(
        lineage_id, {source_id: before_source}
    )[source_id]
    assert projected["next_due_at"] == "2026-08-20T00:01:07Z"

    Clock.current += timedelta(seconds=67)
    pending = lineage_program_schedules.observe_recurring_schedule(
        lineage_id, source_id, config=before_source
    )
    assert pending["due_at"] == "2026-08-20T00:01:07Z"
    assert lineage_program_schedules.observe_recurring_schedule(
        lineage_id, source_id, config=before_source
    ) == pending

    updated = lineage_program_edits.save_lineage_program_component(
        lineage_id,
        {
            "expected_revision": current["revision"],
            "component_id": source_id,
            "component": {
                "config": {
                    **created["component"]["config"],
                    "jitter_seconds": 5,
                }
            },
            "watch_subject": {},
        },
        ACTOR,
    )["lineage"]
    assert updated["revision"] == current["revision"] + 1
    after_source = lineage_program_compilation.compile_program(
        updated["program"], updated["nodes"]
    )["event_sources"][source_id]
    assert after_source["jitter_seconds"] == 5
    assert lineage_program_contracts._program_event_source_contract_fingerprint(
        before_source
    ) != lineage_program_contracts._program_event_source_contract_fingerprint(
        after_source
    )
    replacement = lineage_program_schedules.observe_recurring_schedule(
        lineage_id, source_id, config=after_source
    )
    assert replacement["generation"] == pending["generation"] + 1
    assert lineage_program_schedules.commit_recurring_schedule(
        lineage_id,
        source_id,
        generation=pending["generation"],
        scheduled_at=pending["scheduled_at"],
    )["status"] == "stale"


def test_recurring_pause_preserves_due_cursor_and_resume_skips_the_paused_window(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """暂停周期保留到期游标且恢复时跳过暂停窗口"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    monkeypatch.setattr(lineage_state, "datetime", Clock)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda node, _actor: str(node["id"]) == WORK_A,
    )
    draws = iter([7, 4, 2])
    monkeypatch.setattr(
        lineage_recurring_schedule.secrets, "randbelow", lambda upper: next(draws)
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Recurring pause and resume",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_recurring",
                        "event_source",
                        event_type="time.reached",
                        schedule_mode="recurring",
                        recurrence_kind="interval",
                        interval_seconds=60,
                        jitter_seconds=10,
                        overlap_policy="queue_one",
                    )
                ],
                "wires": [
                    _wire("wire_recurring", 'route', "event_source_recurring", WORK_A)
                ],
            },
        },
        ACTOR,
    )
    lineage_id = created["lineage_id"]
    source_id = "event_source_recurring"

    def source_config() -> dict:
        lineage = lineage_reads.get_lineage(lineage_id, ACTOR)
        return lineage_program_compilation.compile_program(
            lineage["program"], lineage["nodes"]
        )["event_sources"][source_id]

    def set_paused(paused: bool) -> None:
        revision = lineage_reads.get_lineage(lineage_id, ACTOR)["revision"]
        ok("PauseComponent",
           {"lineage_id": lineage_id, "component_id": source_id, "paused": paused},
           actor=ACTOR, key=f"pause-{paused}-{Clock.current.isoformat()}")
        # Pausing is runtime state; the Lineage definition does not change.
        assert lineage_reads.get_lineage(lineage_id, ACTOR)["revision"] == revision

    reset_kernels()
    configured = source_config()
    assert "enabled" not in configured
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    Clock.current += timedelta(seconds=67)
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    runtime = lineage_program_runtime.read_runtime(lineage_id)
    cursor = runtime["event_sources"][source_id]
    assert cursor["next_scheduled_at"] == "2026-08-20T00:02:00Z"
    assert cursor["next_due_at"] == "2026-08-20T00:02:04Z"
    assert len(runtime["event_wires"]["wire_recurring"]["pending_events"]) == 1

    set_paused(True)
    assert source_config() == configured
    runtime = lineage_program_runtime.read_runtime(lineage_id)
    assert runtime["event_sources"][source_id]["paused"] is True
    assert runtime["event_sources"][source_id]["next_due_at"] == cursor["next_due_at"]
    assert lineage_program_occurrences.event_source_runtime_view(
        lineage_id, {source_id: configured}
    )[source_id]["paused"] is True

    state_store.reset_lineage_data_store_for_tests()
    Clock.current += timedelta(seconds=57)
    assert lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)[
        "event_count"
    ] == 0
    assert lineage_program_runtime.read_runtime(lineage_id)["event_sources"][
        source_id
    ]["next_due_at"] == cursor["next_due_at"]

    set_paused(False)
    # The paused window is not replayed; the interval restarts at the resume time.
    assert lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)["event_count"] == 0
    resumed = lineage_program_runtime.read_runtime(lineage_id)["event_sources"][source_id]
    assert "paused" not in resumed
    assert resumed["next_scheduled_at"] == "2026-08-20T00:03:04Z"
    assert resumed["next_due_at"] == "2026-08-20T00:03:06Z"


def test_recurring_jitter_coalesces_once_and_fences_old_generation(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """周期抖动合并遗漏事件且配置变更拒绝旧代次提交"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    lineage_id = lineage_test_support.create_lineage_with_resident_members({"nodes": [_worker(WORK_A)], "title": "Recurring jitter coalescing"}, ACTOR)["lineage_id"]
    draws = iter([0, 10, 2, 3])
    monkeypatch.setattr(lineage_recurring_schedule.secrets, "randbelow", lambda upper: next(draws))
    config = {
        "recurrence_kind": "interval",
        "interval_seconds": 60,
        "jitter_seconds": 10,
        "misfire_policy": "coalesce_latest",
        "overlap_policy": "drop",
    }
    lineage_program_schedules.observe_recurring_schedule(lineage_id, "event_source_recurring", config=config)
    Clock.current += timedelta(seconds=180)
    waiting = lineage_program_schedules.observe_recurring_schedule(lineage_id, "event_source_recurring", config=config)
    assert waiting["status"] == "waiting"
    assert waiting["next_due_at"] == "2026-08-20T00:03:10Z"
    Clock.current += timedelta(seconds=5)
    assert lineage_program_schedules.observe_recurring_schedule(lineage_id, "event_source_recurring", config=config) == waiting
    Clock.current += timedelta(seconds=5)
    pending = lineage_program_schedules.observe_recurring_schedule(lineage_id, "event_source_recurring", config=config)
    assert pending["scheduled_at"] == "2026-08-20T00:03:00Z"
    assert pending["due_at"] == "2026-08-20T00:03:10Z"
    assert pending["missed_count"] == 2
    assert lineage_program_schedules.observe_recurring_schedule(lineage_id, "event_source_recurring", config=config) == pending

    replacement = lineage_program_schedules.observe_recurring_schedule(
        lineage_id, "event_source_recurring", config={**config, "jitter_seconds": 5}
    )
    assert replacement["generation"] == pending["generation"] + 1
    assert replacement["status"] == "waiting"
    assert lineage_program_schedules.commit_recurring_schedule(
        lineage_id, "event_source_recurring",
        generation=pending["generation"], scheduled_at=pending["scheduled_at"],
    )["status"] == "stale"


def test_recurring_jitter_skip_keeps_latest_occurrence_until_its_due_time(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跳过遗漏的周期保留尚未到抖动时间的最新事件"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    lineage_id = lineage_test_support.create_lineage_with_resident_members({"nodes": [_worker(WORK_A)], "title": "Recurring jitter skip"}, ACTOR)["lineage_id"]
    draws = iter([0, 10, 3])
    monkeypatch.setattr(lineage_recurring_schedule.secrets, "randbelow", lambda upper: next(draws))
    config = {
        "recurrence_kind": "interval",
        "interval_seconds": 60,
        "jitter_seconds": 10,
        "misfire_policy": "skip",
        "overlap_policy": "drop",
    }
    lineage_program_schedules.observe_recurring_schedule(lineage_id, "event_source_recurring", config=config)
    Clock.current += timedelta(seconds=120)
    skipped = lineage_program_schedules.observe_recurring_schedule(lineage_id, "event_source_recurring", config=config)
    assert skipped["status"] == "skipped"
    assert skipped["skipped_count"] == 1
    source = lineage_program_runtime.read_runtime(lineage_id)["event_sources"]["event_source_recurring"]
    assert source["next_scheduled_at"] == "2026-08-20T00:02:00Z"
    assert source["next_due_at"] == "2026-08-20T00:02:10Z"
    Clock.current += timedelta(seconds=10)
    due = lineage_program_schedules.observe_recurring_schedule(lineage_id, "event_source_recurring", config=config)
    assert due["status"] == "due"
    assert due["scheduled_at"] == "2026-08-20T00:02:00Z"
    assert due["missed_count"] == 0


def test_recurring_schedule_coalesces_misfires_with_payload_metadata(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """周期调度合并遗漏事件并携带计划时间与遗漏数"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    monkeypatch.setattr(lineage_state, "datetime", Clock)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda _node, _prompt, _actor: {"message_id": "job_coalesced"},
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Recurring misfire coalescing",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_recurring",
                        "event_source",
                        event_type="time.reached",
                        schedule_mode="recurring",
                        recurrence_kind="interval",
                        interval_seconds=60,
                        misfire_policy="coalesce_latest",
                        overlap_policy="drop",
                    )
                ],
                "wires": [
                    _wire("wire_recurring", 'route', "event_source_recurring", WORK_A)
                ],
            },
        },
        ACTOR,
    )
    lineage_domain.reconcile_lineage_system_events(created["lineage_id"], ACTOR)
    Clock.current += timedelta(seconds=185)

    result = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"], ACTOR
    )
    payload = result["events"][0]["event"]["payload"]

    payload = dict(payload)
    assert isinstance(payload.pop("trigger_path_id"), str)
    assert payload == {
        "scheduled_at": "2026-08-20T01:03:00Z",
        "schedule_mode": "recurring",
        "observed_at": "2026-08-20T01:03:05Z",
        "missed_count": 2,
        "generation": 1,
    }


@pytest.mark.parametrize(
    ("overlap_policy", "expected_status", "expected_scheduled_at"),
    [
        ("drop", "dropped", None),
        ("queue_one", "queue_full", "2026-08-20T02:01:00Z"),
        ("latest", "replaced", "2026-08-20T02:02:00Z"),
    ],
)
def test_recurring_overlap_is_bounded_while_target_is_busy(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    overlap_policy: str,
    expected_status: str,
    expected_scheduled_at: str | None,
) -> None:
    """目标忙碌时周期重叠策略最多保留一个待投递事件"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 2, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    monkeypatch.setattr(lineage_state, "datetime", Clock)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda node, _actor: str(node["id"]) == WORK_A,
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": f"Recurring overlap {overlap_policy}",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_recurring",
                        "event_source",
                        event_type="time.reached",
                        schedule_mode="recurring",
                        recurrence_kind="interval",
                        interval_seconds=60,
                        misfire_policy="coalesce_latest",
                        overlap_policy=overlap_policy,
                    )
                ],
                "wires": [
                    _wire("wire_recurring", 'route', "event_source_recurring", WORK_A)
                ],
            },
        },
        ACTOR,
    )
    lineage_domain.reconcile_lineage_system_events(created["lineage_id"], ACTOR)
    Clock.current += timedelta(seconds=60)
    lineage_domain.reconcile_lineage_system_events(created["lineage_id"], ACTOR)
    Clock.current += timedelta(seconds=60)
    second = lineage_domain.reconcile_lineage_system_events(
        created["lineage_id"], ACTOR
    )

    assert second["events"][0]["results"][0]["status"] == expected_status
    runtime = lineage_program_runtime.read_runtime(
        created["lineage_id"]
    )
    pending = runtime["event_wires"].get(
        "wire_recurring", {"pending_events": []}
    )["pending_events"]
    assert len(pending) == (0 if overlap_policy == "drop" else 1)
    if expected_scheduled_at:
        assert pending[0]["event"]["payload"]["scheduled_at"] == expected_scheduled_at


def test_recurring_generation_fences_pending_occurrence_on_config_change(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """周期配置变更后拒绝提交旧代次待触发事件"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    lineage_id = lineage_test_support.create_lineage_with_resident_members(
        {"nodes": [_worker(WORK_A)], "title": "Recurring generation"},
        ACTOR,
    )["lineage_id"]
    first_config = {
        "recurrence_kind": "interval",
        "interval_seconds": 60,
        "misfire_policy": "coalesce_latest",
        "overlap_policy": "drop",
    }
    lineage_program_schedules.observe_recurring_schedule(
        lineage_id, "event_source_recurring", config=first_config
    )
    Clock.current += timedelta(seconds=60)
    stale = lineage_program_schedules.observe_recurring_schedule(
        lineage_id, "event_source_recurring", config=first_config
    )
    replacement = lineage_program_schedules.observe_recurring_schedule(
        lineage_id,
        "event_source_recurring",
        config={**first_config, "interval_seconds": 120},
    )

    assert stale["generation"] == 1
    assert replacement["generation"] == 2
    assert replacement["status"] == "waiting"
    assert lineage_program_schedules.commit_recurring_schedule(
        lineage_id,
        "event_source_recurring",
        generation=1,
        scheduled_at=stale["scheduled_at"],
    )["status"] == "stale"


@pytest.mark.parametrize(
    ("config_change", "expected_next_scheduled_at"),
    [
        ({"interval_seconds": 120}, "2026-08-20T03:18:00Z"),
        ({"jitter_seconds": 5}, "2026-08-20T03:17:00Z"),
    ],
)
def test_recurring_config_change_fences_old_generation_wire_backlog(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_change: dict[str, int],
    expected_next_scheduled_at: str,
) -> None:
    """周期配置变更清除旧代次连线积压并重建调度"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 3, 15, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    monkeypatch.setattr(lineage_state, "datetime", Clock)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_ACTIVE_JOB_RESOLVER",
        lambda node, _actor: str(node["id"]) == WORK_A,
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Recurring generation backlog fence",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_recurring",
                        "event_source",
                        event_type="time.reached",
                        schedule_mode="recurring",
                        recurrence_kind="interval",
                        interval_seconds=60,
                        misfire_policy="coalesce_latest",
                        overlap_policy="queue_one",
                    )
                ],
                "wires": [
                    _wire("wire_recurring", 'route', "event_source_recurring", WORK_A)
                ],
            },
        },
        ACTOR,
    )
    lineage_id = created["lineage_id"]
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    Clock.current += timedelta(seconds=60)
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    assert len(
        lineage_program_runtime.read_runtime(lineage_id)["event_wires"][
            "wire_recurring"
        ]["pending_events"]
    ) == 1

    current = lineage_reads.get_lineage(lineage_id, ACTOR)
    lineage_program_edits.save_lineage_program_component(
        lineage_id,
        {
            "expected_revision": current["revision"],
            "component_id": "event_source_recurring",
            "component": {
                "config": {
                    "event_type": "time.reached",
                    "schedule_mode": "recurring",
                    "recurrence_kind": "interval",
                    "interval_seconds": 60,
                    "misfire_policy": "coalesce_latest",
                    "overlap_policy": "queue_one",
                    **config_change,
                }
            },
            "watch_subject": {},
        },
        ACTOR,
    )
    assert "wire_recurring" not in lineage_program_runtime.read_runtime(lineage_id)[
        "event_wires"
    ]

    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    source = lineage_program_runtime.read_runtime(lineage_id)["event_sources"][
        "event_source_recurring"
    ]
    assert source["generation"] == 2
    assert source["next_scheduled_at"] == expected_next_scheduled_at


def test_recurring_skip_misfire_advances_without_replaying_old_windows(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跳过遗漏周期时推进游标且不重放旧时间窗口"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 3, 30, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    lineage_id = lineage_test_support.create_lineage_with_resident_members(
        {"nodes": [_worker(WORK_A)], "title": "Recurring skip"},
        ACTOR,
    )["lineage_id"]
    config = {
        "recurrence_kind": "interval",
        "interval_seconds": 60,
        "misfire_policy": "skip",
        "overlap_policy": "drop",
    }
    lineage_program_schedules.observe_recurring_schedule(
        lineage_id, "event_source_recurring", config=config
    )
    Clock.current += timedelta(seconds=121)

    skipped = lineage_program_schedules.observe_recurring_schedule(
        lineage_id, "event_source_recurring", config=config
    )
    Clock.current += timedelta(seconds=59)
    next_due = lineage_program_schedules.observe_recurring_schedule(
        lineage_id, "event_source_recurring", config=config
    )

    assert skipped == {
        "status": "skipped",
        "skipped_count": 2,
        "last_scheduled_at": "2026-08-20T03:32:00Z",
        "next_scheduled_at": "2026-08-20T03:33:00Z",
        "generation": 1,
    }
    assert next_due["status"] == "due"
    assert next_due["scheduled_at"] == "2026-08-20T03:33:00Z"
    assert next_due["missed_count"] == 0


def test_recurring_max_fires_advances_schedule_and_reset_only_restores_budget(
    runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """周期额度耗尽仍推进调度且重置只恢复触发预算"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    sent: list[str] = []
    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    monkeypatch.setattr(lineage_state, "datetime", Clock)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: (
            sent.append(str(node["id"])) or {"message_id": f"job_{len(sent)}"}
        ),
    )
    created = lineage_test_support.create_lineage_with_resident_members(
        {
            "title": "Recurring fire budget",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_recurring",
                        "event_source",
                        event_type="time.reached",
                        schedule_mode="recurring",
                        recurrence_kind="interval",
                        interval_seconds=60,
                        misfire_policy="coalesce_latest",
                        overlap_policy="drop",
                        max_fires=1,
                    )
                ],
                "wires": [
                    _wire("wire_recurring", 'route', "event_source_recurring", WORK_A)
                ],
            },
        },
        ACTOR,
    )
    lineage_id = created["lineage_id"]
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    Clock.current += timedelta(seconds=60)
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    Clock.current += timedelta(seconds=60)
    exhausted = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    before_reset = lineage_program_runtime.read_runtime(lineage_id)["event_sources"][
        "event_source_recurring"
    ]
    projected = lineage_program_occurrences.event_source_runtime_view(
        lineage_id,
        {
            "event_source_recurring": {
                "event_type": "time.reached",
                "schedule_mode": "recurring",
                "max_fires": 1,
            }
        },
    )["event_source_recurring"]
    lineage_program_occurrences.reset_event_source_fire_budget(
        lineage_id,
        "event_source_recurring",
        expected_generation=projected["generation"],
    )
    after_reset = lineage_program_runtime.read_runtime(lineage_id)["event_sources"][
        "event_source_recurring"
    ]

    assert exhausted["events"][0]["exhausted"] is True
    assert sent == [WORK_A]
    assert before_reset["next_scheduled_at"] == "2026-08-20T04:03:00Z"
    assert after_reset["next_scheduled_at"] == before_reset["next_scheduled_at"]
    assert after_reset["generation"] == before_reset["generation"]
    assert projected["schedule_generation"] == before_reset["generation"]
    assert after_reset["fire_count"] == 0
    assert lineage_domain.reconcile_lineage_system_events(
        lineage_id, ACTOR
    )["event_count"] == 0
    Clock.current += timedelta(seconds=60)
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    assert sent == [WORK_A, WORK_A]


def test_one_shot_time_source_reports_its_pending_time_until_it_fires(runtime_store: Path, monkeypatch) -> None:
    """一次性时间源触发前展示待执行时间且触发后清空"""
    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: {"message_id": "job_at"})
    created = lineage_test_support.create_lineage_with_resident_members({
        "title": "One-shot time", "nodes": [_worker(WORK_A)],
        "program": {
            "components": [_component("event_source_oneshot", "event_source", event_type="time.reached",
                                      scheduled_at="2000-01-01T00:00:00Z")],
            "wires": [_wire("wire_oneshot", 'route', "event_source_oneshot", WORK_A)],
        },
    }, ACTOR)
    config = {"event_type": "time.reached", "schedule_mode": "at", "scheduled_at": "2000-01-01T00:00:00Z"}
    before = lineage_program_occurrences.event_source_runtime_view(created["lineage_id"], {"event_source_oneshot": config})
    assert before["event_source_oneshot"]["next_due_at"] == "2000-01-01T00:00:00Z"
    assert lineage_domain.reconcile_lineage_system_events(created["lineage_id"], ACTOR)["event_count"] == 1
    after = lineage_program_occurrences.event_source_runtime_view(created["lineage_id"], {"event_source_oneshot": config})
    assert after["event_source_oneshot"]["next_due_at"] == ""
