"""The execution Scheduler, composed of its concerns, and the Worker's client of it."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from management_common.portal_database import portal_database

from .contracts import (
    ExecutionInputControl,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSnapshot,
    ExecutionTurnSettingsControl,
)
from .scheduler_admission import Admission
from .scheduler_capacity import ActiveRequests, SchedulerCapacity
from .scheduler_changes import SchedulerChanges
from .scheduler_claims import Claims
from .scheduler_controls import TurnControls
from .scheduler_health import SchedulerHealth
from .scheduler_leases import Leases
from .scheduler_queue import ExecutionQueue
from .scheduler_quota import SchedulerQuota
from .scheduler_recovery import LeaseRecovery
from .scheduler_schema import initialize_execution_store
from .scheduler_settings import SchedulerSettings
from .scheduler_usage import TokenUsage
from .scheduler_views import SchedulerViews
from .scheduler_workers import WorkerRegistry
from .transport import CONTROL_REQUEST_TIMEOUT_SECONDS, ExecutionServiceClient


DEFAULT_WORKER_HEARTBEAT_TIMEOUT_SECONDS = 180.0


class Scheduler:
    """Persistent control-plane state and scheduling policy.

    Each concern is a component of its own ``scheduler_*`` module; the
    Scheduler composes them over one database and is their published
    interface.
    """

    def __init__(
        self,
        *,
        max_concurrency: int = 8,
        capacity_limits: dict[str, int] | None = None,
        default_lease_seconds: int = 270,
        worker_heartbeat_timeout_seconds: float = (
            DEFAULT_WORKER_HEARTBEAT_TIMEOUT_SECONDS
        ),
        dispatch_interval_min_seconds: float = 2.0,
        dispatch_interval_max_seconds: float = 6.0,
        quota_reserve_enabled: bool = False,
        accounts=None,
    ) -> None:
        from .accounts import default_execution_accounts
        self.accounts = accounts or default_execution_accounts()
        self.database = portal_database(schema="execution_runtime")
        self.changes = SchedulerChanges(self.database)
        self.active_requests = ActiveRequests()
        self.capacity = SchedulerCapacity(
            self.accounts, max_concurrency=max_concurrency, capacity_limits=capacity_limits,
            dispatch_interval_min_seconds=dispatch_interval_min_seconds,
            dispatch_interval_max_seconds=dispatch_interval_max_seconds,
        )
        lease_seconds = max(15, int(default_lease_seconds))
        self.quota = SchedulerQuota(
            self.accounts, changes=self.changes, capacity=self.capacity, active_requests=self.active_requests,
            quota_reserve_enabled=quota_reserve_enabled,
        )
        self.settings = SchedulerSettings(self.accounts, changes=self.changes, capacity=self.capacity,
                                          quota=self.quota)
        self.admission = Admission(self.accounts, capacity=self.capacity, quota=self.quota,
                                   active_requests=self.active_requests)
        self.usage = TokenUsage(self.accounts, changes=self.changes)
        self.recovery = LeaseRecovery(self.changes)
        self.workers = WorkerRegistry(self.changes, self.recovery,
                                      worker_heartbeat_timeout_seconds=worker_heartbeat_timeout_seconds)
        self.views = SchedulerViews(self.changes, self.admission)
        self.queue = ExecutionQueue(self.accounts, changes=self.changes, capacity=self.capacity,
                                    recovery=self.recovery, views=self.views)
        self.claims = Claims(self.changes, self.capacity, self.admission, self.active_requests,
                             default_lease_seconds=lease_seconds)
        self.leases = Leases(
            self.accounts, changes=self.changes, capacity=self.capacity, quota=self.quota,
            active_requests=self.active_requests, usage=self.usage, recovery=self.recovery, views=self.views,
            default_lease_seconds=lease_seconds,
        )
        self.controls = TurnControls(self.changes)
        self.health_report = SchedulerHealth(self.database, changes=self.changes, capacity=self.capacity,
                                             quota=self.quota, workers=self.workers)
        with self.changes.connect() as connection:
            initialize_execution_store(connection)

    @property
    def quota_capacity_keys(self):
        return self.quota.quota_capacity_keys

    @property
    def quota_reserve_enabled(self) -> bool:
        return self.quota.quota_reserve_enabled

    # Capacity and quota settings

    def binding_quota_states(self, bindings: Mapping[str, str]) -> dict[str, Any]:
        return self.settings.binding_quota_states(bindings)

    def configure_binding_quota_overrides(self, policies: list[dict[str, Any]]) -> dict[str, Any]:
        return self.settings.configure_binding_quota_overrides(policies)

    def forget_binding_quota_overrides(self, binding_ids: list[str]) -> dict[str, Any]:
        return self.settings.forget_binding_quota_overrides(binding_ids)

    def update_account_quota(self, account_id: str, sample: dict[str, Any]) -> None:
        self.settings.update_account_quota(account_id, sample)

    def configure_capacity(
        self,
        capacity_key: str,
        limit: int,
        dispatch_interval_min_seconds: float,
        dispatch_interval_max_seconds: float,
        *,
        actor: str,
        close_threshold: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.settings.configure_capacity(
            capacity_key, limit, dispatch_interval_min_seconds, dispatch_interval_max_seconds,
            actor=actor, close_threshold=close_threshold,
        )

    # Token usage

    def record_token_usage_delta(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        event_id: str,
        usage: object,
        occurred_at: str = "",
    ) -> dict[str, Any]:
        return self.usage.record_token_usage_delta(
            execution_id, worker_id=worker_id, claim_token=claim_token, event_id=event_id,
            usage=usage, occurred_at=occurred_at,
        )

    def usage_watermark(self, key: str) -> str:
        return self.usage.usage_watermark(key)

    def latest_runtime_root(self, binding_ids: list[str]) -> str:
        return self.usage.latest_runtime_root(binding_ids)

    def earliest_unfinished_execution(self, resident_id: str) -> str:
        return self.usage.earliest_unfinished_execution(resident_id)

    def record_unattributed_token_usage(
        self,
        key: str,
        *,
        expected: str,
        watermark: str,
        request: dict[str, Any],
        requests: list[tuple[str, object, str]],
    ) -> int:
        return self.usage.record_unattributed_token_usage(
            key, expected=expected, watermark=watermark, request=request, requests=requests,
        )

    def token_usage(
        self,
        *,
        start_hour: str,
        end_hour: str,
        provider: str = "codex",
        include_accounts: bool = False,
    ) -> dict[str, Any]:
        return self.usage.token_usage(
            start_hour=start_hour, end_hour=end_hour, provider=provider,
            include_accounts=include_accounts,
        )

    # Queue

    def submit(self, request: ExecutionRequest, *, prepare=None) -> dict[str, Any]:
        return self.queue.submit(request, prepare=prepare)

    def withdraw_unstarted(
        self, execution_id: str, *, actor: str, binding_id: str,
        operation_id: str, replacement_execution_id: str, performed_by: str,
    ) -> dict[str, Any]:
        return self.queue.withdraw_unstarted(
            execution_id, actor=actor, binding_id=binding_id, operation_id=operation_id,
            replacement_execution_id=replacement_execution_id, performed_by=performed_by,
        )

    def set_priority(
        self,
        execution_id: str,
        *,
        actor: str,
        priority: int,
        performed_by: str = "",
    ) -> dict[str, Any]:
        return self.queue.set_priority(
            execution_id, actor=actor, priority=priority, performed_by=performed_by,
        )

    def reassign_queued_route(
        self,
        execution_id: str,
        *,
        actor: str,
        backend_route_id: str,
        performed_by: str = "",
    ) -> dict[str, Any]:
        return self.queue.reassign_queued_route(
            execution_id, actor=actor, backend_route_id=backend_route_id,
            performed_by=performed_by,
        )

    def cancel(
        self,
        execution_id: str,
        *,
        actor: str,
        reason: str = "",
        performed_by: str = "",
        queued_only: bool = False,
    ) -> dict[str, Any]:
        return self.queue.cancel(
            execution_id, actor=actor, reason=reason, performed_by=performed_by,
            queued_only=queued_only,
        )

    def acknowledge(self, execution_id: str, *, actor: str) -> dict[str, Any]:
        return self.queue.acknowledge(execution_id, actor=actor)

    def prune_terminal(
        self,
        *,
        older_than_epoch: float,
        unacknowledged_older_than_epoch: float | None = None,
    ) -> int:
        return self.queue.prune_terminal(
            older_than_epoch=older_than_epoch,
            unacknowledged_older_than_epoch=unacknowledged_older_than_epoch,
        )

    # Workers

    def register_worker(
        self,
        *,
        worker_id: str,
        build_id: str,
        capabilities: list[str],
        pid: int = 0,
        state: str = "active",
    ) -> dict[str, Any]:
        return self.workers.register_worker(
            worker_id=worker_id, build_id=build_id, capabilities=capabilities, pid=pid,
            state=state,
        )

    def activate_worker(
        self,
        worker_id: str,
        *,
        previous_worker_id: str = "",
    ) -> dict[str, Any]:
        return self.workers.activate_worker(worker_id, previous_worker_id=previous_worker_id)

    def set_worker_state(self, worker_id: str, state: str) -> None:
        self.workers.set_worker_state(worker_id, state)

    def prepare_handover(
        self, execution_id: str, *, worker_id: str, claim_token: str,
    ) -> dict[str, Any]:
        return self.workers.prepare_handover(
            execution_id, worker_id=worker_id, claim_token=claim_token,
        )

    def handover_worker(self, worker_id: str) -> dict[str, Any]:
        return self.workers.handover_worker(worker_id)

    def unregister_worker(self, worker_id: str) -> bool:
        return self.workers.unregister_worker(worker_id)

    def maintain(self) -> dict[str, int]:
        return self.workers.maintain()

    # Claims

    def claim(
        self,
        *,
        worker_id: str,
        capabilities: list[str],
        lease_seconds: int | None = None,
    ) -> dict[str, Any]:
        return self.claims.claim(
            worker_id=worker_id, capabilities=capabilities, lease_seconds=lease_seconds,
        )

    def claim_wait(
        self,
        *,
        worker_id: str,
        capabilities: list[str],
        lease_seconds: int | None = None,
        timeout_seconds: float = 15.0,
    ) -> dict[str, Any]:
        return self.claims.claim_wait(
            worker_id=worker_id, capabilities=capabilities, lease_seconds=lease_seconds,
            timeout_seconds=timeout_seconds,
        )

    # Leases

    def start(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
    ) -> dict[str, Any]:
        return self.leases.start(execution_id, worker_id=worker_id, claim_token=claim_token)

    def heartbeat(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        lease_seconds: int | None = None,
    ) -> dict[str, Any]:
        return self.leases.heartbeat(
            execution_id, worker_id=worker_id, claim_token=claim_token,
            lease_seconds=lease_seconds,
        )

    def append_event(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.leases.append_event(
            execution_id, worker_id=worker_id, claim_token=claim_token, event_type=event_type,
            details=details,
        )

    def checkpoint(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        return self.leases.checkpoint(
            execution_id, worker_id=worker_id, claim_token=claim_token, checkpoint=checkpoint,
        )

    def reassign_capacity(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        backend_route_id: str,
    ) -> dict[str, Any]:
        return self.leases.reassign_capacity(
            execution_id, worker_id=worker_id, claim_token=claim_token,
            backend_route_id=backend_route_id,
        )

    def finish(
        self,
        result: ExecutionResult,
        *,
        worker_id: str,
        claim_token: str,
    ) -> dict[str, Any]:
        return self.leases.finish(result, worker_id=worker_id, claim_token=claim_token)

    def requeue_claim(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        reason: str,
        retry_after_seconds: float = 0,
    ) -> dict[str, Any]:
        return self.leases.requeue_claim(
            execution_id, worker_id=worker_id, claim_token=claim_token, reason=reason,
            retry_after_seconds=retry_after_seconds,
        )

    # Turn controls

    def submit_input_control(self, control: ExecutionInputControl) -> dict[str, Any]:
        return self.controls.submit_input_control(control)

    def submit_turn_settings_control(self, control: ExecutionTurnSettingsControl) -> dict[str, Any]:
        return self.controls.submit_turn_settings_control(control)

    def input_control(self, control_id: str) -> dict[str, Any] | None:
        return self.controls.input_control(control_id)

    def complete_input_control(
        self,
        control_id: str,
        *,
        execution_id: str,
        worker_id: str,
        claim_token: str,
        delivered: bool,
        error: str = "",
    ) -> dict[str, Any]:
        return self.controls.complete_input_control(
            control_id, execution_id=execution_id, worker_id=worker_id, claim_token=claim_token,
            delivered=delivered, error=error,
        )

    def wait_for_worker_control(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        return self.controls.wait_for_worker_control(
            execution_id, worker_id=worker_id, claim_token=claim_token,
            timeout_seconds=timeout_seconds,
        )

    # Views

    def snapshot(
        self,
        execution_id: str,
        *,
        after_sequence: int = 0,
        include_events: bool = True,
        include_result: bool = True,
    ) -> ExecutionSnapshot:
        return self.views.snapshot(
            execution_id, after_sequence=after_sequence, include_events=include_events,
            include_result=include_result,
        )

    def states(self, executions: Mapping[str, str]) -> dict[str, Any]:
        return self.views.states(executions)

    def actor_of(self, execution_id: str) -> str:
        return self.views.actor_of(execution_id)

    def wait_for_change(
        self,
        execution_id: str,
        *,
        after_updated_at: str = "",
        after_sequence: int = 0,
        include_events: bool = True,
        include_result: bool = True,
        timeout_seconds: float = 30.0,
        quiet_reply: bool = False,
    ) -> dict[str, Any]:
        return self.views.wait_for_change(
            execution_id, after_updated_at=after_updated_at, after_sequence=after_sequence,
            include_events=include_events, include_result=include_result,
            timeout_seconds=timeout_seconds, quiet_reply=quiet_reply,
        )

    # Health

    def health(self) -> dict[str, Any]:
        return self.health_report.health()


class SchedulerClient:
    """Worker-side Scheduler protocol client."""

    def __init__(
        self,
        *,
        worker_id: str,
        build_id: str,
        capabilities: list[str],
        pid: int = 0,
        service_client: ExecutionServiceClient | None = None,
    ) -> None:
        self.worker_id = str(worker_id or "").strip()
        self.build_id = str(build_id or "").strip()
        self.capabilities = sorted(
            {
                str(item or "").strip()
                for item in capabilities
                if str(item or "").strip()
            }
        )
        self.pid = int(pid or 0)
        # A Worker learns of an absent service at once: it fences its claims
        # by its lease deadline and retries on its own schedule.
        self.service = service_client or ExecutionServiceClient(restart_wait=0)

    def register(self, state: str = "active") -> dict[str, Any]:
        result = self.service.request(
            "scheduler.worker.register",
            {
                "worker_id": self.worker_id,
                "build_id": self.build_id,
                "capabilities": self.capabilities,
                "pid": self.pid,
                "state": state,
            },
        )
        return result if isinstance(result, dict) else {}

    def requeue(
        self,
        execution_id: str,
        claim_token: str,
        reason: str,
        *,
        retry_after_seconds: float = 0,
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.requeue",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "reason": str(reason or "provider recovery deferred"),
                "retry_after_seconds": max(
                    0.0,
                    float(retry_after_seconds or 0),
                ),
            },
        )
        return result if isinstance(result, dict) else {}

    def prepare_handover(
        self, execution_id: str, claim_token: str,
    ) -> dict[str, Any]:
        result = self.service.request("scheduler.handover.prepare", {
            "execution_id": execution_id,
            "worker_id": self.worker_id,
            "claim_token": claim_token,
        })
        return result if isinstance(result, dict) else {}

    def set_state(self, state: str) -> None:
        self.service.request(
            "scheduler.worker.state",
            {"worker_id": self.worker_id, "state": state},
        )

    def handover(self) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.worker.handover",
            {"worker_id": self.worker_id},
        )
        return result if isinstance(result, dict) else {}

    def unregister(self) -> bool:
        result = self.service.request(
            "scheduler.worker.unregister",
            {"worker_id": self.worker_id},
        )
        return bool(
            isinstance(result, dict) and result.get("removed")
        )

    def claim(
        self,
        lease_seconds: int = 270,
        *,
        wait_seconds: float = 15.0,
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.claim.wait",
            {
                "worker_id": self.worker_id,
                "capabilities": self.capabilities,
                "lease_seconds": lease_seconds,
                "timeout_seconds": max(0.1, float(wait_seconds)),
            },
            timeout=max(6.0, float(wait_seconds) + 6.0),
        )
        return result if isinstance(result, dict) else {}

    def start(self, execution_id: str, claim_token: str) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.start",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
            },
        )
        return result if isinstance(result, dict) else {}

    def heartbeat(
        self,
        execution_id: str,
        claim_token: str,
        lease_seconds: int = 270,
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.heartbeat",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "lease_seconds": lease_seconds,
            },
            timeout=CONTROL_REQUEST_TIMEOUT_SECONDS,
        )
        return result if isinstance(result, dict) else {}

    def wait_for_control(
        self,
        execution_id: str,
        claim_token: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.control.wait",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "timeout_seconds": max(0.1, float(timeout_seconds)),
            },
            timeout=max(6.0, float(timeout_seconds) + 6.0),
        )
        return result if isinstance(result, dict) else {}

    def complete_input_control(
        self,
        execution_id: str,
        claim_token: str,
        control_id: str,
        *,
        delivered: bool,
        error: str = "",
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.input.complete",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "control_id": control_id,
                "delivered": bool(delivered),
                "error": str(error or ""),
            },
        )
        return result if isinstance(result, dict) else {}

    def emit(
        self,
        execution_id: str,
        claim_token: str,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.service.request(
            "scheduler.event",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "event_type": event_type,
                "details": details or {},
            },
        )

    def checkpoint(
        self,
        execution_id: str,
        claim_token: str,
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.checkpoint",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "checkpoint": dict(checkpoint),
            },
        )
        return result if isinstance(result, dict) else {}

    def reassign_capacity(
        self,
        execution_id: str,
        claim_token: str,
        backend_route_id: str,
    ) -> dict[str, Any]:
        result = self.service.request(
            "scheduler.capacity.reassign",
            {
                "execution_id": execution_id,
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "backend_route_id": backend_route_id,
            },
        )
        return result if isinstance(result, dict) else {}

    def finish(
        self,
        result: ExecutionResult,
        claim_token: str,
    ) -> dict[str, Any]:
        payload = self.service.request(
            "scheduler.finish",
            {
                "worker_id": self.worker_id,
                "claim_token": claim_token,
                "result": result.to_payload(),
            },
        )
        return payload if isinstance(payload, dict) else {}
