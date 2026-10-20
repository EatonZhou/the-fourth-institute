from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal.execution import worker_manager
from management_portal.execution.worker_manager import (
    ExecutionWorkerManager,
    WorkerProcess,
)


class FakeProcess:
    def __init__(self, pid: int, returncode=None) -> None:
        self.pid = pid
        self.terminated = False
        self.killed = False
        self.returncode = returncode
        self.tree_stopped = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture(autouse=True)
def process_boundaries(monkeypatch):
    # Synthetic PIDs must never reach the OS. Preserve the scheduling and
    # retirement contracts while mocking only the process/RPC capabilities.
    def stop_tree(process, **kwargs):
        assert isinstance(process, FakeProcess)
        process.tree_stopped = True
        if process.returncode is None:
            process.returncode = -9
    monkeypatch.setattr(worker_manager.process_runtime, "terminate_process_tree", stop_tree)
    monkeypatch.setattr(worker_manager, "ExecutionServiceClient", lambda: SimpleNamespace(request=lambda *args: {}))


def _worker(tmp_path: Path, name: str, pid: int) -> WorkerProcess:
    return WorkerProcess(
        process=FakeProcess(pid),  # type: ignore[arg-type]
        worker_id=name,
        build_id=name,
        health_path=tmp_path / f"{name}.json",
        started_at=1.0,
    )


