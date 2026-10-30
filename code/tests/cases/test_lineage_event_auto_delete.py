from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from management_portal.society_service import lineage_program_backlog
from management_portal.society_service import (
    component_runtime,
    lineage_domain,
    lineage_program_edits,
    lineage_program_occurrences,
    lineage_program_runtime,
    lineage_reads,
    lineage_state,
)
from tests.cases import society_service_test_support as support
from tests.cases.test_lineage_program_runtime_integration import (
    ACTOR, PRODUCER_A, WORK_A, WORK_B, _component, _packet_reply, _wire, _worker,
    runtime_store,
)


SOURCE = "event_source_retire"


def create(*, maximum=1, enabled=True, nodes=None, wires=None, components=None):
    return support.create_lineage_with_resident_members({
        "title": "Event auto deletion",
        "project_id": "project_test",
        "nodes": nodes or [_worker(WORK_A), _worker(WORK_B)],
        "program": {
            "components": [
                _component(SOURCE, "event_source", event_type="manual.triggered",
                           max_fires=maximum, auto_delete_on_exhaustion=enabled),
                *(components or []),
            ],
            "wires": wires or [],
        },
    }, ACTOR)


def emit(lineage_id):
    return lineage_domain.emit_program_manual_event(
        lineage_id, {"event_source_id": SOURCE}, ACTOR,
    )


def source_exists(lineage_id):
    return any(item["id"] == SOURCE for item in lineage_reads.get_lineage(
        lineage_id, ACTOR,
    )["program"]["components"])


@pytest.mark.parametrize("enabled,maximum,expected", [
    (False, 1, True), (True, None, True), (True, 2, True), (True, 1, False),
])
def test_event_retirement_is_opt_in_and_budget_scoped(runtime_store, enabled, maximum, expected):
    """只有开启自动删除且触发次数用尽的事件源才会被删除"""
    created = create(enabled=enabled, maximum=maximum)
    result = emit(created["lineage_id"])
    assert result["fire"]["occurrence_count"] == 1
    assert source_exists(created["lineage_id"]) is expected
    assert len(result["lineage"]["nodes"]) == 2


def test_final_occurrence_fans_out_before_source_and_wires_are_removed(runtime_store, monkeypatch):
    """最后一次触发先投递到全部下游，再删除事件源及其连线"""
    sent = []
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: (
        sent.append(node["id"]) or {"message_id": f"job_retire_{len(sent)}"}
    ))
    created = create(maximum=2, wires=[
        _wire("wire_retire_a", "route", SOURCE, WORK_A),
        _wire("wire_retire_b", "route", SOURCE, WORK_B),
    ])
    lineage_id = created["lineage_id"]
    emit(lineage_id)
    result = emit(lineage_id)
    assert sent == [WORK_A, WORK_B, WORK_A, WORK_B]
    assert result["fire"]["occurrence_count"] == 2
    assert result["lineage"]["program"]["components"] == []
    assert result["lineage"]["program"]["wires"] == []
    revision = result["lineage"]["revision"]
    assert lineage_program_edits._retire_exhausted_event_sources(lineage_id, ACTOR) == []
    assert lineage_reads.get_lineage(lineage_id, ACTOR)["revision"] == revision


@pytest.mark.parametrize("disable_before_delivery", [False, True])
def test_queued_final_invocation_survives_until_delivery_then_retires(runtime_store, monkeypatch, disable_before_delivery):
    """最后一次调用排队时事件源保留到投递后再删除，投递前关闭自动删除则保留"""
    busy = {"value": True}
    sent = []
    monkeypatch.setattr(support, "NODE_ACTIVE_JOB_RESOLVER", lambda node, actor: busy["value"])
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: (
        sent.append(prompt) or {"message_id": "job_retire_queued"}
    ))
    created = create(wires=[_wire("wire_retire_a", "route", SOURCE, WORK_A, config={
        "unavailable_policy": "queue", "include_envelope": False,
        "invoke_profiles": {"agent": {"arguments": {"prompt": {"value": "LAST"}}}},
    })])
    lineage_id = created["lineage_id"]
    emit(lineage_id)
    assert source_exists(lineage_id)
    assert sent == []
    persisted = lineage_program_runtime.read_runtime(lineage_id)
    assert persisted["event_sources"][SOURCE]["fanout_settled_generation"] == 0
    assert lineage_program_edits._retire_exhausted_event_sources(lineage_id, ACTOR) == []
    if disable_before_delivery:
        current = lineage_reads.get_lineage(lineage_id, ACTOR)
        lineage_program_edits.save_lineage_program_component(lineage_id, {
            "component_id": SOURCE, "expected_revision": current["revision"],
            "component": {"config": {"event_type": "manual.triggered", "max_fires": 1,
                                      "auto_delete_on_exhaustion": False}},
        }, ACTOR)
    busy["value"] = False
    result = lineage_program_backlog.drain_event_backlog(lineage_id, ACTOR, authority=lineage_domain._program_control_authority())
    assert sent == ["LAST"], result["results"]
    assert result["deleted_event_source_ids"] == ([] if disable_before_delivery else [SOURCE])
    assert source_exists(lineage_id) is disable_before_delivery


