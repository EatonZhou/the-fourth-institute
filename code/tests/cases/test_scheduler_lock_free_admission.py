"""Claims run side by side; only an execution row and its session binding are atomic.

Capacity, quota and dispatch pacing are read without a lock, so concurrent
claims may briefly exceed them. A claim that pauses mid-transaction must not
hold up other Workers, an execution is claimed once, and one session binding
never runs two executions.

:module: execution.service
:design: decision_8ac0b5a6c0e5
"""

import threading
import time

import pytest

from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler

CAPABILITIES = ["agent_session:codex", "chat_completion"]


def new_scheduler():
    return Scheduler(
        capacity_limits={"codex:one": 8},
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )


@pytest.fixture
def scheduler():
    result = new_scheduler()
    for worker_id in ("worker-a", "worker-b"):
        result.register_worker(worker_id=worker_id, build_id="test", capabilities=CAPABILITIES)
    return result


def submit(scheduler, *, binding_id="", service_type="agent_session"):
    if service_type == "chat_completion":
        request = ExecutionRequest.new(
            actor="alice", prompt="generate", service_type="chat_completion",
            provider="openai", operation="generate",
        )
    else:
        request = ExecutionRequest.new(
            actor="alice", prompt="turn", service_type="agent_session", provider="codex",
            operation="turn", session=SessionOptions(
                mode="persistent", binding_id=binding_id, backend_route_id="one",
            ),
        )
    scheduler.submit(request)
    return request.execution_id


class PausedClaim:
    """Run worker-a's claim in a thread and hold it inside ``component.method`` until released."""

    def __init__(self, scheduler, monkeypatch, component, method):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.result = {}
        owner = getattr(scheduler, component)
        original = getattr(owner, method)

        def pause(*args, **kwargs):
            value = original(*args, **kwargs)
            if threading.current_thread() is self.thread:
                self.entered.set()
                assert self.release.wait(10)
            return value

        monkeypatch.setattr(owner, method, pause)
        self.thread = threading.Thread(
            target=lambda: self.result.update(scheduler.claim(worker_id="worker-a", capabilities=CAPABILITIES)),
            daemon=True,
        )

    def __enter__(self):
        self.thread.start()
        assert self.entered.wait(10), "worker-a must reach the paused step"
        return self

    def __exit__(self, *exc):
        self.release.set()
        self.thread.join(10)


def claim_within(scheduler, seconds, worker_id="worker-b"):
    result = {}
    thread = threading.Thread(
        target=lambda: result.update(scheduler.claim(worker_id=worker_id, capabilities=CAPABILITIES)),
        daemon=True,
    )
    started = time.monotonic()
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), "the claim waited for another Worker's claim"
    return result, time.monotonic() - started


@pytest.mark.parametrize("pause_in", [("admission", "fair_queue_order"), ("capacity", "reserve_capacity_dispatch")],
                         ids=["fair_queue_order", "reserve_capacity_dispatch"])
@pytest.mark.parametrize("claimer", ["same_process", "peer_process"])
def test_a_paused_claim_does_not_hold_up_other_workers(scheduler, monkeypatch, claimer, pause_in):
    """一个 Worker 的领取停顿时，其他 Worker 照常领取

    领取不取全局准入锁：一个 Worker 的领取事务停在读取候选、核对会话或写入派发记录的任一步时，另一个 Worker 仍能在限定时间内领到另一条执行。

    :design-point:
    """
    # An earlier dispatch on this account leaves the pacing rows in place.
    submit(scheduler, binding_id="resident:earlier")
    assert scheduler.claim(worker_id="worker-b", capabilities=CAPABILITIES)["claimed"] is True
    first = submit(scheduler, binding_id="resident:first")
    second = submit(scheduler, binding_id="resident:second")
    other = scheduler if claimer == "same_process" else new_scheduler()

    with PausedClaim(scheduler, monkeypatch, *pause_in) as paused:
        result, _elapsed = claim_within(other, 3)
        assert result["claimed"] is True
    assert paused.result["claimed"] is True
    assert {result["execution"]["execution_id"], paused.result["execution"]["execution_id"]} == {first, second}


