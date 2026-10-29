"""One Event attempt has the same settlement in dispatch, backlog and recovery.

The failed route injects an outage before downstream ISA admission. Queue
claims, durable settlement, retry selection and the manual Event path are real.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from management_portal.society_service import (
    lineage_domain, lineage_event_claims, lineage_event_delivery, lineage_program_backlog,
    lineage_program_event_dispatch, lineage_program_runtime as runtime,
)
from tests.cases import society_service_test_support as support
from tests.cases.test_lineage_program_runtime_integration import (
    ACTOR, WORK_A, WORK_B, _component, _wire, _worker, runtime_store,  # noqa: F401
)

SOURCE = "event_source_settlement"
WIRE_A = "wire_settlement_a"
WIRE_B = "wire_settlement_b"
FILTER = "filter_settlement"
ERROR = "downstream admission temporarily unavailable"


def _clock():
    class Clock(datetime):
        moment = datetime(2026, 10, 19, 3, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.moment.astimezone(tz) if tz else cls.moment.replace(tzinfo=None)

    return Clock


def _due(pending):
    return datetime.fromisoformat(pending["retry_at"].replace("Z", "+00:00"))


@pytest.fixture()
def delivery(runtime_store, monkeypatch):
    Clock = _clock()
    busy = {"value": False}
    calls = []

    def send(node, _prompt, _actor):
        calls.append(node["id"])
        return {"message_id": f"msg_settlement_{len(calls)}"}

    dispatch = lineage_program_event_dispatch._dispatch_program_foundational_event_route

    def unavailable_route(*args, **kwargs):
        route = args[4]
        if route["wire_id"] == WIRE_A:
            calls.append(WORK_A)
            raise RuntimeError(ERROR)
        return dispatch(*args, **kwargs)

    monkeypatch.setattr(support, "NODE_ACTIVE_JOB_RESOLVER", lambda *_: busy["value"])
    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", send)
    monkeypatch.setattr(lineage_program_event_dispatch, "_dispatch_program_foundational_event_route", unavailable_route)
    lineage_id = support.create_lineage_with_resident_members({
        "title": "Event settlement", "project_id": "project_test",
        "nodes": [_worker(WORK_A), _worker(WORK_B)],
        "program": {
            "components": [_component(SOURCE, "event_source", event_type="manual.triggered")],
            "wires": [
                _wire(WIRE_A, "route", SOURCE, WORK_A, config={"unavailable_policy": "queue"}),
                _wire(WIRE_B, "route", SOURCE, WORK_B, config={"unavailable_policy": "queue"}),
            ],
        },
    }, ACTOR)["lineage_id"]
    monkeypatch.setattr(runtime, "datetime", Clock)
    return lineage_id, busy, calls, Clock


def _emit(lineage_id):
    return lineage_domain.emit_program_manual_event(lineage_id, {"event_source_id": SOURCE}, ACTOR)


def _drain(lineage_id):
    return lineage_program_backlog.drain_event_backlog(
        lineage_id, ACTOR, authority=lineage_domain._program_control_authority(),
    )


def _pending(lineage_id, wire_id=WIRE_A):
    return (runtime.read_runtime(lineage_id)["event_wires"].get(wire_id) or {}).get("pending_events", [])


@pytest.mark.parametrize("entry", ["immediate", "backlog"])
def test_first_failure_waits_before_retry_without_blocking_another_wire(delivery, entry):
    """首次失败即持久保存 retry_at，同轮/到期前不重试，另一连线正常受理。"""
    lineage_id, busy, calls, clock = delivery
    busy["value"] = entry == "backlog"
    _emit(lineage_id)
    if entry == "backlog":
        assert calls == []
        busy["value"] = False
        _drain(lineage_id)

    assert calls == [WORK_A, WORK_B]
    [pending] = _pending(lineage_id)
    assert pending["attempt_count"] == 1
    assert pending["last_error"] == ERROR
    assert not pending.get("dispatch_token")
    due = datetime.fromisoformat(pending["retry_at"].replace("Z", "+00:00"))
    assert due > clock.moment
    assert _pending(lineage_id, WIRE_B) == []
    assert _drain(lineage_id)["attempted_count"] == 0
    assert calls == [WORK_A, WORK_B]

    clock.moment = due + timedelta(seconds=1)
    assert _drain(lineage_id)["attempted_count"] == 1
    assert calls == [WORK_A, WORK_B, WORK_A]
    [retried] = _pending(lineage_id)
    assert retried["attempt_count"] == 2
    assert retried["last_error"] == ERROR
    assert retried["retry_at"] > pending["retry_at"]


@pytest.mark.parametrize("consumed", [False, True])
def test_barrier_failure_releases_claim_with_retry_or_explicit_cancellation(delivery, consumed):
    """准备屏障失败释放认领并等待；明确取消已消费则清掉原项。"""
    lineage_id, busy, calls, clock = delivery
    busy["value"] = True
    _emit(lineage_id)
    claimed = runtime.claim_pending_event(lineage_id, WIRE_A)
    assert claimed
    lineage_event_claims.release_barrier_failure_claims(
        lineage_id,
        lineage_event_claims.GenericDeliveryClaims([], {WIRE_A: claimed["dispatch_token"]}),
        ERROR, consumed=consumed,
    )
    assert calls == []
    if consumed:
        assert _pending(lineage_id) == []
    else:
        [pending] = _pending(lineage_id)
        assert pending["attempt_count"] == 1 and pending["last_error"] == ERROR
        assert not pending.get("dispatch_token")
        assert datetime.fromisoformat(pending["retry_at"].replace("Z", "+00:00")) > clock.moment
        assert runtime.claim_pending_event(lineage_id, WIRE_A) is None


def test_expired_claim_recovery_uses_same_failure_delay_and_retains_identity(delivery):
    """进程退出留下的过期认领可重新投递；失败结算保留原 Event 身份并等待。"""
    lineage_id, busy, calls, clock = delivery
    busy["value"] = True
    _emit(lineage_id)
    claim = runtime.claim_pending_event(lineage_id, WIRE_A)
    assert claim
    busy["value"] = False
    # A still-live lease cannot be stolen by recovery; the unrelated wire runs.
    _drain(lineage_id)
    assert calls == [WORK_B]
    clock.moment += timedelta(seconds=runtime.PENDING_EVENT_DISPATCH_LEASE_SECONDS + 1)
    assert _drain(lineage_id)["attempted_count"] == 1
    assert calls == [WORK_B, WORK_A]
    [pending] = _pending(lineage_id)
    assert pending["event"]["id"] == claim["event"]["id"]
    assert pending["claim_token"] == claim["claim_token"]
    assert pending["attempt_count"] == 2 and pending["last_error"] == ERROR
    assert not pending.get("dispatch_token")
    assert datetime.fromisoformat(pending["retry_at"].replace("Z", "+00:00")) > clock.moment
    assert _drain(lineage_id)["attempted_count"] == 0


def test_retry_into_an_operator_that_cancelled_its_barrier_consumes_the_event(runtime_store, monkeypatch):
    """Filter 下游投递失败后屏障已取消：积压重试读到已取消屏障即消费该 Event，不再无间隔反复认领。"""
    sent = []

    def refuse(node, _prompt, _actor):
        sent.append(node["id"])
        raise ValueError("resident workspace_path missing")

    monkeypatch.setattr(support, "NODE_MESSAGE_SENDER", refuse)
    lineage_id = support.create_lineage_with_resident_members({
        "title": "Cancelled barrier", "project_id": "project_test",
        "nodes": [_worker(WORK_A)],
        "program": {
            "components": [_component(SOURCE, "event_source", event_type="manual.triggered"),
                           {"id": FILTER, "kind": "filter", "config": {}}],
            "wires": [
                _wire(WIRE_A, "route", SOURCE, FILTER, config={"unavailable_policy": "drop"}),
                _wire(WIRE_B, "route", FILTER, WORK_A),
            ],
        },
    }, ACTOR)["lineage_id"]
    clock = _clock()
    monkeypatch.setattr(runtime, "datetime", clock)
    _emit(lineage_id)
    assert sent == [WORK_A]
    [failed] = _pending(lineage_id)
    assert failed["attempt_count"] == 1 and failed["last_error"] == "resident workspace_path missing"

    clock.moment = _due(failed) + timedelta(seconds=1)
    assert _drain(lineage_id)["attempted_count"] == 1
    # The Filter is not run again and the wire's head is no longer queued.
    assert sent == [WORK_A]
    assert _pending(lineage_id) == []
    assert _drain(lineage_id)["attempted_count"] == 0


@pytest.mark.parametrize("outcome,waits", [
    ({"results": []}, True),
    ({"exhausted": True, "results": []}, True),
    ({"paused": True, "results": []}, False),
])
def test_an_attempt_without_an_outcome_for_its_wire_waits_for_its_retry(delivery, outcome, waits):
    """投递结果没有提到这条连线时按失败保存 retry_at；来源暂停仍不记重试时间。"""
    lineage_id, busy, _calls, clock = delivery
    busy["value"] = True
    _emit(lineage_id)
    claimed = runtime.claim_pending_event(lineage_id, WIRE_A)
    assert claimed
    settled = lineage_event_delivery.settle_delivery(lineage_id, WIRE_A, claimed["dispatch_token"], outcome)
    assert not settled.delivered
    [pending] = _pending(lineage_id)
    assert not pending.get("dispatch_token")
    if waits:
        assert settled.error and pending["last_error"] == settled.error
        assert _due(pending) > clock.moment
        assert runtime.claim_pending_event(lineage_id, WIRE_A) is None
    else:
        assert not settled.error and "retry_at" not in pending
