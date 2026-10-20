from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .transport import ExecutionServiceClient

from management_common.instance_paths import instance_root
from management_common import process_runtime
from management_portal.shared_state import atomic_replace_json


@dataclass
class WorkerProcess:
    process: subprocess.Popen[bytes]
    worker_id: str
    build_id: str
    health_path: Path
    started_at: float
    draining_started_at: float = 0.0
    drain_timed_out: bool = False
    terminate_sent_at: float = 0.0
    kill_sent_at: float = 0.0
    handover: dict[str, Any] | None = None
    retirement_noted: bool = False
    tree_stopped: bool = False
    cleanup_error: str = ""


class ExecutionWorkerManager:
    """Keep one accepting worker while older generations drain within a deadline."""

    def __init__(self) -> None:
        self.instance = str(os.getenv("PORTAL_INSTANCE", "dev") or "dev")
        self.worker_id_prefix = str(
            os.getenv(
                "PORTAL_EXECUTION_WORKER_ID",
                f"{self.instance}:execution-worker",
            )
            or f"{self.instance}:execution-worker"
        )
        self.source_root = Path(
            os.getenv("PORTAL_EXECUTION_WORKER_SOURCE_ROOT", os.getcwd())
        )
        self.manager_source_path = (
            self.source_root
            / "management_portal"
            / "execution"
            / "worker_manager.py"
        )
        self.loaded_manager_source_digest = self._source_digest(
            self.manager_source_path
        )
        # The manager keeps an RPC client in memory across child rollouts.
        # Client/authentication updates therefore require its own graceful restart.
        self.rpc_source_paths = (
            self.source_root / "management_portal/execution/transport.py",
            self.source_root / "management_common/rpc_auth.py",
        )
        self.loaded_rpc_source_digests = {
            path: self._source_digest(path) for path in self.rpc_source_paths
        }
        instance_directory = instance_root() / self.instance
        self.generation_env = Path(
            os.getenv(
                "PORTAL_EXECUTION_WORKER_GENERATION_ENV",
                str(instance_directory / "source-generation.env"),
            )
        )
        self.component_generations = Path(
            os.getenv(
                "PORTAL_EXECUTION_WORKER_COMPONENT_GENERATIONS",
                str(instance_directory / "component-generations.json"),
            )
        )
        self.health_path = Path(
            os.getenv(
                "PORTAL_EXECUTION_WORKER_HEALTH_PATH",
                str(instance_directory / "execution-worker-health.json"),
            )
        )
        self.child_health_root = self.health_path.parent / "execution-workers"
        self.ready_timeout = max(
            2.0,
            float(os.getenv("PORTAL_EXECUTION_WORKER_READY_TIMEOUT", "30")),
        )
        self.retry_initial = max(
            1.0,
            float(
                os.getenv(
                    "PORTAL_EXECUTION_WORKER_RETRY_INITIAL_SECONDS",
                    "5",
                )
            ),
        )
        self.retry_max = max(
            self.retry_initial,
            float(
                os.getenv(
                    "PORTAL_EXECUTION_WORKER_RETRY_MAX_SECONDS",
                    "300",
                )
            ),
        )
        self.retry_multiplier = max(
            1.0,
            float(
                os.getenv(
                    "PORTAL_EXECUTION_WORKER_RETRY_MULTIPLIER",
                    "2",
                )
            ),
        )
        self.retry_jitter = min(
            0.5,
            max(
                0.0,
                float(
                    os.getenv(
                        "PORTAL_EXECUTION_WORKER_RETRY_JITTER",
                        "0.2",
                    )
                ),
            ),
        )
        self.failure_log_interval = max(
            30.0,
            float(
                os.getenv(
                    "PORTAL_EXECUTION_WORKER_FAILURE_LOG_INTERVAL_SECONDS",
                    "300",
                )
            ),
        )
        self.drain_timeout = max(
            1.0,
            float(
                os.getenv(
                    "PORTAL_EXECUTION_WORKER_DRAIN_TIMEOUT_SECONDS",
                    "60",
                )
                or 60
            ),
        )
        self.kill_grace = max(
            1.0,
            float(
                os.getenv(
                    "PORTAL_EXECUTION_WORKER_KILL_GRACE_SECONDS",
                    "10",
                )
                or 10
            ),
        )
        self._reload = threading.Event()
        self._explicit_restart = threading.Event()
        self._stopping = threading.Event()
        self._restart_requested = False
        self._sequence = 0
        self._current: WorkerProcess | None = None
        self._draining: list[WorkerProcess] = []
        self._consecutive_failures = 0
        self._failure_started_at = 0.0
        self._last_error = ""
        self._last_error_signature = ""
        self._last_failure_log_at = 0.0
        self._suppressed_failures = 0
        self._next_retry_at = 0.0
        self._next_retry_monotonic = 0.0
        self._health_write_error_signature = ""
        self._health_write_error_logged_at = 0.0
        self._health_write_suppressed = 0

    def request_reload(self, _signum: int, _frame: object) -> None:
        self._reload.set()

    def request_worker_restart(self, _signum: int, _frame: object) -> None:
        self._explicit_restart.set()
        self._reload.set()

    def request_stop(self, _signum: int, _frame: object) -> None:
        self._stopping.set()
        self._reload.set()

    @staticmethod
    def _source_digest(path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return ""

    def _manager_source_changed(self) -> bool:
        current = self._source_digest(self.manager_source_path)
        return bool(
            current
            and self.loaded_manager_source_digest
            and current != self.loaded_manager_source_digest
        ) or any(
            self._source_digest(path) != digest
            for path, digest in self.loaded_rpc_source_digests.items()
        )

    def _reload_worker(self) -> None:
        if self._manager_source_changed():
            print(
                "ExecutionWorkerManager source changed; draining before "
                "systemd restarts the manager",
                flush=True,
            )
            self._restart_requested = True
            self._stopping.set()
            return
        self._roll_out()

    def _roll_out(self, *, force: bool = False) -> bool:
        """Replace on explicit restart, or reload a different generation.

        A publish reloads both pools; replacing a Worker that already runs
        the current generation would only hand its executions over again.
        An explicit restart uses the same ready/activate/drain lifecycle.
        """
        current = self._current
        build_id = self._build_id()
        if (
            not force
            and current is not None
            and current.process.poll() is None
            and current.build_id == build_id
        ):
            print(
                f"ExecutionWorker generation {build_id} is already running; "
                "reload keeps the current Worker",
                flush=True,
            )
            return False
        self._replace()
        return True

    def _note_retired_current(self) -> None:
        """Start this pool's newest generation once its Worker was retired.

        A Worker that another pool's newer generation retired hands over and
        exits by itself.  The shared generation file then already names that
        newer build, so the replacement starts at once.  If it still names
        the retired build, the pools disagree about the generation and an
        immediate restart would take the work straight back, so back off.
        """
        current = self._current
        if (
            current is None
            or current.retirement_noted
            or current.process.poll() is None
            or not self._health(current).get("retired")
        ):
            return
        current.retirement_noted = True
        build_id = self._build_id()
        if build_id == current.build_id:
            self._record_replacement_failure(
                RuntimeError(
                    f"ExecutionWorker {current.worker_id} was retired while "
                    f"this pool's generation is still {build_id}"
                )
            )
            return
        print(
            f"ExecutionWorker {current.worker_id} was retired; starting this "
            f"pool's generation {build_id}",
            flush=True,
        )

    @staticmethod
    def _read_env_value(path: Path, key: str) -> str:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        for line in lines:
            name, separator, value = line.partition("=")
            if separator and name.strip() == key:
                return value.strip()
        return ""

    def _build_id(self) -> str:
        try:
            component_payload = json.loads(
                self.component_generations.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            component_payload = {}
        component_values = (
            component_payload.get("components")
            if isinstance(component_payload, dict)
            else {}
        )
        if isinstance(component_values, dict):
            value = str(component_values.get("execution_worker") or "").strip()
            if value:
                return value
        value = self._read_env_value(self.generation_env, "PORTAL_BUILD_ID")
        return value or str(os.getenv("PORTAL_BUILD_ID", "development"))

    @staticmethod
    def _safe_generation(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")[:80]

    def _spawn(self) -> WorkerProcess:
        build_id = self._build_id()
        self._sequence += 1
        generation = self._safe_generation(build_id) or "unversioned"
        worker_id = (
            f"{self.worker_id_prefix}:{generation}:{os.getpid()}:{self._sequence}"
        )
        health_path = self.child_health_root / (
            f"{generation}-{os.getpid()}-{self._sequence}.json"
        )
        env = dict(os.environ)
        env.update(
            {
                "PORTAL_BUILD_ID": build_id,
                "PORTAL_EXECUTION_WORKER_ID": worker_id,
                "PORTAL_EXECUTION_WORKER_HEALTH_PATH": str(health_path),
                "PORTAL_EXECUTION_WORKER_INITIAL_STATE": "standby",
                # Keep the first traceback for diagnosis, then let the manager
                # own repeated-failure summaries for the rest of the incident.
                "PORTAL_EXECUTION_WORKER_FATAL_MODE": (
                    "traceback"
                    if self._consecutive_failures == 0
                    else "silent"
                ),
            }
        )
        process = process_runtime.spawn_process(
            [sys.executable, "-m", "management_portal.execution.worker"],
            cwd=str(self.source_root),
            env=env,
        )
        return WorkerProcess(
            process=process,
            worker_id=worker_id,
            build_id=build_id,
            health_path=health_path,
            started_at=time.time(),
        )

    @staticmethod
    def _health(worker: WorkerProcess) -> dict[str, Any]:
        try:
            payload = json.loads(worker.health_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _wait_ready(self, worker: WorkerProcess) -> None:
        deadline = time.monotonic() + self.ready_timeout
        while (
            time.monotonic() < deadline
            and not self._stopping.is_set()
        ):
            return_code = worker.process.poll()
            if return_code is not None:
                raise RuntimeError(
                    f"replacement ExecutionWorker exited with code {return_code}"
                )
            health = self._health(worker)
            if (
                health.get("ok")
                and health.get("worker_id") == worker.worker_id
                and health.get("build_id") == worker.build_id
            ):
                return
            time.sleep(0.1)
        if self._stopping.is_set():
            raise InterruptedError("ExecutionWorkerManager is stopping")
        raise TimeoutError(
            f"replacement ExecutionWorker was not ready after {self.ready_timeout:g}s"
        )

    def _discard_failed_replacement(self, worker: WorkerProcess) -> None:
        try:
            process_runtime.terminate_process_tree(worker.process, timeout=self.kill_grace)
        except Exception as exc:
            worker.cleanup_error = self._compact_error(exc)
            # Retain the handle/Job and its health until a later reap can
            # prove the tree empty. A failed launch is not a cleaned launch.
            if worker not in self._draining:
                self._draining.append(worker)
            self._mark_draining(worker)
            raise
        worker.tree_stopped = True
        worker.cleanup_error = ""
        try:
            worker.health_path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _compact_error(exc: BaseException) -> str:
        detail = " ".join(str(exc).split())
        return f"{type(exc).__name__}: {detail or 'no detail'}"[:1000]

    def _retry_delay(self, failure_count: int) -> float:
        exponent = min(max(0, failure_count - 1), 20)
        delay = min(
            self.retry_max,
            self.retry_initial * (self.retry_multiplier**exponent),
        )
        if self.retry_jitter:
            delay *= random.uniform(
                1.0 - self.retry_jitter,
                1.0 + self.retry_jitter,
            )
        return max(1.0, min(self.retry_max, delay))

    def _record_replacement_failure(self, exc: BaseException) -> None:
        now_monotonic = time.monotonic()
        now = time.time()
        self._consecutive_failures += 1
        if not self._failure_started_at:
            self._failure_started_at = now
        self._last_error = self._compact_error(exc)
        delay = self._retry_delay(self._consecutive_failures)
        self._next_retry_monotonic = now_monotonic + delay
        self._next_retry_at = now + delay
        signature = self._last_error
        should_log = (
            signature != self._last_error_signature
            or now_monotonic - self._last_failure_log_at
            >= self.failure_log_interval
        )
        if not should_log:
            self._suppressed_failures += 1
            return
        suppressed = (
            f" suppressed={self._suppressed_failures}"
            if self._suppressed_failures
            else ""
        )
        print(
            "ExecutionWorker unavailable; "
            f"attempt={self._consecutive_failures} "
            f"retry_in={delay:.1f}s error={self._last_error}{suppressed}",
            file=sys.stderr,
            flush=True,
        )
        self._last_error_signature = signature
        self._last_failure_log_at = now_monotonic
        self._suppressed_failures = 0

    def _record_replacement_success(self) -> None:
        failures = self._consecutive_failures
        if failures:
            elapsed = max(0.0, time.time() - self._failure_started_at)
            suppressed = (
                f"; suppressed {self._suppressed_failures} repeated logs"
                if self._suppressed_failures
                else ""
            )
            print(
                "ExecutionWorker recovered after "
                f"{failures} failed attempt(s) over {elapsed:.1f}s{suppressed}",
                flush=True,
            )
        self._consecutive_failures = 0
        self._failure_started_at = 0.0
        self._last_error = ""
        self._last_error_signature = ""
        self._last_failure_log_at = 0.0
        self._suppressed_failures = 0
        self._next_retry_at = 0.0
        self._next_retry_monotonic = 0.0

    def _attempt_replacement(self) -> bool:
        try:
            self._replace()
        except Exception as exc:
            if self._stopping.is_set():
                return False
            self._record_replacement_failure(exc)
            self._write_health_safely()
            return False
        self._record_replacement_success()
        self._write_health_safely()
        return True

    def _mark_draining(self, worker: WorkerProcess) -> None:
        if not worker.draining_started_at:
            worker.draining_started_at = time.time()
        self._request_worker_stop(worker)

    @staticmethod
    def _request_worker_stop(worker: WorkerProcess) -> None:
        """Wake the Worker's existing durable handover path on every platform.

        Windows Popen.terminate is a hard kill, not a graceful shutdown.
        Scheduler draining wakes claims and active control waits without
        signalling Provider descendants or interrupting their original turn.
        The manager separately enforces the bounded process-tree deadline.
        """
        if worker.terminate_sent_at:
            return
        try:
            ExecutionServiceClient().request(
                "scheduler.worker.state",
                {"worker_id": worker.worker_id, "state": "draining"},
            )
        except Exception:
            return
        # Retain the health field as the time stop was requested; it does not
        # prove the process or tree has exited. Failed requests are retried.
        worker.terminate_sent_at = time.time()

    def _kill_worker(self, worker: WorkerProcess) -> None:
        if worker.tree_stopped:
            return
        if not worker.kill_sent_at:
            worker.kill_sent_at = time.time()
        try:
            process_runtime.terminate_process_tree(
                worker.process, timeout=self.kill_grace, force=True,
            )
        except Exception as exc:
            worker.cleanup_error = self._compact_error(exc)
            raise
        worker.tree_stopped = True
        worker.cleanup_error = ""

    @staticmethod
    def _handover(worker: WorkerProcess) -> dict[str, Any] | None:
        try:
            result = ExecutionServiceClient().request(
                "scheduler.worker.handover",
                {"worker_id": worker.worker_id},
            )
        except Exception:
            return None
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _activate_replacement(
        replacement: WorkerProcess,
        previous: WorkerProcess | None,
    ) -> None:
        error: Exception | None = None
        for _attempt in range(3):
            try:
                ExecutionServiceClient().request(
                    "scheduler.worker.activate",
                    {
                        "worker_id": replacement.worker_id,
                        "previous_worker_id": (
                            previous.worker_id if previous else ""
                        ),
                    },
                )
                return
            except Exception as exc:
                error = exc
                time.sleep(0.1)
        assert error is not None
        raise error

    def _replace(self) -> None:
        replacement = self._spawn()
        previous = self._current
        try:
            self._wait_ready(replacement)
            self._activate_replacement(replacement, previous)
        except Exception:
            self._discard_failed_replacement(replacement)
            raise
        self._cleanup_offline_registrations()
        self._current = replacement
        if previous is not None:
            self._mark_draining(previous)
            self._draining.append(previous)

    def _unregister(self, worker_id: str) -> bool:
        try:
            ExecutionServiceClient().request(
                "scheduler.worker.unregister",
                {"worker_id": worker_id},
            )
        except Exception:
            return False
        return True

    def _cleanup_offline_registrations(self) -> None:
        try:
            health = ExecutionServiceClient().health()
        except Exception:
            return
        scheduler = (
            health.get("scheduler")
            if isinstance(health.get("scheduler"), dict)
            else {}
        )
        workers = scheduler.get("workers")
        if not isinstance(workers, list) or any(
            not isinstance(worker, dict) or not worker.get("worker_id") for worker in workers
        ):
            return
        registered_ids = {str(worker["worker_id"]) for worker in workers}
        offline_ids = {
            str(worker.get("worker_id") or "")
            for worker in workers
            if isinstance(worker, dict)
            and worker.get("state") == "offline"
            and str(worker.get("worker_id") or "").startswith(
                f"{self.worker_id_prefix}:"
            )
        }
        removed: set[str] = set()
        for worker_id in offline_ids:
            if self._unregister(worker_id):
                removed.add(worker_id)
        managed_ids = {worker.worker_id for worker in [self._current, *self._draining] if worker is not None}
        for path in self.child_health_root.glob("*.json"):
            try:
                original = path.read_bytes()
                record = json.loads(original)
                worker_id = str(record.get("worker_id") or "")
                pid = int(record.get("pid") or 0)
                if (not worker_id.startswith(f"{self.worker_id_prefix}:")
                        or worker_id in managed_ids
                        or (worker_id in registered_ids and worker_id not in removed)
                        or pid <= 0):
                    continue
                if process_runtime.process_alive(pid):
                    continue
                # Health writers use atomic replacement; retain a newer observation.
                if path.read_bytes() == original:
                    path.unlink()
            except (OSError, ValueError, AttributeError):
                continue

    def _reap(self) -> None:
        remaining: list[WorkerProcess] = []
        for worker in self._draining:
            if worker.process.poll() is None:
                if not worker.draining_started_at:
                    worker.draining_started_at = time.time()
                self._request_worker_stop(worker)
                now = time.time()
                drain_deadline = (
                    worker.draining_started_at + self.drain_timeout
                )
                if now >= drain_deadline:
                    worker.drain_timed_out = True
                if now >= drain_deadline + self.kill_grace:
                    try:
                        self._kill_worker(worker)
                    except Exception:
                        remaining.append(worker)
                        continue
                if worker.process.poll() is None:
                    remaining.append(worker)
                    continue
            # Exiting the leader does not prove that its descendants exited.
            # Releasing Scheduler ownership before this check would permit
            # another generation to execute while the old tree is still live.
            try:
                self._kill_worker(worker)
            except Exception:
                remaining.append(worker)
                continue
            if worker.handover is None or worker.handover.get("blocked_execution_ids"):
                worker.handover = self._handover(worker)
            if worker.handover is None:
                remaining.append(worker)
                continue
            if worker.handover.get("blocked_execution_ids"):
                remaining.append(worker)
                continue
            if not self._unregister(worker.worker_id):
                remaining.append(worker)
                continue
            try:
                worker.health_path.unlink()
            except FileNotFoundError:
                pass
        self._draining = remaining

    def _worker_payload(self, worker: WorkerProcess) -> dict[str, Any]:
        health = self._health(worker)
        draining_seconds = (
            max(0.0, time.time() - worker.draining_started_at)
            if worker.draining_started_at
            else 0.0
        )
        return {
            "worker_id": worker.worker_id,
            "build_id": worker.build_id,
            "pid": worker.process.pid,
            "state": "running" if worker.process.poll() is None else "exited",
            "active_executions": list(health.get("active_executions") or []),
            "started_at": worker.started_at,
            "draining_started_at": worker.draining_started_at,
            "draining_seconds": draining_seconds,
            "drain_timeout_seconds": self.drain_timeout,
            "drain_deadline_at": (
                worker.draining_started_at + self.drain_timeout
                if worker.draining_started_at
                else 0.0
            ),
            "drain_timed_out": worker.drain_timed_out,
            "terminate_sent_at": worker.terminate_sent_at,
            "tree_stopped": worker.tree_stopped,
            "cleanup_error": worker.cleanup_error,
            "kill_sent_at": worker.kill_sent_at,
            "handover": worker.handover,
        }

    def _write_health(self) -> None:
        current = self._current
        payload = {
            "ok": (
                not self._stopping.is_set()
                and current is not None
                and current.process.poll() is None
            ),
            "schema": "tfi.execution-worker-manager-health.v2",
            "instance": self.instance,
            "manager_pid": os.getpid(),
            "explicit_restart_supported": True,
            "state": (
                "stopping"
                if self._stopping.is_set()
                else (
                    "running"
                    if current is not None
                    and current.process.poll() is None
                    else "degraded"
                )
            ),
            "current": self._worker_payload(current) if current else None,
            "draining": [self._worker_payload(item) for item in self._draining],
            "recovery": {
                "consecutive_failures": self._consecutive_failures,
                "last_error": self._last_error,
                "next_retry_at": self._next_retry_at,
            },
            "updated_at": time.time(),
        }
        atomic_replace_json(self.health_path, payload)

    def _write_health_safely(self) -> bool:
        try:
            self._write_health()
        except OSError as exc:
            now = time.monotonic()
            signature = self._compact_error(exc)
            should_log = (
                signature != self._health_write_error_signature
                or now - self._health_write_error_logged_at
                >= self.failure_log_interval
            )
            if should_log:
                suppressed = (
                    f" suppressed={self._health_write_suppressed}"
                    if self._health_write_suppressed
                    else ""
                )
                print(
                    "ExecutionWorkerManager health write failed; "
                    f"error={signature}{suppressed}",
                    file=sys.stderr,
                    flush=True,
                )
                self._health_write_error_signature = signature
                self._health_write_error_logged_at = now
                self._health_write_suppressed = 0
            else:
                self._health_write_suppressed += 1
            return False
        if self._health_write_error_signature:
            print(
                "ExecutionWorkerManager health writes recovered; "
                f"suppressed={self._health_write_suppressed}",
                flush=True,
            )
            self._health_write_error_signature = ""
            self._health_write_error_logged_at = 0.0
            self._health_write_suppressed = 0
        return True

    def run(self) -> int:
        previous = process_runtime.install_shutdown_handlers(
            self.request_stop, reload=self.request_reload,
            restart=self.request_worker_restart,
        )
        try:
            return self._run()
        finally:
            process_runtime.restore_shutdown_handlers(previous)

    def _run(self) -> int:
        try:
            while not self._stopping.is_set():
                if self._current is not None and self._current.process.poll() is not None:
                    self._note_retired_current()
                    # A crashed generation's descendants need reaping even
                    # while replacement startup is backing off or unavailable.
                    self._mark_draining(self._current)
                    self._draining.append(self._current)
                    self._current = None
                self._reap()
                current_running = (
                    self._current is not None
                    and self._current.process.poll() is None
                )
                if not current_running:
                    now = time.monotonic()
                    if now >= self._next_retry_monotonic:
                        self._attempt_replacement()
                        continue
                    wait_seconds = min(
                        0.5,
                        max(0.05, self._next_retry_monotonic - now),
                    )
                else:
                    self._write_health_safely()
                    wait_seconds = 0.5
                self._reload.wait(wait_seconds)
                if self._stopping.is_set():
                    break
                if self._reload.is_set():
                    self._reload.clear()
                    force = self._explicit_restart.is_set()
                    self._explicit_restart.clear()
                    current_running = (
                        self._current is not None
                        and self._current.process.poll() is None
                    )
                    if self._manager_source_changed():
                        self._reload_worker()
                    elif current_running:
                        try:
                            rolled_out = self._roll_out(force=force)
                        except Exception as exc:
                            print(
                                "ExecutionWorker rollout failed; keeping "
                                f"current generation: {self._compact_error(exc)}",
                                file=sys.stderr,
                                flush=True,
                            )
                        else:
                            if rolled_out:
                                self._write_health_safely()
                    else:
                        self._next_retry_monotonic = 0.0
        finally:
            workers = [
                item
                for item in [self._current, *self._draining]
                if item is not None
            ]
            for worker in workers:
                self._mark_draining(worker)
            self._current = None
            self._draining = list(
                {worker.worker_id: worker for worker in workers}.values()
            )
            deadline = (
                time.monotonic() + self.drain_timeout + self.kill_grace
            )
            while self._draining and time.monotonic() < deadline:
                self._reap()
                if self._draining:
                    time.sleep(0.25)
            for worker in self._draining:
                if not worker.tree_stopped:
                    worker.drain_timed_out = True
                    try:
                        self._kill_worker(worker)
                    except Exception:
                        # Continue cleaning the other generations; retain this
                        # one's failure and report unsuccessful shutdown below.
                        pass
            self._reap()
            self._write_health_safely()
            uncleared = [worker.worker_id for worker in self._draining if not worker.tree_stopped]
            if uncleared:
                raise RuntimeError(f"Worker process trees did not stop: {', '.join(uncleared)}")
        return 75 if self._restart_requested else 0


def main() -> int:
    return ExecutionWorkerManager().run()


if __name__ == "__main__":
    raise SystemExit(main())
