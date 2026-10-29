"""Claims: a Worker taking the next admitted execution under a lease."""
from __future__ import annotations

import secrets
import time
from typing import Any

from management_common.database_time import epoch_seconds, instant

from .contracts import execution_timestamp
from .scheduler_admission import Admission, record_actor_dispatch
from .scheduler_capacity import ActiveRequests, SchedulerCapacity
from .scheduler_changes import SHARED_SCHEDULER_POLL_SECONDS, SchedulerChanges
from .scheduler_rows import json_object, row_payload


def _try_binding_claim(connection: Any, binding_id: str) -> bool:
    """Reserve a session binding for this claim without waiting.

    One binding runs one execution at a time. A claimer that finds the
    binding reserved by a concurrent claim skips that candidate.
    """
    row = connection.execute(
        "SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema() || ':binding:' || %s, 0)) AS reserved",
        (binding_id,),
    ).fetchone()
    return bool(row["reserved"])


class Claims:
    """Claims of queued executions and the waits between them."""

    def __init__(self, changes: SchedulerChanges, capacity: SchedulerCapacity, admission: Admission,
                 active_requests: ActiveRequests, *, default_lease_seconds: int) -> None:
        self.changes = changes
        self.capacity = capacity
        self.admission = admission
        self.active_requests = active_requests
        self.default_lease_seconds = default_lease_seconds

    def _claim_candidate(
        self, connection: Any, row: Any, request: dict[str, Any], *,
        worker_id: str, token: str, lease_expires_at: Any, now: str,
    ) -> bool:
        """Take one queued execution unless a concurrent claim took it or its binding."""
        binding = str((request.get("session") or {}).get("binding_id") or "")
        if binding:
            if not _try_binding_claim(connection, binding):
                return False
            # A claim of this binding that committed after the admission read
            # is visible now that the binding is reserved.
            if any(
                str((active.get("session") or {}).get("binding_id") or "") == binding
                for _execution_id, active in self.active_requests.scheduling_requests(connection)
            ):
                return False
        # A row another claim is taking is skipped instead of waited for.
        return connection.execute(
            "\n            UPDATE executions\n            SET status = 'claimed', owner_worker_id = %s, claim_token = %s,\n                lease_expires_at = %s, available_at = 'epoch'::timestamptz,\n                attempts = attempts + 1, updated_at = %s\n            WHERE execution_id = (\n                SELECT execution_id FROM executions\n                WHERE execution_id = %s AND status = 'queued' AND cancel_requested = '0'\n                FOR UPDATE SKIP LOCKED\n            )\n            ",
            (worker_id, token, lease_expires_at, now, str(row["execution_id"])),
        ).rowcount == 1

    def claim(
        self,
        *,
        worker_id: str,
        capabilities: list[str],
        lease_seconds: int | None = None,
    ) -> dict[str, Any]:
        supported = {
            str(item or "").strip()
            for item in capabilities
            if str(item or "").strip()
        }
        if not supported:
            raise ValueError("worker must support at least one capability")
        duration = max(15, int(lease_seconds or self.default_lease_seconds))
        # Claims run side by side. Expired leases and stale Workers are
        # recovered by maintain(); capacity, quota and dispatch pacing are
        # read without a lock, so concurrent claims may briefly exceed them.
        # Only the execution row (claimed once) and its session binding (one
        # execution at a time) are decided atomically.
        with self.changes.connect() as connection:
            now_epoch = time.time()
            worker = connection.execute(
                '\n                SELECT state\n                FROM execution_workers\n                WHERE worker_id = %s\n                ',
                (worker_id,),
            ).fetchone()
            if worker is None:
                connection.commit()
                return {"claimed": False, "reason": "worker_not_registered"}
            if worker["state"] != "active":
                connection.commit()
                # A standby Worker waits for activation; a draining or offline
                # one was retired and leaves.
                return {
                    "claimed": False,
                    "reason": "worker_not_active",
                    "worker_state": str(worker["state"]),
                }
            connection.execute(
                '\n                UPDATE execution_workers\n                SET heartbeat_at = %s, updated_at = %s\n                WHERE worker_id = %s\n                ',
                (instant(now_epoch), execution_timestamp(), worker_id),
            )
            candidates = connection.execute(
                "\n                SELECT execution_id, capability, actor, priority,\n                       request_json, created_at, checkpoint_json\n                FROM executions\n                WHERE status = 'queued'\n                  AND cancel_requested = '0'\n                  AND available_at <= %s\n                ORDER BY priority DESC, created_at ASC\n                ",
                (instant(now_epoch),),
            ).fetchall()
            deferred_blocked = connection.execute(
                "\n                SELECT 1\n                FROM executions\n                WHERE status = 'queued'\n                  AND cancel_requested = '0'\n                  AND available_at > %s\n                LIMIT 1\n                ",
                (instant(now_epoch),),
            ).fetchone() is not None
            eligible_candidates: list[Any] = []
            # A lost Worker's Provider work waiting to be interrupted makes no
            # model call: it passes quota, capacity and dispatch pacing.
            interrupt_candidates: list[Any] = []
            requests = {row["execution_id"]: json_object(row["request_json"]) for row in candidates}
            admission = self.admission.admission_snapshot(connection, list(requests.values()), now=now_epoch)
            blocked = set()
            for row in candidates:
                if row["capability"] not in supported:
                    continue
                interrupt_only = isinstance(json_object(row["checkpoint_json"]).get("abandoned"), dict)
                reason = self.admission.admission_reason(requests[row["execution_id"]], 0, interrupt_only, admission)
                if reason != "ready":
                    blocked.add(reason)
                    continue
                (interrupt_candidates if interrupt_only else eligible_candidates).append(row)
            quota_blocked = "quota_reserve" in blocked
            capacity_blocked = "capacity" in blocked
            dispatch_interval_blocked = "dispatch_interval" in blocked
            fair_candidates = self.admission.fair_queue_order(
                connection,
                eligible_candidates,
            )
            token = secrets.token_hex(24)
            now = execution_timestamp()
            candidate = None
            for option in [*interrupt_candidates, *fair_candidates]:
                if self._claim_candidate(
                    connection, option, requests[option["execution_id"]],
                    worker_id=worker_id, token=token,
                    lease_expires_at=instant(now_epoch + duration), now=now,
                ):
                    candidate = option
                    break
            if candidate is None:
                connection.commit()
                return {
                    "claimed": False,
                    "reason": (
                        "quota_reserve" if quota_blocked else
                        "capacity"
                        if capacity_blocked
                        else (
                            "dispatch_interval"
                            if dispatch_interval_blocked
                            else (
                                "deferred"
                                if deferred_blocked
                                else "empty"
                            )
                        )
                    ),
                }
            execution_id = str(candidate["execution_id"])
            capacity_key = self.capacity.capacity_key(requests[execution_id])
            interrupt_only = any(candidate is option for option in interrupt_candidates)
            dispatch_state = (
                {"interval_seconds": 0, "next_dispatch_at": 0} if interrupt_only
                else self.capacity.reserve_capacity_dispatch(
                    connection,
                    capacity_key,
                    now_epoch=now_epoch,
                )
            )
            if not interrupt_only:
                record_actor_dispatch(
                    connection,
                    actor=str(candidate["actor"]),
                    priority=int(candidate["priority"]),
                    now_epoch=now_epoch,
                )
            self.changes.event(
                connection,
                execution_id,
                "claimed",
                {
                    "worker_id": worker_id,
                    "lease_seconds": duration,
                    "capacity_key": capacity_key,
                    "dispatch_interval_seconds": dispatch_state[
                        "interval_seconds"
                    ],
                    "next_dispatch_at": dispatch_state[
                        "next_dispatch_at"
                    ],
                },
            )
            row = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s',
                (execution_id,),
            ).fetchone()
            connection.commit()
        return {
            "claimed": True,
            "claim_token": token,
            "execution": row_payload(row),
        }

    def _next_claim_wake_seconds(self, maximum: float) -> float:
        now = time.time()
        # Dev and Release use separate ExecutionService processes backed by the
        # same Scheduler PostgreSQL.  Condition notifications only wake claimers in
        # the submitting process, so a bounded database poll is required for a
        # worker waiting through the peer service endpoint.
        candidates = [
            max(
                0.05,
                min(float(maximum), SHARED_SCHEDULER_POLL_SECONDS),
            )
        ]
        with self.changes.connect() as connection:
            dispatch = connection.execute(
                '\n                SELECT MIN(next_dispatch_at) AS value\n                FROM capacity_dispatch_state\n                WHERE next_dispatch_at > %s\n                ',
                (instant(now),),
            ).fetchone()
            lease = connection.execute(
                "\n                SELECT MIN(lease_expires_at) AS value\n                FROM executions\n                WHERE status IN ('claimed', 'running')\n                  AND lease_expires_at > %s\n                ",
                (instant(now),),
            ).fetchone()
            deferred = connection.execute(
                "\n                SELECT MIN(available_at) AS value\n                FROM executions\n                WHERE status = 'queued' AND available_at > %s\n                ",
                (instant(now),),
            ).fetchone()
        for row in (dispatch, lease, deferred):
            value = (epoch_seconds(row["value"]) or 0) if row is not None else 0
            if value > now:
                candidates.append(max(0.05, value - now))
        return min(candidates)

    def claim_wait(
        self,
        *,
        worker_id: str,
        capabilities: list[str],
        lease_seconds: int | None = None,
        timeout_seconds: float = 15.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        with self.changes.change_notifications():
            while True:
                seen = self.changes.change_generation("claim")
                result = self.claim(
                    worker_id=worker_id,
                    capabilities=capabilities,
                    lease_seconds=lease_seconds,
                )
                if result.get("claimed") or result.get("reason") in {
                    "worker_not_active",
                    "worker_not_registered",
                }:
                    return result
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return result
                self.changes.wait_for_change_signal(
                    "claim", seen, self._next_claim_wake_seconds(remaining),
                )
