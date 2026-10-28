"""One Event Source watches several Residents; any one of them fires it (decision_44cf587c1721).

Resident Events (finished, failed, started, pressure, state, message received)
may have several Watch wires from Residents.  Each Event names the Resident it
is about, and every watched Resident keeps its own observation, so one
Resident crossing a threshold never hides or repeats another's.
``residents.all_finished`` keeps meaning "all of a Run's Residents finished".

:module: lineage.hatg
:design: decision_4adcc1ff2f2c
"""
from __future__ import annotations

from pathlib import Path

import pytest

from management_portal.society_service import component_runtime, lineage_domain, lineage_program, lineage_program_runtime
from tests.cases import society_service_test_support as lineage_test_support
from tests.cases.test_lineage_generic_primitives import (  # noqa: F401 - primitive_store is a fixture
    ACTOR,
    WORK_A,
    WORK_B,
    WORK_C,
    _worker,
    primitive_store,
)


def _watched_by_two(event_type: str, config: dict | None = None) -> dict:
    return lineage_domain.create_lineage(
        {
            "title": "Watch several Residents",
            "nodes": [_worker(WORK_A), _worker(WORK_B), _worker(WORK_C)],
            "program": {
                "components": [
                    {"id": "event_source_members", "kind": "event_source",
                     "config": {"event_type": event_type, **(config or {})}},
                ],
                "wires": [
                    {"id": "wire_watch_a", "kind": "watch", "source": "event_source_members", "target": WORK_A},
                    {"id": "wire_watch_b", "kind": "watch", "source": "event_source_members", "target": WORK_B},
                    {'id': 'wire_tell_c', 'kind': 'route', 'source': 'event_source_members', 'target': WORK_C},
                ],
            },
        },
        ACTOR,
    )


