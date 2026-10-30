"""A Resident finishing, failing or cancelling work may be observed for a whole set.

``config.resident_scope`` of ``resident.work_finished``, ``work_failed`` and
``work_cancelled``: ``watched`` (the Residents its Watch wires name),
``lineage`` (every Resident of the Lineage, including ones added later) or
``system`` (every Resident of Portal).  Any one Resident of the set fires the
source once and the Event names it.  A scoped source needs no Watch wire, and
it never fires for work it delivered itself, directly or through an Effect.

:module: lineage.hatg
:design: decision_4adcc1ff2f2c
"""
from __future__ import annotations

from pathlib import Path

import pytest

from management_portal.society_service import (
    lineage_catalog,
    lineage_domain,
    lineage_lifecycle,
    lineage_primitive_definitions,
    lineage_program_compilation,
    lineage_program_diagnostics,
    lineage_program_edits,
    lineage_program_event_scope,
    lineage_program_resident_inputs,
    lineage_program_terminal_reconciliation as terminal,
    lineage_reads,
)
from tests.cases import society_service_test_support as lineage_test_support
from tests.cases.test_lineage_generic_primitives import (  # noqa: F401 - primitive_store is a fixture
    ACTOR,
    WORK_A,
    WORK_B,
    WORK_C,
    _worker,
    primitive_store,
)

OUTSIDER = "resident_0000000000ff"


