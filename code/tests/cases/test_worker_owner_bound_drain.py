"""Rolling generations must not kill a healthy, non-reattachable owner turn.

:module: execution.service
:design: decision_075f66430606
:importance: P2
"""
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from management_portal.execution.worker_manager import ExecutionWorkerManager
from tests.cases.test_execution_worker_manager import _worker, process_boundaries  # noqa: F401


@pytest.mark.parametrize("mode", ["valid", "expired", "wrong_pid", "shutdown", "shutdown_deadline", "invalid_schema", "nan"])
def test_manager_retains_only_current_owner_lease(tmp_path, monkeypatch, mode):
    manager = ExecutionWorkerManager()
    manager.drain_timeout = .01
    manager.kill_grace = 0
    worker = _worker(tmp_path, "old-owner", 123)
    worker.draining_started_at = time.time() - 2
    manager._draining = [worker]
    health = {"schema": "tfi.execution-worker-health.v1", "worker_id": worker.worker_id,
              "build_id": worker.build_id, "pid": 123 if mode != "wrong_pid" else 124,
              "owner_bound_executions": [{"execution_id": "test-turn", "lease_expires_at": time.time() + (30 if mode != "expired" else -1)}]}
    worker.health_path.write_text(json.dumps(health))
    if mode == "shutdown":
        manager._stopping.set()
    if mode == "shutdown_deadline":
        manager._shutdown_deadline = time.monotonic() + 1
    if mode == "invalid_schema":
        health["schema"] = "unknown"
    if mode == "nan":
        health["owner_bound_executions"][0]["lease_expires_at"] = float("nan")
    worker.health_path.write_text(json.dumps(health))
    monkeypatch.setattr(manager, "_handover", lambda w: {"blocked_execution_ids": []})
    monkeypatch.setattr(manager, "_unregister", lambda _: True)
    manager._reap()
    assert worker.process.tree_stopped is (mode != "valid")
    assert manager._draining == ([worker] if mode == "valid" else [])


@pytest.mark.parametrize("state", ["running", "expired", "fenced", "finished", "completed_receipt", "codex", "not_sent"])
def test_owner_bound_lease_is_actual_unfinished_local_ownership(state):
    from management_portal.execution.worker import ExecutionWorker, _ClaimedExecution

    provider = "codex" if state == "codex" else "antigravity"
    worker = ExecutionWorker(build_id="test", worker_id="old", runtime=SimpleNamespace(), scheduler=SimpleNamespace())
    request = {"provider": provider, "operation": "turn"}
    checkpoint = {"provider": provider, "operation": "turn", "resumable": False,
                  "provider_recovery_state": {"phase": "prompt_sent", "turn_id": "x"}}
    claim = _ClaimedExecution(worker, {"claim_token": "test", "execution": {"execution_id": "x", "request": request, "checkpoint": checkpoint}}, lease_seconds=30)
    worker._claims["x"] = claim
    if state == "expired":
        claim.lease_deadline = time.monotonic() - 1
    if state == "fenced":
        claim.ownership_lost.set()
    if state == "finished":
        claim.heartbeat_stop.set()
    if state == "completed_receipt":
        claim._last_checkpoint = {"provider_completed": True, "resumable": True}
    if state == "not_sent":
        claim._last_checkpoint = {}
    leases = worker.owner_bound_executions()
    assert bool(leases) is (state == "running")
    if leases:
        assert time.time() < leases[0]["lease_expires_at"] <= time.time() + 30


@pytest.mark.parametrize("explicit_stop", [False, True])
def test_daemon_waits_past_rollout_budget_only_for_live_owner(explicit_stop):
    from management_portal.execution.worker import ExecutionWorkerDaemon

    daemon = object.__new__(ExecutionWorkerDaemon)
    daemon.drain_timeout = .01
    daemon._stopped = True
    daemon._terminating = threading.Event()
    daemon._stopping = threading.Event()
    daemon._draining_started_at = time.time()
    daemon._drain_timed_out = False
    daemon._active_changed = threading.Condition(threading.RLock())
    daemon._active = {"x": {}}
    daemon.worker = SimpleNamespace(owner_bound_executions=lambda: [{"execution_id": "x"}])
    publications = []
    daemon._write_health = lambda: publications.append(time.monotonic())
    if explicit_stop:
        daemon.request_stop(15, None)

    def complete():
        with daemon._active_changed:
            daemon._active.clear()
            daemon._active_changed.notify_all()

    timer = threading.Timer(.08, complete)
    timer.start()
    try:
        daemon._drain_active()
        assert daemon._drain_timed_out is explicit_stop
        assert bool(daemon._active) is explicit_stop
        assert publications
    finally:
        timer.cancel()
        timer.join()


