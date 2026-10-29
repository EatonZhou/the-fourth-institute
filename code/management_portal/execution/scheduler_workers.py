"""Workers: their registrations, handovers between builds and periodic maintenance."""
from __future__ import annotations

import time
from typing import Any

from management_common.database_time import instant

from .contracts import execution_timestamp
from .scheduler_changes import SchedulerChanges
from .scheduler_recovery import LeaseRecovery
from .scheduler_rows import (
    ACTIVE_STATUSES,
    assert_owner,
    compact_json,
    handover_checkpoint,
    json_object,
    turn_kept_running,
)


class WorkerRegistry:
    """Worker registrations, planned handovers and lease maintenance."""

    def __init__(self, changes: SchedulerChanges, recovery: LeaseRecovery, *,
                 worker_heartbeat_timeout_seconds: float) -> None:
        self.changes = changes
        self.recovery = recovery
        self.worker_heartbeat_timeout_seconds = max(
            1.0,
            float(worker_heartbeat_timeout_seconds),
        )
        self._last_maintenance_result = {
            "expired_worker_registrations": 0,
            "recovered_expired_leases": 0,
        }

    def _prune_stale_workers(
        self,
        connection: Any,
        *,
        now_epoch: float | None = None,
    ) -> int:
        cutoff = (
            float(now_epoch if now_epoch is not None else time.time())
            - self.worker_heartbeat_timeout_seconds
        )
        cursor = connection.execute(
            "\n            DELETE FROM execution_workers\n            WHERE heartbeat_at < %s\n              AND worker_id NOT IN (\n                  SELECT owner_worker_id\n                  FROM executions\n                  WHERE status IN ('claimed', 'running')\n              )\n            ",
            (instant(cutoff),),
        )
        return max(0, int(cursor.rowcount or 0))

    def register_worker(
        self,
        *,
        worker_id: str,
        build_id: str,
        capabilities: list[str],
        pid: int = 0,
        state: str = "active",
    ) -> dict[str, Any]:
        clean_capabilities = sorted(
            {
                str(item or "").strip()
                for item in capabilities
                if str(item or "").strip()
            }
        )
        if (
            not worker_id
            or not build_id
            or not clean_capabilities
        ):
            raise ValueError("worker registration is incomplete")
        if state not in {"standby", "active", "draining", "offline"}:
            raise ValueError("invalid worker state")
        now = execution_timestamp()
        with self.changes.connect() as connection:
            # A restarted Worker can take its predecessor's expired leases at once.
            self.recovery.recover_expired(connection)
            self._prune_stale_workers(connection)
            existing = connection.execute(
                'SELECT build_id, state FROM execution_workers WHERE worker_id = %s FOR UPDATE',
                (worker_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["build_id"]) != build_id
                ):
                    connection.rollback()
                    raise ValueError("worker identity cannot be rebound")
                if (
                    str(existing["state"]) in {"draining", "offline"}
                    and state in {"standby", "active"}
                ):
                    connection.rollback()
                    raise RuntimeError(
                        "retired worker cannot reactivate by registration"
                    )
            connection.execute(
                '\n                INSERT INTO execution_workers (\n                    worker_id, build_id, state, capabilities_json,\n                    pid, heartbeat_at, updated_at\n                ) VALUES (%s, %s, %s, %s, %s, %s, %s)\n                ON CONFLICT(worker_id) DO UPDATE SET\n                    build_id=excluded.build_id,\n                    state=excluded.state,\n                    capabilities_json=excluded.capabilities_json,\n                    pid=excluded.pid,\n                    heartbeat_at=excluded.heartbeat_at,\n                    updated_at=excluded.updated_at\n                ',
                (
                    worker_id,
                    build_id,
                    state,
                    compact_json(clean_capabilities),
                    int(pid or 0),
                    instant(time.time()),
                    now,
                ),
            )
            connection.commit()
        self.changes.notify("execution", "claim")
        return {
            "worker_id": worker_id,
            "build_id": build_id,
            "state": state,
            "capabilities": clean_capabilities,
        }

    def activate_worker(
        self,
        worker_id: str,
        *,
        previous_worker_id: str = "",
    ) -> dict[str, Any]:
        target_id = str(worker_id or "").strip()
        previous_id = str(previous_worker_id or "").strip()
        if not target_id:
            raise ValueError("worker_id is required")
        if previous_id == target_id:
            raise ValueError("worker cannot replace itself")
        now = execution_timestamp()
        with self.changes.connect() as connection:
            # A generation switch retires every other build; locking the few
            # Worker rows (in one order) keeps two switches from both winning.
            connection.execute(
                'SELECT worker_id FROM execution_workers ORDER BY worker_id FOR UPDATE'
            ).fetchall()
            target = connection.execute(
                'SELECT state, build_id FROM execution_workers WHERE worker_id = %s',
                (target_id,),
            ).fetchone()
            if target is None:
                connection.rollback()
                raise KeyError(target_id)
            if str(target["state"]) not in {"standby", "active"}:
                connection.rollback()
                raise RuntimeError(
                    f"cannot activate worker in state {target['state']}"
                )
            previous_state = ""
            if previous_id:
                previous = connection.execute(
                    'SELECT state FROM execution_workers WHERE worker_id = %s',
                    (previous_id,),
                ).fetchone()
                if previous is not None and str(previous["state"]) not in {
                    "active",
                    "draining",
                    "offline",
                }:
                    connection.rollback()
                    raise RuntimeError(
                        f"cannot retire worker in state {previous['state']}"
                    )
                if previous is not None and str(previous["state"]) == "offline":
                    # It already left (a Worker another generation retired
                    # exits by itself); there is nothing left to retire.
                    previous_state = "offline"
                elif previous is not None:
                    previous_state = "draining"
                    connection.execute(
                        "\n                        UPDATE execution_workers\n                        SET state = 'draining', heartbeat_at = %s, updated_at = %s\n                        WHERE worker_id = %s\n                        ",
                        (instant(time.time()), now, previous_id),
                    )
            # Workers of the target's generation keep claiming side by side
            # (the Dev and Release pools run the same build); every other
            # generation stops claiming and hands its executions over, so a
            # publish that reloads both pools moves each execution only once.
            connection.execute(
                "\n                UPDATE execution_workers\n                SET state = 'draining', heartbeat_at = %s, updated_at = %s\n                WHERE state = 'active' AND worker_id != %s AND build_id != %s\n                ",
                (instant(time.time()), now, target_id, str(target["build_id"])),
            )
            connection.execute(
                "\n                UPDATE execution_workers\n                SET state = 'active', heartbeat_at = %s, updated_at = %s\n                WHERE worker_id = %s\n                ",
                    (instant(time.time()), now, target_id),
                )
            connection.commit()
        self.changes.notify("execution", "control")
        return {
            "worker_id": target_id,
            "state": "active",
            "previous_worker_id": previous_id,
            "previous_state": previous_state,
        }

    def set_worker_state(self, worker_id: str, state: str) -> None:
        if state not in {"draining", "offline"}:
            raise ValueError(
                "worker state can only retire through this endpoint"
            )
        with self.changes.connect() as connection:
            cursor = connection.execute(
                '\n                UPDATE execution_workers\n                SET state = %s, heartbeat_at = %s, updated_at = %s\n                WHERE worker_id = %s\n                ',
                (state, instant(time.time()), execution_timestamp(), worker_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(worker_id)
            connection.commit()
        self.changes.notify("execution", "control")

    def prepare_handover(
        self, execution_id: str, *, worker_id: str, claim_token: str,
    ) -> dict[str, Any]:
        with self.changes.connect() as connection:
            row = assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id, claim_token=claim_token,
            )
            if row["status"] not in ACTIVE_STATUSES:
                raise PermissionError("execution claim is no longer active")
            if row["cancel_requested"]:
                return {"ready": False, "cancel_requested": True}
            checkpoint = json_object(row["checkpoint_json"])
            if not checkpoint.get("resumable"):
                return {"ready": False, "cancel_requested": False}
            payload = handover_checkpoint(checkpoint, worker_id)
            connection.execute(
                'UPDATE executions SET checkpoint_json = %s, updated_at = %s WHERE execution_id = %s',
                (compact_json(payload), execution_timestamp(), execution_id),
            )
            if payload != checkpoint:
                self.changes.event(connection, execution_id, "handover_prepared", {
                    "worker_id": worker_id,
                    "turn_id": str(payload.get("turn_id") or ""),
                })
            connection.commit()
        return {
            "ready": True,
            "cancel_requested": False,
            "interrupt_required": bool(
                payload.get("turn_id") and not payload.get("provider_completed")
                and not turn_kept_running(payload)
            ),
        }

    def handover_worker(self, worker_id: str) -> dict[str, Any]:
        clean_worker_id = str(worker_id or "").strip()
        if not clean_worker_id:
            raise ValueError("worker_id is required")
        requeued: list[str] = []
        blocked: list[str] = []
        with self.changes.connect() as connection:
            worker = connection.execute(
                'SELECT state FROM execution_workers WHERE worker_id = %s FOR UPDATE',
                (clean_worker_id,),
            ).fetchone()
            if worker is None:
                connection.rollback()
                return {
                    "worker_id": clean_worker_id,
                    "requeued_execution_ids": [],
                    "blocked_execution_ids": [],
                }
            if str(worker["state"]) not in {"draining", "offline"}:
                connection.rollback()
                raise RuntimeError("only a retiring worker can hand over claims")
            rows = connection.execute(
                "\n                SELECT execution_id, status, checkpoint_json, cancel_requested,\n                       owner_worker_id, request_json\n                FROM executions\n                WHERE owner_worker_id = %s\n                  AND status IN ('claimed', 'running') FOR UPDATE\n                ",
                (clean_worker_id,),
            ).fetchall()
            for row in rows:
                execution_id = str(row["execution_id"])
                checkpoint = json_object(row["checkpoint_json"])
                if (
                    str(row["status"]) != "claimed"
                    and not bool(checkpoint.get("resumable"))
                ):
                    blocked.append(execution_id)
                    continue
                if bool(row["cancel_requested"]):
                    # The retired Worker's Provider turn may still run.
                    if self.recovery.requeue_abandoned_provider_work(
                        connection, row, cancelled_error="execution cancelled during worker handover",
                    ):
                        requeued.append(execution_id)
                        continue
                    self.recovery.finalize_released_cancellation(
                        connection, row,
                        error="execution cancelled during worker handover",
                        details={
                            "reason": "worker handover after cancellation",
                            "previous_worker_id": clean_worker_id,
                        },
                    )
                    continue
                if checkpoint.get("resumable"):
                    checkpoint = handover_checkpoint(checkpoint, clean_worker_id)
                connection.execute(
                    "\n                    UPDATE executions\n                    SET status = 'queued', owner_worker_id = '',\n                        claim_token = '', lease_expires_at = NULL, updated_at = %s,\n                        checkpoint_json = %s\n                    WHERE execution_id = %s AND owner_worker_id = %s\n                    ",
                    (
                        execution_timestamp(), compact_json(checkpoint),
                        execution_id, clean_worker_id,
                    ),
                )
                self.changes.event(
                    connection,
                    execution_id,
                    "requeued",
                    {
                        "reason": "worker drain handover",
                        "previous_worker_id": clean_worker_id,
                        "checkpoint": checkpoint,
                    },
                )
                requeued.append(execution_id)
            connection.execute(
                "\n                UPDATE execution_workers\n                SET state = 'offline', heartbeat_at = %s, updated_at = %s\n                WHERE worker_id = %s\n                ",
                (instant(time.time()), execution_timestamp(), clean_worker_id),
            )
            connection.commit()
        self.changes.notify("execution", "control")
        return {
            "worker_id": clean_worker_id,
            "requeued_execution_ids": requeued,
            "blocked_execution_ids": blocked,
        }

    def unregister_worker(self, worker_id: str) -> bool:
        clean_worker_id = str(worker_id or "").strip()
        if not clean_worker_id:
            raise ValueError("worker_id is required")
        with self.changes.connect() as connection:
            worker = connection.execute(
                'SELECT state FROM execution_workers WHERE worker_id = %s FOR UPDATE',
                (clean_worker_id,),
            ).fetchone()
            if worker is None:
                connection.rollback()
                return False
            if str(worker["state"]) != "offline":
                connection.rollback()
                raise RuntimeError(
                    f"cannot unregister worker in state {worker['state']}"
                )
            active = connection.execute(
                "\n                SELECT COUNT(*) AS count\n                FROM executions\n                WHERE owner_worker_id = %s\n                  AND status IN ('claimed', 'running')\n                ",
                (clean_worker_id,),
            ).fetchone()
            if int(active["count"] if active else 0):
                connection.rollback()
                raise RuntimeError(
                    "cannot unregister worker with active executions"
                )
            connection.execute(
                'DELETE FROM execution_workers WHERE worker_id = %s',
                (clean_worker_id,),
            )
            connection.commit()
        self.changes.notify("execution")
        return True

    def maintain(self) -> dict[str, int]:
        """Recover expired leases and remove stale worker registrations.

        Claims do not recover leases themselves; ExecutionService runs this
        every few seconds, well inside the shortest lease.
        """
        with self.changes.connect() as connection:
            recovered = self.recovery.recover_expired(connection)
            pruned_workers = self._prune_stale_workers(connection)
            connection.commit()
        result = {
            "expired_worker_registrations": pruned_workers,
            "recovered_expired_leases": recovered,
        }
        with self.changes.lock:
            self._last_maintenance_result = result
        if recovered or pruned_workers:
            self.changes.notify("execution", "claim", "control")
        return dict(result)

    def maintenance_result(self) -> dict[str, int]:
        """What the last ``maintain`` recovered and removed."""
        with self.changes.lock:
            return dict(self._last_maintenance_result)
