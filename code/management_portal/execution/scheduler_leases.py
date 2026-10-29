"""Leases: what the Worker holding an execution reports until it finishes or gives it back."""
from __future__ import annotations

import time
from typing import Any

from management_common.database_time import instant

from .contracts import ExecutionRequest, ExecutionResult, execution_timestamp
from .scheduler_capacity import ActiveRequests, SchedulerCapacity
from .scheduler_changes import SchedulerChanges
from .scheduler_quota import SchedulerQuota
from .scheduler_recovery import LeaseRecovery
from .scheduler_rows import (
    ACTIVE_STATUSES,
    assert_owner,
    compact_json,
    handover_checkpoint,
    json_object,
    turn_kept_running,
)
from .scheduler_usage import TokenUsage, merge_normalized_usage, remaining_normalized_usage
from .scheduler_views import SchedulerViews


class Leases:
    """Starts, heartbeats, events, checkpoints and outcomes of claimed executions."""

    def __init__(self, accounts: Any, *, changes: SchedulerChanges, capacity: SchedulerCapacity,
                 quota: SchedulerQuota, active_requests: ActiveRequests, usage: TokenUsage,
                 recovery: LeaseRecovery, views: SchedulerViews,
                 default_lease_seconds: int) -> None:
        self.accounts = accounts
        self.changes = changes
        self.capacity = capacity
        self.quota = quota
        self.active_requests = active_requests
        self.usage = usage
        self.recovery = recovery
        self.views = views
        self.default_lease_seconds = default_lease_seconds

    def requeue_claim(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        reason: str,
        retry_after_seconds: float = 0,
    ) -> dict[str, Any]:
        with self.changes.connect() as connection:
            row = assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if str(row["status"]) not in ACTIVE_STATUSES:
                connection.rollback()
                raise PermissionError("execution claim is no longer active")
            # The owner is releasing its claim. Cancellation must win in this
            # transaction: a cancelled queued execution cannot be claimed again.
            if bool(row["cancel_requested"]):
                if turn_kept_running(json_object(row["checkpoint_json"])) and self.recovery.requeue_abandoned_provider_work(
                    connection, row, cancelled_error="execution cancelled during worker handover",
                ):
                    # The handover left the turn running: a Worker stops it first.
                    connection.commit()
                    self.changes.notify("execution", "control")
                    return self.views.snapshot(execution_id)
                self.recovery.finalize_released_cancellation(
                    connection, row,
                    error="execution cancelled before recovery requeue",
                    details={
                        "reason": "recovery requeue after cancellation",
                        "previous_worker_id": worker_id,
                    },
                )
                connection.commit()
                self.changes.notify("execution", "control")
                return self.views.snapshot(execution_id)
            checkpoint = json_object(row["checkpoint_json"])
            if not bool(checkpoint.get("resumable")):
                connection.rollback()
                raise RuntimeError(
                    "execution has no resumable checkpoint"
                )
            connection.execute(
                "\n                UPDATE executions\n                SET status = 'queued', owner_worker_id = '', claim_token = '',\n                    lease_expires_at = NULL, available_at = %s, updated_at = %s\n                WHERE execution_id = %s AND owner_worker_id = %s\n                  AND claim_token = %s\n                ",
                (
                    instant(time.time() + max(
                        0.0,
                        min(3600.0, float(retry_after_seconds or 0)),
                    )),
                    execution_timestamp(),
                    execution_id,
                    worker_id,
                    claim_token,
                ),
            )
            self.changes.event(
                connection,
                execution_id,
                "requeued",
                {
                    "reason": str(reason or "provider recovery deferred"),
                    "previous_worker_id": worker_id,
                    "checkpoint": checkpoint,
                    "retry_after_seconds": max(
                        0.0,
                        min(3600.0, float(retry_after_seconds or 0)),
                    ),
                },
            )
            connection.commit()
        self.changes.notify("execution", "control")
        return self.views.snapshot(execution_id)

    def start(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
    ) -> dict[str, Any]:
        with self.changes.connect() as connection:
            row = assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] == "claimed":
                connection.execute(
                    "\n                    UPDATE executions\n                    SET status = 'running', updated_at = %s\n                    WHERE execution_id = %s\n                    ",
                    (execution_timestamp(), execution_id),
                )
                self.changes.event(
                    connection,
                    execution_id,
                    "started",
                    {"worker_id": worker_id},
                )
            elif row["status"] != "running":
                connection.rollback()
                raise RuntimeError(
                    f"cannot start execution in state {row['status']}"
                )
            connection.commit()
        return self.views.snapshot(execution_id)

    def heartbeat(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        lease_seconds: int | None = None,
    ) -> dict[str, Any]:
        duration = max(15, int(lease_seconds or self.default_lease_seconds))
        with self.changes.connect() as connection:
            row = assert_owner(
                connection.execute(
                    # Only ownership and state: request_json alone is often hundreds of KB,
                    # and this runs for every streamed event / heartbeat.
                    'SELECT execution_id, actor, status, owner_worker_id, claim_token, cancel_requested FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                connection.rollback()
                raise RuntimeError(
                    f"cannot heartbeat execution in state {row['status']}"
                )
            now = execution_timestamp()
            connection.execute(
                '\n                UPDATE executions\n                SET lease_expires_at = %s\n                WHERE execution_id = %s\n                ',
                (instant(time.time() + duration), execution_id),
            )
            connection.execute(
                '\n                UPDATE execution_workers\n                SET heartbeat_at = %s, updated_at = %s\n                WHERE worker_id = %s\n                ',
                (instant(time.time()), now, worker_id),
            )
            connection.commit()
        return {"cancel_requested": bool(row["cancel_requested"])}

    def append_event(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(event_type or "").strip():
            raise ValueError("execution event type is required")
        with self.changes.connect() as connection:
            row = assert_owner(
                connection.execute(
                    # Only ownership and state: request_json alone is often hundreds of KB,
                    # and this runs for every streamed event / heartbeat.
                    'SELECT execution_id, actor, status, owner_worker_id, claim_token, cancel_requested FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                connection.rollback()
                raise RuntimeError(
                    f"cannot append event in state {row['status']}"
                )
            event = self.changes.event(
                connection,
                execution_id,
                event_type,
                details,
            )
            connection.commit()
            return {
                **event,
                "actor": str(row["actor"]),
                "status": str(row["status"]),
            }

    def checkpoint(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(checkpoint or {})
        if not payload:
            raise ValueError("execution checkpoint is required")
        with self.changes.connect() as connection:
            row = assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                connection.rollback()
                raise RuntimeError(
                    f"cannot checkpoint execution in state {row['status']}"
                )
            previous_handover = json_object(row["checkpoint_json"]).get("worker_handover")
            if (
                isinstance(previous_handover, dict)
                and previous_handover.get("worker_id") == worker_id
            ):
                payload = handover_checkpoint(payload, worker_id)
            elif (
                isinstance(previous_handover, dict)
                and previous_handover.get("turn_id") == payload.get("turn_id")
                and not payload.get("provider_completed")
            ):
                payload["worker_handover"] = dict(previous_handover)
                payload["turn_start_reason"] = "backend_service_update"
            connection.execute(
                '\n                UPDATE executions\n                SET checkpoint_json = %s, updated_at = %s\n                WHERE execution_id = %s\n                ',
                (compact_json(payload), execution_timestamp(), execution_id),
            )
            self.changes.event(
                connection,
                execution_id,
                "checkpointed",
                {
                    "provider": str(payload.get("provider") or ""),
                    "backend_session_id": str(
                        payload.get("backend_session_id") or ""
                    ),
                    "turn_id": str(payload.get("turn_id") or ""),
                    "resumable": bool(payload.get("resumable")),
                },
            )
            connection.commit()
        return payload

    def reassign_capacity(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        backend_route_id: str,
    ) -> dict[str, Any]:
        route_id = str(backend_route_id or "").strip()
        if not route_id:
            raise ValueError("backend route id is required")
        with self.changes.connect() as connection:
            row = assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                connection.rollback()
                raise RuntimeError(
                    f"cannot reassign capacity in state {row['status']}"
                )
            request_payload = json_object(row["request_json"])
            if (
                str(request_payload.get("service_type") or "")
                != "agent_session"
            ):
                connection.rollback()
                raise ValueError(
                    "backend route capacity requires an agent session"
                )
            current_key = self.capacity.capacity_key(request_payload)
            request_payload = self.accounts.reroute(request_payload, route_id)
            normalized = ExecutionRequest.from_payload(
                request_payload
            ).to_payload()
            next_key = self.capacity.capacity_key(normalized)
            if self.quota.quota_paused(connection, normalized):
                connection.rollback()
                return {"accepted": False, "capacity_key": current_key,
                        "changed": False, "reason": "quota_reserve"}
            if next_key == current_key:
                connection.commit()
                return {
                    "accepted": True,
                    "capacity_key": current_key,
                    "changed": False,
                }
            destination_count = 0
            for _active_id, active_request in self.active_requests.scheduling_requests(
                connection, exclude=execution_id,
            ):
                if self.capacity.capacity_key(active_request) == next_key:
                    destination_count += 1
            if destination_count >= self.capacity.capacity_limit(connection, next_key):
                connection.rollback()
                return {"accepted": False, "capacity_key": current_key,
                        "changed": False, "reason": "capacity_full"}
            connection.execute(
                '\n                UPDATE executions\n                SET request_json = %s, updated_at = %s\n                WHERE execution_id = %s\n                ',
                (compact_json(normalized), execution_timestamp(), execution_id),
            )
            self.changes.event(
                connection,
                execution_id,
                "capacity_reassigned",
                {
                    "from_capacity_key": current_key,
                    "to_capacity_key": next_key,
                },
            )
            connection.commit()
        return {
            "accepted": True,
            "capacity_key": next_key,
            "changed": True,
        }

    def finish(
        self,
        result: ExecutionResult,
        *,
        worker_id: str,
        claim_token: str,
    ) -> dict[str, Any]:
        result.to_payload()
        with self.changes.connect() as connection:
            row = assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (result.execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                connection.rollback()
                raise RuntimeError(
                    f"cannot finish execution in state {row['status']}"
                )
            status = "cancelled" if bool(row["cancel_requested"]) else result.status
            payload = {
                **result.to_payload(),
                "status": status,
            }
            checkpoint = json_object(row["checkpoint_json"])
            if isinstance(checkpoint.get("abandoned"), dict) and not payload.get("backend_session_id"):
                # Ended for a lost Worker: name its session, as ending it in place did.
                payload["backend_session_id"] = str(checkpoint.get("backend_session_id") or "")
            now = execution_timestamp()
            accounted_usage = json_object(row["usage_accounted_json"])
            if not bool(row["usage_accounted"]):
                remaining_usage = remaining_normalized_usage(
                    result.usage,
                    accounted_usage,
                )
                self.usage.record_token_usage(
                    connection,
                    request=json_object(row["request_json"]),
                    usage=remaining_usage,
                    occurred_at=now,
                )
                accounted_usage = merge_normalized_usage(
                    accounted_usage,
                    remaining_usage,
                )
            # Every Provider request this execution counted, across Workers and
            # recoveries: the turn's usage, whatever the Provider's own report
            # covers (Codex reports its last request and its thread total).
            if accounted_usage:
                payload["accounted_usage"] = dict(accounted_usage)
            connection.execute(
                "\n                UPDATE executions\n                SET status = %s, result_json = %s, error = %s,\n                    owner_worker_id = '', claim_token = '',\n                    lease_expires_at = NULL, usage_accounted = '1',\n                    usage_accounted_json = %s,\n                    updated_at = %s\n                WHERE execution_id = %s\n                ",
                (
                    status,
                    compact_json(payload),
                    str(payload.get("error") or ""),
                    compact_json(accounted_usage),
                    now,
                    result.execution_id,
                ),
            )
            connection.execute(
                "\n                UPDATE execution_input_controls\n                SET status = 'failed',\n                    error = 'provider_turn_finished_before_delivery',\n                    updated_at = %s\n                WHERE execution_id = %s AND status = 'pending'\n                ",
                (now, result.execution_id),
            )
            self.changes.event(
                connection,
                result.execution_id,
                status,
                {
                    "worker_id": worker_id,
                    "backend_session_id": result.backend_session_id,
                    "error": result.error,
                    "error_code": result.error_code,
                },
            )
            connection.commit()
        self.changes.notify("execution", "control")
        return self.views.snapshot(result.execution_id)
