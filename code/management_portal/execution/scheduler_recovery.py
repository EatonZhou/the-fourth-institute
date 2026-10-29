"""Recovery of released work: cancellations, abandoned Provider turns and expired leases."""
from __future__ import annotations

import time
from typing import Any

from management_common.database_time import instant

from .contracts import ExecutionResult, execution_timestamp
from .scheduler_changes import SchedulerChanges
from .scheduler_rows import compact_json, json_object, turn_kept_running


def _with_accounted_usage(row: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Add what the execution counted for its Provider requests: the turn's usage."""
    accounted = json_object(row["usage_accounted_json"])
    return {**result, "accounted_usage": dict(accounted)} if accounted else result


class LeaseRecovery:
    """Settles executions whose Worker released them or lost its lease."""

    def __init__(self, changes: SchedulerChanges) -> None:
        self.changes = changes

    def finalize_released_cancellation(
        self,
        connection: Any,
        row: Any,
        *,
        error: str,
        details: dict[str, Any],
    ) -> None:
        """Commit cancellation only when no live claim must finish it first.

        Callers establish that boundary (owner release, expired lease, or
        unowned queued work) inside the same write transaction.
        """
        execution_id = str(row["execution_id"])
        checkpoint = json_object(row["checkpoint_json"])
        result = _with_accounted_usage(row, ExecutionResult(
            execution_id=execution_id,
            status="cancelled",
            error=error,
            backend_session_id=str(checkpoint.get("backend_session_id") or ""),
        ).to_payload())
        now = execution_timestamp()
        connection.execute(
            "\n            UPDATE executions\n            SET status = 'cancelled', cancel_requested = '1',\n                result_json = %s, error = %s, owner_worker_id = '',\n                claim_token = '', lease_expires_at = NULL, updated_at = %s\n            WHERE execution_id = %s\n            ",
            (compact_json(result), error, now, execution_id),
        )
        connection.execute(
            "\n            UPDATE execution_input_controls\n            SET status = 'failed',\n                error = 'provider_turn_finished_before_delivery', updated_at = %s\n            WHERE execution_id = %s AND status = 'pending'\n            ",
            (now, execution_id),
        )
        self.changes.event(connection, execution_id, "cancelled", details)

    def requeue_abandoned_provider_work(
        self, connection: Any, row: Any, *,
        cancelled_error: str = "execution cancellation finalized after worker lease expired",
    ) -> bool:
        """A lost Worker's Provider work is interrupted before its execution ends.

        The Provider runs a turn outside the Worker (Codex app server, Claude
        Code daemon, DSH process, Kimi server), so it keeps running when its
        Worker dies.  A turn or compaction that cannot be resumed (cancelled,
        or its turn not yet confirmed by the Provider) would otherwise end
        while its Provider work goes on, and the Resident would look idle.
        It returns to the queue marked ``abandoned``: the next Worker only
        interrupts that work and then ends the execution as failed (or
        cancelled).  The execution stays active until then.
        """

        checkpoint = json_object(row["checkpoint_json"])
        status = str(row["status"])
        cancel_requested = bool(row["cancel_requested"])
        abandoned = checkpoint.get("abandoned")
        if isinstance(abandoned, dict):
            if status == "running":
                # The Worker sent to interrupt was lost too: end it as decided.
                details = {"reason": "worker lost while interrupting abandoned provider work",
                           "previous_worker_id": str(row["owner_worker_id"] or "")}
                if abandoned.get("status") == "cancelled":
                    self.finalize_released_cancellation(
                        connection, row, error=str(abandoned.get("error") or "cancelled"), details=details,
                    )
                    return True
                return False
            # Waiting to be interrupted: a cancellation does not end it early.
            if status != "queued" or not cancel_requested:
                return False
            abandoned = {**abandoned, "status": "cancelled"}
        else:
            if status != "running" and not turn_kept_running(checkpoint):
                return False
            if not cancel_requested and checkpoint.get("resumable"):
                return False
            # Only a Provider turn or compaction can still be running for this
            # execution; the Worker may have died before it learned any id.
            request = json_object(row["request_json"])
            if (
                str(request.get("service_type") or "") != "agent_session"
                or str(request.get("operation") or "") not in {"turn", "compact"}
            ):
                return False
            abandoned = {
                "status": "cancelled" if cancel_requested else "failed",
                "error": (
                    cancelled_error if cancel_requested else
                    "execution worker lease expired after backend execution started"
                ),
                "previous_worker_id": str(row["owner_worker_id"] or ""),
            }
        execution_id = str(row["execution_id"])
        connection.execute(
            "\n            UPDATE executions\n            SET status = 'queued', cancel_requested = '0', checkpoint_json = %s,\n                owner_worker_id = '', claim_token = '', lease_expires_at = NULL,\n                updated_at = %s\n            WHERE execution_id = %s\n            ",
            (compact_json({**checkpoint, "resumable": False, "abandoned": abandoned}), execution_timestamp(), execution_id),
        )
        self.changes.event(
            connection,
            execution_id,
            "requeued",
            {
                "reason": "worker lost; interrupt its provider work before ending",
                "previous_worker_id": str(row["owner_worker_id"] or ""),
                "abandoned": abandoned,
            },
        )
        return True

    def recover_expired(self, connection: Any) -> int:
        # A row another transaction holds is being renewed or recovered
        # elsewhere; the next maintenance pass looks at it again.
        rows = connection.execute(
            "\n            SELECT execution_id, owner_worker_id, status, cancel_requested,\n                   checkpoint_json, request_json, usage_accounted_json\n            FROM executions\n            WHERE (status IN ('claimed', 'running')\n              AND lease_expires_at IS NOT NULL\n              AND lease_expires_at < %s) FOR UPDATE SKIP LOCKED\n            ",
            (instant(time.time()),),
        ).fetchall()
        for row in rows:
            execution_id = str(row["execution_id"])
            if self.requeue_abandoned_provider_work(connection, row):
                continue
            if bool(row["cancel_requested"]):
                self.finalize_released_cancellation(
                    connection, row,
                    error=(
                        "execution cancellation finalized after worker lease expired"
                    ),
                    details={
                        "reason": (
                            "worker lease expired after cancellation"
                        ),
                        "previous_worker_id": row["owner_worker_id"],
                    },
                )
                continue
            checkpoint = json_object(row["checkpoint_json"])
            resumable = bool(checkpoint.get("resumable"))
            if str(row["status"]) == "running" and not resumable:
                error = (
                    "execution worker lease expired after backend execution "
                    "started"
                )
                result = _with_accounted_usage(row, ExecutionResult(
                    execution_id=execution_id,
                    status="failed",
                    error=error,
                ).to_payload())
                connection.execute(
                    "\n                    UPDATE executions\n                    SET status = 'failed', result_json = %s, error = %s,\n                        owner_worker_id = '', claim_token = '',\n                        lease_expires_at = NULL, updated_at = %s\n                    WHERE execution_id = %s\n                    ",
                    (compact_json(result), error, execution_timestamp(), execution_id),
                )
                self.changes.event(
                    connection,
                    execution_id,
                    "failed",
                    {
                        "reason": "worker lease expired after start",
                        "previous_worker_id": row["owner_worker_id"],
                    },
                )
                continue
            connection.execute(
                "\n                UPDATE executions\n                SET status = 'queued', owner_worker_id = '', claim_token = '',\n                    lease_expires_at = NULL, updated_at = %s\n                WHERE execution_id = %s\n                ",
                (execution_timestamp(), execution_id),
            )
            self.changes.event(
                connection,
                execution_id,
                "requeued",
                {
                    "reason": (
                        "worker lease expired with resumable provider turn"
                        if checkpoint
                        else "worker lease expired"
                    ),
                    "previous_worker_id": row["owner_worker_id"],
                    "checkpoint": checkpoint,
                },
            )
        return len(rows)
