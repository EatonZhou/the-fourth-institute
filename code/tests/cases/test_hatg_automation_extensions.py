from __future__ import annotations

from datetime import datetime, timezone

import pytest

from management_portal.society_service import (
    lineage_resident_controls,
    lineage_graph_state,
    lineage_human_notifications,
    lineage_program,
    lineage_program_identity,
    lineage_program_schedules,
    lineage_program_wire_queue,
    trigger_paths,
    state_store,
)
from management_portal.society_service import lineage_aggregate


ACTOR = "sample_admin"
WORK_A = "resident_aaaaaaaaaaaa"
LINEAGE_ID = "lineage_aaaaaaaaaaaa"


@pytest.fixture()
def lineage_id() -> str:
    state_store.get_lineage_data_store().put_lineage_definition(
        LINEAGE_ID,
        {
            "schema": "tfi.lineage.v1",
            "schema_version": 1,
            "lineage_id": LINEAGE_ID,
            "title": "HATG runtime unit test",
            "nodes": [],
            "edges": [],
            "program": {},
        },
        {
            "schema": "tfi.lineage-runtime.v1",
            "lineage_id": LINEAGE_ID,
        },
    )
    return LINEAGE_ID


def _worker(resident_id: str) -> dict:
    return {
        "id": resident_id,
        "title": resident_id,
        "status": "idle",
    }


def test_graph_state_update_and_compare_and_set_are_atomic(lineage_id: str) -> None:
    """Lineage 共享状态的更新、比较并设置和删除按期望值原子执行并递增 state_revision"""
    first = lineage_graph_state.update_cell(lineage_id, "loop.count", 1)
    assert first == {
        "key": "loop.count",
        "previous_value": None,
        "value": 1,
        "changed": True,
        "matched": True,
        "state_revision": 1,
        "updated_at": first["updated_at"],
    }
    assert lineage_graph_state.read_cell(lineage_id, "loop.count") == {
        "key": "loop.count",
        "exists": True,
        "value": 1,
        "state_revision": 1,
        "updated_at": first["updated_at"],
    }

    mismatch = lineage_graph_state.update_cell(
        lineage_id,
        "loop.count",
        2,
        expected_value=0,
    )
    assert mismatch["matched"] is False
    assert mismatch["changed"] is False
    assert mismatch["value"] == 1
    assert mismatch["state_revision"] == 1

    matched = lineage_graph_state.update_cell(
        lineage_id,
        "loop.count",
        2,
        expected_value=1,
    )
    assert matched["matched"] is True
    assert matched["changed"] is True
    assert matched["value"] == 2
    assert matched["state_revision"] == 2

    delete_conflict = lineage_graph_state.delete_cell(
        lineage_id,
        "loop.count",
        1,
    )
    assert delete_conflict["matched"] is False
    assert delete_conflict["deleted"] is False
    assert delete_conflict["state_revision"] == 2

    deleted = lineage_graph_state.delete_cell(
        lineage_id,
        "loop.count",
        2,
    )
    assert deleted["matched"] is True
    assert deleted["deleted"] is True
    assert deleted["changed"] is True
    assert deleted["previous_value"] == 2
    assert deleted["state_revision"] == 3

    missing = lineage_graph_state.delete_cell(lineage_id, "loop.count")
    assert missing["deleted"] is False
    assert missing["changed"] is False
    assert missing["state_revision"] == 3
    assert lineage_graph_state.read_cell(lineage_id, "loop.count") == {
        "key": "loop.count",
        "exists": False,
        "value": None,
        "state_revision": 3,
        "updated_at": deleted["updated_at"],
    }


def test_countdown_timer_keeps_one_runtime_anchor(lineage_id: str) -> None:
    """倒计时事件源重复登记时保持同一计时锚点，改变延时才进入新一代"""
    first = lineage_program_schedules.ensure_countdown_timer(
        lineage_id,
        "event_source_countdown",
        delay_seconds=60,
    )
    second = lineage_program_schedules.ensure_countdown_timer(
        lineage_id,
        "event_source_countdown",
        delay_seconds=60,
    )
    assert second == first
    changed = lineage_program_schedules.ensure_countdown_timer(
        lineage_id,
        "event_source_countdown",
        delay_seconds=120,
    )
    assert changed["generation"] == first["generation"] + 1
    assert changed["delay_seconds"] == 120


