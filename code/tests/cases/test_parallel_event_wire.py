"""A parallel Invoke wire takes each Event as it comes (decision_44cf587c1721).

A Family Router needs no ordering: while one facade message is being routed
the next must start routing too, instead of waiting behind it (an ordered wire
blocks a newer occurrence until the older one is delivered).

:module: lineage.service
:design: decision_44cf587c1721
"""

from __future__ import annotations

import pytest

from management_portal.society_service import lineage_domain, lineage_event_routing, lineage_primitives
from management_portal.society_service import lineage_program_runtime as runtime, trigger_paths
from tests.cases.test_lineage_generic_primitives import primitive_store, ACTOR, WORK_A, _worker  # noqa: F401


def _lineage(wire_config: dict) -> str:
    return lineage_domain.create_lineage({
        "title": "parallel wire", "nodes": [_worker(WORK_A)],
        "program": {
            "components": [
                {"id": "event_source_start", "kind": "event_source", "config": {"event_type": "manual.triggered"}},
                {"id": "effect_route", "kind": "effect", "config": {"script": "output routed=yes", "endpoints": []}},
            ],
            "wires": [{'id': 'wire_route', 'kind': 'route', 'source': 'event_source_start', 'target': 'effect_route', 'config': wire_config}],
        },
    }, ACTOR)["lineage_id"]


def _stage(lineage_id: str, cursor: str) -> None:
    event = lineage_primitives.make_event("manual.triggered", correlation=cursor).to_dict()
    event["payload"]["trigger_path_id"] = trigger_paths.begin_event(
        lineage_id, "event_source_start", event, cause_kind="input", cause_id=cursor,
    )
    runtime.stage_event_delivery(lineage_id, "event_source_start", "wire_route", cursor, event)


def test_a_parallel_wire_claims_a_later_occurrence_while_an_earlier_one_is_delivered(primitive_store):
    """并行连线在前一次投递未完成时领取后一次

    有序连线只领取队首；并行连线按事件键领取，前一次仍在投递时后一次也能开始，同一事件不会被领取两次。
    """
    lineage_id = _lineage({"parallel": True})
    _stage(lineage_id, "c1")
    _stage(lineage_id, "c2")

    first = runtime.claim_pending_event(lineage_id, "wire_route")
    assert first["cursor"] == "c1"
    assert runtime.claim_pending_event(lineage_id, "wire_route") is None  # ordered: waits behind c1
    second = runtime.claim_pending_event(lineage_id, "wire_route", cursor="c2")
    assert second["cursor"] == "c2" and second["dispatch_token"] != first["dispatch_token"]
    assert runtime.claim_pending_event(lineage_id, "wire_route", cursor="c2") is None  # already being delivered
    assert runtime.finish_pending_event(lineage_id, "wire_route", second["dispatch_token"], delivered=True)
    assert runtime.finish_pending_event(lineage_id, "wire_route", first["dispatch_token"], delivered=True)


def test_only_an_ordered_wire_is_blocked_by_its_backlog():
    """只有有序连线被积压阻挡

    连线已有未完成的投递时，有序连线的新事件判为积压不可用，并行连线照常可用。
    """
    event = lineage_primitives.make_event("manual.triggered", correlation="c2")
    backlog = {"wire_parallel": {"pending_count": 1, "oldest_cursor": "c1", "oldest_event_source_id": "source"},
               "wire_ordered": {"pending_count": 1, "oldest_cursor": "c1", "oldest_event_source_id": "source"}}
    routes = [
        {"wire_id": "wire_parallel", "target_id": "effect_a", "target_kind": "effect", "config": {"parallel": True}},
        {"wire_id": "wire_ordered", "target_id": "effect_b", "target_kind": "effect", "config": {}},
    ]

    selection = lineage_event_routing.select_event_routes(
        "lineage_000000000001", "source", routes, event, "c2", backlog,
        bypass_unavailable_policy=True, can_consume_target=lambda route: (True, ""),
    )

    assert [route["wire_id"] for route in selection.available_routes] == ["wire_parallel"]
    assert [item["reason"] for item in selection.unavailable_results] == ["wire_backlog"]


def test_only_a_wire_into_an_effect_can_be_parallel(primitive_store):
    """只有指向 Effect 的 Invoke wire 可以设为并行

    指向 Resident 的连线设置 parallel 时被拒绝，Resident 仍按顺序处理输入。

    :module: lineage.hatg
    """
    assert _lineage({"parallel": True})
    with pytest.raises(ValueError, match="只有指向 Effect 的 Invoke wire 可以并行投递"):
        lineage_domain.create_lineage({
            "title": "parallel to a Resident", "nodes": [_worker(WORK_A)],
            "program": {
                "components": [{"id": "event_source_start", "kind": "event_source",
                                "config": {"event_type": "manual.triggered"}}],
                "wires": [{'id': 'wire_route', 'kind': 'route', 'source': 'event_source_start', 'target': WORK_A, 'config': {'parallel': True}}],
            },
        }, ACTOR)