def test_reset_fences_old_auto_delete_readiness(runtime_store):
    """重置事件源后，旧一代的扇出完成不会触发自动删除"""
    created = create()
    lineage_id = created["lineage_id"]
    lineage_program_occurrences.claim_event_occurrence(lineage_id, SOURCE, "old", max_fires=1)
    lineage_program_occurrences.mark_event_fanout_complete(lineage_id, SOURCE, generation=0, occurrence_count=1)
    component_runtime.reset(lineage_id, SOURCE, ACTOR)
    lineage_program_occurrences.mark_event_fanout_complete(lineage_id, SOURCE, generation=0, occurrence_count=1)
    lineage_program_occurrences.claim_event_occurrence(lineage_id, SOURCE, "new", max_fires=1)
    assert lineage_program_edits._retire_exhausted_event_sources(lineage_id, ACTOR) == []
    assert source_exists(lineage_id)
    lineage_program_occurrences.mark_event_fanout_complete(lineage_id, SOURCE, generation=1, occurrence_count=1)
    assert lineage_program_edits._retire_exhausted_event_sources(lineage_id, ACTOR) == [SOURCE]


def test_older_fanout_cannot_mark_a_newer_occurrence_as_settled(runtime_store):
    """较早一次触发的扇出完成不能把较新的触发标为已结算"""
    created = create(maximum=2)
    lineage_id = created["lineage_id"]
    lineage_program_occurrences.claim_event_occurrence(lineage_id, SOURCE, "first", max_fires=2)
    lineage_program_occurrences.mark_event_fanout_complete(lineage_id, SOURCE, generation=0, occurrence_count=1)
    lineage_program_occurrences.claim_event_occurrence(lineage_id, SOURCE, "second", max_fires=2)
    lineage_program_occurrences.mark_event_fanout_complete(lineage_id, SOURCE, generation=0, occurrence_count=1)
    assert lineage_program_edits._retire_exhausted_event_sources(lineage_id, ACTOR) == []
    lineage_program_occurrences.mark_event_fanout_complete(lineage_id, SOURCE, generation=0, occurrence_count=2)
    assert lineage_program_edits._retire_exhausted_event_sources(lineage_id, ACTOR) == [SOURCE]


@pytest.mark.parametrize("enabled,maximum", [(False, 1), (True, 2)])
def test_enabling_auto_delete_or_lowering_limit_keeps_existing_fire_count(runtime_store, enabled, maximum):
    """开启自动删除或调低次数上限时保留已触发次数，已达上限的事件源随即退役"""
    created = create(enabled=enabled, maximum=maximum)
    lineage_id = created["lineage_id"]
    result = emit(lineage_id)
    lineage_program_edits.save_lineage_program_component(lineage_id, {
        "component_id": SOURCE, "expected_revision": result["lineage"]["revision"],
        "component": {"config": {"event_type": "manual.triggered", "max_fires": 1,
                                  "auto_delete_on_exhaustion": True}},
    }, ACTOR)
    assert lineage_program_runtime.read_runtime(lineage_id)["event_sources"][SOURCE]["fire_count"] == 1
    assert lineage_program_edits._retire_exhausted_event_sources(lineage_id, ACTOR) == [SOURCE]


def test_zero_budget_retires_during_reconcile_without_firing(runtime_store):
    """次数上限为 0 的事件源在对账时直接删除，不产生事件"""
    created = create(maximum=0)
    result = lineage_domain.reconcile_lineage_system_events(created["lineage_id"], ACTOR)
    assert result["event_count"] == 0
    assert not source_exists(created["lineage_id"])


