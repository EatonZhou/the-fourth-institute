"""An account's cooldown starts at successful dispatch, never at enqueue.

:module: execution.service
:design: decision_8ac0b5a6c0e5
"""

import uuid

import pytest

from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler
from tests.cases.test_scheduler_lock_free_admission import PausedClaim, claim_within

CAPABILITIES = ["agent_session:codex"]
pytestmark = pytest.mark.usefixtures("registered_execution_defaults")


@pytest.fixture
def paced(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    scheduler = Scheduler(capacity_limits={"codex:zero": 10, "codex:one": 10})
    for worker in ("worker-a", "worker-b"):
        scheduler.register_worker(worker_id=worker, build_id="test", capabilities=CAPABILITIES)
    return scheduler, clock


def submit(scheduler, *, route="zero", binding=""):
    request = ExecutionRequest.new(
        actor="alice", prompt="countdown", service_type="agent_session",
        provider="codex", operation="turn",
        session=SessionOptions(mode="persistent", binding_id=binding or f"resident:{uuid.uuid4().hex}", backend_route_id=route),
    )
    scheduler.submit(request)
    return request


def claim(scheduler):
    return scheduler.claim(worker_id="worker-b", capabilities=CAPABILITIES)


def dispatch(scheduler, key="codex:zero"):
    return scheduler.health()["dispatch_interval"]["partitions"].get(key)


def test_idle_expired_and_remaining_countdown_do_not_resample_at_enqueue(paced, monkeypatch):
    scheduler, clock = paced
    samples = []

    def interval(low, high):
        samples.append((low, high))
        return 4.0

    monkeypatch.setattr("management_portal.execution.scheduler_capacity.random.uniform", interval)
    first = submit(scheduler)
    assert samples == []
    assert claim(scheduler)["execution"]["execution_id"] == first.execution_id
    assert samples == [(2.0, 6.0)]
    original = dispatch(scheduler)
    clock[0] = 1002.5
    second = submit(scheduler)
    for _ in range(3):
        assert claim(scheduler) == {"claimed": False, "reason": "dispatch_interval"}
    assert dispatch(scheduler)["remaining_seconds"] == 1.5
    assert dispatch(scheduler)["next_dispatch_at"] == original["next_dispatch_at"]
    assert len(samples) == 1
    clock[0] = 1004.0
    assert claim(scheduler)["execution"]["execution_id"] == second.execution_id
    clock[0] = 1100.0
    third = submit(scheduler)
    assert len(samples) == 2
    assert claim(scheduler)["execution"]["execution_id"] == third.execution_id
    assert dispatch(scheduler)["remaining_seconds"] == 4
    assert len(samples) == 3


@pytest.mark.parametrize("prior_dispatch", [False, True], ids=["first", "expired"])
def test_same_account_rechecks_stale_admission_before_dispatch(paced, monkeypatch, prior_dispatch):
    scheduler, clock = paced
    if prior_dispatch:
        submit(scheduler)
        assert claim(scheduler)["claimed"]
        clock[0] = 1010.0
    submit(scheduler)
    submit(scheduler)
    # worker-a has already read ready when worker-b commits its dispatch.
    with PausedClaim(scheduler, monkeypatch, "admission", "fair_queue_order") as paused:
        assert claim(scheduler)["claimed"]
    assert paused.result == {"claimed": False, "reason": "dispatch_interval"}


def test_account_inflight_lock_skips_without_blocking_other_accounts(paced, monkeypatch):
    scheduler, _clock = paced
    submit(scheduler)
    with PausedClaim(scheduler, monkeypatch, "capacity", "reserve_capacity_dispatch") as paused:
        submit(scheduler)
        same, _ = claim_within(scheduler, 3)
        assert same == {"claimed": False, "reason": "dispatch_interval"}
        independent = submit(scheduler, route="one")
        other, _ = claim_within(scheduler, 3)
        assert other["claimed"]
        assert other["execution"]["execution_id"] == independent.execution_id
    assert paused.result["claimed"]
    assert dispatch(scheduler)["remaining_seconds"] > 0
    assert dispatch(scheduler, "codex:one")["remaining_seconds"] > 0


@pytest.mark.parametrize("blocked", ["binding", "capacity", "cancelled"])
def test_unsuccessful_claim_does_not_restart_elapsed_cooldown(paced, blocked):
    scheduler, clock = paced
    if blocked == "capacity":
        scheduler.configure_capacity("codex:zero", 1, 2, 6, actor="admin")
    submit(scheduler, binding="resident:occupied")
    assert claim(scheduler)["claimed"]
    previous = dispatch(scheduler)
    clock[0] = 1010.0
    request = submit(scheduler, binding="resident:occupied" if blocked == "binding" else "")
    if blocked == "cancelled":
        scheduler.cancel(request.execution_id, actor=request.actor)
    assert not claim(scheduler)["claimed"]
    current = dispatch(scheduler)
    assert current["last_dispatched_at"] == previous["last_dispatched_at"]
    assert current["next_dispatch_at"] == previous["next_dispatch_at"]
    assert current["remaining_seconds"] == 0


def test_failed_claim_transaction_rolls_back_execution_and_cooldown(paced, monkeypatch):
    scheduler, _clock = paced
    request = submit(scheduler)
    original = scheduler.changes.event

    def fail(*args, **kwargs):
        raise RuntimeError("abort before commit")

    monkeypatch.setattr(scheduler.changes, "event", fail)
    with pytest.raises(RuntimeError, match="abort before commit"):
        claim(scheduler)
    assert dispatch(scheduler) is None
    monkeypatch.setattr(scheduler.changes, "event", original)
    assert claim(scheduler)["execution"]["execution_id"] == request.execution_id


def test_cooldown_starts_after_claim_preparation_not_before(paced, monkeypatch):
    scheduler, clock = paced
    submit(scheduler)
    original = scheduler.claims._claim_candidate

    def delayed(*args, **kwargs):
        clock[0] += 10
        return original(*args, **kwargs)

    monkeypatch.setattr(scheduler.claims, "_claim_candidate", delayed)
    assert claim(scheduler)["claimed"]
    assert dispatch(scheduler)["remaining_seconds"] >= 2


def test_lost_worker_interrupt_does_not_consume_or_wait_for_cooldown(paced):
    scheduler, clock = paced
    request = submit(scheduler)
    claimed = claim(scheduler)
    owner = {"worker_id": "worker-b", "claim_token": claimed["claim_token"]}
    scheduler.start(request.execution_id, **owner)
    previous = dispatch(scheduler)
    clock[0] += 1
    # Only the isolated fixture DB is touched: simulate an expired lease while
    # the account's dispatch countdown is still positive.
    with scheduler.changes.connect() as connection:
        connection.execute(
            "UPDATE executions SET lease_expires_at='epoch' WHERE execution_id=%s",
            (request.execution_id,),
        )
        connection.commit()
    assert scheduler.maintain()["recovered_expired_leases"] == 1
    interrupted = claim(scheduler)
    assert interrupted["claimed"]
    assert interrupted["execution"]["execution_id"] == request.execution_id
    assert interrupted["execution"]["checkpoint"]["abandoned"]["status"] == "failed"
    assert dispatch(scheduler)["next_dispatch_at"] == previous["next_dispatch_at"]
    assert dispatch(scheduler)["last_dispatched_at"] == previous["last_dispatched_at"]


def test_rejected_candidates_on_one_account_only_probe_its_window_once(paced, monkeypatch):
    scheduler, _clock = paced
    for _ in range(12):
        submit(scheduler)
    submit(scheduler, route="one")
    attempted = []
    original = scheduler.capacity.try_dispatch

    def blocked(connection, key):
        attempted.append(key)
        return False if key == "codex:zero" else original(connection, key)

    monkeypatch.setattr(scheduler.capacity, "try_dispatch", blocked)
    assert claim(scheduler)["execution"]["request"]["session"]["backend_route_id"] == "one"
    assert attempted.count("codex:zero") <= 1