def _sender(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []

    def send(node: dict, prompt: str, _actor: str) -> dict:
        sent.append((str(node["id"]), prompt))
        return {"message_id": f"msg_{len(sent):032x}"}

    monkeypatch.setattr(lineage_test_support, "NODE_MESSAGE_SENDER", send)
    return sent


def test_a_source_watching_several_residents_fires_for_each_and_names_it(
    primitive_store: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一个 Event Source 监听多个 Resident，任一触发并注明是哪一个

    resident.work_finished 连着两个 Resident 时，计划中是 subject_resident_ids；每个 Resident 完成都触发一次，Event 主体是那个 Resident；重复的完成不再触发。

    :design: decision_44cf587c1721
    """
    del primitive_store
    sent = _sender(monkeypatch)
    created = _watched_by_two("resident.work_finished")
    lineage_id = str(created["lineage_id"])
    plan = lineage_program.compile_program(created["program"], created["nodes"])
    source = plan["event_sources"]["event_source_members"]
    assert source["subject_resident_ids"] == [WORK_A, WORK_B]
    assert "subject_resident_id" not in source
    assert "event.missing_subject" not in {
        item.get("code") for item in lineage_program.program_diagnostics(created["program"], created["nodes"])
    }

    finish = lineage_domain.lineage_program_terminal_reconciliation.dispatch_program_resident_work_finished
    ports = lineage_domain._program_terminal_ports()
    lineage_test_support.record_terminal_execution(WORK_A, "msg_" + "a" * 32, ACTOR)
    lineage_test_support.record_terminal_execution(WORK_B, "msg_" + "b" * 32, ACTOR)
    finish(lineage_id, WORK_A, "msg_" + "a" * 32, ACTOR, ports=ports)
    finish(lineage_id, WORK_B, "msg_" + "b" * 32, ACTOR, ports=ports)
    repeated = finish(lineage_id, WORK_A, "msg_" + "a" * 32, ACTOR, ports=ports)

    assert [target for target, _prompt in sent] == [WORK_C, WORK_C]
    assert WORK_A in sent[0][1] and WORK_B in sent[1][1]
    assert repeated[0]["duplicate"] is True
    # A Resident nobody watches fires nothing.
    assert finish(lineage_id, WORK_C, "msg_" + "c" * 32, ACTOR, ports=ports) == []


def test_replay_under_another_actor_is_a_duplicate(
    primitive_store: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对账以另一身份重放已接受的完成事件时，按重复处理，不报冲突也不再触发

    :importance: P1
    """
    del primitive_store
    sent = _sender(monkeypatch)
    lineage_id = str(_watched_by_two("resident.work_finished")["lineage_id"])
    finish = lineage_domain.lineage_program_terminal_reconciliation.dispatch_program_resident_work_finished
    ports = lineage_domain._program_terminal_ports()
    lineage_test_support.record_terminal_execution(WORK_A, "msg_" + "a" * 32, ACTOR)
    # The live completion ran as the Resident; reconciliation runs as the Lineage's User.
    first = finish(lineage_id, WORK_A, "msg_" + "a" * 32, WORK_A, ports=ports)
    replay = finish(lineage_id, WORK_A, "msg_" + "a" * 32, "portal", ports=ports)

    assert [target for target, _prompt in sent] == [WORK_C]
    assert replay[0]["duplicate"] is True
    assert replay[0]["trigger_path_id"] == first[0]["trigger_path_id"]


def test_each_watched_resident_crosses_a_threshold_on_its_own(
    primitive_store: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """监听多个 Resident 时每个 Resident 各自判断越过阈值

    队列积压 Event Source 监听两个 Resident：各自从阈值以下升到以上时各触发一次，持续偏高不重复，回落后再次越过会再触发；运行记录按 Resident 保存观察。
    """
    del primitive_store
    sent = _sender(monkeypatch)
    created = _watched_by_two(
        "resident.queue_pressure_exceeded",
        {"pressure_metric": "queue_length", "queue_length_threshold": 2},
    )
    lineage_id = str(created["lineage_id"])

    def reconcile(queues: dict[str, int]) -> dict:
        return lineage_domain.reconcile_lineage_system_events(
            lineage_id, ACTOR, resident_activity_snapshot={
                "captured_at": "2026-10-04T10:00:00Z",
                "resident_ids": [WORK_A, WORK_B],
                "active_resident_ids": [rid for rid, count in queues.items() if count],
                "residents": {
                    rid: {"resident_id": rid, "active": bool(count), "status": "running" if count else "idle",
                          "message_id": f"job_{rid}" if count else "", "queue_count": count, "oldest_queued_at": ""}
                    for rid, count in queues.items()
                },
            },
        )

    reconcile({WORK_A: 0, WORK_B: 0})
    assert sent == []
    reconcile({WORK_A: 3, WORK_B: 0})
    reconcile({WORK_A: 5, WORK_B: 0})
    assert len(sent) == 1 and WORK_A in sent[0][1]
    reconcile({WORK_A: 5, WORK_B: 3})
    assert len(sent) == 2 and WORK_B in sent[1][1]
    reconcile({WORK_A: 0, WORK_B: 3})
    reconcile({WORK_A: 4, WORK_B: 3})
    assert len(sent) == 3 and WORK_A in sent[2][1]
    states = lineage_program_runtime.read_runtime(lineage_id)["event_sources"]["event_source_members"]["subjects"]
    assert set(states) == {WORK_A, WORK_B}


def test_resuming_starts_every_watched_resident_from_its_current_state(
    primitive_store: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复监听多个 Resident 的 Event Source 时每个 Resident 重新建立基线

    暂停后恢复，监听多个 Resident 的 Event Source 为每个 Resident 标记重新建立基线，不在源级别留下标记。
    """
    del primitive_store
    _sender(monkeypatch)
    created = _watched_by_two("resident.queue_pressure_exceeded", {"queue_length_threshold": 2})
    lineage_id = str(created["lineage_id"])
    source = {"consumed_cursors": [], "subjects": {WORK_A: {"state_locator": "x", "state_signature": "y", "state_value": "z"}}}
    plan_source = lineage_program.compile_program(created["program"], created["nodes"])["event_sources"]["event_source_members"]
    component_runtime._resume_event_source(source, plan_source)
    assert source["subjects"] == {WORK_A: {"rebaseline": True}, WORK_B: {"rebaseline": True}}
    assert "rebaseline" not in source


def test_only_resident_events_watch_several_and_each_resident_once(primitive_store: Path) -> None:
    """只有 Resident 类 Event 可以监听多个 Resident，且同一 Resident 只能连一次

    resident.work_failed 可以连多个 Resident；file.changed 连多个时报错；同一个 Resident 重复连到同一 Event Source 报错。
    """
    del primitive_store
    nodes = [_worker(WORK_A), _worker(WORK_B)]

    def compile_with(event_type: str, sources: list[str]) -> dict:
        return lineage_program.compile_program({
            "components": [{"id": "event_source_xyz", "kind": "event_source", "config": {"event_type": event_type}}],
            "wires": [
                {"id": f"wire_watch_n{index}", "kind": "watch", "source": "event_source_xyz", "target": source}
                for index, source in enumerate(sources)
            ],
        }, nodes)

    assert compile_with("resident.work_failed", [WORK_A, WORK_B])["event_sources"]["event_source_xyz"][
        "subject_resident_ids"] == [WORK_A, WORK_B]
    with pytest.raises(ValueError, match="只有 Resident 类 Event"):
        compile_with("file.changed", [WORK_A, WORK_B])
    with pytest.raises(ValueError):
        compile_with("resident.work_failed", [WORK_A, WORK_A])


def test_the_inspector_sets_the_watched_residents_at_once() -> None:
    """检查器一次设定 Event Source 监听的 Resident 集合

    watch_subject 为 {kind: resident, ids} 时保留已有连线、补建缺少的、删除多余的；只能监听一个 Resident 的类型拒绝多个。
    """
    program = {
        "components": [{"id": "event_source_xyz", "kind": "event_source", "config": {"event_type": "resident.work_finished"}}],
        "wires": [{"id": "wire_watch_a", "kind": "watch", "source": "event_source_xyz", "target": WORK_A,
                   "source_port": "watch"}],
    }
    added = lineage_program.reconcile_event_source_watch_binding(
        program, "event_source_xyz", {"kind": "resident", "ids": [WORK_A, WORK_B]}, lineage_resident_ids=[WORK_A, WORK_B],
    )
    assert added["updated_wire_ids"] == ["wire_watch_a"] and len(added["created_wire_ids"]) == 1
    assert sorted(wire["target"] for wire in program["wires"]) == [WORK_A, WORK_B]
    narrowed = lineage_program.reconcile_event_source_watch_binding(
        program, "event_source_xyz", {"kind": "resident", "ids": [WORK_B]}, lineage_resident_ids=[WORK_A, WORK_B],
    )
    assert narrowed["removed_wire_ids"] == ["wire_watch_a"]
    assert [wire["target"] for wire in program["wires"]] == [WORK_B]
    program["components"][0]["config"]["event_type"] = "file.changed"
    with pytest.raises(ValueError, match="只能监听一个 Resident"):
        lineage_program.reconcile_event_source_watch_binding(
            program, "event_source_xyz", {"kind": "resident", "ids": [WORK_A, WORK_B]}, lineage_resident_ids=[WORK_A, WORK_B],
        )
