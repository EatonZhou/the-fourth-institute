from __future__ import annotations

import logging
import os
import queue
import threading
import time
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import execution_state_root, portal_instance
from .contracts import (
    ExecutionRecoveryDeferred,
    ExecutionRequest,
    ExecutionResult,
)
from .runtime import ExecutionCancelled, ExecutionRuntime, RuntimeContext
from .scheduler import SchedulerClient
from .transport import (
    ExecutionClaimOwnershipLost,
    ExecutionResponseUnknown,
    ExecutionServiceClient,
    ExecutionServiceUnavailable,
)

from management_common.instance_paths import instance_root
from management_common import loaded_modules, process_runtime
from management_portal.shared_state import atomic_replace_json

LOGGER = logging.getLogger(__name__)


class ExecutionWorker:
    """Drive one generic execution lifecycle at a time."""

    def __init__(
        self,
        *,
        build_id: str,
        worker_id: str,
        scheduler: SchedulerClient | None = None,
        runtime: ExecutionRuntime | None = None,
        state_root: Path | str | None = None,
        shutdown_requested: Callable[[], bool] | None = None,
    ) -> None:
        state_root = state_root or execution_state_root()
        self.build_id = str(build_id or "").strip()
        self.worker_id = str(worker_id or "").strip()
        self.runtime = runtime or ExecutionRuntime(
            state_root=state_root,
        )
        self.scheduler = scheduler or SchedulerClient(
            worker_id=self.worker_id,
            build_id=self.build_id,
            capabilities=self.runtime.capabilities(),
            pid=os.getpid(),
        )
        self.shutdown_requested = shutdown_requested or (lambda: False)

    def register(self, state: str = "active") -> dict[str, Any]:
        return self.scheduler.register(state)

    def claim(
        self,
        lease_seconds: int = 270,
        *,
        wait_seconds: float = 15.0,
    ) -> dict[str, Any]:
        return self.scheduler.claim(
            lease_seconds,
            wait_seconds=wait_seconds,
        )

    def execute_claim(
        self,
        claim: dict[str, Any],
        *,
        lease_seconds: int = 270,
    ) -> dict[str, Any]:
        return _ClaimedExecution(self, claim, lease_seconds=lease_seconds).run()


_CLAIM_LOST_REASON = "execution claim ownership changed"
_HANDOVER_REASON = "worker generation handover"


