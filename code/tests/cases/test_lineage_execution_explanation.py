""":module: lineage.hatg"""
from __future__ import annotations


from management_portal.society_service import lineage_execution_explanation
from tests.cases.effect_script_support import effect_config, lower_effect_call


def _lineage(*, components=None, wires=None, nodes=None):
    return {
        "program": {
            "components": components or [],
            "wires": wires or [],
        },
        "nodes": nodes or [],
    }


def _event(event_type="manual.triggered", **config):
    return {
        "id": "event_source_test",
        "kind": "event_source",
        "config": {"event_type": event_type, "max_fires": None, **config},
    }


def _effect():
    return {
        "id": "effect_test",
        "kind": "effect",
        "config": effect_config("DeliverMessage", {"resident_id": {"from": "endpoint.target"}}, ["target"]),
    }


def _wire(wire_id, kind, source, target, source_port="output"):
    return {
        "id": wire_id,
        "kind": kind,
        "source": source,
        "source_port": source_port,
        "target": target,
        **({"target_port": "invoke"} if kind == "route" else {}),
    }


def test_event_explains_waiting_unrouted_and_zero_limit_without_history_contracts(subtests, isolated_portal_case):
    """执行说明解释事件源等待、无下游、零上限和缺 watch，Effect 缺端点或待调用，连线排队

    :module: lineage.service"""
    with subtests.test(contract='test_event_explains_waiting_unrouted_and_zero_limit_without_history'), isolated_portal_case() as case_path:
        routed = _lineage(
            components=[_event()],
            wires=[_wire("wire_event", "route", "event_source_test", "resident_target")],
        )
        waiting = lineage_execution_explanation.explain_program(
            routed,
            event_source_runtime={
                "event_source_test": {
                    "occurrence_count": 0,
                    "max_fires": None,
                },
            },
        )["nodes"]["event_source_test"]
        assert waiting["state"] == "waiting"
        assert "手动触发" in waiting["summary"]
        assert "1 条路径" in waiting["next_action"]

        unrouted = lineage_execution_explanation.explain_program(
            _lineage(components=[_event()]),
        )["nodes"]["event_source_test"]
        assert unrouted["state"] == "unrouted"
        assert "没有下游" in unrouted["summary"]

        exhausted = lineage_execution_explanation.explain_program(
            _lineage(components=[_event(max_fires=0)]),
            event_source_runtime={
                "event_source_test": {
                    "occurrence_count": 0,
                    "max_fires": 0,
                    "exhausted": True,
                },
            },
        )["nodes"]["event_source_test"]
        assert exhausted["state"] == "completed"
        assert "不会产生事件" in exhausted["why"]
    with subtests.test(contract='test_observer_event_explains_missing_watch_as_a_binding_problem'), isolated_portal_case() as case_path:
        explanation = lineage_execution_explanation.explain_program(
            _lineage(components=[_event("resident.work_finished")]),
        )["nodes"]["event_source_test"]

        assert explanation["state"] == "blocked"
        assert "Watch 检查对象" in explanation["blocked_reason"]
    with subtests.test(contract='test_effect_separates_target_binding_from_invoke_readiness'), isolated_portal_case() as case_path:
        missing_target = lineage_execution_explanation.explain_program(
            _lineage(components=[_effect()]),
        )["nodes"]["effect_test"]
        assert missing_target["state"] == "blocked"
        assert "缺少端点绑定：target" in missing_target["blocked_reason"]

        target_wire = _wire(
            "wire_target",
            "target",
            "effect_test",
            "resident_target",
            "target",
        )
        waiting = lineage_execution_explanation.explain_program(
            _lineage(components=[_effect()], wires=[target_wire]),
        )["nodes"]["effect_test"]
        assert waiting["state"] == "waiting"
        assert "等待 Invoke" in waiting["summary"]

        ready = lineage_execution_explanation.explain_program(
            _lineage(
                components=[_event(), _effect()],
                wires=[
                    target_wire,
                    _wire("wire_invoke", "route", "event_source_test", "effect_test"),
                ],
            ),
        )["nodes"]["effect_test"]
        assert ready["state"] == "ready"
        assert "到达时运行一次脚本" in ready["next_action"]
    with subtests.test(contract='test_invoke_wire_explains_empty_and_queued_branches'), isolated_portal_case() as case_path:
        lineage = _lineage(
            wires=[_wire("wire_invoke", "route", "event_source_test", "resident_target")],
        )
        ready = lineage_execution_explanation.explain_program(lineage)["wires"]["wire_invoke"]
        assert ready["state"] == "ready"

        queued = lineage_execution_explanation.explain_program(
            lineage,
            invoke_wire_runtime={"wire_invoke": {"pending_count": 3}},
        )["wires"]["wire_invoke"]
        assert queued["state"] == "queued"
        assert "3 个 Invocation" in queued["summary"]


def test_a_paused_event_source_is_explained_as_paused():
    """暂停的 Event Source 在执行说明中显示已暂停，恢复后回到等待

    A full TAU run paused a timed source from Home's Scheduled & Triggered
    panel; the Lineage page kept saying it was waiting for the time."""
    routed = _lineage(
        components=[_event("time.reached", schedule_mode="countdown", delay_seconds=60)],
        wires=[_wire("wire_event", "route", "event_source_test", "resident_target")],
    )
    explanation = lineage_execution_explanation.explain_program(
        routed,
        event_source_runtime={"event_source_test": {"occurrence_count": 1, "max_fires": None, "paused": True}},
    )["nodes"]["event_source_test"]
    assert (explanation["state"], explanation["summary"]) == ("paused", "已暂停，不会触发")
    resumed = lineage_execution_explanation.explain_program(
        routed,
        event_source_runtime={"event_source_test": {"occurrence_count": 1, "max_fires": None, "paused": False}},
    )["nodes"]["event_source_test"]
    assert resumed["state"] == "waiting" and resumed["summary"] == "等待设定时间到达"