def test_worker_rollout_waits_for_new_worker_before_draining_old_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """新工作进程就绪后才排空旧进程，替换失败按退避重试"""
    with subtests.test(contract='test_worker_rollout_waits_for_new_worker_before_draining_old'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            manager = ExecutionWorkerManager()
            old = _worker(tmp_path, "old", 1)
            new = _worker(tmp_path, "new", 2)
            manager._current = old
            events: list[str] = []
            monkeypatch.setattr(manager, "_spawn", lambda: events.append("spawn") or new)
            monkeypatch.setattr(
                manager,
                "_wait_ready",
                lambda worker: events.append(f"ready:{worker.worker_id}"),
            )
            monkeypatch.setattr(
                manager,
                "_mark_draining",
                lambda worker: events.append(f"drain:{worker.worker_id}"),
            )
            monkeypatch.setattr(
                manager,
                "_activate_replacement",
                lambda replacement, previous: events.append(
                    f"activate:{replacement.worker_id}:{previous.worker_id}"
                ),
            )
            monkeypatch.setattr(
                manager,
                "_handover",
                lambda worker: events.append(f"handover:{worker.worker_id}") or {
                    "worker_id": worker.worker_id,
                    "requeued_execution_ids": [],
                    "blocked_execution_ids": [],
                },
            )
            monkeypatch.setattr(manager, "_write_health", lambda: None)

            manager._replace()

            assert events == [
                "spawn",
                "ready:new",
                "activate:new:old",
                "drain:old",
            ]
            assert manager._current is new
            assert manager._draining == [old]

            manager.retry_initial = 2.0
            manager.retry_max = 30.0
            manager.retry_multiplier = 2.0
            manager.retry_jitter = 0.0
            attempts = iter([RuntimeError("service unavailable"), None])

            def replace_with_retry() -> None:
                error = next(attempts)
                if error is not None:
                    raise error

            monkeypatch.setattr(manager, "_replace", replace_with_retry)
            monkeypatch.setattr(
                manager,
                "_write_health_safely",
                lambda: True,
            )
            assert manager._retry_delay(1) == 2.0
            assert manager._retry_delay(2) == 4.0
            assert manager._retry_delay(20) == 30.0
            assert manager._attempt_replacement() is False
            assert manager._consecutive_failures == 1
            assert manager._last_error == "RuntimeError: service unavailable"
            assert manager._next_retry_monotonic > time.monotonic()
            assert manager._attempt_replacement() is True
            assert manager._consecutive_failures == 0
            assert manager._last_error == ""
            assert manager._next_retry_monotonic == 0.0
    with subtests.test(contract='test_worker_reload_exits_stale_manager_instead_of_rolling_another_child'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            manager = ExecutionWorkerManager()
            manager.manager_source_path = tmp_path / "worker_manager.py"
            manager.manager_source_path.write_text("new manager", encoding="utf-8")
            manager.loaded_manager_source_digest = "old-manager-digest"
            rollouts: list[str] = []
            monkeypatch.setattr(manager, "_replace", lambda: rollouts.append("replace"))

            manager._reload_worker()

            assert manager._stopping.is_set()
            assert manager._restart_requested is True
            assert rollouts == []

            manager = ExecutionWorkerManager()
            client = tmp_path / "transport.py"
            client.write_text("old unsigned client", encoding="utf-8")
            manager.loaded_rpc_source_digests = {client: manager._source_digest(client)}
            client.write_text("signed client", encoding="utf-8")
            monkeypatch.setattr(manager, "_roll_out", lambda: rollouts.append("rollout"))
            manager._reload_worker()
            assert manager._stopping.is_set() and manager._restart_requested
            assert rollouts == []
    with subtests.test(contract='test_worker_reload_keeps_manager_for_child_only_rollout'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            manager = ExecutionWorkerManager()
            manager.manager_source_path = tmp_path / "worker_manager.py"
            manager.manager_source_path.write_text("same manager", encoding="utf-8")
            manager.loaded_manager_source_digest = manager._source_digest(
                manager.manager_source_path
            )
            rollouts: list[str] = []
            monkeypatch.setattr(manager, "_replace", lambda: rollouts.append("replace"))

            manager._reload_worker()

            assert not manager._stopping.is_set()
            assert rollouts == ["replace"]
    with subtests.test(contract='test_worker_manager_uses_component_generation_for_new_child'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            instance_root = tmp_path / "dev"
            instance_root.mkdir()
            generation_env = instance_root / "source-generation.env"
            generation_env.write_text("PORTAL_BUILD_ID=global-new\n", encoding="utf-8")
            components = instance_root / "component-generations.json"
            components.write_text(
                json.dumps(
                    {"components": {"execution_worker": "worker-current"}}
                ),
                encoding="utf-8",
            )
            monkeypatch.setenv("PORTAL_INSTANCE", "dev")
            monkeypatch.setenv(
                "PORTAL_EXECUTION_WORKER_GENERATION_ENV",
                str(generation_env),
            )
            monkeypatch.setenv(
                "PORTAL_EXECUTION_WORKER_COMPONENT_GENERATIONS",
                str(components),
            )
            monkeypatch.setenv(
                "PORTAL_EXECUTION_WORKER_HEALTH_PATH",
                str(instance_root / "health.json"),
            )

            manager = ExecutionWorkerManager()

            assert manager._build_id() == "worker-current"
            child_environments = []

            def spawn_process(*_args, **kwargs):
                child_environments.append(kwargs["env"])
                return FakeProcess(100 + len(child_environments))

            monkeypatch.setattr(
                worker_manager.process_runtime,
                "spawn_process",
                spawn_process,
            )
            manager._consecutive_failures = 0
            manager._spawn()
            manager._consecutive_failures = 1
            manager._spawn()

            assert child_environments[0][
                "PORTAL_EXECUTION_WORKER_FATAL_MODE"
            ] == "traceback"
            assert child_environments[1][
                "PORTAL_EXECUTION_WORKER_FATAL_MODE"
            ] == "silent"
    with subtests.test(contract='test_worker_manager_unregisters_exited_generation_before_deleting_health'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            manager = ExecutionWorkerManager()
            worker = _worker(tmp_path, "retired", 3)
            worker.process.returncode = 0  # type: ignore[attr-defined]
            worker.health_path.write_text("{}\n", encoding="utf-8")
            manager._draining = [worker]
            unregistered: list[str] = []
            monkeypatch.setattr(
                manager,
                "_handover",
                lambda worker: {
                    "worker_id": worker.worker_id,
                    "requeued_execution_ids": [],
                    "blocked_execution_ids": [],
                },
            )
            monkeypatch.setattr(
                manager,
                "_unregister",
                lambda worker_id: unregistered.append(worker_id) or True,
            )

            manager._reap()

            assert unregistered == ["retired"]
            assert manager._draining == []
            assert not worker.health_path.exists()
    with subtests.test(contract='test_worker_manager_reaps_timed_out_draining_generation_with_active_execution'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            manager = ExecutionWorkerManager()
            manager.drain_timeout = 5
            manager.kill_grace = 0
            worker = _worker(tmp_path, "stuck", 4)
            worker.draining_started_at = time.time() - 6
            worker.health_path.write_text(
                json.dumps({
                    "active_executions": [{"execution_id": "execution-stuck"}],
                }),
                encoding="utf-8",
            )
            manager._draining = [worker]
            handovers: list[str] = []
            monkeypatch.setattr(
                manager,
                "_handover",
                lambda item: handovers.append(item.worker_id) or {
                    "worker_id": item.worker_id,
                    "requeued_execution_ids": ["execution-stuck"],
                    "blocked_execution_ids": [],
                },
            )
            monkeypatch.setattr(manager, "_unregister", lambda _worker_id: True)

            manager._reap()
            payload = manager._worker_payload(worker)

            assert worker.terminate_sent_at > 0
            assert worker.process.tree_stopped is True  # type: ignore[attr-defined]
            assert payload["drain_timed_out"] is True
            assert payload["draining_seconds"] >= 5
            assert handovers == ["stuck"]
            assert manager._draining == []


def test_worker_manager_reaps_multiple_timed_out_draining_generations_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """工作进程管理器回收多代排空超时进程并保留当前代"""
    with subtests.test(contract='test_worker_manager_reaps_multiple_timed_out_draining_generations'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            manager = ExecutionWorkerManager()
            manager.drain_timeout = 2
            manager.kill_grace = 0
            workers = [
                _worker(tmp_path, f"old-{index}", 10 + index)
                for index in range(3)
            ]
            for worker in workers:
                worker.draining_started_at = time.time() - 3
            manager._draining = workers
            monkeypatch.setattr(
                manager,
                "_handover",
                lambda worker: {
                    "worker_id": worker.worker_id,
                    "requeued_execution_ids": [],
                    "blocked_execution_ids": [],
                },
            )
            monkeypatch.setattr(manager, "_unregister", lambda _worker_id: True)

            manager._reap()

            assert all(
                worker.terminate_sent_at > 0
                and worker.process.tree_stopped  # type: ignore[attr-defined]
                and worker.drain_timed_out
                for worker in workers
            )
            assert manager._draining == []
    with subtests.test(contract='test_worker_manager_allows_active_generation_to_drain_before_terminate'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            manager = ExecutionWorkerManager()
            worker = _worker(tmp_path, "draining", 20)
            worker.draining_started_at = time.time()
            worker.health_path.write_text(
                json.dumps({
                    "active_executions": [{"execution_id": "execution-active"}],
                }),
                encoding="utf-8",
            )
            manager._draining = [worker]
            handovers: list[str] = []
            monkeypatch.setattr(
                manager,
                "_handover",
                lambda item: handovers.append(item.worker_id) or {
                    "worker_id": item.worker_id,
                    "requeued_execution_ids": [],
                    "blocked_execution_ids": [],
                },
            )
            monkeypatch.setattr(manager, "_unregister", lambda _worker_id: True)

            manager._reap()

            assert worker.terminate_sent_at > 0
            assert worker.process.tree_stopped is False  # type: ignore[attr-defined]
            assert handovers == []

            worker.health_path.write_text(
                json.dumps({"active_executions": []}),
                encoding="utf-8",
            )
            manager._reap()

            assert handovers == []

            worker.process.returncode = 0  # type: ignore[attr-defined]
            manager._reap()

            assert handovers == ["draining"]
            assert manager._draining == []
    with subtests.test(contract='test_worker_reload_keeps_a_worker_that_already_runs_the_generation'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            manager = ExecutionWorkerManager()
            current = _worker(tmp_path, "build-a", 30)
            manager._current = current
            rollouts: list[str] = []
            monkeypatch.setattr(manager, "_replace", lambda: rollouts.append("replace"))
            generation = {"build": "build-a"}
            monkeypatch.setattr(manager, "_build_id", lambda: generation["build"])

            # A publish reloads both pools; the pool already on the
            # generation keeps its Worker and its executions.
            assert manager._roll_out() is False
            assert rollouts == []

            generation["build"] = "build-b"
            assert manager._roll_out() is True
            assert rollouts == ["replace"]
    with subtests.test(contract='test_worker_manager_starts_newest_generation_after_its_worker_was_retired'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            manager = ExecutionWorkerManager()
            manager.retry_jitter = 0.0
            monkeypatch.setattr(manager, "_build_id", lambda: "build-b")

            retired = _worker(tmp_path, "build-a", 40)
            retired.process.returncode = 0  # type: ignore[attr-defined]
            retired.health_path.write_text(json.dumps({"retired": True}), encoding="utf-8")
            manager._current = retired
            manager._note_retired_current()

            # Another pool's newer generation retired it; start that one now.
            assert retired.retirement_noted is True
            assert manager._consecutive_failures == 0
            assert manager._next_retry_monotonic == 0.0

            stale = _worker(tmp_path, "build-b", 41)
            stale.process.returncode = 0  # type: ignore[attr-defined]
            stale.health_path.write_text(json.dumps({"retired": True}), encoding="utf-8")
            manager._current = stale
            manager._note_retired_current()

            # Retired although this pool already runs the file's generation:
            # back off instead of taking the work straight back.
            assert manager._consecutive_failures == 1
            assert manager._next_retry_monotonic > time.monotonic()

            crashed = _worker(tmp_path, "build-c", 42)
            crashed.process.returncode = 1  # type: ignore[attr-defined]
            crashed.health_path.write_text(json.dumps({"retired": False}), encoding="utf-8")
            manager._current = crashed
            manager._note_retired_current()

            assert crashed.retirement_noted is False
            assert manager._consecutive_failures == 1


def test_cleanup_removes_absent_dead_worker_health_but_preserves_live_and_unknown(monkeypatch, tmp_path):
    """清理失联且已退出的工作进程记录，保留存活或状态未知者"""
    from types import SimpleNamespace
    import os
    manager = ExecutionWorkerManager()
    manager.child_health_root = tmp_path
    manager.worker_id_prefix = "fixture:execution-worker"
    manager._current = None
    manager._draining = []
    for name, pid in (("absent", 2147483647), ("registered", 2147483647), ("live", os.getpid())):
        (tmp_path / (name + ".json")).write_text(json.dumps({"worker_id": manager.worker_id_prefix + ":" + name, "pid": pid}))
    monkeypatch.setattr(worker_manager, "ExecutionServiceClient", lambda: SimpleNamespace(health=lambda: {}))
    manager._cleanup_offline_registrations()
    assert (tmp_path / "absent.json").exists()
    monkeypatch.setattr(worker_manager, "ExecutionServiceClient", lambda: SimpleNamespace(health=lambda: {
        "scheduler": {"workers": [{"worker_id": manager.worker_id_prefix + ":registered", "state": "online"}]}}))
    manager._cleanup_offline_registrations()
    assert not (tmp_path / "absent.json").exists()
    assert (tmp_path / "registered.json").exists()
    assert (tmp_path / "live.json").exists()