def _scoped(scope: str, event_type: str = "resident.work_finished") -> dict:
    return lineage_lifecycle.create_lineage(
        {
            "title": "Observe a set of Residents",
            "nodes": [_worker(WORK_A), _worker(WORK_B), _worker(WORK_C)],
            "program": {
                "components": [
                    {"id": "event_source_any", "kind": "event_source",
                     "config": {"event_type": event_type, "resident_scope": scope}},
                ],
                "wires": [
                    {'id': 'wire_tell_c', 'kind': 'route', 'source': 'event_source_any', 'target': WORK_C},
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


def test_a_lineage_scoped_source_fires_for_any_member_without_watch_wires(
    primitive_store: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """按本 Lineage 范围监听的完成事件：任一成员完成都触发，不需要 Watch 连线

    resident.work_finished 的 resident_scope=lineage 时编译后没有 Watch 主体也不报 event.missing_subject；成员 A、B 各自完成各触发一次并注明主体，不是成员的 Resident 不触发。
    """
    del primitive_store
    sent = _sender(monkeypatch)
    created = _scoped("lineage")
    lineage_id = str(created["lineage_id"])
    plan = lineage_program_compilation.compile_program(created["program"], created["nodes"])
    source = plan["event_sources"]["event_source_any"]
    assert source["resident_scope"] == "lineage"
    assert lineage_program_event_scope.event_source_subject_ids(source) == ()
    assert "event.missing_subject" not in {
        item.get("code") for item in lineage_program_diagnostics.program_diagnostics(created["program"], created["nodes"])
    }

    from tests.cases.society_service_test_support import record_terminal_execution
    def finish(lid, resident, message, actor, **options):
        if resident != OUTSIDER:
            record_terminal_execution(resident, message, actor)
        return terminal.dispatch_program_resident_work_finished(lid, resident, message, actor, **options)
    ports = lineage_domain._program_terminal_ports()
    finish(lineage_id, WORK_A, "msg_" + "a" * 32, ACTOR, ports=ports)
    finish(lineage_id, WORK_B, "msg_" + "b" * 32, ACTOR, ports=ports)
    assert [target for target, _prompt in sent] == [WORK_C, WORK_C]
    assert WORK_A in sent[0][1] and WORK_B in sent[1][1]
    # Not a member of this Lineage: a lineage-scoped source does not observe it.
    assert finish(lineage_id, OUTSIDER, "msg_" + "f" * 32, ACTOR, ports=ports) == []


def test_a_scoped_source_never_fires_for_work_it_delivered(
    primitive_store: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """按范围监听的事件源不因自己投递出去的工作再次触发

    事件投递给 C 的工作结束时返回 self_caused 且不再投递，避免自我循环；C 的其他工作仍然触发。
    """
    del primitive_store
    sent = _sender(monkeypatch)
    created = _scoped("lineage")
    lineage_id = str(created["lineage_id"])
    from tests.cases.society_service_test_support import record_terminal_execution
    def finish(lid, resident, message, actor, **options):
        if resident != OUTSIDER:
            record_terminal_execution(resident, message, actor)
        return terminal.dispatch_program_resident_work_finished(lid, resident, message, actor, **options)
    ports = lineage_domain._program_terminal_ports()

    finish(lineage_id, WORK_A, "msg_" + "a" * 32, ACTOR, ports=ports)
    assert len(sent) == 1
    delivered = f"msg_{1:032x}"
    # C finishing the work this source gave it would otherwise loop forever.
    [skipped] = finish(lineage_id, WORK_C, delivered, ACTOR, ports=ports)
    assert skipped["self_caused"] is True and skipped["matched_count"] == 0
    assert len(sent) == 1
    # C's other work is still observed.
    finish(lineage_id, WORK_C, "msg_" + "c" * 32, ACTOR, ports=ports)
    assert len(sent) == 2 and WORK_C in sent[1][1]


def test_work_delivered_through_an_effect_counts_as_the_sources_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """经 Effect 投递的工作也算事件源自己的工作

    投递账本记录的连线是进入 Effect 的那条 Event 连线时，按范围监听的事件源跳过这次结束；按 Watch 监听的事件源不受影响。

    Event -> Effect (ClearContext, DeliverMessage) -> Resident: the ledger names the wire into the Effect."""

    plan = {"event_sources": {
        "event_source_any": {"event_type": "resident.work_finished", "resident_scope": "system",
                             "subscriber_routes": [{"wire_id": "wire_into_effect"}]},
        "event_source_watched": {"event_type": "resident.work_finished", "subject_resident_id": WORK_C,
                                 "subscriber_routes": [{"wire_id": "wire_into_effect"}]},
    }}
    monkeypatch.setattr(lineage_program_resident_inputs, "resident_handoff", lambda lineage_id, resident_id, message_id: (
        {"message_id": "msg_fresh", "resident_id": WORK_C, "source_wire_id": "wire_into_effect"}
        if (resident_id, message_id) == (WORK_C, "msg_fresh") else None
    ))
    matched = [("event_source_any", "", WORK_C), ("event_source_watched", "", WORK_C)]

    # Only scoped sources skip their own work; a watched source keeps today's meaning.
    assert terminal._self_caused_sources(plan, "lineage_x", matched, WORK_C, "msg_fresh") == {"event_source_any"}
    assert terminal._self_caused_sources(plan, "lineage_x", matched, WORK_C, "msg_other") == set()


def test_a_system_scoped_source_observes_any_resident_and_a_facade_once() -> None:
    """Portal 范围的事件源观察任意 Resident，门面转交的工作只报告一次

    system 范围只报告做这项工作的成员，不再以门面名义重复；Watch 门面的事件源仍收到门面主体。
    """
    plan = {"event_sources": {
        "event_source_any": {"event_type": "resident.work_failed", "resident_scope": "system"},
        "event_source_facade": {"event_type": "resident.work_failed", "subject_resident_id": WORK_B},
    }}
    # A member finishing work relayed from the facade WORK_B.
    subjects = {OUTSIDER: ("msg_member", {}), WORK_B: ("msg_facade", {"member_resident_id": OUTSIDER})}
    matched = terminal._watching_sources(
        plan, "resident.work_failed", subjects, None, resident_id=OUTSIDER, member_ids=[WORK_A],
    )
    assert [(source_id, subject) for source_id, _contract, subject in matched] == [
        ("event_source_any", OUTSIDER),
        ("event_source_facade", WORK_B),
    ]
    assert terminal._watching_sources(
        plan, "resident.work_finished", subjects, None, resident_id=OUTSIDER, member_ids=[],
    ) == []


def test_the_catalog_finds_scoped_observers_for_any_resident(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """目录反向观察索引列出按范围监听的事件源

    system 范围的事件源对任意 Resident 列出，lineage 范围只对该 Lineage 的成员列出，并按事件类型区分。
    """
    definitions = [{
        "lineage_id": "lineage_aaaaaaaaaaaa",
        "nodes": [{"id": WORK_A}],
        "program": {"components": [
            {"id": "event_source_members", "kind": "event_source",
             "config": {"event_type": "resident.work_finished", "resident_scope": "lineage"}},
            {"id": "event_source_portal", "kind": "event_source",
             "config": {"event_type": "resident.work_finished", "resident_scope": "system"}},
            {"id": "event_source_failed", "kind": "event_source",
             "config": {"event_type": "resident.work_failed", "resident_scope": "system"}},
        ], "wires": []},
    }]
    from tests.cases.society_service_test_support import install_resource_boundary, create_lineage_with_resident_members
    install_resource_boundary(monkeypatch)
    created = create_lineage_with_resident_members(definitions[0], ACTOR)
    catalog = lineage_catalog.get_catalog()

    assert catalog.program_resident_observers(WORK_A, "resident.work_finished") == {
        created["lineage_id"]: ["event_source_members", "event_source_portal"],
    }
    assert catalog.program_resident_observers(OUTSIDER, "resident.work_finished") == {
        created["lineage_id"]: ["event_source_portal"],
    }


def test_a_scoped_source_takes_no_watch_wire(primitive_store: Path) -> None:
    """按范围监听的事件源不接受 Watch 连线，范围取值受限

    范围为 system 时连 Watch 被拒绝；未知范围被拒绝；非完成/失败/取消类型不接受 resident_scope。
    """
    del primitive_store
    with pytest.raises(ValueError, match="不使用 Watch 连线"):
        lineage_lifecycle.create_lineage(
            {
                "title": "Scope and Watch together",
                "nodes": [_worker(WORK_A), _worker(WORK_C)],
                "program": {
                    "components": [{"id": "event_source_any", "kind": "event_source",
                                    "config": {"event_type": "resident.work_cancelled", "resident_scope": "system"}}],
                    "wires": [{"id": "wire_watch_a", "kind": "watch", "source": "event_source_any", "target": WORK_A}],
                },
            },
            ACTOR,
        )
    with pytest.raises(ValueError, match="监听范围"):
        _scoped("everyone")
    with pytest.raises(ValueError, match="不接受字段"):
        _scoped("lineage", event_type="resident.work_started")


def test_choosing_a_scope_drops_the_watch_wires(primitive_store: Path) -> None:
    """把监听范围改为本 Lineage 时删除原有 Watch 连线

    保存组件把 resident_scope 改为 lineage 时同时删除指向它的 Watch 连线，保留 Event 连线。
    """
    del primitive_store
    created = lineage_lifecycle.create_lineage(
        {
            "title": "Watched first",
            "nodes": [_worker(WORK_A), _worker(WORK_C)],
            "program": {
                "components": [{"id": "event_source_any", "kind": "event_source",
                                "config": {"event_type": "resident.work_finished"}}],
                "wires": [
                    {"id": "wire_watch_a", "kind": "watch", "source": "event_source_any", "target": WORK_A},
                    {'id': 'wire_tell_c', 'kind': 'route', 'source': 'event_source_any', 'target': WORK_C},
                ],
            },
        },
        ACTOR,
    )
    saved = lineage_program_edits.save_lineage_program_component(
        str(created["lineage_id"]),
        {"component_id": "event_source_any", "component": {
            "config": {"event_type": "resident.work_finished", "resident_scope": "lineage"},
        }},
        ACTOR,
    )
    assert saved["removed_wire_ids"] == ["wire_watch_a"]
    lineage = lineage_reads.get_lineage(str(created["lineage_id"]), ACTOR)
    assert [wire["id"] for wire in lineage["program"]["wires"]] == ["wire_tell_c"]
    assert lineage["program"]["components"][0]["config"]["resident_scope"] == "lineage"


def test_the_watch_requirement_follows_the_scope() -> None:
    """Watch 是否必需随监听范围变化

    watched 范围需要 Watch，lineage 与 system 不需要；三种终态类型都提供 watched、lineage、system 三个选项。
    """
    assert lineage_primitive_definitions.watch_required("resident.work_finished", {}) is True
    assert lineage_primitive_definitions.watch_required("resident.work_finished", {"resident_scope": "watched"}) is True
    for scope in ("lineage", "system"):
        for event_type in sorted(lineage_program_event_scope.SCOPED_RESIDENT_EVENT_TYPES):
            assert lineage_primitive_definitions.watch_required(event_type, {"resident_scope": scope}) is False
    assert lineage_primitive_definitions.watch_required("resident.work_started", {}) is True
    [scope_field] = [
        field for field in lineage_primitive_definitions.fields_for("event", "resident.work_failed")
        if field["path"] == "config.resident_scope"
    ]
    assert [option["value"] for option in scope_field["options"]] == ["watched", "lineage", "system"]