def test_resident_output_is_delivered_after_its_source_retires(runtime_store, monkeypatch):
    """事件源清理后，Resident Output 仍沿自己的 Route 投递

    事件源耗尽并清理自身连线后，已经激活的 Resident 完成本轮工作，其 Output 经独立的 Route 投递给下游。
    """
    sent = []
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: (
        sent.append((node["id"], prompt)) or {"message_id": f"job_retire_producer_{len(sent)}"}
    ))
    monkeypatch.setattr(support, "RESIDENT_MESSAGE_EVIDENCE_RESOLVER", lambda *args: {
        "job_id": "job_retire_producer_1", "reply": _packet_reply("FINAL OUTPUT"),
    })
    created = create(nodes=[_worker(PRODUCER_A), _worker(WORK_A)], wires=[
        _wire("wire_retire_producer", "route", SOURCE, PRODUCER_A),
        _wire("wire_output", "route", PRODUCER_A, WORK_A, config={"include_envelope": False}),
    ])
    lineage_id = created["lineage_id"]
    emit(lineage_id)
    # Delivering to the Resident settles the final fanout; the Output it
    # sends later travels on its own wire.
    assert not source_exists(lineage_id)
    support.record_terminal_execution(PRODUCER_A, "job_retire_producer_1", ACTOR)
    lineage_domain.handle_resident_message_completion(
        PRODUCER_A, ACTOR, "job_retire_producer_1", lineage_id=lineage_id,
    )
    lineage_program_backlog.drain_event_backlog(lineage_id, ACTOR, authority=lineage_domain._program_control_authority())
    assert sent[-1] == (WORK_A, "FINAL OUTPUT")


def test_auto_delete_preserves_budget_observer_node_and_delivers_its_event(runtime_store, monkeypatch):
    """事件源自动删除时，监听其预算耗尽的事件源保留并投递事件，只移除 watch 连线"""
    sent = []
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", lambda node, prompt, actor: (
        sent.append(node["id"]) or {"message_id": "job_budget_observer"}
    ))
    observer = "event_source_budget_observer"
    created = create(components=[_component(observer, "event_source", event_type="budget.exhausted")], wires=[
        _wire("wire_watch_budget", "watch", observer, SOURCE),
        _wire("wire_observer_invoke", "route", observer, WORK_A),
    ])
    result = emit(created["lineage_id"])
    assert sent == [WORK_A]
    assert [component["id"] for component in result["lineage"]["program"]["components"]] == [observer]
    assert [wire["id"] for wire in result["lineage"]["program"]["wires"]] == ["wire_observer_invoke"]


def test_recurring_timer_commits_final_tick_before_auto_delete(runtime_store, monkeypatch):
    """周期定时器先提交最后一次触发再自动删除，之后对账不再改动 revision"""
    class Clock(datetime):
        current = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    monkeypatch.setattr(lineage_state, "datetime", Clock)
    created = support.create_lineage_with_resident_members({
        "title": "Recurring source retirement", "project_id": "project_test", "nodes": [_worker(WORK_A)],
        "program": {"components": [_component(
            SOURCE, "event_source", event_type="time.reached", schedule_mode="recurring",
            recurrence_kind="interval", interval_seconds=60, misfire_policy="coalesce_latest",
            overlap_policy="drop", max_fires=1, auto_delete_on_exhaustion=True,
        )], "wires": []},
    }, ACTOR)
    lineage_id = created["lineage_id"]
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    assert source_exists(lineage_id)
    Clock.current += timedelta(seconds=60)
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    assert not source_exists(lineage_id)
    current = lineage_reads.get_lineage(lineage_id, ACTOR)
    lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    assert lineage_reads.get_lineage(lineage_id, ACTOR)["revision"] == current["revision"]


def test_auto_delete_updates_collapsed_function_block_membership(runtime_store):
    """事件源自动删除后，也从折叠的功能块成员中移除"""
    created = support.create_lineage_with_resident_members({
        "title": "Block source retirement", "project_id": "project_test", "nodes": [_worker(WORK_A)],
        "program": {"components": [_component(SOURCE, "event_source", event_type="manual.triggered",
                     max_fires=1, auto_delete_on_exhaustion=True)], "wires": []},
        "function_blocks": [{"id": "fbi_aaaaaaaaaaaa", "title": "Self-cleaning Event",
                            "elements": [{"id": SOURCE, "kind": "component", "key": None}]}],
    }, ACTOR)
    result = emit(created["lineage_id"])
    assert not source_exists(created["lineage_id"])
    assert result["lineage"]["function_blocks"] == []


@pytest.mark.parametrize("invalid", ["true", "false", 1, {}, []])
def test_auto_delete_rejects_non_boolean_config(runtime_store, invalid):
    """auto_delete_on_exhaustion 只接受布尔值"""
    with pytest.raises(ValueError, match="auto_delete_on_exhaustion"):
        create(enabled=invalid)