class _ClaimedExecution:
    """One claimed Execution in this Worker, from start to its terminal report.

    A heartbeat thread keeps the lease and relays the Scheduler's controls
    (cancellation, handover, turn input) while the runtime executes the
    request; every Scheduler write is scoped to the claim and fenced when the
    claim is lost.
    """

    def __init__(
        self,
        worker: ExecutionWorker,
        claim: dict[str, Any],
        *,
        lease_seconds: int,
    ) -> None:
        execution = (
            claim.get("execution")
            if isinstance(claim.get("execution"), dict)
            else {}
        )
        self._worker = worker
        self._execution = execution
        self.execution_id = str(execution.get("execution_id") or "")
        self.claim_token = str(claim.get("claim_token") or "")
        self.recovery_checkpoint = (
            dict(execution.get("checkpoint"))
            if isinstance(execution.get("checkpoint"), dict)
            else {}
        )
        if not self.execution_id or not self.claim_token:
            raise ValueError("claimed execution identity is incomplete")
        self.lease_seconds = lease_seconds
        self.cancelled = threading.Event()
        self.heartbeat_stop = threading.Event()
        self.ownership_lost = threading.Event()
        self.planned_handover = threading.Event()
        self._turn_controls: queue.Queue[dict[str, Any]] = queue.Queue()
        self._seen_turn_controls: set[str] = set()
        self._turn_control_lock = threading.Lock()
        self._receipt_lock = threading.Lock()
        self._checkpoint_lock = threading.Lock()
        self.lease_deadline = time.monotonic() + float(lease_seconds)
        self._queue_wait_ms = _elapsed_since_iso_ms(str(execution.get("created_at") or ""))
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            name=f"execution-heartbeat-{self.execution_id}",
            daemon=True,
        )

    def run(self) -> dict[str, Any]:
        self._heartbeat.start()
        result, deferred_reason = self._execute()
        if self.planned_handover.is_set() and not deferred_reason:
            released = self._release_for_handover()
            if released is not None:
                return released
        if self.ownership_lost.is_set():
            self._heartbeat.join(timeout=6)
            return self._outcome("claim_lost", _CLAIM_LOST_REASON)
        if deferred_reason:
            self._heartbeat.join(timeout=6)
            return self._outcome("requeued", deferred_reason)
        if result is None:
            self._heartbeat.join(timeout=6)
            raise RuntimeError("execution produced no terminal result")
        return self._finish(result)

    def fence(self, *, interrupt_provider: bool = True) -> None:
        # The first fence decides about the Provider turn. Later lease checks
        # must not interrupt it again, nor a turn a handover left running.
        if self.heartbeat_stop.is_set() or self.ownership_lost.is_set():
            return
        self.ownership_lost.set()
        self.cancelled.set()
        try:
            if interrupt_provider:
                self._worker.runtime.interrupt(self.execution_id)
        except Exception:
            pass

    def claim_scoped(self, call: Any, *, retry_unknown: bool = False) -> Any:
        # A missing service socket is not a Provider failure. Retain the
        # operation while the independent heartbeat maintains our lease.
        # Only idempotent writes may be replayed after a lost response;
        # an event append, for example, could already have been accepted.
        while True:
            if time.monotonic() >= self.lease_deadline:
                self.fence()
            if self.ownership_lost.is_set():
                raise ExecutionClaimOwnershipLost(_CLAIM_LOST_REASON)
            try:
                return call()
            except ExecutionClaimOwnershipLost:
                self.fence()
                raise
            except ExecutionResponseUnknown:
                if not retry_unknown:
                    raise
            except ExecutionServiceUnavailable:
                pass
            self.ownership_lost.wait(max(0.0, min(0.25, self.lease_deadline - time.monotonic())))

    def _execute(self) -> tuple[ExecutionResult | None, str]:
        """Run the request; the terminal result, or why the claim was given back."""

        scheduler = self._worker.scheduler
        result: ExecutionResult | None = None
        deferred_reason = ""
        try:
            self.claim_scoped(
                lambda: scheduler.start(self.execution_id, self.claim_token),
                retry_unknown=True,
            )
            request = self._request()
            backend_result = self._worker.runtime.execute(
                request,
                self._runtime_context(),
            )
            result = ExecutionResult(
                execution_id=self.execution_id,
                status="completed",
                output=dict(backend_result.output),
                backend_session_id=backend_result.backend_session_id,
                backend_route_id=backend_result.backend_route_id,
                usage=dict(backend_result.usage),
            )
        except ExecutionRecoveryDeferred as exc:
            deferred_reason = self._defer(exc)
        except (ExecutionCancelled, InterruptedError) as exc:
            result = ExecutionResult(
                execution_id=self.execution_id,
                status="cancelled",
                error=str(exc),
            )
        except ExecutionClaimOwnershipLost:
            self.ownership_lost.set()
        except BaseException as exc:
            result = _failed_result(self.execution_id, exc)
        finally:
            # A Provider call can end while its input pump still persists a
            # receipt. Keep the lease alive until that receipt is acknowledged.
            with self._receipt_lock:
                pass
            self.heartbeat_stop.set()
        return result, deferred_reason

    def _request(self) -> ExecutionRequest:
        request = ExecutionRequest.from_payload(self._execution.get("request"))
        if request.execution_id != self.execution_id:
            raise ValueError("claimed execution identity is incomplete")
        return replace(
            request,
            metadata={
                **dict(request.metadata or {}),
                "execution_queue_wait_ms": self._queue_wait_ms,
            },
        )

    def _runtime_context(self) -> RuntimeContext:
        return RuntimeContext(
            accepted_at=str(self._execution.get("created_at") or ""),
            emit_event=self._emit_event,
            cancellation_requested=self.cancelled.is_set,
            handover_requested=self.ownership_lost.is_set,
            reassign_backend_route=self._reassign_backend_route,
            checkpoint_provider=self._checkpoint,
            recovery_checkpoint=self.recovery_checkpoint,
            turn_control_provider=self._next_turn_control,
            turn_control_result_callback=self._record_turn_control_result,
        )

    def _defer(self, exc: ExecutionRecoveryDeferred) -> str:
        """Give the claim back for a later attempt; return the reason."""

        with self._receipt_lock:
            pass
        self.heartbeat_stop.set()
        if self.planned_handover.is_set():
            self._heartbeat.join()
        deferred_reason = (
            _HANDOVER_REASON
            if self.planned_handover.is_set()
            else str(exc or "provider recovery deferred")
        )
        try:
            retry_after_seconds = float(
                getattr(exc, "retry_after_seconds", 0) or 0
            )
            if retry_after_seconds > 0:
                self._worker.scheduler.requeue(
                    self.execution_id,
                    self.claim_token,
                    deferred_reason,
                    retry_after_seconds=retry_after_seconds,
                )
            else:
                self._worker.scheduler.requeue(
                    self.execution_id,
                    self.claim_token,
                    deferred_reason,
                )
            if self.planned_handover.is_set():
                # A successful fenced requeue released our own claim. The
                # local detach flag is not evidence that another owner stole it.
                self.ownership_lost.clear()
        except ExecutionClaimOwnershipLost:
            self.ownership_lost.set()
        return deferred_reason

    def _release_for_handover(self) -> dict[str, Any] | None:
        """Requeue for the next Worker generation; None when the claim is gone."""

        self._heartbeat.join()
        try:
            released = self._worker.scheduler.requeue(
                self.execution_id, self.claim_token, _HANDOVER_REASON,
            )
        except (ExecutionClaimOwnershipLost, ExecutionServiceUnavailable):
            self.ownership_lost.set()
            return None
        return self._outcome(
            "cancelled" if released.get("status") == "cancelled" else "requeued",
            _HANDOVER_REASON,
        )

    def _finish(self, result: ExecutionResult) -> dict[str, Any]:
        try:
            while True:
                try:
                    snapshot = self._worker.scheduler.finish(result, self.claim_token)
                    break
                except (ExecutionServiceUnavailable, ExecutionResponseUnknown):
                    # Keep the already-produced result across a rolling socket
                    # replacement. Retry only this fenced terminal write: never
                    # execute the accepted Provider input again. A lost reply
                    # may mean finish committed; its cleared claim then fences
                    # the next attempt without a second terminal transition.
                    remaining = self.lease_deadline - time.monotonic()
                    if remaining <= 0:
                        raise
                    if self.ownership_lost.wait(min(0.25, remaining)):
                        raise ExecutionClaimOwnershipLost(_CLAIM_LOST_REASON)
                    if time.monotonic() >= self.lease_deadline:
                        raise
        except ExecutionClaimOwnershipLost:
            return self._outcome("claim_lost", _CLAIM_LOST_REASON)
        finally:
            # Publishing the terminal state wakes the blocking control wait,
            # so the heartbeat can be reclaimed without paying its join
            # timeout on every successful execution.
            self._heartbeat.join(timeout=6)
        return {
            "execution_id": self.execution_id,
            "status": result.status,
            "result": result.to_payload(),
            "snapshot": snapshot,
        }

    def _outcome(self, status: str, reason: str) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "status": status,
            "reason": reason,
        }

    def _heartbeat_loop(self) -> None:
        heartbeat_interval = max(5.0, min(30.0, float(self.lease_seconds) / 3))
        while not self.heartbeat_stop.is_set():
            try:
                if self._heartbeat_once(heartbeat_interval):
                    return
            except ExecutionClaimOwnershipLost:
                self.fence()
                return
            except (ExecutionServiceUnavailable, ExecutionResponseUnknown):
                if time.monotonic() >= self.lease_deadline:
                    self.fence()
                    return
                if self.heartbeat_stop.wait(1.0):
                    return
            except BaseException:
                self.fence()
                return

    def _heartbeat_once(self, heartbeat_interval: float) -> bool:
        """Relay one control wait and renew the lease; True ends the heartbeat."""

        worker = self._worker
        if not self.cancelled.is_set() and worker.shutdown_requested() and self._prepare_handover():
            return True
        if self.cancelled.is_set():
            if self.heartbeat_stop.wait(heartbeat_interval):
                return True
            status = {}
        else:
            status = worker.scheduler.wait_for_control(
                self.execution_id, self.claim_token,
                timeout_seconds=min(heartbeat_interval, 0.25) if worker.shutdown_requested() else heartbeat_interval,
            )
        if self.heartbeat_stop.is_set():
            return True
        if status.get("cancel_requested"):
            self.cancelled.set()
        interrupt_attempted = self.cancelled.is_set()
        if interrupt_attempted:
            self._interrupt_cancelled_turn()
        if not self.cancelled.is_set() and (worker.shutdown_requested() or status.get("handover_requested")):
            if self._prepare_handover():
                return True
        elif not self.cancelled.is_set():
            self._enqueue_turn_controls(status.get("turn_controls"))
        if time.monotonic() >= self.lease_deadline:
            self.fence()
            return True
        status = worker.scheduler.heartbeat(
            self.execution_id,
            self.claim_token,
            self.lease_seconds,
        )
        self.lease_deadline = time.monotonic() + float(self.lease_seconds)
        if status.get("cancel_requested"):
            self.cancelled.set()
            if not interrupt_attempted:
                self._interrupt_cancelled_turn()
        return False

    def _interrupt_cancelled_turn(self) -> None:
        # The cancellation flag records intent, not delivery. A lost reply or
        # a Provider still starting must be retried while this Worker owns
        # the execution, until execute() actually returns.
        try:
            self._worker.runtime.interrupt(self.execution_id)
        except Exception:
            LOGGER.warning("Cancellation interrupt failed for %s; will retry", self.execution_id)

    def _prepare_handover(self) -> bool:
        status = self._worker.scheduler.prepare_handover(self.execution_id, self.claim_token)
        if status.get("cancel_requested"):
            self.cancelled.set()
            return False
        if not status.get("ready"):
            self.heartbeat_stop.wait(0.25)
            return False
        self.planned_handover.set()
        self.fence(interrupt_provider=bool(status.get("interrupt_required", True)))
        return True

    def _enqueue_turn_controls(self, payload: object) -> None:
        controls = payload if isinstance(payload, list) else []
        with self._turn_control_lock:
            for control in controls:
                if not isinstance(control, dict):
                    continue
                control_id = str(control.get("control_id") or "").strip()
                if not control_id or control_id in self._seen_turn_controls:
                    continue
                self._seen_turn_controls.add(control_id)
                self._turn_controls.put(dict(control))

    def _next_turn_control(self) -> dict[str, Any] | None:
        try:
            return self._turn_controls.get_nowait()
        except queue.Empty:
            return None

    def _record_turn_control_result(
        self,
        control: dict[str, Any],
        delivered: bool,
        error: str,
    ) -> None:
        control_id = str(control.get("control_id") or "").strip()
        if not control_id:
            return
        # Retain the Provider's answer across transport failures. Replaying
        # this idempotent receipt must never deliver the input a second time.
        with self._receipt_lock:
            while not self.ownership_lost.is_set():
                try:
                    self.claim_scoped(lambda: self._worker.scheduler.complete_input_control(
                        self.execution_id, self.claim_token, control_id,
                        delivered=delivered, error=error,
                    ))
                    return
                except (ExecutionServiceUnavailable, ExecutionResponseUnknown, OSError):
                    self.ownership_lost.wait(0.25)

    def _checkpoint(self, checkpoint: dict[str, Any]) -> None:
        payload = deepcopy(checkpoint)
        # Keep an interrupted write ahead of subsequent snapshots. A late
        # retry must not replace a newer checkpoint from another callback.
        with self._checkpoint_lock:
            self.claim_scoped(
                lambda: self._worker.scheduler.checkpoint(self.execution_id, self.claim_token, payload),
                retry_unknown=True,
            )

    def _emit_event(self, event_type: str, details: dict[str, Any]) -> None:
        if event_type == "provider_event" and self.planned_handover.is_set():
            details = {**details, "interruption_reason": "worker_handover"}
        self.claim_scoped(
            lambda: self._worker.scheduler.emit(
                self.execution_id,
                self.claim_token,
                event_type,
                details,
            )
        )

    def _reassign_backend_route(self, backend_route_id: str) -> Any:
        return self.claim_scoped(
            lambda: self._worker.scheduler.reassign_capacity(
                self.execution_id,
                self.claim_token,
                backend_route_id,
            )
        )