def test_concurrent_claims_never_run_one_binding_twice(scheduler, monkeypatch):
    """并发领取不会让同一会话同时运行两条执行

    两个 Worker 同时领取同一 binding_id 的两条排队执行时，最多一条进入 claimed，另一条留在队列里。

    :design-point:
    """
    # An earlier dispatch on this account leaves the pacing rows in place.
    submit(scheduler, binding_id="resident:earlier")
    assert scheduler.claim(worker_id="worker-b", capabilities=CAPABILITIES)["claimed"] is True
    submit(scheduler, binding_id="resident:shared")
    submit(scheduler, binding_id="resident:shared")

    # worker-a holds its claim of the first execution open, uncommitted.
    with PausedClaim(scheduler, monkeypatch, "capacity", "reserve_capacity_dispatch") as paused:
        result, _elapsed = claim_within(scheduler, 3)
        assert result["claimed"] is False
    assert paused.result["claimed"] is True

    # After the commit the binding is busy for every claimer.
    assert scheduler.claim(worker_id="worker-b", capabilities=CAPABILITIES)["claimed"] is False


def test_an_execution_is_claimed_once_when_claims_race(scheduler, monkeypatch):
    """并发领取同一条执行时只有一个 Worker 得到它

    两个 Worker 同时领取唯一一条排队执行时，只有一个领到；另一个得到 empty，不等待对方的行锁。

    :design-point:
    """
    execution_id = submit(scheduler, service_type="chat_completion")

    # worker-b skips the row worker-a is claiming instead of waiting for it.
    with PausedClaim(scheduler, monkeypatch, "capacity", "reserve_capacity_dispatch") as paused:
        result, _elapsed = claim_within(scheduler, 3)
    assert paused.result["claimed"] is True
    assert paused.result["execution"]["execution_id"] == execution_id
    assert result == {"claimed": False, "reason": "empty"}
    assert scheduler.snapshot(execution_id)["scheduler"]["attempts"] == 1


def test_claims_leave_expired_leases_to_maintenance(scheduler, monkeypatch):
    """领取不回收过期租约，由维护周期回收

    领取只读取排队执行，不顺带回收过期租约；维护把过期租约放回队列后，执行才能再次被领取。

    :design-point:
    """
    execution_id = submit(scheduler, binding_id="resident:lease")
    claimed = scheduler.claim(worker_id="worker-a", capabilities=CAPABILITIES, lease_seconds=15)
    assert claimed["claimed"] is True

    later = time.time() + 60
    monkeypatch.setattr("time.time", lambda: later)
    assert scheduler.claim(worker_id="worker-b", capabilities=CAPABILITIES)["claimed"] is False

    assert scheduler.maintain()["recovered_expired_leases"] == 1
    reclaimed = scheduler.claim(worker_id="worker-b", capabilities=CAPABILITIES)
    assert reclaimed["claimed"] is True
    assert reclaimed["execution"]["execution_id"] == execution_id


def test_unattributed_usage_counts_a_range_once_when_counters_race(scheduler, monkeypatch):
    """并发记录未归因用量时同一区间只计一次

    两个调用同时推进未归因用量计数时，计数器以比较后写入推进，同一段用量不会重复计入。

    :design-point:
    """
    request = {
        "provider": "claude", "service_type": "agent_session",
        "session": {"backend_route_id": "one"}, "metadata": {"resident_id": "resident_0123456789ab"},
    }
    usage = [("request-1", {"input_tokens": 10, "output_tokens": 2}, "2026-10-16T01:00:00Z")]
    entered, release = threading.Event(), threading.Event()
    original = scheduler.usage.record_token_usage
    first_thread = []

    def pause(*args, **kwargs):
        if threading.current_thread() in first_thread:
            entered.set()
            assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(scheduler.usage, "record_token_usage", pause)
    counts = {}

    def count(name):
        counts[name] = scheduler.record_unattributed_token_usage(
            "self_started_usage:claude:race", expected="", watermark="2026-10-16T01:00:05Z",
            request=request, requests=usage,
        )

    first = threading.Thread(target=count, args=("first",), daemon=True)
    first_thread.append(first)
    first.start()
    assert entered.wait(10)
    second = threading.Thread(target=count, args=("second",), daemon=True)
    second.start()
    time.sleep(0.3)
    release.set()
    first.join(10)
    second.join(10)

    assert counts == {"first": 1, "second": 0}
    assert scheduler.usage_watermark("self_started_usage:claude:race") == "2026-10-16T01:00:05Z"