def test_committed_checkpoint_health_retains_owner_then_fencing_releases_it(tmp_path, monkeypatch):
    from management_portal.execution.worker import ExecutionWorker, ExecutionWorkerDaemon, _ClaimedExecution

    manager = ExecutionWorkerManager()
    manager.drain_timeout = .01
    manager.kill_grace = 0
    process = _worker(tmp_path, "old-owner", os.getpid())
    process.draining_started_at = time.time() - 2
    manager._draining = [process]
    worker = ExecutionWorker(build_id=process.build_id, worker_id=process.worker_id,
                             runtime=SimpleNamespace(capabilities=lambda: ["agent_session:antigravity"]),
                             scheduler=SimpleNamespace(checkpoint=lambda *a: {}))
    claim = _ClaimedExecution(worker, {"claim_token": "t", "execution": {"execution_id": "x", "request": {"provider": "antigravity", "operation": "turn"}}}, lease_seconds=30)
    worker._claims["x"] = claim
    assert worker.owner_bound_executions() == []
    claim._checkpoint({"provider": "antigravity", "resumable": False,
                       "provider_recovery_state": {"phase": "prompt_sent", "turn_id": "x"}})
    daemon = object.__new__(ExecutionWorkerDaemon)
    daemon.instance, daemon.build_id, daemon.worker_id = "isolated", process.build_id, process.worker_id
    daemon.worker, daemon.health_path, daemon.max_parallel = worker, process.health_path, 1
    daemon._active_lock, daemon._health_lock = threading.RLock(), threading.Lock()
    daemon._active = {"x": {"execution_id": "x"}}
    daemon._stopping = threading.Event()
    daemon._stopping.set()
    daemon._stopped, daemon._draining_started_at, daemon._drain_timed_out = True, process.draining_started_at, False
    daemon.drain_timeout, daemon._service_error = .01, ""
    daemon._write_health()
    monkeypatch.setattr(manager, "_handover", lambda w: {"blocked_execution_ids": []})
    monkeypatch.setattr(manager, "_unregister", lambda _: True)
    manager._reap()
    assert not process.process.tree_stopped and manager._draining == [process]
    claim.ownership_lost.set()
    daemon._write_health()
    manager._reap()
    assert process.process.tree_stopped and manager._draining == []


def test_drain_health_and_concurrent_completion_do_not_deadlock():
    from management_portal.execution.worker import ExecutionWorkerDaemon

    daemon = object.__new__(ExecutionWorkerDaemon)
    daemon._active_changed = threading.Condition(threading.RLock())
    daemon._active = {"x": {}}
    daemon._health_lock = threading.Lock()
    daemon._stopped = True
    daemon._terminating = threading.Event()
    daemon._drain_timed_out = False
    daemon.drain_timeout = .01
    daemon.worker = SimpleNamespace(owner_bound_executions=lambda: [{"execution_id": "x"}])
    publisher_ready, health_requested = threading.Event(), threading.Event()

    def publish_completion():
        with daemon._health_lock:
            publisher_ready.set()
            if health_requested.wait(1):
                with daemon._active_changed:
                    daemon._active.clear()
                    daemon._active_changed.notify_all()

    def health():
        health_requested.set()
        with daemon._health_lock, daemon._active_changed:
            pass

    daemon._write_health = health
    publisher = threading.Thread(target=publish_completion, daemon=True)
    drainer = threading.Thread(target=daemon._drain_active, daemon=True)
    publisher.start()
    assert publisher_ready.wait(1)
    drainer.start()
    publisher.join(1)
    drainer.join(1)
    assert not publisher.is_alive() and not drainer.is_alive()
    assert not daemon._active and not daemon._drain_timed_out
