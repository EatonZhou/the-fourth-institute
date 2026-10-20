from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest


from management_portal.society_service import lineage_program_runtime, state_store


LINEAGE_ID = "lineage_aaaaaaaaaaaa"


@pytest.fixture()
def lineage_id() -> str:
    state_store.get_lineage_data_store().put_lineage_definition(
        LINEAGE_ID,
        {
            "schema": "tfi.lineage.v1",
            "schema_version": 1,
            "lineage_id": LINEAGE_ID,
            "title": "Program runtime unit test",
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


def _event(index: int) -> dict:
    from management_portal.society_service import trigger_paths
    event = {"id": f"event_{index}", "type": "manual.triggered",
             "occurred_at": "2026-10-14T00:00:00Z", "payload": {"index": index}}
    event["payload"]["trigger_path_id"] = trigger_paths.begin_event(
        LINEAGE_ID, "event_source_pending", event, cause_kind="input", cause_id=f"message_{index}")
    return event


def _queue_invocation(lineage_id: str, index: int, **options) -> dict:
    lineage_program_runtime.defer_event_for_wire(
        lineage_id, "event_source_pending", "wire_pending", f"cursor_{index}",
        _event(index), policy="queue", **options,
    )
    return lineage_program_runtime.pending_wire_deliveries(lineage_id, "wire_pending")[-1]


def test_runtime_transaction_notifies_only_after_a_committed_change(
    lineage_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行事务仅在提交实际变更后通知"""
    notifications: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "management_portal.society_service.changes.lineage_changed",
        lambda changed_id, *, runtime=False: notifications.append(
            (changed_id, runtime)
        ),
    )

    with lineage_program_runtime._runtime_transaction(lineage_id):
        pass
    assert notifications == []

    assert lineage_program_runtime.claim_event(
        lineage_id,
        "event_source_notification",
        "cursor_notification",
    )
    assert notifications == [(lineage_id, True)]


def test_pending_delivery_edit_delete_and_reload_preserve_claims(lineage_id: str) -> None:
    """待投递事件编辑删除与重读保留提示覆盖和去重记录"""
    first = _queue_invocation(lineage_id, 1)
    second = _queue_invocation(lineage_id, 2)
    third = _queue_invocation(lineage_id, 3)
    lineage_program_runtime.update_pending_wire_delivery(
        lineage_id, "wire_pending", first["delivery_id"],
        prompt="只处理本次任务\n保留换行",
    )
    lineage_program_runtime.update_pending_wire_delivery(
        lineage_id, "wire_pending", second["delivery_id"],
        delete=True,
    )
    queued = lineage_program_runtime.pending_wire_deliveries(lineage_id, "wire_pending")
    assert [item["delivery_id"] for item in queued] == [first["delivery_id"], third["delivery_id"]]
    assert queued[0]["revision"] == 1
    assert queued[0]["prompt_override"] == "只处理本次任务\n保留换行"
    assert queued[1]["prompt_override"] is None
    claimed = lineage_program_runtime.claim_pending_event(lineage_id, "wire_pending")
    assert claimed["prompt_override"] == queued[0]["prompt_override"]
    assert lineage_program_runtime.defer_event_for_wire(
        lineage_id, "event_source_pending", "wire_pending", "cursor_2", _event(2),
        policy="queue",
    )["status"] == "duplicate"


@pytest.mark.parametrize("delete", [False, True])
def test_pending_delivery_rejects_inflight_edits(lineage_id: str, delete: bool) -> None:
    """投递中的待投递事件拒绝修改与删除"""
    item = _queue_invocation(lineage_id, 1)
    lineage_program_runtime.update_pending_wire_delivery(
        lineage_id, "wire_pending", item["delivery_id"], prompt="new",
    )
    claimed = lineage_program_runtime.claim_pending_event(lineage_id, "wire_pending")
    with pytest.raises(ValueError, match="正在投递"):
        lineage_program_runtime.update_pending_wire_delivery(
            lineage_id, "wire_pending", item["delivery_id"],
            prompt="late", delete=delete,
        )
    assert lineage_program_runtime.finish_pending_event(
        lineage_id, "wire_pending", claimed["dispatch_token"], delivered=False,
    )
    lineage_program_runtime.update_pending_wire_delivery(
        lineage_id, "wire_pending", item["delivery_id"], prompt="",
    )
    assert lineage_program_runtime.pending_wire_deliveries(
        lineage_id, "wire_pending",
    )[0]["prompt_override"] is None


def test_pending_delivery_removed_latest_cannot_modify_replacement(lineage_id: str) -> None:
    """已被替换事件的旧标识无法修改新事件"""
    item = _queue_invocation(lineage_id, 1)
    lineage_program_runtime.defer_event_for_wire(
        lineage_id, "event_source_pending", "wire_pending", "new", _event(2), policy="latest",
    )
    with pytest.raises(KeyError, match="已投递、过期或被替换"):
        lineage_program_runtime.update_pending_wire_delivery(
            lineage_id, "wire_pending", item["delivery_id"], delete=True,
        )
    assert len(lineage_program_runtime.pending_wire_deliveries(lineage_id, "wire_pending")) == 1


def test_pending_delivery_expires_before_edit(lineage_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """待投递事件到期后拒绝编辑"""
    item = _queue_invocation(lineage_id, 1, expires_after_seconds=1)
    future = datetime.now(timezone.utc) + timedelta(seconds=5)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return future

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    assert lineage_program_runtime.pending_wire_deliveries(lineage_id, "wire_pending") == []
    with pytest.raises(KeyError):
        lineage_program_runtime.update_pending_wire_delivery(
            lineage_id, "wire_pending", item["delivery_id"], prompt="late",
        )


def test_pending_delivery_claim_and_delete_are_atomic(lineage_id: str) -> None:
    """待投递事件的认领与删除互斥且原子完成"""
    item = _queue_invocation(lineage_id, 1)

    def delete():
        try:
            return lineage_program_runtime.update_pending_wire_delivery(
                lineage_id, "wire_pending", item["delivery_id"], delete=True,
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion = pool.submit(delete)
        claiming = pool.submit(lineage_program_runtime.claim_pending_event, lineage_id, "wire_pending")
    assert bool(deletion.result()) != bool(claiming.result())


def test_event_source_budget_counts_one_occurrence_before_fanout(
    lineage_id: str,
) -> None:
    """事件源在分发前按发生次数计费且重复游标不计数"""
    source_id = "event_source_limited"
    first = lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        "manual:one",
        max_fires=2,
    )
    assert first["status"] == "fired"
    assert first["occurrence_count"] == 1
    assert first["occurrence_count"] == 1
    assert first["remaining_fires"] == 1

    duplicate = lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        "manual:one",
        max_fires=2,
    )
    assert duplicate["status"] == "duplicate"
    assert duplicate["occurrence_count"] == 1
    assert duplicate["occurrence_count"] == 1

    second = lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        "manual:two",
        max_fires=2,
    )
    assert second["status"] == "fired"
    assert second["occurrence_count"] == 2
    assert second["occurrence_count"] == 2
    assert second["exhausted"] is True
    exhausted = lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        "manual:three",
        max_fires=2,
    )
    assert exhausted["status"] == "exhausted"
    assert exhausted["accepted"] is False
    assert exhausted["occurrence_count"] == 2
    assert exhausted["occurrence_count"] == 2


def test_unbounded_event_source_counts_occurrences_and_zero_blocks_all(
    lineage_id: str,
) -> None:
    """无上限事件源累计次数且零上限拒绝全部事件"""
    source_id = "event_source_nullable_limit"
    first = lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        "manual:unbounded",
        max_fires=None,
    )
    assert first["status"] == "fired"
    assert first["max_fires"] is None
    assert first["accepted"] is True
    assert first["occurrence_count"] == 1
    assert first["remaining_fires"] is None

    duplicate = lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        "manual:unbounded",
        max_fires=None,
    )
    assert duplicate["status"] == "duplicate"
    assert duplicate["occurrence_count"] == 1

    second = lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        "manual:another",
        max_fires=None,
    )
    assert second["status"] == "fired"
    assert second["occurrence_count"] == 2
    unbounded_runtime = lineage_program_runtime.event_source_runtime_view(
        lineage_id,
        {source_id: {"max_fires": None}},
    )[source_id]
    assert unbounded_runtime["occurrence_count"] == 2
    assert unbounded_runtime["max_fires"] is None
    assert unbounded_runtime["remaining_fires"] is None
    assert unbounded_runtime["exhausted"] is False

    zero_source_id = "event_source_zero_limit"
    blocked = lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        zero_source_id,
        "manual:zero",
        max_fires=0,
    )
    assert blocked == {
        "status": "exhausted",
        "accepted": False,
        "occurrence_count": 0,
        "max_fires": 0,
        "remaining_fires": 0,
        "generation": 0,
        "exhausted": True,
    }
    runtime = lineage_program_runtime.event_source_runtime_view(
        lineage_id,
        {zero_source_id: {"max_fires": 0}},
    )[zero_source_id]
    assert runtime["occurrence_count"] == 0
    assert runtime["max_fires"] == 0
    assert runtime["remaining_fires"] == 0
    assert runtime["exhausted"] is True


def test_event_source_reset_rearms_count_without_replaying_old_cursor(
    lineage_id: str,
) -> None:
    """重置事件源恢复额度并继续拒绝旧游标重放"""
    source_id = "event_source_limited"
    lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        "manual:one",
        max_fires=1,
    )
    before = lineage_program_runtime.event_source_runtime_view(
        lineage_id,
        {source_id: {"max_fires": 1}},
    )[source_id]
    assert before["state"] == "exhausted"
    assert before["generation"] == 0

    reset = lineage_program_runtime.reset_event_source_fire_budget(
        lineage_id,
        source_id,
        expected_generation=0,
    )
    assert reset == {
        "status": "reset",
        "occurrence_count": 0,
        "generation": 1,
    }
    after = lineage_program_runtime.event_source_runtime_view(
        lineage_id,
        {source_id: {"max_fires": 1}},
    )[source_id]
    assert after["state"] == "listening"
    assert after["occurrence_count"] == 0
    assert after["occurrence_count"] == 0
    assert after["generation"] == 1

    duplicate = lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        "manual:one",
        max_fires=1,
    )
    assert duplicate["status"] == "duplicate"
    assert duplicate["occurrence_count"] == 0
    assert duplicate["occurrence_count"] == 0
    fresh = lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        "manual:two",
        max_fires=1,
    )
    assert fresh["status"] == "fired"
    assert fresh["occurrence_count"] == 1
    assert fresh["occurrence_count"] == 1


def test_resident_completion_observer_baselines_history_then_advances_once(
    lineage_id: str,
) -> None:
    """Resident 完成观察先建立历史基线再单次推进"""
    source_id = "event_source_work_finished"
    locator = "resident:resident_aaaaaaaaaaaa"

    historical = lineage_program_runtime.observe_resident_completion(
        lineage_id,
        source_id,
        locator=locator,
        message_id="message_before_watch",
        completed_at="2026-07-31T08:00:00Z",
    )
    assert historical["status"] == "baseline"
    assert historical["message_id"] == "message_before_watch"
    assert lineage_program_runtime.observe_resident_completion(
        lineage_id,
        source_id,
        locator=locator,
        message_id="message_before_watch",
        completed_at="2026-07-31T08:00:00Z",
    )["status"] == "unchanged"

    fresh = lineage_program_runtime.observe_resident_completion(
        lineage_id,
        source_id,
        locator=locator,
        message_id="message_after_watch",
        completed_at="2026-07-31T08:05:00Z",
    )
    assert fresh["status"] == "changed"
    assert fresh["previous_message_id"] == "message_before_watch"
    assert lineage_program_runtime.commit_resident_completion(
        lineage_id,
        source_id,
        locator=locator,
        previous_message_id="message_before_watch",
        message_id="message_after_watch",
        completed_at="2026-07-31T08:05:00Z",
    )
    assert lineage_program_runtime.observe_resident_completion(
        lineage_id,
        source_id,
        locator=locator,
        message_id="message_after_watch",
        completed_at="2026-07-31T08:05:00Z",
    )["status"] == "unchanged"


def test_completion_subscription_baseline_is_dedupe_not_a_fire(
    lineage_id: str,
) -> None:
    """完成订阅基线登记去重游标且不消耗触发次数"""
    source_id = "event_source_persisted_baseline"
    subject_id = "resident_aaaaaaaaaaaa"
    cursor = f"message:{subject_id}:message_before_subscription"
    subscribers = ["wire_first", "wire_second"]

    baseline = (
        lineage_program_runtime
        .establish_resident_completion_subscription_baseline(
            lineage_id,
            source_id,
            locator=f"resident:{subject_id}",
            subject_resident_id=subject_id,
            message_id="message_before_subscription",
            completed_at="2026-07-31T08:00:00Z",
            subscriber_ids=subscribers,
        )
    )

    assert baseline["occurrence_added"] is True
    assert baseline["claimed_subscriber_ids"] == subscribers
    assert baseline["occurrence_count"] == 0
    runtime = lineage_program_runtime.read_runtime(lineage_id)
    source = runtime["event_sources"][source_id]
    assert source["fired_cursors"] == [cursor]
    assert source["fire_count"] == 0

    # A later observation of the baseline Message is not a new occurrence.
    duplicate = lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        cursor,
        max_fires=1,
    )
    assert duplicate["status"] == "duplicate"
    assert duplicate["occurrence_count"] == 0

    fresh_cursor = f"message:{subject_id}:message_after_subscription"
    fresh = lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        fresh_cursor,
        max_fires=1,
    )
    assert fresh["status"] == "fired"
    assert fresh["occurrence_count"] == 1


def test_resident_completion_observer_preserves_an_empty_initial_baseline(
    lineage_id: str,
) -> None:
    """Resident 完成观察保留空基线并识别首次成功"""
    source_id = "event_source_first_work_finished"
    locator = "resident:resident_bbbbbbbbbbbb"
    baseline = lineage_program_runtime.observe_resident_completion(
        lineage_id,
        source_id,
        locator=locator,
    )
    assert baseline["status"] == "baseline"
    assert baseline["message_id"] == ""

    # Reading the runtime record must not erase the explicit no-completion
    # baseline. The Resident's first future success is therefore observable.
    lineage_program_runtime.read_runtime(lineage_id)
    first_success = lineage_program_runtime.observe_resident_completion(
        lineage_id,
        source_id,
        locator=locator,
        message_id="message_first_success",
        completed_at="2026-07-31T09:00:00Z",
    )
    assert first_success["status"] == "changed"
    assert first_success["previous_message_id"] == ""


def test_resident_completion_observer_rebinding_establishes_a_new_baseline(
    lineage_id: str,
) -> None:
    """Resident 完成观察更换目标后重新建立历史基线"""
    source_id = "event_source_rebound_work_finished"
    lineage_program_runtime.observe_resident_completion(
        lineage_id,
        source_id,
        locator="resident:resident_aaaaaaaaaaaa",
        message_id="message_a",
    )

    rebound = lineage_program_runtime.observe_resident_completion(
        lineage_id,
        source_id,
        locator="resident:resident_bbbbbbbbbbbb",
        message_id="message_b_historical",
    )
    assert rebound["status"] == "baseline"
    assert rebound["message_id"] == "message_b_historical"


def test_resident_completion_cursor_commit_is_atomic_under_concurrency(
    lineage_id: str,
) -> None:
    """并发提交同一 Resident 完成游标仅成功一次"""
    source_id = "event_source_concurrent_work_finished"
    locator = "resident:resident_cccccccccccc"
    lineage_program_runtime.observe_resident_completion(
        lineage_id,
        source_id,
        locator=locator,
    )

    def commit(_index: int) -> bool:
        return lineage_program_runtime.commit_resident_completion(
            lineage_id,
            source_id,
            locator=locator,
            previous_message_id="",
            message_id="message_one_success",
            completed_at="2026-07-31T10:00:00Z",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        committed = list(executor.map(commit, range(16)))

    assert committed.count(True) == 1
    assert committed.count(False) == 15
    assert lineage_program_runtime.observe_resident_completion(
        lineage_id,
        source_id,
        locator=locator,
        message_id="message_one_success",
        completed_at="2026-07-31T10:00:00Z",
    )["status"] == "unchanged"


def test_event_source_budget_is_atomic_across_concurrent_occurrences(
    lineage_id: str,
) -> None:
    """并发事件原子占用触发额度且不超过上限"""
    source_id = "event_source_concurrent_limit"

    def claim(index: int) -> dict:
        return lineage_program_runtime.claim_event_occurrence(
            lineage_id,
            source_id,
            f"manual:{index}",
            max_fires=2,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(claim, range(24)))

    assert sum(item["status"] == "fired" for item in results) == 2
    assert sum(item["status"] == "exhausted" for item in results) == 22
    view = lineage_program_runtime.event_source_runtime_view(
        lineage_id,
        {source_id: {"max_fires": 2}},
    )[source_id]
    assert view["occurrence_count"] == 2
    assert view["occurrence_count"] == 2
    assert view["remaining_fires"] == 0
    assert view["exhausted"] is True


def test_unbounded_event_source_count_is_atomic_across_occurrences(
    lineage_id: str,
) -> None:
    """无上限事件源并发发生次数原子累加"""
    source_id = "event_source_concurrent_unbounded"

    def claim(index: int) -> dict:
        return lineage_program_runtime.claim_event_occurrence(
            lineage_id,
            source_id,
            f"system:{index}",
            max_fires=None,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(claim, range(24)))

    assert all(item["status"] == "fired" for item in results)
    assert sorted(item["occurrence_count"] for item in results) == list(
        range(1, 25)
    )
    view = lineage_program_runtime.event_source_runtime_view(
        lineage_id,
        {source_id: {"max_fires": None}},
    )[source_id]
    assert view["occurrence_count"] == 24
    assert view["remaining_fires"] is None
    assert view["exhausted"] is False


def test_event_source_runtime_projects_and_resets_latest_delivery_summary(
    lineage_id: str,
) -> None:
    """事件源视图展示最近投递汇总并在重置后清空"""
    source_id = "event_source_delivery_feedback"
    lineage_program_runtime.claim_event_occurrence(
        lineage_id,
        source_id,
        "manual:delivery-feedback",
        max_fires=2,
    )
    recorded = lineage_program_runtime.record_event_delivery_summary(
        lineage_id,
        source_id,
        [
            {"status": "sent"},
            {"status": "queued"},
            {"status": "dropped"},
            {"status": "queue_full"},
            {"status": "failed"},
        ],
        event_id="event_delivery_summary",
        trigger_path_id="trigger_path_delivery_summary",
    )

    assert recorded == {
        "at": recorded["at"],
        "total_count": 5,
        "delivered_count": 1,
        "queued_count": 1,
        "dropped_count": 2,
        "failed_count": 1,
        "statuses": {
            "sent": 1,
            "queued": 1,
            "dropped": 1,
            "queue_full": 1,
            "failed": 1,
        },
        "event_id": "event_delivery_summary",
        "trigger_path_id": "trigger_path_delivery_summary",
    }
    projected = lineage_program_runtime.event_source_runtime_view(
        lineage_id,
        {source_id: {"max_fires": 2}},
    )[source_id]
    assert projected["last_delivery"] == recorded

    reset = lineage_program_runtime.reset_event_source_fire_budget(
        lineage_id,
        source_id,
        expected_generation=projected["generation"],
    )
    assert reset["status"] == "reset"
    after_reset = lineage_program_runtime.event_source_runtime_view(
        lineage_id,
        {source_id: {"max_fires": 2}},
    )[source_id]
    assert after_reset["last_delivery"] == {}


def test_drop_consumes_occurrence_without_creating_backlog(
    lineage_id: str,
) -> None:
    """丢弃策略消费事件并去重且不创建待投递队列"""
    result = lineage_program_runtime.defer_event_for_wire(
        lineage_id,
        "event_source_manual",
        "wire_manual_worker",
        "manual:1",
        _event(1),
        policy="drop",
    )

    assert result == {
        "status": "dropped",
        "wire_id": "wire_manual_worker",
        "pending_count": 0,
    }
    assert lineage_program_runtime.event_wire_runtime_view(
        lineage_id,
        {"wire_manual_worker": {"unavailable_policy": "drop"}},
    )["wire_manual_worker"]["pending_count"] == 0
    assert lineage_program_runtime.defer_event_for_wire(
        lineage_id,
        "event_source_manual",
        "wire_manual_worker",
        "manual:1",
        _event(1),
        policy="drop",
    )["status"] == "duplicate"


def test_latest_replaces_waiting_occurrence_and_delivers_newest(
    lineage_id: str,
) -> None:
    """最新策略替换等待事件并只投递最新一条"""
    first = lineage_program_runtime.defer_event_for_wire(
        lineage_id,
        "event_source_manual",
        "wire_manual_worker",
        "manual:1",
        _event(1),
        policy="latest",
    )
    second = lineage_program_runtime.defer_event_for_wire(
        lineage_id,
        "event_source_manual",
        "wire_manual_worker",
        "manual:2",
        _event(2),
        policy="latest",
    )

    assert first["status"] == "queued"
    assert second["status"] == "replaced"
    assert second["replaced_count"] == 1
    pending = lineage_program_runtime.claim_pending_event(
        lineage_id,
        "wire_manual_worker",
    )
    assert pending is not None
    assert pending["event"]["payload"]["index"] == 2
    assert lineage_program_runtime.finish_pending_event(
        lineage_id,
        "wire_manual_worker",
        pending["dispatch_token"],
        delivered=True,
    )
    assert lineage_program_runtime.event_wire_runtime_view(
        lineage_id,
        {"wire_manual_worker": {"unavailable_policy": "latest"}},
    )["wire_manual_worker"]["pending_count"] == 0


def test_queue_is_fifo_and_drops_new_arrivals_when_limit_is_full(
    lineage_id: str,
) -> None:
    """队列按先进先出投递且满额时丢弃新事件"""
    statuses = []
    for index in (1, 2, 3):
        statuses.append(
            lineage_program_runtime.defer_event_for_wire(
                lineage_id,
                "event_source_manual",
                "wire_manual_worker",
                f"manual:{index}",
                _event(index),
                policy="queue",
                queue_limit=2,
            )
        )

    assert [item["status"] for item in statuses] == [
        "queued",
        "queued",
        "queue_full",
    ]
    assert statuses[-1]["pending_count"] == 2
    delivered_indexes = []
    for _index in range(2):
        pending = lineage_program_runtime.claim_pending_event(
            lineage_id,
            "wire_manual_worker",
        )
        assert pending is not None
        delivered_indexes.append(pending["event"]["payload"]["index"])
        assert lineage_program_runtime.finish_pending_event(
            lineage_id,
            "wire_manual_worker",
            pending["dispatch_token"],
            delivered=True,
        )
    assert delivered_indexes == [1, 2]


def test_pending_event_dispatch_claim_is_recovered_after_lease(
    lineage_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """待投递事件认领租约到期后可重新认领"""
    class Clock(datetime):
        current = datetime(
            2026,
            8,
            28,
            8,
            0,
            tzinfo=timezone.utc,
        )

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            current = cls.current
            return current if tz is None else current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    lineage_program_runtime.defer_event_for_wire(
        lineage_id,
        "event_source_manual",
        "wire_manual_worker",
        "manual:recover",
        _event(1),
        policy="queue",
        queue_limit=1,
    )
    first = lineage_program_runtime.claim_pending_event(
        lineage_id,
        "wire_manual_worker",
    )
    assert first is not None
    assert lineage_program_runtime.claim_pending_event(
        lineage_id,
        "wire_manual_worker",
    ) is None

    Clock.current += timedelta(
        seconds=(
            lineage_program_runtime.PENDING_EVENT_DISPATCH_LEASE_SECONDS
            + 1
        )
    )
    recovered = lineage_program_runtime.claim_pending_event(
        lineage_id,
        "wire_manual_worker",
    )

    assert recovered is not None
    assert recovered["dispatch_token"] != first["dispatch_token"]
    assert lineage_program_runtime.finish_pending_event(
        lineage_id,
        "wire_manual_worker",
        recovered["dispatch_token"],
        delivered=True,
    )


def test_waiting_event_expires_at_configured_ttl(
    lineage_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """等待事件达到配置的存活时限后不再投递"""
    class Clock(datetime):
        current = datetime(
            2026,
            7,
            31,
            8,
            0,
            tzinfo=timezone.utc,
        )

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            current = cls.current
            return current if tz is None else current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    queued = lineage_program_runtime.defer_event_for_wire(
        lineage_id,
        "event_source_manual",
        "wire_manual_worker",
        "manual:ttl",
        _event(1),
        policy="latest",
        expires_after_seconds=30,
    )
    assert queued["expires_at"] == "2026-07-31T08:00:30Z"

    Clock.current += timedelta(seconds=31)
    view = lineage_program_runtime.event_wire_runtime_view(
        lineage_id,
        {
            "wire_manual_worker": {
                "unavailable_policy": "latest",
                "expires_after_seconds": 30,
            },
        },
    )
    assert view["wire_manual_worker"]["pending_count"] == 0
    assert lineage_program_runtime.claim_pending_event(
        lineage_id,
        "wire_manual_worker",
    ) is None


def test_effect_view_reads_terminal_facts_without_a_current_state_copy(lineage_id: str) -> None:
    """Effect 视图直接读取最新终态记录并保留暂停控制"""
    from management_portal.society_service import trigger_paths
    store = state_store.get_lineage_data_store()
    effect_id = "effect_fork_resident"
    for key, status, at in (("first", "succeeded", "2026-10-11T01:00:00Z"),
                            ("second", "failed", "2026-10-11T02:00:00Z")):
        trigger_paths.effect_started(lineage_id, key, {"effect_id": effect_id, "started_at": at})
        trigger_paths.effect_settled(lineage_id, key, {
            "effect_id": effect_id, "status": status, "finished_at": at,
            "outputs": {"diagnostic": key}, "calls": {},
        })
    view = lineage_program_runtime.effect_runtime_view(lineage_id, {effect_id, "unrelated"})
    # Runtime views include controls even before an Effect has executed.
    assert set(view) == {effect_id, "unrelated"}
    assert view["unrelated"] == {"paused": False, "state": "active", "paused_at": ""}
    assert view[effect_id]["status"] == "failed"
    assert view[effect_id]["outputs"] == {"diagnostic": "second"}
    assert json.loads(json.dumps(view)) == view
    assert view[effect_id]["at"] == "2026-10-11T02:00:00Z"
    assert view[effect_id]["step_id"]
    assert view[effect_id]["trigger_path_id"]
    assert "effects" not in lineage_program_runtime.read_runtime(lineage_id)
    with store.database.read_current() as connection:
        assert connection.execute("SELECT count(*) FROM trigger_path_steps s JOIN trigger_paths p USING(trigger_path_id) WHERE p.lineage_id=%s AND s.kind='effect'", (lineage_id,)).fetchone()[0] == 2
    trigger_paths.effect_started(lineage_id, "cancelled", {"effect_id": effect_id, "started_at": "2026-10-11T03:00:00Z"})
    trigger_paths.effect_settled(lineage_id, "cancelled", {
        "effect_id": effect_id, "status": "cancelled", "finished_at": "2026-10-11T03:00:00Z",
        "outputs": {}, "calls": {},
    })
    assert lineage_program_runtime.effect_runtime_view(lineage_id, {effect_id})[effect_id]["status"] == "cancelled"


def test_runtime_ids_reject_only_empty_long_or_control_characters():
    """运行时 ID 只拒绝空值、超长和控制字符

    存储的 ID（包括事件游标）每次读取都要检查：空值、超过 240 个字符或含 U+0000–U+001F 的被拒绝，
    其余字符（包括 DEL、中文和标点）照常接受，前后空白被去掉。
    """
    for accepted in ("event_1", " cursor:wire_1:0 ", "msg_\x7f", "事件-1", "a" * 240):
        assert lineage_program_runtime._item_id(accepted, field="id") == accepted.strip()
    for rejected in ("", "   ", None, "a" * 241, "event\x00", "event\x1fx", "line\nbreak", "tab\there"):
        with pytest.raises(ValueError):
            lineage_program_runtime._item_id(rejected, field="id")
