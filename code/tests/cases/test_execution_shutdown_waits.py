"""Service shutdown wakes long polls and drains committed RPC mutations.

:module: execution.service
:design: decision_6ad0384e020d, decision_25eb93d803a4
"""
import threading
import time
from unittest.mock import Mock

import pytest

from management_portal.execution.contracts import ExecutionRequest, ExecutionInputControl, SessionOptions
from management_portal.execution import service_rpc
from tests.cases.execution_layer_test_support import _stack, _close

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")
CAPABILITIES = ["agent_session:codex"]


def setup_running(scheduler):
    worker = "release:shutdown-running"
    scheduler.register_worker(worker_id=worker, build_id="release", capabilities=CAPABILITIES)
    request = ExecutionRequest.new(actor="sample_admin", prompt="work", service_type="agent_session",
        provider="codex", operation="turn", session=SessionOptions(mode="persistent",
            binding_id="resident:shutdown", backend_route_id="zero"))
    scheduler.submit(request)
    claimed = scheduler.claim(worker_id=worker, capabilities=CAPABILITIES)
    owner = {"worker_id": worker, "claim_token": claimed["claim_token"]}
    scheduler.start(request.execution_id, **owner)
    return request, owner


def test_shutdown_drains_real_rpc_long_polls_without_changing_snapshots(tmp_path, monkeypatch):
    scheduler, server, serving, client, _service = _stack(tmp_path)
    request, owner = setup_running(scheduler)
    scheduler.register_worker(worker_id="release:shutdown-idle", build_id="release", capabilities=CAPABILITIES)
    waiting = {kind: threading.Event() for kind in ("claim", "control", "execution")}
    original = scheduler.changes.wait_for_change_signal
    def observe(kind, seen, timeout):
        waiting[kind].set()
        return original(kind, seen, timeout)
    monkeypatch.setattr(scheduler.changes, "wait_for_change_signal", observe)
    snapshot = scheduler.snapshot(request.execution_id)
    calls = {
        "claim": ("scheduler.claim.wait", {"worker_id": "release:shutdown-idle", "capabilities": CAPABILITIES, "timeout_seconds": 30}),
        "control": ("scheduler.control.wait", {"execution_id": request.execution_id, **owner, "timeout_seconds": 60}),
        "execution": ("execution.wait", {"execution_id": request.execution_id, "actor": request.actor,
            "after_updated_at": snapshot["scheduler"]["updated_at"], "include_events": False, "timeout_seconds": 60}),
    }
    results, errors = {}, []
    def poll(kind):
        try: results[kind] = client.request(*calls[kind])
        except Exception as exc: errors.append(exc)
    threads = [threading.Thread(target=poll, args=(kind,)) for kind in calls]
    try:
        for thread in threads: thread.start()
        assert all(event.wait(2) for event in waiting.values())
        started = time.monotonic()
        server.shutdown()
        server.server_close()
        for thread in threads: thread.join(1)
        assert not errors and all(not thread.is_alive() for thread in threads)
        assert time.monotonic() - started < 2
        assert results["claim"]["claimed"] is False
        assert results["control"] == {"status": "running", "cancel_requested": False, "handover_requested": False}
        assert results["execution"]["status"] == "running"
        assert server.daemon_threads is False
        # Closing cannot authorize a different actor or a stale owner.
        with pytest.raises(KeyError):
            service_rpc.wait_for_execution(server, {"execution_id": request.execution_id, "actor": "other"})
        with pytest.raises(PermissionError, match="claim ownership changed"):
            service_rpc.wait_for_worker_control(server, {"execution_id": request.execution_id,
                "worker_id": owner["worker_id"], "claim_token": "wrong"})
        started = time.monotonic()
        assert service_rpc.wait_for_claim(server, calls["claim"][1])["reason"] == "service_closing"
        assert service_rpc.wait_for_worker_control(server, calls["control"][1])["status"] == "running"
        quiet = service_rpc.wait_for_execution(server, {**calls["execution"][1], "quiet_reply": True})
        assert quiet == {"execution_id": request.execution_id, "status": "running", "unchanged": True}
        assert time.monotonic() - started < 1
        assert service_rpc.wait_for_claim(server, {"worker_id": "unregistered", "capabilities": CAPABILITIES})["reason"] == "worker_not_registered"
        control = ExecutionInputControl.new(execution_id=request.execution_id, actor=request.actor,
            input=[{"type": "text", "text": "pending input"}])
        scheduler.submit_input_control(control)
        scheduler.cancel(request.execution_id, actor=request.actor)
        pending = service_rpc.wait_for_worker_control(server, calls["control"][1])
        assert pending["cancel_requested"] is True and pending["handover_requested"] is False
        assert pending["turn_controls"][0]["control_id"] == control.control_id
    finally:
        _close(server, serving)


@pytest.mark.parametrize("kind", ["execution", "claim", "control"])
def test_shutdown_between_database_read_and_condition_wait_never_sleeps(kind):
    from management_portal.execution.scheduler_changes import SchedulerChanges
    changes = SchedulerChanges(Mock())
    seen = changes.change_generation(kind)
    changes.stop_waits()
    condition = changes.signal(kind)
    original = condition.wait
    condition.wait = Mock(side_effect=AssertionError("missed shutdown wakeup"))
    try:
        changes.wait_for_change_signal(kind, seen, 60)
        changes.wait_for_change_signal(kind, changes.change_generation(kind), 60)
    finally:
        condition.wait = original


def test_committed_claim_reply_and_mutation_drain_survive_shutdown(tmp_path, monkeypatch):
    scheduler, server, serving, client, _service = _stack(tmp_path)
    worker = "release:committed-claim"
    scheduler.register_worker(worker_id=worker, build_id="release", capabilities=CAPABILITIES)
    request = ExecutionRequest.new(actor="sample_admin", prompt="claim", service_type="agent_session",
        provider="codex", operation="turn", session=SessionOptions(mode="persistent",
            binding_id="resident:committed-shutdown", backend_route_id="zero"))
    scheduler.submit(request)
    committed, release, closed = threading.Event(), threading.Event(), threading.Event()
    original = scheduler.claims.claim
    def claim(**kwargs):
        result = original(**kwargs)
        assert result["claimed"]
        committed.set()
        assert release.wait(3)
        return result
    monkeypatch.setattr(scheduler.claims, "claim", claim)
    result = {}
    def call():
        result.update(client.request("scheduler.claim.wait", {"worker_id": worker, "capabilities": CAPABILITIES}))
    polling = threading.Thread(target=call)
    polling.start()
    closing = None
    try:
        assert committed.wait(2)
        server.shutdown()
        def close():
            server.server_close()
            closed.set()
        closing = threading.Thread(target=close)
        closing.start()
        assert not closed.wait(.05)  # An accepted mutation still drains.
        release.set()
        polling.join(2)
        closing.join(2)
        assert closed.is_set() and result["claimed"] is True
        assert result["execution"]["execution_id"] == request.execution_id
        from management_portal.execution.scheduler import Scheduler
        replacement = Scheduler(accounts=scheduler.accounts)
        assert replacement.changes.waits_stopped is False
        resumed = replacement.start(request.execution_id, worker_id=worker, claim_token=result["claim_token"])
        assert resumed["status"] == "running"  # The returned token is the committed, usable lease.
    finally:
        release.set()
        polling.join(2)
        if closing: closing.join(2)
        _close(server, serving)
