"""Persistent Event/Effect controls at public ISA and execution boundaries."""
import pytest

from management_portal.hatg import routing
from management_common.json_columns import json_value
from management_common.principal import OperationIdentity
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.society_service import (
    component_runtime, lineage_domain, lineage_program_runtime as runtime,
    lineage_program_effect_execution as execution, lineage_effect_script_journal as journal,
    lineage_primitives, trigger_path_observation,
)
from management_portal.society_service.isa import build_kernel
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_isa_audit_g4 import make_lineage, source, event_wire, registered_members
from tests.cases.test_lineage_generic_primitives import primitive_store, ACTOR
from tests.cases.test_effect_script import _lineage, _plan, _Host, _Outcome

EFFECT = "effect_aaaaaaaaaaaa"


def instruction(name, lid, cid, **extra):
    result = build_kernel().execute(InstructionCall.from_payload({
        "instruction": name, "arguments": {"lineage_id": lid, "component_id": cid, **extra},
    }), InstructionContext(caller=ACTOR, entry="web"))
    assert result.status == "committed", result.error
    return result.result


def test_effect_switch_is_durable_and_does_not_change_definition(primitive_store):
    """Effect 暂停状态持久化且不改变定义修订"""
    current = make_lineage([{"id": EFFECT, "kind": "effect", "config": {"script": ""}}])
    lid = current["lineage_id"]
    before = lineage_domain.get_lineage(lid, ACTOR)
    assert instruction("PauseComponent", lid, EFFECT, paused=True)["runtime"]["paused"] is True
    with get_lineage_data_store().database.read() as connection:
        row = connection.execute("SELECT checkpoint_json FROM operator_work WHERE lineage_id=%s AND operator_id=%s",
                                 (lid, EFFECT)).fetchone()
    assert json_value(row["checkpoint_json"])["paused"] is True
    assert instruction("GetComponentRuntime", lid, EFFECT)["components"][0]["state"] == "paused"
    after = lineage_domain.get_lineage(lid, ACTOR)
    assert after["revision"] == before["revision"]
    assert after["program"] == before["program"]
    assert after["program_effect_runtime"][EFFECT]["paused"] is True
    # paused=false resumes the same component.
    assert instruction("PauseComponent", lid, EFFECT, paused=False)["runtime"]["paused"] is False
    assert lineage_domain.get_lineage(lid, ACTOR)["program_effect_runtime"][EFFECT]["paused"] is False
    with pytest.raises(ValueError, match="没有可重置"):
        component_runtime.reset(lid, EFFECT, ACTOR)


def test_closed_event_cannot_create_trigger_path(primitive_store, monkeypatch):
    """暂停的事件源拒绝触发且不创建 Trigger Path"""
    sent = []
    monkeypatch.setattr(lineage_domain, "_send_node_message", lambda *a, **kw: sent.append(a))
    current = make_lineage([source()], [event_wire()])
    lid = current["lineage_id"]
    instruction("PauseComponent", lid, "event_source_start", paused=True)
    result = build_kernel().execute(InstructionCall.from_payload({
        "instruction": "TriggerEvent", "arguments": {"lineage_id": lid, "event_source_id": "event_source_start"},
    }), InstructionContext(caller=ACTOR, entry="web"))
    assert result.status == "failed"
    assert sent == []
    assert trigger_path_observation.observe(lid, {})["total"] == 0
    assert runtime.read_runtime(lid)["event_sources"]["event_source_start"].get("fire_count", 0) == 0


def test_effect_pause_blocks_initial_execution_and_recovery_between_calls(primitive_store, monkeypatch):
    """Effect 暂停阻止初始执行并在调用间阻止恢复"""
    script = 'Ping\nPong\noutput done="yes"'
    lineage = make_lineage([{"id": EFFECT, "kind": "effect", "config": {"script": script}}])
    lid = lineage["lineage_id"]
    plan = _plan(script)
    activation = lineage_primitives.EffectInvocation(id="switch_invocation", activation=
        lineage_primitives.make_event("manual.triggered", payload={}, identity=OperationIdentity.for_actor(ACTOR).to_dict()))
    calls = []
    def execute(call, context):
        calls.append(call["instruction"])
        if call["instruction"] == "Ping":
            component_runtime.set_paused(lid, EFFECT, True, ACTOR)
        return _Outcome("committed", {}, instruction_id="inst_switch")
    monkeypatch.setattr(routing, "execute", execute)
    host = _Host()
    def run():
        return execution._execute_program_effect_component(lineage, plan, EFFECT, activation, ACTOR, host=host)
    component_runtime.set_paused(lid, EFFECT, True, ACTOR)
    assert run()["status"] == "paused"
    assert calls == []
    component_runtime.set_paused(lid, EFFECT, False, ACTOR)
    assert run()["status"] == "paused"
    assert calls == ["Ping"]
    assert run()["status"] == "paused"
    assert calls == ["Ping"]
    key = execution.effect_idempotency_key(lid, EFFECT, activation)
    assert journal.load(lid, key)["status"] not in journal.FINISHED_STATES
    component_runtime.set_paused(lid, EFFECT, False, ACTOR)
    assert run()["status"] == "executed"
    assert calls == ["Ping", "Pong"]


def test_queued_effect_waits_while_closed_and_executes_once_after_open(primitive_store, monkeypatch):
    """排队的 Effect 暂停时保留事件并在恢复后执行一次"""
    from management_portal.society_service import lineage_program_backlog
    calls = []
    monkeypatch.setattr(routing, "execute", lambda call, context: (
        calls.append(call["instruction"]) or _Outcome("committed", {}, instruction_id="inst_switch_queue")))
    current = make_lineage([source(), {"id": EFFECT, "kind": "effect", "config": {"script": "GetLineage"}}],
                           [{**event_wire(), "target": EFFECT}])
    lid = current["lineage_id"]
    component_runtime.set_paused(lid, EFFECT, True, ACTOR)
    result = lineage_domain.emit_program_manual_event(lid, {"event_source_id": "event_source_start"}, ACTOR)
    assert calls == []
    assert result["results"][0]["status"] == "paused"
    assert result["duplicate"] is False
    pending = runtime.read_runtime(lid)["event_wires"]["wire_start"]["pending_events"]
    assert len(pending) == 1
    assert not pending[0].get("dispatch_token")
    assert not pending[0].get("attempt_count")
    assert not pending[0].get("retry_at")
    component_runtime.set_paused(lid, EFFECT, False, ACTOR)
    lineage_program_backlog.drain_event_backlog(lid, ACTOR, authority=lineage_domain._program_control_authority())
    assert calls == ["GetLineage"]


@pytest.mark.parametrize("event_type", ["time.reached", "portal.release_published"])
def test_closed_event_rejects_system_callbacks_and_survives_reset(primitive_store, event_type):
    """事件源暂停状态经重置保留并拒绝系统回调"""
    current = make_lineage([source({"event_type": event_type})])
    lid = current["lineage_id"]
    component_runtime.set_paused(lid, "event_source_start", True, ACTOR)
    component_runtime.reset(lid, "event_source_start", ACTOR)
    event = lineage_primitives.make_event(event_type, identity=OperationIdentity.for_actor(ACTOR).to_dict())
    result = lineage_domain.dispatch_lineage_program_event(lid, "event_source_start", event, ACTOR, cursor="callback")
    assert result["paused"] is True
    assert trigger_path_observation.observe(lid, {})["total"] == 0
