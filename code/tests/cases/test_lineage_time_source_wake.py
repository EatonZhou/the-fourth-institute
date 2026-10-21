"""A Lineage with a time source is looked at again when the source is due.

Time and file sources fire only when their Lineage is reconciled.  Each
reconcile reports when the Lineage next needs to be looked at, and the Lineage
coordinator wakes for it, so a recurring timer-triggered Resident runs on schedule
instead of waiting for an unrelated change or a service restart.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from management_portal.society_service import lineage_domain, lineage_program_runtime
from management_portal.society_service.coordinator import LineageCoordinator
from tests.cases import society_service_test_support as lineage_test_support
from tests.cases.test_lineage_program_runtime_integration import (  # noqa: F401 - fixture
    ACTOR,
    WORK_A,
    _component,
    _wire,
    _worker,
    runtime_store,
)


@pytest.fixture
def registered_runtime_store(runtime_store: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from management_portal.society_service import lineage_catalog
    from management_portal.society_service.lineage_catalog import LineageCatalog
    from management_portal.society_service.state_store import get_lineage_data_store
    from tests.cases.test_lineage_catalogs import _record

    # Cached catalogs can carry instance method patches restored by other cases.
    catalog = LineageCatalog(get_lineage_data_store())
    monkeypatch.setattr(lineage_catalog, "get_catalog", lambda: catalog)
    upsert = LineageCatalog.upsert

    def register_members(catalog, definition):
        # Commit each graph together with its Resident identities. The fixture
        # Workspace may already exist without its Resident after another case.
        with catalog.state_store.database.transaction() as connection:
            result = upsert(catalog, definition)
            for node in definition.get("nodes") or []:
                resident_id = node["id"]
                if connection.execute("SELECT 1 FROM residents WHERE resident_id=%s", (resident_id,)).fetchone() is None:
                    catalog.state_store.put_resident_record(
                        resident_id, _record(resident_id, lineage_id=definition["lineage_id"]),
                    )
            return result

    monkeypatch.setattr(LineageCatalog, "upsert", register_members)
    return runtime_store


def test_reconcile_reports_when_a_recurring_source_is_next_due(
    registered_runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """协调报告周期时间事件源的下次到期时间，到期后触发一次"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    sent: list[str] = []
    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    monkeypatch.setattr(lineage_domain, "datetime", Clock)
    monkeypatch.setattr(
        lineage_test_support,
        "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: sent.append(str(node["id"])) or {"message_id": f"job_wake_{len(sent)}"},
    )
    lineage_id = lineage_domain.create_lineage(
        {
            "run_as_principal": {"kind": "user", "id": ACTOR},
            "title": "Recurring wake",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_every_two_minutes", "event_source",
                        event_type="time.reached", schedule_mode="recurring", recurrence_kind="interval",
                        interval_seconds=120, misfire_policy="skip", overlap_policy="drop",
                    ),
                ],
                "wires": [_wire("wire_wake_worker", "route", "event_source_every_two_minutes", WORK_A)],
            },
        },
        ACTOR,
    )["lineage_id"]

    first = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    assert first["event_count"] == 0
    assert first["next_wake_at"] == "2026-08-20T00:02:00Z"

    Clock.current += timedelta(seconds=120)
    fired = lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)
    assert fired["event_count"] == 1
    assert sent == [WORK_A]
    assert fired["next_wake_at"] == "2026-08-20T00:04:00Z"


def test_an_exhausted_source_needs_no_wake(
    registered_runtime_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """触发次数耗尽的时间事件源不再要求唤醒"""
    class Clock(datetime):
        current = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(lineage_program_runtime, "datetime", Clock)
    monkeypatch.setattr(lineage_domain, "datetime", Clock)
    monkeypatch.setattr(
        lineage_test_support, "NODE_MESSAGE_SENDER",
        lambda node, _prompt, _actor: {"message_id": f"job_once_{node['id']}"},
    )
    lineage_id = lineage_domain.create_lineage(
        {
            "run_as_principal": {"kind": "user", "id": ACTOR},
            "title": "Fires once",
            "nodes": [_worker(WORK_A)],
            "program": {
                "components": [
                    _component(
                        "event_source_once", "event_source",
                        event_type="time.reached", schedule_mode="recurring", recurrence_kind="interval",
                        interval_seconds=120, misfire_policy="skip", overlap_policy="drop", max_fires=1,
                    ),
                ],
                "wires": [_wire("wire_once_worker", "route", "event_source_once", WORK_A)],
            },
        },
        ACTOR,
    )["lineage_id"]

    assert lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)["next_wake_at"] == "2026-08-20T00:02:00Z"
    Clock.current += timedelta(seconds=120)
    assert lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)["event_count"] == 1
    Clock.current += timedelta(seconds=600)
    assert lineage_domain.reconcile_lineage_system_events(lineage_id, ACTOR)["next_wake_at"] == ""


def _coordinator(**overrides) -> LineageCoordinator:
    return LineageCoordinator(
        resume_residents=lambda: {},
        resume_resident=lambda _resident_id: {},
        reconcile_all=overrides.get("reconcile_all", lambda: {}),
        reconcile_lineage=overrides.get("reconcile_lineage", lambda _lineage_id: {}),
        housekeeping_seconds=60,
    )


