"""Behavior regressions for the G4 ISA audit; no live services are mutated.

:module: spec.design-governance
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.society_service import (
    component_runtime, lineage_domain, lineage_primitives, lineage_program,
    lineage_program_authoring, lineage_program_runtime, trigger_path_observation, trigger_paths,
)
from management_portal.society_service.isa import build_kernel, definition, runs
from management_portal.society_service.lineage_program_packet_routing import input_prompt
from tests.cases.test_lineage_generic_primitives import primitive_store, ACTOR, WORK_A, WORK_B, _worker
from tests.cases.test_lineage_program_runtime_claims import lineage_id
from tests.cases import society_service_test_support as support
from tests.cases.effect_script_support import effect_config, lower_effect_call


@pytest.fixture(autouse=True)
def registered_members(monkeypatch):
    support.install_registered_lineage_members(monkeypatch)


def make_lineage(components=None, wires=None):
    return support.create_lineage_with_resident_members({
        "title": "G4 audit",
        "nodes": [_worker(WORK_A), _worker(WORK_B)],
        "program": {"components": components or [], "wires": wires or []},
    }, ACTOR)


def source(config=None, source_id="event_source_start"):
    return {"id": source_id, "kind": "event_source", "config": config or {"event_type": "manual.triggered"}}


def event_wire(config=None):
    return {"id": "wire_start", "kind": "route", "source": "event_source_start",
            "source_port": "output", "target": WORK_A, "target_port": "invoke", "config": config or {}}


def test_isa_clone_commits_and_resolves_local_ref(primitive_store):
    """ApplyLineageChanges 的 clone 操作提交新组件，后续操作可用 $ref 引用它"""
    current = make_lineage([source()])
    result = build_kernel().execute(InstructionCall.from_payload({
        "instruction": "ApplyLineageChanges", "arguments": {
            "lineage_id": current["lineage_id"], "operations": [
                {"op": "clone", "kind": "component", "id": "event_source_start", "ref": "copy",
                 "value": {"title": "Copied"}},
                {"op": "update", "kind": "component", "id": "$ref:copy", "changes": {"title": "Renamed"}},
            ],
        },
    }), InstructionContext(caller=ACTOR, entry="web"))
    assert result.status == "committed", result.error
    cloned_id = result.result["refs"]["copy"]
    assert cloned_id in result.result["created_ids"]
    saved = lineage_domain.get_lineage(current["lineage_id"], ACTOR)
    assert next(c for c in saved["program"]["components"] if c["id"] == cloned_id)["title"] == "Renamed"


def test_effect_resolves_nested_upstream_output():
    """Effect 参数可以引用上游事件中嵌套的输出字段"""
    event = lineage_primitives.make_event("effect.succeeded", payload={"outputs": {"component_id": "effect_aaaaaaaaaaaa"}})
    call = lower_effect_call("lineage_aaaaaaaaaaaa", "effect_b", {**effect_config("ApplyLineageChanges", {
            "operations": {"value": [{"op": "delete", "kind": "component", "id": "$event.outputs.component_id"}]},
        }), "endpoints": {}}, lineage_primitives.EffectInvocation(id="invoke_b", activation=event))
    assert call["arguments"]["operations"][0]["id"] == "effect_aaaaaaaaaaaa"


def test_resident_deletion_reports_removed_wire(primitive_store, monkeypatch):
    """删除 Resident 时结果列出随之删除的连线"""
    # Filesystem archiving belongs to Resident deletion; exercise the graph edit and its public result here.
    monkeypatch.setattr(lineage_domain.resident_domain, "delete_resident_identities", lambda *_, removed_family_ids=(): None)
    current = make_lineage([source()], [event_wire()])
    result = lineage_domain.apply_lineage_changes(current["lineage_id"], {"resident_ids": [WORK_A]}, ACTOR)
    assert {WORK_A, "wire_start"} <= set(result["deleted_ids"])


@pytest.mark.parametrize("mapping,expected", [
    ({"prompt": {"value": "Fixed"}}, "Fixed\n\nInput"),
    ({"prompt": {"from": "event.payload.prompt"}}, "Input"),
    ({"prompt": {"concat": [{"value": "Fixed: "}, {"from": "event.payload.prompt"}]}}, "Fixed: Input"),
])
def test_direct_event_combines_input_once(mapping, expected):
    """事件直连 Resident 时固定提示与事件输入只拼接一次"""
    event = lineage_primitives.make_event("manual.triggered", payload={"prompt": "Input"})
    packet = lineage_primitives.event_packet(event).to_dict()
    actual = input_prompt(packet, lineage_program.resolve_invocation_argument_mapping(mapping, event, packet=packet), mapping)
    assert actual == expected


def test_direct_event_delivers_content_parts(primitive_store, monkeypatch):
    """手动事件携带的 content_parts 随消息投递给 Resident"""
    received = []
    def send(node, prompt, actor, **kwargs):
        received.append({"text": prompt, "parts": kwargs.get("input_media")})
        return {"message_id": "message_g4"}
    monkeypatch.setattr(lineage_domain, "_send_node_message", send)
    current = make_lineage([source()], [event_wire()])
    parts = [{"type": "file", "path": "/tmp/input.txt"}]
    lineage_domain.emit_program_manual_event(current["lineage_id"], {
        "event_source_id": "event_source_start", "prompt": "Input", "content_parts": parts,
    }, ACTOR)
    assert received and received[0]["parts"] == parts
    assert "Input" in received[0]["text"]


@pytest.mark.parametrize("mode", ["forget", "fence"])
def test_definition_change_keeps_paused_state(lineage_id, mode):
    """事件源定义变更后保留暂停状态和暂停时间"""
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        runtime["event_sources"]["event_source_start"] = {"paused": True, "paused_at": "2026-01-01T00:00:00Z"}
    if mode == "fence":
        lineage_program_runtime.fence_event_source_contract_preserving_fire_budget(lineage_id, "event_source_start")
    else:
        lineage_program_runtime.forget_event_source(lineage_id, "event_source_start", preserve_pause=True)
    saved = lineage_program_runtime.read_runtime(lineage_id)["event_sources"]["event_source_start"]
    assert saved["paused"] is True
    assert saved["paused_at"] == "2026-01-01T00:00:00Z"


def test_expired_delivery_remains_observable_after_notification(lineage_id, monkeypatch):
    """过期投递只通知一次，之后触发路径仍可观测为失败"""
    class Clock(datetime):
        current = datetime(2026, 1, 1, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    event = lineage_primitives.make_event("manual.triggered").to_dict()
    identity = trigger_paths.begin_event(lineage_id, "event_source_start", event, cause_kind="input", cause_id="test")
    event["payload"]["trigger_path_id"] = identity
    lineage_program_runtime.defer_event_for_wire(lineage_id, "event_source_start", "wire_start", "cursor_start",
        event, policy="queue", expires_after_seconds=1)
    trigger_paths.event_settled(identity, "event_source_start", event["id"])
    Clock.current += timedelta(seconds=2)
    assert len(lineage_program_runtime.drain_expired_deliveries(lineage_id)) == 1
    assert lineage_program_runtime.drain_expired_deliveries(lineage_id) == []
    assert lineage_program_runtime.read_runtime(lineage_id)["expired_deliveries"] == []
    observed = trigger_path_observation.observe(lineage_id, {"trigger_path_id": identity})
    assert observed["path"]["status"] == "failed"


def budget_lineage():
    return make_lineage([source({"event_type": "manual.triggered", "max_fires": 1}),
        source({"event_type": "budget.exhausted"}, "event_source_budget")], [{
            "id": "wire_watch", "kind": "watch", "source": "event_source_budget", "source_port": "watch",
            "target": "event_source_start",
        }])


def test_budget_exhaustion_notifies_once_per_generation(primitive_store):
    """事件源触发次数用尽时每一代只发一次 budget.exhausted 事件"""
    current = budget_lineage()
    lineage_id = current["lineage_id"]
    first = lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": "event_source_start"}, ACTOR)
    second = lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": "event_source_start"}, ACTOR)
    assert first["budget_exhausted_events"]
    assert not second.get("budget_exhausted_events")
    assert lineage_program_runtime.read_runtime(lineage_id)["event_sources"]["event_source_budget"]["fire_count"] == 1


def test_runtime_derived_event_starts_an_independent_path(primitive_store):
    """运行时派生的事件开启独立的触发路径"""
    current = budget_lineage()
    emitted = lineage_domain.emit_program_manual_event(current["lineage_id"], {"event_source_id": "event_source_start"}, ACTOR)
    derived = emitted["budget_exhausted_events"][0]
    assert derived["trigger_path_id"] != emitted["trigger_path_id"]
    assert trigger_path_observation.observe(current["lineage_id"], {"trigger_path_id": derived["trigger_path_id"]})["path"]["occurrence_id"]


def activity(queue=0, active=False):
    return {"captured_at": "2026-10-01T00:01:00Z", "resident_ids": [WORK_A],
        "active_resident_ids": [WORK_A] if active else [], "residents": {WORK_A: {
            "resident_id": WORK_A, "active": active, "status": "running" if active else "idle",
            "message_id": "message_active" if active else "", "started_at": "2026-10-01T00:00:00Z",
            "queue_count": queue, "oldest_queued_at": "",
        }}}


def test_queue_pressure_baselines_before_crossing(primitive_store):
    """队列压力事件先建立基线，只在队列长度从低于阈值越过阈值时触发"""
    current = make_lineage([source({"event_type": "resident.queue_pressure_exceeded", "queue_length_threshold": 3})], [{
        "id": "wire_watch", "kind": "watch", "source": "event_source_start", "source_port": "watch",
        "target": WORK_A,
    }])
    reconcile = lambda queue: lineage_domain.reconcile_lineage_system_events(current["lineage_id"], ACTOR,
        resident_activity_snapshot=activity(queue=queue))
    assert reconcile(5)["events"] == []
    assert reconcile(0)["events"] == []
    crossed = reconcile(5)
    assert len(crossed["events"]) == 1
    assert crossed["events"][0]["event"]["type"] == "resident.queue_pressure_exceeded"
    assert reconcile(5)["events"] == []


def test_default_event_source_uses_work_finished():
    """新建事件源默认监听 resident.work_finished"""
    assert lineage_program_authoring.new_component_details({"kind": "event_source"})[3]["event_type"] == "resident.work_finished"


@pytest.mark.parametrize("from_source", ["event.payload.prompt", "packet.message.text"])
def test_effect_without_envelope_cannot_read_envelope(from_source):
    """不含信封的 Effect 读不到事件载荷和消息内容，只能用映射参数和默认值"""
    event = lineage_primitives.make_event("manual.triggered", payload={"prompt": "Private envelope"})
    invocation = lineage_primitives.EffectInvocation(id="invoke_test", activation=event,
        arguments={"note": "Mapped"}, packet={"message": {"text": "Private message"}}, include_envelope=False)
    definition = {**effect_config("CreateNotification", {
        "title": {"from": from_source, "default": "No envelope"}, "message": {"from": "invocation.note"},
    }), "endpoints": {}}
    assert lower_effect_call("lineage_aaaaaaaaaaaa", "effect_a", definition, invocation)["arguments"] == {
        "title": "No envelope", "message": "Mapped",
    }


@pytest.mark.parametrize("event_type", ["resident.work_started", "resident.work_duration_exceeded"])
def test_resume_observer_baselines_paused_work(primitive_store, event_type):
    """恢复监听事件源时以正在进行的工作为基线，不补发开始或超时事件"""
    config = {"event_type": event_type}
    if event_type == "resident.work_duration_exceeded":
        config["duration_seconds"] = 1
    current = make_lineage([source(config)], [{
        "id": "wire_watch", "kind": "watch", "source": "event_source_start", "source_port": "watch",
        "target": WORK_A,
    }])
    lineage_id = current["lineage_id"]
    component_runtime.set_paused(lineage_id, "event_source_start", True, ACTOR)
    component_runtime.set_paused(lineage_id, "event_source_start", False, ACTOR)
    result = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR, resident_activity_snapshot=activity(active=True))
    assert result["events"] == []


def test_countdown_emits_time_reached(primitive_store, monkeypatch):
    """倒计时事件源到时发出 time.reached 事件"""
    class Clock(datetime):
        current = datetime(2026, 1, 1, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    monkeypatch.setattr(lineage_domain, "datetime", Clock)
    current = make_lineage([source({"event_type": "time.reached", "schedule_mode": "countdown", "delay_seconds": 1})])
    lineage_domain.reconcile_lineage_system_events(current["lineage_id"], ACTOR)
    Clock.current += timedelta(seconds=120)
    monkeypatch.setattr(lineage_domain, "_scheduled_time_reached", lambda _: True)
    result = lineage_domain.reconcile_lineage_system_events(current["lineage_id"], ACTOR)
    assert result["events"][0]["event"]["type"] == "time.reached"


def test_deleted_operator_executor_leaves_diagnosable_operator(primitive_store, monkeypatch):
    """删除算子的执行 Resident 后清除绑定，诊断报告算子缺少执行者"""
    monkeypatch.setattr(lineage_domain.resident_domain, "delete_resident_identities", lambda *_, removed_family_ids=(): None)
    current = make_lineage([{"id": "filter_test", "kind": "filter", "config": {
        "operator_implementation": "resident", "resident_executor_id": WORK_A,
    }}])
    result = lineage_domain.apply_lineage_changes(current["lineage_id"], {"resident_ids": [WORK_A]}, ACTOR)
    component = next(c for c in result["lineage"]["program"]["components"] if c["id"] == "filter_test")
    assert "resident_executor_id" not in component["config"]
    diagnostics = lineage_program.program_diagnostics(result["lineage"]["program"], result["lineage"]["nodes"])
    assert any(d["code"] == "packet_operator.missing_executor" and d["item_id"] == component["id"] for d in diagnostics)


def test_resume_duration_observer_rearms_after_idle_sample(primitive_store):
    """恢复后的工作超时监听在观察到空闲后重新生效，下一次工作超时时触发"""
    current = make_lineage([source({"event_type": "resident.work_duration_exceeded", "duration_seconds": 1})], [{
        "id": "wire_watch", "kind": "watch", "source": "event_source_start", "source_port": "watch",
        "target": WORK_A,
    }])
    lineage_id = current["lineage_id"]
    component_runtime.set_paused(lineage_id, "event_source_start", True, ACTOR)
    component_runtime.set_paused(lineage_id, "event_source_start", False, ACTOR)
    idle = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR, resident_activity_snapshot=activity())
    assert idle["events"] == []
    next_job = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR, resident_activity_snapshot=activity(active=True))
    assert len(next_job["events"]) == 1
