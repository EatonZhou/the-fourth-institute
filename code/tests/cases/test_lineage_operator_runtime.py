from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.society_service import (
    lineage_operator_runtime,
    lineage_primitives,
    state_store,
)


LINEAGE_ID = "lineage_aaaaaaaaaaaa"
OPERATOR_ID = "reducer_aaaaaaaaaaaa"
EXECUTOR_ID = "resident_aaaaaaaaaaaa"
TARGET_ID = "resident_bbbbbbbbbbbb"


@pytest.fixture()
def lineage_id() -> str:
    state_store.get_lineage_data_store().put_lineage_definition(
        LINEAGE_ID,
        {
            "schema": "tfi.lineage.v1",
            "schema_version": 1,
            "lineage_id": LINEAGE_ID,
            "title": "Operator runtime unit test",
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


def _invocation(message_id: str, event_id: str) -> dict:
    return {
        "message_id": message_id,
        "operator_id": OPERATOR_ID,
        "executor_resident_id": EXECUTOR_ID,
        "event": {
            "id": event_id,
            "type": "resident.work_finished",
            "subject_resident_id": TARGET_ID,
            "occurred_at": "2026-07-29T12:00:00Z",
            "payload": {"message_id": "message_target"},
            "identity": {},
        },
        "source_wire_ids": ["wire_input"],
    }


def test_concurrent_operator_updates_do_not_lose_invocations(
    lineage_id: str,
) -> None:
    """并发更新算子待投递记录保留全部调用"""
    def store(index: int) -> None:
        lineage_operator_runtime.update_pending_delivery(
            lineage_id,
            OPERATOR_ID,
            _invocation(f"message_{index}", f"event_{index}"),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(store, range(40)))

    runtime = lineage_operator_runtime.read_operator_runtime(
        lineage_id,
        OPERATOR_ID,
    )
    assert set(runtime["pending_deliveries"]) == {
        f"message_{index}" for index in range(40)
    }


def test_operator_inputs_require_the_prepared_route_set(lineage_id):
    """算子输入遵循预先冻结的 Route 集合

    输入必须进入预先准备好的批次；批次成员不可临时改变，批次取出后迟到输入不能隐式重建它。
    """
    event = lineage_primitives.make_event("manual.triggered")

    def deliver(wire_id):
        packet = lineage_primitives.packet_envelope(
            lineage_primitives.make_packet("result", wire_id),
            event_id=event.id,
            from_node_id=TARGET_ID,
            source_wire_id=wire_id,
        )
        return lineage_operator_runtime.update_inbox(
            lineage_id, OPERATOR_ID, event=event,
            source_wire_id=wire_id, source_node_id=TARGET_ID, packets=[packet],
        )

    with pytest.raises(ValueError, match="requires a prepared barrier"):
        deliver("wire_left")
    lineage_operator_runtime.prepare_inbox(
        lineage_id, OPERATOR_ID, event=event,
        expected_wire_ids=["wire_left", "wire_right"],
    )
    with pytest.raises(ValueError, match="different operator barrier"):
        lineage_operator_runtime.prepare_inbox(
            lineage_id, OPERATOR_ID, event=event, expected_wire_ids=["wire_left"],
        )
    with pytest.raises(ValueError, match="outside the operator barrier"):
        deliver("wire_outside")
    assert set(deliver("wire_left")["inputs"]) == {"wire_left"}
    assert set(deliver("wire_right")["inputs"]) == {"wire_left", "wire_right"}
    frozen = lineage_operator_runtime.pop_inbox(lineage_id, OPERATOR_ID, event.id)
    with pytest.raises(ValueError, match="requires a prepared barrier"):
        deliver("wire_left")
    assert set(frozen["inputs"]) == {"wire_left", "wire_right"}
    assert lineage_operator_runtime.read_operator_runtime(lineage_id, OPERATOR_ID)["inbox"] == {}


def test_cancelled_operator_batch_consumes_inputs_without_reopening(lineage_id):
    """已取消的算子批次消费迟到输入

    取消批次后，迟到输入被消费且不会创建新收件箱；同一事件不能再次准备执行。
    """
    event = lineage_primitives.make_event("manual.triggered")
    lineage_operator_runtime.prepare_inbox(
        lineage_id, OPERATOR_ID, event=event, expected_wire_ids=["wire_input"],
    )
    lineage_operator_runtime.cancel_event(lineage_id, OPERATOR_ID, event.id)
    assert lineage_operator_runtime.update_inbox(
        lineage_id, OPERATOR_ID, event=event,
        source_wire_id="wire_input", source_node_id=TARGET_ID, packets=[],
    ) is None
    with pytest.raises(lineage_operator_runtime.PacketOperatorEventCancelledError):
        lineage_operator_runtime.prepare_inbox(
            lineage_id, OPERATOR_ID, event=event, expected_wire_ids=["wire_input"],
        )
    assert lineage_operator_runtime.read_operator_runtime(lineage_id, OPERATOR_ID)["inbox"] == {}