def test_coordinator_wakes_a_lineage_when_its_source_is_due() -> None:
    """协调器按时间事件源的到期时刻安排唤醒对应 Lineage"""
    soon = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    coordinator = _coordinator(reconcile_all=lambda: {"wake_lineages": {"lineage_aaaaaaaaaaaa": soon}})
    coordinator.start()
    try:
        deadline = time.monotonic() + 2
        while "lineage_aaaaaaaaaaaa" not in coordinator.snapshot()["wake_lineage_ids"] and time.monotonic() < deadline:
            time.sleep(0.01)
        snapshot = coordinator.snapshot()
        wake = coordinator._lineage_wakes["lineage_aaaaaaaaaaaa"] - time.monotonic()
    finally:
        coordinator.stop()

    # A wake is not a failure retry; the status shows them apart.
    assert snapshot["wake_lineage_ids"] == ["lineage_aaaaaaaaaaaa"] and snapshot["retry_lineage_ids"] == []
    assert 25 < wake <= 30


def test_a_past_due_time_does_not_spin_the_coordinator() -> None:
    """协调返回已过期的唤醒时间时，协调器不反复空转重试"""
    attempts: list[float] = []
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")

    def reconcile_one(_lineage_id: str) -> dict:
        attempts.append(time.monotonic())
        return {"next_wake_at": past}

    coordinator = _coordinator(reconcile_lineage=reconcile_one)
    coordinator.notify_lineage("lineage_bbbbbbbbbbbb")
    coordinator.start()
    try:
        time.sleep(0.5)
        snapshot = coordinator.snapshot()
    finally:
        coordinator.stop()

    assert len(attempts) == 1
    assert snapshot["wake_lineage_ids"] == ["lineage_bbbbbbbbbbbb"]


def test_only_an_eligible_instance_wakes_lineages_for_due_sources(monkeypatch) -> None:
    """Dev 与 Release 共享数据时，只有被指定运行定时器的实例为到期事件源唤醒 Lineage

    Dev and Release share Lineage data; one coordinator runs the timers."""
    from management_portal.society_service.config import runs_lineage_timers

    monkeypatch.delenv("PORTAL_LINEAGE_TIMER_INSTANCE", raising=False)
    assert runs_lineage_timers("release") and runs_lineage_timers("dev")  # the lease decides
    monkeypatch.setenv("PORTAL_LINEAGE_TIMER_INSTANCE", "dev")
    assert runs_lineage_timers("dev") and not runs_lineage_timers("release")

    soon = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    coordinator = LineageCoordinator(
        resume_residents=lambda: {}, resume_resident=lambda _resident_id: {},
        reconcile_all=lambda: {"wake_lineages": {"lineage_cccccccccccc": soon}},
        reconcile_lineage=lambda _lineage_id: {}, housekeeping_seconds=60, wake_for_due_sources=False,
    )
    coordinator.start()
    try:
        time.sleep(0.3)
        snapshot = coordinator.snapshot()
    finally:
        coordinator.stop()
    assert snapshot["wake_lineage_ids"] == []


def test_only_the_timer_lease_holder_wakes_lineages_and_a_new_holder_looks_again() -> None:
    """只有定时器租约持有者唤醒 Lineage，新取得租约时重新扫描到期事件源"""
    soon = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    holds = {"value": False}
    reconciles: list[float] = []

    def reconcile_all() -> dict:
        reconciles.append(time.monotonic())
        return {"wake_lineages": {"lineage_dddddddddddd": soon}}

    coordinator = LineageCoordinator(
        resume_residents=lambda: {}, resume_resident=lambda _resident_id: {},
        reconcile_all=reconcile_all, reconcile_lineage=lambda _lineage_id: {}, housekeeping_seconds=60,
        timer_lease=lambda: holds["value"], timer_lease_seconds=0.1,
    )
    coordinator.start()
    try:
        time.sleep(0.3)
        assert coordinator.snapshot()["wake_lineage_ids"] == []  # another instance holds the lease
        holds["value"] = True
        deadline = time.monotonic() + 2
        while "lineage_dddddddddddd" not in coordinator.snapshot()["wake_lineage_ids"] and time.monotonic() < deadline:
            time.sleep(0.02)
        snapshot = coordinator.snapshot()
    finally:
        coordinator.stop()
    assert snapshot["wake_lineage_ids"] == ["lineage_dddddddddddd"]
    assert snapshot["timer_owner"] is True
    assert len(reconciles) >= 2  # the start, and again on gaining the lease


def test_an_event_wire_can_keep_background_work_in_the_session(
    registered_runtime_store: Path,
) -> None:
    """事件连线可让后台工作留在会话中并交给模型

    连线配置 context_policy=session 让时间与文件事件在 Resident 会话中处理，execution=agent 让单命令任务交给模型执行，两者均被接受并生效。

    decision_481dc4e6292c: wire config ``context_policy: "session"`` keeps
    time and file work in the Resident's session.  Every delivery is a model
    turn, so a wire has no execution mode to choose."""
    def create(config: dict) -> str:
        return lineage_domain.create_lineage(
            {
                "run_as_principal": {"kind": "user", "id": ACTOR},
                "title": "Session wake",
                "nodes": [_worker(WORK_A)],
                "program": {
                    "components": [
                        _component("event_source_check", "event_source", event_type="time.reached",
                                   schedule_mode="recurring", recurrence_kind="interval", interval_seconds=1500),
                    ],
                    "wires": [_wire("wire_check", "route", "event_source_check", WORK_A, config=config)],
                },
            },
            ACTOR,
        )["lineage_id"]

    lineage_id = create({"context_policy": "session", "unavailable_policy": "drop"})
    lineage = lineage_domain.get_lineage(lineage_id, ACTOR)
    wire = next(w for w in lineage["program"]["wires"] if w["id"] == "wire_check")
    assert wire["config"]["context_policy"] == "session"
    assert "execution" not in wire["config"]
    for bad in ({"context_policy": "shared"}, {"execution": "agent"}, {"execution": "command"}):
        with pytest.raises(ValueError):
            create(bad)