def _failed_result(execution_id: str, exc: BaseException) -> ExecutionResult:
    error_details = getattr(exc, "error_details", {})
    return ExecutionResult(
        execution_id=execution_id,
        status="failed",
        error=str(exc),
        error_code=str(getattr(exc, "error_code", "") or ""),
        error_details=dict(error_details) if isinstance(error_details, dict) else {},
    )


class ExecutionWorkerDaemon:
    def __init__(self) -> None:
        self.instance = (
            os.getenv("PORTAL_INSTANCE", portal_instance()).strip()
            or portal_instance()
        )
        self.build_id = (
            os.getenv("PORTAL_BUILD_ID", "development").strip()
            or "development"
        )
        self.worker_id = (
            os.getenv(
                "PORTAL_EXECUTION_WORKER_ID",
                f"{self.instance}:execution-worker",
            ).strip()
            or f"{self.instance}:execution-worker"
        )
        self._configured_max_parallel = int(
            os.getenv("PORTAL_EXECUTION_WORKER_PARALLELISM", "0")
        )
        self.max_parallel = max(1, self._configured_max_parallel)
        self._stopping = threading.Event()
        state_root = execution_state_root()
        self.worker = ExecutionWorker(
            build_id=self.build_id,
            worker_id=self.worker_id,
            state_root=state_root,
            shutdown_requested=self._stopping.is_set,
        )
        self.health_path = Path(
            os.getenv(
                "PORTAL_EXECUTION_WORKER_HEALTH_PATH",
                (
                    f'{instance_root()}/{self.instance}/execution-worker-health.json'
                ),
            )
        )
        self._active: dict[str, dict[str, Any]] = {}
        self._active_lock = threading.RLock()
        self._active_changed = threading.Condition(self._active_lock)
        self._health_lock = threading.Lock()
        self._service_error = ""
        self.drain_timeout = max(
            1.0,
            float(
                os.getenv(
                    "PORTAL_EXECUTION_WORKER_DRAIN_TIMEOUT_SECONDS",
                    "180",
                )
                or 180
            ),
        )
        self._draining_started_at = 0.0
        self._drain_timed_out = False
        self._retired = False

    def request_stop(self, _signum: int, _frame: object) -> None:
        if not self._draining_started_at:
            self._draining_started_at = time.time()
        self._stopping.set()
        with self._active_changed:
            self._active_changed.notify_all()

    def _leave_retired(self) -> None:
        """The scheduler retired this Worker (usually another generation took over).

        A retired Worker never claims again, so it hands over what it still
        runs and exits; the WorkerManager then starts its pool's newest
        generation instead of keeping an idle process.
        """
        if not self._retired:
            self._retired = True
            print(
                f"ExecutionWorker {self.worker_id} no longer claims work; "
                "leaving after handover",
                flush=True,
            )
        self.request_stop(0, None)

    def _active_count(self) -> int:
        with self._active_lock:
            return len(self._active)

    def _write_health(self) -> None:
        with self._health_lock:
            with self._active_lock:
                active = [
                    {
                        key: value
                        for key, value in item.items()
                        if not key.startswith("_")
                    }
                    for item in self._active.values()
                ]
            payload = {
                "ok": not self._stopping.is_set(),
                "schema": "tfi.execution-worker-health.v1",
                "instance": self.instance,
                "build_id": self.build_id,
                "worker_id": self.worker_id,
                "pid": os.getpid(),
                "capabilities": self.worker.runtime.capabilities(),
                "max_parallel": self.max_parallel,
                "active_executions": active,
                "state": (
                    "draining" if self._stopping.is_set() else "active"
                ),
                "retired": self._retired,
                "draining_started_at": self._draining_started_at,
                "draining_seconds": (
                    max(0.0, time.time() - self._draining_started_at)
                    if self._draining_started_at
                    else 0.0
                ),
                "drain_timeout_seconds": self.drain_timeout,
                "drain_deadline_at": (
                    self._draining_started_at + self.drain_timeout
                    if self._draining_started_at
                    else 0.0
                ),
                "drain_timed_out": self._drain_timed_out,
                "execution_service": {
                    "ok": not bool(self._service_error),
                    "error": self._service_error,
                },
                "updated_at": time.time(),
            }
            atomic_replace_json(self.health_path, payload)

    def _execute(self, claim: dict[str, Any], claim_marker: object) -> None:
        execution = (
            claim.get("execution")
            if isinstance(claim.get("execution"), dict)
            else {}
        )
        execution_id = str(execution.get("execution_id") or "")
        try:
            self.worker.execute_claim(claim)
        except Exception as exc:
            print(
                (
                    "ExecutionWorker could not report a terminal result: "
                    f"execution_id={execution_id}; error={exc}"
                ),
                flush=True,
            )
        finally:
            with self._active_changed:
                active = self._active.get(execution_id)
                if (
                    active is not None
                    and active.get("_claim_marker") is claim_marker
                ):
                    self._active.pop(execution_id, None)
                self._active_changed.notify_all()
            self._write_health()

    def _fill_slots(self) -> bool:
        claimed_any = False
        while (
            not self._stopping.is_set()
            and self._active_count() < self.max_parallel
        ):
            claim = self.worker.claim(
                wait_seconds=(
                    30.0
                    if not claimed_any
                    else 0.1
                )
            )
            if not claim.get("claimed"):
                reason = claim.get("reason")
                if reason == "worker_not_registered" or (
                    reason == "worker_not_active"
                    and claim.get("worker_state") in {"draining", "offline"}
                ):
                    self._leave_retired()
                elif reason == "worker_not_active":
                    self._stopping.wait(1.0)
                break
            execution = (
                claim.get("execution")
                if isinstance(claim.get("execution"), dict)
                else {}
            )
            execution_id = str(execution.get("execution_id") or "")
            claim_marker = object()
            with self._active_lock:
                self._active[execution_id] = {
                    "_claim_marker": claim_marker,
                    "execution_id": execution_id,
                    "capability": str(
                        execution.get("capability") or ""
                    ),
                    "started_at": time.time(),
                }
            threading.Thread(
                target=self._execute,
                args=(claim, claim_marker),
                name=f"execution-{execution_id}",
                daemon=True,
            ).start()
            claimed_any = True
        return claimed_any

    def _fill_slots_resilient(self) -> bool:
        try:
            worked = self._fill_slots()
        except (ExecutionServiceUnavailable, ExecutionResponseUnknown) as exc:
            # A service replacement may lose a claim reply. Keep existing
            # executions and their heartbeat owners alive; an unobserved claim
            # expires through the Scheduler's lease recovery.
            self._service_error = str(exc)[:1000]
            return False
        self._service_error = ""
        return worked

    def _wait_for_service(
        self,
        timeout_seconds: float = 90.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, timeout_seconds)
        last_error = ""
        client = ExecutionServiceClient()
        while (
            time.monotonic() < deadline
            and not self._stopping.is_set()
        ):
            try:
                health = client.health()
                if health.get("ok"):
                    return health
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.25)
        raise RuntimeError(
            f"ExecutionService did not become ready: {last_error}"
        )

    def run(self) -> int:
        previous = process_runtime.install_shutdown_handlers(self.request_stop)
        try:
            return self._run()
        finally:
            process_runtime.restore_shutdown_handlers(previous)

    def _run(self) -> int:
        service_health = self._wait_for_service()
        if self._configured_max_parallel < 1:
            scheduler_health = (
                service_health.get("scheduler")
                if isinstance(service_health.get("scheduler"), dict)
                else {}
            )
            self.max_parallel = max(
                1,
                int(scheduler_health.get("total_capacity") or 1),
            )
        initial_state = str(
            os.environ["PORTAL_EXECUTION_WORKER_INITIAL_STATE"]
            or "active"
        ).strip()
        self.worker.register(initial_state)
        self._write_health()
        try:
            while not self._stopping.is_set():
                with self._active_changed:
                    while (
                        not self._stopping.is_set()
                        and len(self._active) >= self.max_parallel
                    ):
                        self._active_changed.wait()
                if self._stopping.is_set():
                    break
                worked = self._fill_slots_resilient()
                self._write_health()
                if not worked and self._service_error:
                    self._stopping.wait(1.0)
        finally:
            if not self._draining_started_at:
                self._draining_started_at = time.time()
            try:
                self.worker.scheduler.set_state("draining")
            except Exception:
                pass
            deadline = time.monotonic() + self.drain_timeout
            with self._active_changed:
                while self._active and (
                    time.monotonic() < deadline
                ):
                    self._active_changed.wait(
                        max(0.0, deadline - time.monotonic())
                    )
                if self._active:
                    self._drain_timed_out = True
            try:
                self.worker.scheduler.set_state("offline")
            except Exception:
                pass
            self._write_health()
        return 0


def _elapsed_since_iso_ms(value: str) -> float:
    try:
        created = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(
            0.0,
            (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
            * 1000,
        )
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    loaded_modules.track()
    try:
        return ExecutionWorkerDaemon().run()
    except Exception:
        if os.getenv("PORTAL_EXECUTION_WORKER_FATAL_MODE") == "silent":
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
