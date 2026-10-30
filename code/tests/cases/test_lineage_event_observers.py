"""Each system Event type's observer: baselines, commits and candidate Events.

A reconciliation pass hands every Event Source to the observer registered for
its event type. These cases drive single observers with scripted baselines
from the Event runtime and check what they commit and which Events they list.

:module: lineage.service
:importance: P1
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from management_portal.society_service import (
    lineage_event_observers as observers, lineage_message_events, lineage_primitives,
    lineage_program_observations, lineage_release_events,
)

LINEAGE = "lineage_0123456789ab"
ALICE = "resident_0000000000a1"
BOB = "resident_0000000000b2"


class Runtime:
    """Scripted Event runtime: each observation answers the next scripted status."""

    def __init__(self, monkeypatch, *answers):
        self.answers = list(answers)
        self.observed, self.committed, self.file_commits = [], [], []
        monkeypatch.setattr(lineage_program_observations, "observe_event_source_value", self.observe)
        monkeypatch.setattr(lineage_program_observations, "commit_event_source_value", self.commit)
        monkeypatch.setattr(lineage_program_observations, "observe_file_signature", self.observe_file)
        monkeypatch.setattr(lineage_program_observations, "commit_file_signature",
                            lambda *args, **kwargs: self.file_commits.append(kwargs))

    def observe(self, lineage_id, source_id, **kwargs):
        self.observed.append(kwargs)
        return {"previous_signature": "sig-before", "previous_value": "", "generation": 0,
                "occurrence_key": "occ-1", **self.answers.pop(0)}

    def observe_file(self, lineage_id, source_id, **kwargs):
        self.observed.append(kwargs)
        return {"previous_signature": "file-before", "generation": 0, **self.answers.pop(0)}

    def commit(self, lineage_id, source_id, **kwargs):
        self.committed.append(kwargs)
        return True


def scan(*, lineage=None, facts=None, runtime_sources=None, **ports):
    lineage = lineage or {"revision": 1, "nodes": [{"id": ALICE}, {"id": BOB}]}
    return observers.Scan(
        lineage_id=LINEAGE, actor_id="sample_admin", lineage=lineage,
        nodes={node["id"]: node for node in lineage.get("nodes") or []},
        runtime_sources=runtime_sources or {}, facts=facts or observers.Facts(),
        ports=SimpleNamespace(event_contract_fingerprint=lambda source: "contract", clone_json=copy.deepcopy,
                              **ports),
    )


def observe(state, config, *, source_id="es_watch", subject="", exhausted=False):
    observers.OBSERVERS[config["event_type"]](state, observers.Source(source_id, config, subject, exhausted))


def test_registry_polls_only_event_types_no_other_subsystem_delivers():
    """登记表只为需要轮询观察的 Event 类型提供观察器，由其他子系统推送的类型不在其中"""
    assert set(observers.OBSERVERS) <= lineage_primitives.EVENT_TYPES
    assert set(observers.VALUE_READERS) <= set(observers.OBSERVERS)
    for pushed in ("resident.work_finished", "resident.work_failed", "manual.triggered",
                   "effect.succeeded", "script.finished", "event.delivery_failed"):
        assert pushed not in observers.OBSERVERS


def test_work_running_at_first_look_fires_without_moving_the_baseline(monkeypatch):
    """首次观察时 Resident 已在工作则产生 work_started 且不提交基线；之后转为空闲只提交基线"""
    activity = {"residents": {ALICE: {"active": True, "message_id": "msg_1", "status": "running",
                                      "started_at": "2026-10-27T10:00:00Z"}}}
    runtime = Runtime(monkeypatch, {"status": "baseline"}, {"status": "changed"})
    state = scan(facts=observers.Facts(resident_activity=activity))
    config = {"event_type": "resident.work_started", "subject_resident_id": ALICE}
    observe(state, config)
    [item] = state.pending
    assert item["cursor"] == f"message-started:{ALICE}:msg_1" and item["source_contract"] == "contract"
    assert item["event"].payload == {"message_id": "msg_1", "status": "running"}
    assert "state_locator" not in item and state.baseline_count == 0

    activity["residents"][ALICE] = {"active": False}
    observe(state, config)
    assert len(state.pending) == 1
    assert runtime.committed == [{**{key: runtime.observed[1][key] for key in ("locator", "signature", "value")},
                                  "previous_signature": "sig-before", "subject": ""}]
    assert runtime.observed[1]["value"] == "idle"


def test_resumed_source_takes_running_work_as_its_baseline(monkeypatch):
    """恢复观察的来源把正在进行的工作当作基线，不产生 work_started"""
    Runtime(monkeypatch, {"status": "baseline", "resumed": True})
    state = scan(facts=observers.Facts(resident_activity={"residents": {ALICE: {"active": True, "message_id": "m"}}}))
    observe(state, {"event_type": "resident.work_started", "subject_resident_id": ALICE})
    assert state.pending == [] and state.baseline_count == 1


def test_handoff_source_reports_only_its_status(monkeypatch):
    """工作区交接来源只在交接进入它报告的状态时产生 Event，其他变化只提交基线"""
    handoff = {"type": "handoff", "id": "handoff_1", "status": "failed", "error": "disk full",
               "items": [{"attempts": [1, 2]}]}
    lineage = {"revision": 3, "nodes": [], "edges": [handoff]}
    runtime = Runtime(monkeypatch, {"status": "changed"}, {"status": "changed"})
    state = scan(lineage=lineage)
    config = {"event_type": "workspace_handoff.ready", "subject_handoff_id": "handoff_1"}
    observe(state, config)
    assert state.pending == [] and runtime.committed[0]["value"] == "failed:2"

    handoff.update(status="ready", error="")
    observe(state, config)
    [item] = state.pending
    assert item["event"].type == "workspace_handoff.ready"
    assert item["event"].payload == {"handoff_id": "handoff_1", "status": "ready"}
    assert item["state_value"] == "ready:2" and item["previous_state_signature"] == "sig-before"


def test_lineage_change_reports_the_previous_revision(monkeypatch):
    """Lineage 版本变化的 Event 带上之前的版本号，接受后才提交基线"""
    Runtime(monkeypatch, {"status": "changed", "previous_value": "4"})
    state = scan(lineage={"revision": 6, "nodes": []})
    observe(state, {"event_type": "lineage.changed"})
    [item] = state.pending
    assert item["event"].payload == {"revision": 6, "previous_revision": 4}
    assert item["cursor"] == f"value:occ-1:{item['state_signature']}" and item["state_value"] == "6"


def test_all_finished_fires_once_per_run_whose_named_residents_all_completed(monkeypatch):
    """所有指定 Resident 在同一次运行中都完成时，每次运行产生一个 residents.all_finished"""
    monkeypatch.setattr(observers.trigger_paths, "resident_inputs", lambda lineage_id, finished_since: [
        {"trigger_path_id": "tpath_done", "resident_id": ALICE, "status": "completed"},
        {"trigger_path_id": "tpath_done", "resident_id": BOB, "status": "completed"},
        {"trigger_path_id": "tpath_open", "resident_id": ALICE, "status": "completed"},
        {"trigger_path_id": "tpath_open", "resident_id": BOB, "status": "running"},
        {"trigger_path_id": "tpath_partial", "resident_id": ALICE, "status": "completed"},
    ])
    state = scan()
    observe(state, {"event_type": "residents.all_finished", "resident_ids": [ALICE, BOB]})
    [item] = state.pending
    assert item["cursor"] == "resident-run-finished:tpath_done"
    assert item["event"].payload == {"trigger_path_id": "tpath_done", "resident_ids": [ALICE, BOB], "resident_count": 2}
    observe(state, {"event_type": "residents.all_finished", "resident_ids": [ALICE, BOB],
                    "trigger_path_id": "tpath_open"})
    assert len(state.pending) == 1


def test_consumed_file_change_moves_the_signature_without_an_event(monkeypatch):
    """文件变化的游标已被消费时只推进文件签名，不再产生 Event；未消费时产生 Event 并在接受后提交"""
    runtime = Runtime(monkeypatch, {"status": "changed"}, {"status": "changed"})
    state = scan(runtime_sources={"es_watch": {"consumed_cursors": ["file:1:sig-new"]}},
                 role_file_signature=lambda lineage_id, subject, path: "sig-new")
    config = {"event_type": "file.changed", "subject_resident_id": ALICE, "path": "notes.md"}
    observe(state, config)
    assert state.pending == [] and runtime.file_commits[0]["signature"] == "sig-new"
    observe(state, config, source_id="es_other")
    [item] = state.pending
    assert item["cursor"] == "file:1:sig-new" and item["file_signature"] == "sig-new"
    assert item["event"].payload == {"path": "notes.md", "file_signature": "sig-new"}
    assert len(state.wake_times) == 2


def test_release_source_takes_a_baseline_then_lists_new_publications(monkeypatch):
    """发布来源首次记录基线；基线未变时列出之后的发布，耗尽的来源什么也不做"""
    runtime = Runtime(monkeypatch, {"status": "baseline"})
    config = {"event_type": lineage_release_events.EVENT}
    state = scan(facts=observers.Facts(release_publications={"es_watch": {"baseline": 7, "records": [], "previous": ""}}))
    observe(state, config)
    assert state.baseline_count == 1 and runtime.observed[0]["locator"] == lineage_release_events.LOCATOR
    state = scan(facts=observers.Facts(release_publications={"es_watch": {
        "baseline": None, "records": [{"sequence": 8, "completed_at": "2026-10-27T12:00:00Z"}],
        "previous": "publication-sig"}}),
        runtime_sources={"es_watch": {"state_signature": "publication-sig"}})
    observe(state, config)
    assert [item["event"].type for item in state.pending] == [lineage_release_events.EVENT]
    moved = scan(facts=state.facts, runtime_sources={"es_watch": {"state_signature": "later-sig"}})
    observe(moved, config)
    assert moved.pending == []
    exhausted = scan(facts=state.facts, runtime_sources=state.runtime_sources)
    observe(exhausted, config, exhausted=True)
    assert exhausted.pending == [] and exhausted.wake_times == []


@pytest.mark.parametrize("reached", [False, True])
def test_one_time_schedule_wakes_before_it_is_due_and_fires_once(monkeypatch, reached):
    """一次性时间来源到期前登记唤醒时间，到期后产生一次 Event，已触发的游标不再产生"""
    config = {"event_type": "time.reached", "schedule_mode": "at", "scheduled_at": "2999-01-01T00:00:00Z"}
    state = scan(scheduled_time_reached=lambda scheduled_at: reached)
    observe(state, config)
    assert len(state.pending) == int(reached) and len(state.wake_times) == int(not reached)
    fired = scan(scheduled_time_reached=lambda scheduled_at: reached,
                 runtime_sources={"es_watch": {"fired_cursors": ["time:2999-01-01T00:00:00Z"]}})
    observe(fired, config)
    assert fired.pending == [] and fired.wake_times == []


def test_work_duration_fires_only_once_the_work_runs_past_the_threshold(monkeypatch):
    """工作时长恰好等于阈值时只记录基线；超过阈值后产生 Event，接受后才提交基线"""
    runtime = Runtime(monkeypatch, {"status": "baseline"}, {"status": "changed", "previous_value": "false"})
    work = {"active": True, "message_id": "msg_1", "status": "running", "started_at": "2026-10-27T10:00:00Z"}
    config = {"event_type": "resident.work_duration_exceeded", "subject_resident_id": ALICE, "duration_seconds": 900}
    at_threshold = scan(facts=observers.Facts(resident_activity={
        "captured_at": "2026-10-27T10:15:00Z", "residents": {ALICE: work}}))
    observe(at_threshold, config)
    assert at_threshold.pending == [] and at_threshold.baseline_count == 1
    past = scan(facts=observers.Facts(resident_activity={
        "captured_at": "2026-10-27T10:15:01Z", "residents": {ALICE: work}}))
    observe(past, config)
    [item] = past.pending
    assert item["cursor"] == f"work-duration:{ALICE}:msg_1:900"
    assert item["event"].payload["duration_seconds"] == 901 and item["state_value"] == "true"
    assert [observed["value"] for observed in runtime.observed] == ["false", "true"]


def test_queue_pressure_compares_the_configured_metric(monkeypatch):
    """队列压力来源用配置的指标比较阈值：最久等待超过阈值时产生 Event，队列长度未超过也一样"""
    Runtime(monkeypatch, {"status": "changed", "previous_value": "false"})
    state = scan(facts=observers.Facts(resident_activity={"captured_at": "2026-10-27T10:01:00Z", "residents": {
        ALICE: {"queue_count": 2, "oldest_queued_at": "2026-10-27T10:00:00Z"}}}))
    observe(state, {"event_type": "resident.queue_pressure_exceeded", "subject_resident_id": ALICE,
                    "pressure_metric": "oldest_wait_seconds", "wait_seconds_threshold": 30}, subject=ALICE)
    [item] = state.pending
    assert item["event"].payload["pressure_value"] == 60 and item["event"].payload["queue_count"] == 2
    assert item["cursor"].endswith(f":{ALICE}") and item["state_subject"] == ALICE


@pytest.mark.parametrize("answer, fires", [
    ({"status": "baseline"}, True),
    ({"status": "baseline", "resumed": True}, False),
    ({"status": "changed", "previous_value": "80.0"}, True),
    ({"status": "changed", "previous_value": "88.0"}, False),
])
def test_context_pressure_fires_when_use_rises_past_the_threshold(monkeypatch, answer, fires):
    """上下文占用从阈值以下升到阈值以上时产生 Event；首次观察已超过也产生，恢复观察或之前已超过时只记录基线"""
    runtime = Runtime(monkeypatch, answer)
    state = scan(facts=observers.Facts(context_pressures={ALICE: {"used_percent": 90}}),
                 lineage_node_session_key=lambda node: node["id"])
    observe(state, {"event_type": "resident.context_pressure_exceeded", "subject_resident_id": ALICE,
                    "threshold_percent": 85}, subject=ALICE)
    assert runtime.observed[0].get("subject") == ALICE and runtime.observed[0]["value"] == "90.0"
    assert len(state.pending) == int(fires)
    if answer["status"] == "changed":
        assert [commit["value"] for commit in runtime.committed] == ([] if fires else ["90.0"])
    for item in state.pending:
        assert item["cursor"].endswith(f":{ALICE}")
        assert ("state_value" in item) == (answer["status"] == "changed")


def test_all_idle_fires_when_running_work_drops_to_none(monkeypatch):
    """系统中正在工作的 Resident 数从非零降为零时产生 residents.all_idle；开始工作或之前的数目未知时只提交基线"""
    runtime = Runtime(monkeypatch, {"status": "changed", "previous_value": "2"},
                      {"status": "changed", "previous_value": "0"}, {"status": "changed", "previous_value": ""})
    idle = observers.Facts(resident_activity={"resident_ids": [ALICE, BOB], "active_resident_ids": []})
    state = scan(facts=idle)
    observe(state, {"event_type": "residents.all_idle"})
    [item] = state.pending
    assert item["event"].payload == {"resident_scope": "system", "resident_count": 2,
                                     "previous_running_count": 2, "running_count": 0}
    busy = scan(facts=observers.Facts(resident_activity={"resident_ids": [ALICE, BOB], "active_resident_ids": [BOB]}))
    observe(busy, {"event_type": "residents.all_idle"})
    unknown = scan(facts=idle)
    observe(unknown, {"event_type": "residents.all_idle"})
    assert busy.pending == unknown.pending == [] and [commit["value"] for commit in runtime.committed] == ["1", "0"]


def test_resident_state_source_watches_the_intake_domain_by_default(monkeypatch):
    """Resident 状态来源未指定状态域时观察 intake 域，变化时报告之前和现在的状态"""
    Runtime(monkeypatch, {"status": "changed", "previous_value": "idle"})
    domains = []
    state = scan(resident_state_value=lambda lineage, subject, domain, actor, states: domains.append(domain) or "busy")
    observe(state, {"event_type": "resident.state_changed", "subject_resident_id": ALICE})
    [item] = state.pending
    assert domains == ["intake"]
    assert item["event"].payload == {"state_domain": "intake", "previous_state": "idle", "state": "busy"}


def test_message_arrivals_advance_the_cursor_one_after_another():
    """新消息逐条产生 Event，每条的基线从前一条推进；预读后基线已移动时这一轮不产生 Event"""
    base = lineage_message_events.cursor_signature(5)
    arrivals = [{"cursor": cursor, "message_id": f"msg_{cursor}", "author_id": BOB, "relay_depth": 0, "identity": {}}
                for cursor in (6, 7)]
    facts = observers.Facts(message_arrivals={("es_watch", ""): {
        "locator": "message_locator", "base_signature": base, "base_cursor": 5, "cursor": 7,
        "subject_id": ALICE, "arrivals": arrivals}})
    config = {"event_type": lineage_message_events.MESSAGE_RECEIVED_EVENT, "subject_resident_id": ALICE}
    state = scan(facts=facts, runtime_sources={"es_watch": {"state_locator": "message_locator", "state_signature": base}})
    observe(state, config)
    assert [item["cursor"] for item in state.pending] == [f"message-received:{ALICE}:msg_6",
                                                          f"message-received:{ALICE}:msg_7"]
    assert [item["previous_state_signature"] for item in state.pending] == [
        base, lineage_message_events.cursor_signature(6)]
    moved = scan(facts=facts, runtime_sources={"es_watch": {"state_locator": "message_locator",
                                                            "state_signature": lineage_message_events.cursor_signature(6)}})
    observe(moved, config)
    assert moved.pending == []


@pytest.mark.parametrize("fire_on_initial_match", [False, True])
def test_state_condition_met_at_first_look_fires_only_when_configured(monkeypatch, fire_on_initial_match):
    """首次观察时状态已满足条件，配置了 fire_on_initial_match 才产生 Event，否则记录基线"""
    Runtime(monkeypatch, {"status": "baseline"})
    state = scan(facts=observers.Facts(graph_state={"revision": 4, "cells": {"phase": "done"}}))
    observe(state, {"event_type": "state.condition_met", "key": "phase", "operator": "equals",
                    "expected_value": "done", "fire_on_initial_match": fire_on_initial_match})
    assert len(state.pending) == int(fire_on_initial_match)
    assert state.baseline_count == int(not fire_on_initial_match)
