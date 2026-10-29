from __future__ import annotations

import time
import threading

from management_portal.society_service.coordinator import LineageCoordinator
from management_portal.society_service import invariant_locks, resident_execution_work


def test_cancellation_notification_bypasses_busy_coordination_and_lifecycle_lanes_contracts(subtests, isolated_portal_case):
    """协调器的停止通知绕过占满的工作槽且只送达一次，慢清理不阻塞准入，维护失败单独报错"""
    with subtests.test(contract='test_cancellation_notification_bypasses_busy_coordination_and_lifecycle_lanes'), isolated_portal_case() as case_path:
        release = threading.Event()
        delivered = threading.Event()
        calls = []

        def cancel(execution_id, actor):
            calls.append((execution_id, actor))
            delivered.set()
            release.wait(5)

        coordinator = LineageCoordinator(
            resume_residents=lambda: {}, resume_resident=lambda _: {},
            reconcile_all=lambda: {}, reconcile_lineage=lambda _: {},
            cancel_execution=cancel,
        )
        coordinator.start()
        try:
            deadline = time.monotonic() + 2
            while coordinator._work and time.monotonic() < deadline:
                time.sleep(0.01)
            for index in range(coordinator._worker_count):
                for lane in ("resident", "lifecycle"):
                    while not coordinator._submit((lane, str(index)), lambda: release.wait(5)):
                        assert time.monotonic() < deadline
                        time.sleep(0.01)
            coordinator.notify_execution_cancellation("execution-stop", "original-actor")
            assert delivered.wait(0.5), "Stop must not wait for a normal worker slot"
            for _ in range(10):
                coordinator.notify_execution_cancellation("execution-stop", "original-actor")
            assert calls == [("execution-stop", "original-actor")]
        finally:
            release.set()
            coordinator.stop()
    with subtests.test(contract='test_saturated_lifecycle_effects_do_not_block_resident_admission'), isolated_portal_case() as case_path:
        release = threading.Event()
        admitted = threading.Event()
        all_effects_entered = threading.Event()
        started = set()
        lock = threading.Lock()

        def effect(operation_id):
            with lock:
                started.add(operation_id)
                if len(started) == 8:
                    all_effects_entered.set()
            assert release.wait(4)

        coordinator = LineageCoordinator(
            resume_residents=lambda: {},
            resume_resident=lambda _: admitted.set() or {},
            reconcile_all=lambda: {}, reconcile_lineage=lambda _: {},
            lifecycle_ready=lambda: [str(index) for index in range(8)],
            lifecycle_run=effect,
        )
        coordinator.start()
        try:
            assert all_effects_entered.wait(2)
            coordinator.notify_resident("resident_999999999999")
            assert admitted.wait(1), "Slow cleanup must not exhaust the command workers"
        finally:
            release.set()
            coordinator.stop()
    with subtests.test(contract='test_periodic_recovery_does_not_poll_all_lineages'), isolated_portal_case() as case_path:
        calls: list[str] = []
        coordinator = LineageCoordinator(
            resume_residents=lambda: calls.append("resume_all") or {},
            resume_resident=lambda _resident_id: {},
            reconcile_all=lambda: calls.append("reconcile_all") or {},
            reconcile_lineage=lambda _lineage_id: {},
            housekeeping=lambda: calls.append("housekeeping"),
            housekeeping_seconds=60,
            recovery_seconds=0.05,
        )
        coordinator.start()
        try:
            deadline = time.monotonic() + 1
            while (
                calls.count("resume_all") < 2
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            snapshot = coordinator.snapshot()
        finally:
            coordinator.stop()

        assert calls.count("resume_all") >= 2
        assert calls.count("reconcile_all") == 1
        assert calls.count("housekeeping") == 1
        assert snapshot["recovery_count"] >= 2
        assert snapshot["reconcile_count"] == 1
        assert snapshot["thread_alive"] is True
    with subtests.test(contract='test_terminal_facts_are_coalesced_and_consumed_by_the_coordinator'), isolated_portal_case() as case_path:
        consumed: list[dict] = []
        coordinator = LineageCoordinator(
            resume_residents=lambda: {},
            resume_resident=lambda _resident_id: {},
            reconcile_all=lambda: {},
            reconcile_lineage=lambda _lineage_id: {},
            consume_terminal=lambda fact: consumed.append(dict(fact)) or {},
        )
        fact = {
            "resident_id": "resident_222222222222",
            "message_id": "msg_terminal",
            "terminal_status": "completed",
            "lineage_id": "lineage_111111111111",
        }
        coordinator.notify_terminal(fact)
        coordinator.notify_terminal(fact)
        coordinator.start()
        try:
            deadline = time.monotonic() + 2
            while not consumed and time.monotonic() < deadline:
                time.sleep(0.01)
            snapshot = coordinator.snapshot()
        finally:
            coordinator.stop()

        assert consumed == [fact]
        assert snapshot["terminal_consumption_count"] == 1
        assert snapshot["pending_terminal_count"] == 0
    with subtests.test(contract='test_housekeeping_cannot_clear_a_coordinator_failure'), isolated_portal_case() as case_path:
        coordinator = LineageCoordinator(
            resume_residents=lambda: {},
            resume_resident=lambda _resident_id: {},
            reconcile_all=lambda: {},
            reconcile_lineage=lambda _lineage_id: {},
            housekeeping=lambda: None,
        )
        coordinator._record_error(RuntimeError("resident notification failed"))

        coordinator._run_housekeeping()

        snapshot = coordinator.snapshot()
        assert snapshot["last_error"] == (
            "RuntimeError: resident notification failed"
        )
        assert snapshot["last_housekeeping_error"] == ""
    with subtests.test(contract='test_housekeeping_failure_has_an_independent_visible_error'), isolated_portal_case() as case_path:
        def fail_housekeeping() -> None:
            raise RuntimeError("archive cleanup failed")

        coordinator = LineageCoordinator(
            resume_residents=lambda: {},
            resume_resident=lambda _resident_id: {},
            reconcile_all=lambda: {},
            reconcile_lineage=lambda _lineage_id: {},
            housekeeping=fail_housekeeping,
        )

        coordinator._run_housekeeping()

        snapshot = coordinator.snapshot()
        assert snapshot["last_error"] == ""
        assert snapshot["last_housekeeping_error"] == (
            "RuntimeError: archive cleanup failed"
        )


def test_completion_waiters_exceed_coordinator_capacity_without_blocking_commands(tmp_path, monkeypatch):
    """Resident 完成等待各占一个线程，数量超过协调器容量也不阻塞图协调和终态投递

    Long waits have one owner per Resident, not a slot in the shared pool."""

    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
    release = threading.Event()
    all_started = threading.Event()
    reconciled = threading.Event()
    terminal = threading.Event()
    guard = threading.Lock()
    entered = set()
    starts = {}
    errors = []
    records = {
        f"resident_{index:012x}": {
            "resident_id": f"resident_{index:012x}",
            "lineage_id": "lineage_111111111111",
        }
        for index in range(1, 33)
    }

    def wait_for_execution(resident_id, continuation_id):
        try:
            with guard:
                entered.add(resident_id)
                starts[resident_id] = starts.get(resident_id, 0) + 1
                if len(entered) == len(records):
                    all_started.set()
            if not release.wait(10):
                raise AssertionError("test did not release completion waiters")
        except BaseException as exc:
            with guard:
                errors.append(exc)
        finally:
            resident_execution_work.finish_current_thread(records[resident_id])

    def resume(resident_id):
        resident_execution_work.start(records[resident_id], "continuation-test", wait_for_execution)
        return {"residents": 1}

    coordinator = LineageCoordinator(
        resume_residents=lambda: {}, resume_resident=resume,
        reconcile_all=lambda: {}, reconcile_lineage=lambda _: reconciled.set() or {},
        consume_terminal=lambda _: terminal.set() or {},
    )
    coordinator.start()
    try:
        for resident_id in records:
            coordinator.notify_resident(resident_id)
        assert all_started.wait(3), "completion waits consumed the shared admission pool"
        coordinator.notify_lineage("lineage_111111111111")
        assert reconciled.wait(1), "long Resident execution blocked graph coordination"
        coordinator.notify_terminal({
            "resident_id": next(iter(records)),
            "message_id": "msg_11111111111111111111111111111111",
            "lineage_id": "lineage_111111111111",
            "terminal_status": "completed",
        })
        assert terminal.wait(1), "long Resident execution blocked terminal-fact delivery"
        for resident_id in records:
            resume(resident_id)
        assert set(starts.values()) == {1}, "duplicate recovery created competing completion owners"
        for resident_id in records:
            with invariant_locks.resident_lock(resident_id):
                assert resident_id in entered
        snapshot = coordinator.snapshot()
        assert len(entered) > snapshot["worker_limit"]
        assert not snapshot["last_error"]
    finally:
        threads = [resident_execution_work.thread(record) for record in records.values()]
        release.set()
        coordinator.stop()
        for thread in threads:
            if thread is not None:
                thread.join(2)
                assert not thread.is_alive()
    assert not errors


def test_coordinator_coalesces_targeted_wakes_without_full_recovery() -> None:
    """协调器把重复的 Resident 与 Lineage 唤醒各合并为一次定向处理

    :design: decision_d918ebebc062
    :design-point:
    """
    calls: list[tuple] = []

    def resume_all():
        calls.append(("resume_all",))
        return {"active": 0}

    def resume_one(resident_id: str):
        calls.append(("resume_one", resident_id))
        return {"active": 1}

    def reconcile_all():
        calls.append(("reconcile_all",))
        return {"checked": 1}

    def reconcile_one(lineage_id: str):
        calls.append(("reconcile_one", lineage_id))
        return {"lineage_id": lineage_id}

    coordinator = LineageCoordinator(
        resume_residents=resume_all,
        resume_resident=resume_one,
        reconcile_all=reconcile_all,
        reconcile_lineage=reconcile_one,
        housekeeping_seconds=60,
    )
    coordinator.notify_resident("resident_222222222222")
    coordinator.notify_resident("resident_222222222222")
    coordinator.notify_lineage("lineage_111111111111")
    coordinator.notify_lineage("lineage_111111111111")
    coordinator.start()
    try:
        deadline = time.monotonic() + 2
        while len(calls) < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        coordinator.stop()

    assert sorted(calls) == sorted([
        ("resume_all",),
        ("reconcile_all",),
        ("resume_one", "resident_222222222222"),
        ("reconcile_one", "lineage_111111111111"),
    ])


def test_failed_terminal_consumption_is_retried_without_a_busy_loop() -> None:
    """终态消费失败后按恢复间隔重试，不忙等"""
    attempts: list[float] = []

    def consume(_fact: dict) -> dict:
        attempts.append(time.monotonic())
        if len(attempts) == 1:
            raise RuntimeError("temporary terminal delivery failure")
        return {}

    coordinator = LineageCoordinator(
        resume_residents=lambda: {},
        resume_resident=lambda _resident_id: {},
        reconcile_all=lambda: {},
        reconcile_lineage=lambda _lineage_id: {},
        consume_terminal=consume,
        recovery_seconds=0.05,
    )
    coordinator.notify_terminal({
        "resident_id": "resident_222222222222",
        "message_id": "msg_retry",
        "terminal_status": "completed",
        "lineage_id": "lineage_111111111111",
    })
    coordinator.start()
    try:
        deadline = time.monotonic() + 2
        while len(attempts) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        snapshot = coordinator.snapshot()
    finally:
        coordinator.stop()

    assert len(attempts) == 2
    assert attempts[1] - attempts[0] >= 0.04
    assert snapshot["terminal_consumption_count"] == 1
    assert snapshot["pending_terminal_count"] == 0


def test_terminal_facts_dropped_by_a_stop_are_requeued_once_per_start() -> None:
    """启动一个恢复间隔后补回停止时丢失的终态，每次启动只补一次，读取失败下次再试"""
    calls: list[tuple[float, str]] = []
    consumed: list[dict] = []

    def recover(started_at: str) -> list[dict]:
        calls.append((time.monotonic(), started_at))
        if len(calls) == 1:
            raise RuntimeError("temporary read failure")
        return [{"resident_id": "resident_222222222222", "message_id": "msg_dropped", "terminal_status": "completed"}]

    coordinator = LineageCoordinator(
        resume_residents=lambda: {},
        resume_resident=lambda _resident_id: {},
        reconcile_all=lambda: {},
        reconcile_lineage=lambda _lineage_id: {},
        consume_terminal=lambda fact: consumed.append(fact) or {},
        recover_terminals=recover,
        recovery_seconds=0.05,
    )
    began = time.monotonic()
    coordinator.start()
    try:
        deadline = time.monotonic() + 2
        while not consumed and time.monotonic() < deadline:
            time.sleep(0.01)
        # Several more recovery intervals pass without another read.
        time.sleep(0.3)
        snapshot = coordinator.snapshot()
    finally:
        coordinator.stop()

    assert [started_at for _at, started_at in calls] == [snapshot["started_at"]] * 2
    assert calls[0][0] - began >= 0.04
    assert [fact["message_id"] for fact in consumed] == ["msg_dropped"]
    assert snapshot["recovered_terminal_count"] == 1


def test_reconcile_suppresses_its_own_lineage_change_notification() -> None:
    """Lineage 协调自身产生的变更通知被抑制，不引发再次协调"""
    calls: list[str] = []
    coordinator: LineageCoordinator

    def reconcile_one(lineage_id: str):
        calls.append(lineage_id)
        coordinator.notify_lineage(lineage_id)
        return {"lineage_id": lineage_id}

    coordinator = LineageCoordinator(
        resume_residents=lambda: {},
        resume_resident=lambda _resident_id: {},
        reconcile_all=lambda: {},
        reconcile_lineage=reconcile_one,
        housekeeping_seconds=60,
    )
    coordinator.notify_lineage("lineage_111111111111")
    coordinator.start()
    try:
        deadline = time.monotonic() + 2
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
    finally:
        coordinator.stop()

    snapshot = coordinator.snapshot()
    assert calls == ["lineage_111111111111"]
    assert snapshot["suppressed_self_notifications"] == 1
    assert snapshot["pending_lineage_ids"] == []


def test_failed_lineage_delivery_is_retried_after_requested_delay() -> None:
    """Lineage 协调返回 retry_after_seconds 时按请求的延迟再试一次"""
    attempts: list[float] = []

    def reconcile_one(_lineage_id: str) -> dict:
        attempts.append(time.monotonic())
        return {
            "retry_after_seconds": 0.05 if len(attempts) == 1 else 0,
        }

    coordinator = LineageCoordinator(
        resume_residents=lambda: {},
        resume_resident=lambda _resident_id: {},
        reconcile_all=lambda: {},
        reconcile_lineage=reconcile_one,
        housekeeping_seconds=60,
    )
    coordinator.notify_lineage("lineage_111111111111")
    coordinator.start()
    try:
        deadline = time.monotonic() + 2
        while len(attempts) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        snapshot = coordinator.snapshot()
    finally:
        coordinator.stop()

    assert len(attempts) == 2
    assert attempts[1] - attempts[0] >= 0.04
    assert snapshot["retry_lineage_ids"] == []


def test_explicit_lineage_notification_supersedes_delayed_retry() -> None:
    """显式 Lineage 通知立即重新协调，取代尚未到期的延迟重试"""
    attempts: list[float] = []

    def reconcile_one(_lineage_id: str) -> dict:
        attempts.append(time.monotonic())
        return {
            "retry_after_seconds": 1 if len(attempts) == 1 else 0,
        }

    coordinator = LineageCoordinator(
        resume_residents=lambda: {},
        resume_resident=lambda _resident_id: {},
        reconcile_all=lambda: {},
        reconcile_lineage=reconcile_one,
        housekeeping_seconds=60,
    )
    coordinator.notify_lineage("lineage_111111111111")
    coordinator.start()
    try:
        deadline = time.monotonic() + 2
        while len(attempts) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        coordinator.notify_lineage("lineage_111111111111")
        while len(attempts) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        snapshot = coordinator.snapshot()
    finally:
        coordinator.stop()

    assert len(attempts) == 2
    assert attempts[1] - attempts[0] < 0.5
    assert snapshot["retry_lineage_ids"] == []


def test_message_arrivals_dispatch_in_parallel_and_then_reconcile_their_lineage() -> None:
    """多条消息到达在独立线程中并发派发

    协调器为每条到达消息使用到达通道的独立线程，三条同时运行；派发后再对所属 Lineage 对账。

    A Family Router starts when its message arrives; several messages route at once.

    :design: decision_44cf587c1721
    """

    arrived = threading.Barrier(3, timeout=5)
    dispatched: list[tuple[str, str, str]] = []
    reconciled: list[str] = []

    def dispatch(lineage_id: str, resident_id: str, message_id: str) -> None:
        arrived.wait()  # passes only when all three run at the same time
        dispatched.append((lineage_id, resident_id, message_id))

    coordinator = LineageCoordinator(
        resume_residents=lambda: {}, resume_resident=lambda _: {},
        reconcile_all=lambda: {}, reconcile_lineage=lambda lineage_id: reconciled.append(lineage_id) or {},
        dispatch_arrival=dispatch,
    )
    coordinator.start()
    try:
        for index in range(3):
            coordinator.notify_message_arrival("lineage_000000000001", "resident_aaaaaaaaaaaa", f"msg_{index}")
        deadline = time.monotonic() + 5
        while (len(dispatched) < 3 or not reconciled) and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        coordinator.stop()

    assert sorted(item[2] for item in dispatched) == ["msg_0", "msg_1", "msg_2"]
    assert "lineage_000000000001" in reconciled