def test_expired_event_delivery_becomes_a_durable_runtime_fact_contracts(subtests, isolated_portal_case):
    """过期未投递的事件从连线移除并记为失败的触发路径，事件源之间的监听不能成环"""
    with subtests.test(contract='test_expired_event_delivery_becomes_a_durable_runtime_fact'), isolated_portal_case() as case_path:
        from tests.cases.test_hatg_run_observation import occurrence, AT
        lid = "lineage_expiring_delivery"
        identity, event = occurrence(lid)
        pending = {"event_source_id": "source", "cursor": "cursor", "event": event,
                   "queued_at": AT, "expires_at": "2026-07-31T00:00:00Z"}
        trigger_paths.delivery_waiting(lid, "wire", pending)
        trigger_paths.event_settled(identity, "source", event["id"])
        runtime = {"event_sources": {}, "event_wires": {"wire": {"pending_events": [pending]}}, "expired_deliveries": []}
        removed = lineage_program_wire_queue._prune_expired_pending_events(
            runtime, lineage_id=lid, now=datetime(2026,7,31,0,0,1,tzinfo=timezone.utc))
        assert removed == 1 and runtime["event_wires"] == {}
        assert runtime["expired_deliveries"][0]["event"] == event
        history = trigger_paths.get(lid, identity)
        assert history["status"] == "failed"
        assert any(step["node_id"] == "wire" and step["status"] == "failed" for step in history["steps"])
    with subtests.test(contract='test_runtime_event_source_observer_cycles_are_rejected'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match="监听关系不能形成循环"):
            lineage_program.normalize_program(
                {
                    "components": [
                        {
                            "id": "event_source_runtime_a",
                            "kind": "event_source",
                            "config": {
                                "event_type": "event.delivery_failed",
                            },
                        },
                        {
                            "id": "event_source_runtime_b",
                            "kind": "event_source",
                            "config": {
                                "event_type": "event.delivery_expired",
                            },
                        },
                    ],
                    "wires": [
                        {
                            "id": "wire_watch_runtime_a",
                            "kind": "watch",
                            "source": "event_source_runtime_a",
                            "source_port": "watch",
                            "target": "event_source_runtime_b",
                        },
                        {
                            "id": "wire_watch_runtime_b",
                            "kind": "watch",
                            "source": "event_source_runtime_b",
                            "source_port": "watch",
                            "target": "event_source_runtime_a",
                        },
                    ],
                },
                lineage_nodes=[],
            )
    with subtests.test(contract='test_time_event_normalizes_countdown_without_timer_effect'), isolated_portal_case() as case_path:
        normalized = lineage_program.normalize_program(
            {
                "components": [
                    {
                        "id": "event_source_countdown_test",
                        "kind": "event_source",
                        "config": {
                            "event_type": "time.reached",
                            "schedule_mode": "countdown",
                            "delay_seconds": 15,
                        },
                    }
                ],
                "wires": [],
            },
            lineage_nodes=[],
        )
        event_source = normalized["components"][0]
        assert event_source["config"]["event_type"] == "time.reached"
        assert event_source["config"]["schedule_mode"] == "countdown"
        assert event_source["config"]["delay_seconds"] == 15


def test_human_notifications_are_durable_and_idempotent(lineage_id: str) -> None:
    """Lineage 发给人的通知持久保存，按 notification_id 重复创建只保留一条"""
    first = lineage_human_notifications.create_notification(
        lineage_id,
        title="Need approval",
        message="Please inspect the result.",
        severity="warning",
        recipients=[ACTOR],
        created_by=ACTOR,
        notification_id="human_notice_fixed",
    )
    duplicate = lineage_human_notifications.create_notification(
        lineage_id,
        title="Need approval",
        message="Please inspect the result.",
        severity="warning",
        recipients=[ACTOR],
        created_by=ACTOR,
        notification_id="human_notice_fixed",
    )
    assert first["created"] is True
    assert duplicate["created"] is False
    notifications = lineage_human_notifications.list_actor_notifications(ACTOR, [lineage_id])[0]["notifications"]
    assert [item["id"] for item in notifications] == ["human_notice_fixed"]
    assert notifications[0]["created_by"] == ACTOR
    assert notifications[0]["recipients"] == [ACTOR]


def test_quiesce_delegates_close_and_non_continuing_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """让 Lineage 中的 Resident 静默时关闭它并停止当前执行，不继续处理队列"""
    monkeypatch.setattr(
        lineage_aggregate,
        "read_lineage",
        lambda _lineage_id, **_kwargs: {
            "lineage_id": "lineage_aaaaaaaaaaaa",
            "nodes": [_worker(WORK_A)],
        },
    )
    monkeypatch.setattr(lineage_aggregate, "ensure_manager", lambda *_args: None)
    state_calls: list[dict] = []
    from management_portal.society_service import resident_protocol

    monkeypatch.setattr(
        resident_protocol,
        "change_resident_state",
        lambda resident_id, actor, **kwargs: (
            state_calls.append(
                {"resident_id": resident_id, "actor": actor, **kwargs}
            )
            or {"state": "closed"}
        ),
    )
    stop_calls: list[dict] = []
    monkeypatch.setattr(
        resident_protocol,
        "get_resident_state",
        lambda resident_id, actor: {
            "resident_id": resident_id,
            "state": {
                "active_message_id": "message_active",
                "work_state": "running",
            },
        },
    )
    monkeypatch.setattr(
        resident_protocol,
        "cancel_resident_execution",
        lambda resident_id, actor, message_id, **kwargs: (
            stop_calls.append(
                {
                    "resident_id": resident_id,
                    "actor": actor,
                    "message_id": message_id,
                    **kwargs,
                }
            )
            or {
                "resident_id": resident_id,
                "cancelled": True,
                "cleared_queue_count": 2,
                "state": {"work_state": "idle"},
                "cursor": 4,
            }
        ),
    )
    result = lineage_resident_controls.quiesce_lineage_resident(
        "lineage_aaaaaaaaaaaa",
        {"target": WORK_A},
        ACTOR,
    )
    assert state_calls[0]["transition"] == "close"
    assert stop_calls[0]["continue_queue"] is False
    assert result["cleared_queue_count"] == 2
