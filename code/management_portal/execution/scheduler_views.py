"""Views of executions: snapshots, batched states and waits for their changes."""
from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from management_common.database_time import epoch_seconds, iso_time

from .contracts import TERMINAL_STATUSES, ExecutionSnapshot
from .scheduler_admission import Admission
from .scheduler_changes import SHARED_SCHEDULER_POLL_SECONDS, SchedulerChanges
from .scheduler_rows import row_payload


class SchedulerViews:
    """Reads of executions as their callers see them."""

    def __init__(self, changes: SchedulerChanges, admission: Admission) -> None:
        self.changes = changes
        self.admission = admission

    def snapshot(
        self,
        execution_id: str,
        *,
        after_sequence: int = 0,
        include_events: bool = True,
        include_result: bool = True,
    ) -> ExecutionSnapshot:
        # PostgreSQL rows and transient in-memory events form one projection.
        # Events are read after the row and published only after their commit,
        # so a snapshot may show an event its row does not yet show, never the
        # reverse: a reader that stops at a terminal row has every event.
        with self.changes.connect() as connection:
            row = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s',
                (execution_id,),
            ).fetchone()
            if row is None:
                raise KeyError(execution_id)
            payload = row_payload(
                row,
                include_result=include_result,
            )
            queue_state = self.admission.queued_execution_state(
                connection,
                row,
                payload,
            )
            events = (
                self.changes.events(
                    connection,
                    execution_id,
                    after_sequence=after_sequence,
                )
                if include_events
                else []
            )
        scheduler_state = {
            key: payload[key]
            for key in (
                "capability",
                "priority",
                "correlation_id",
                "status",
                "owner_worker_id",
                "lease_expires_at",
                "cancel_requested",
                "attempts",
                "acknowledged_at",
                "created_at",
                "updated_at",
            )
        }
        scheduler_state.update(queue_state)
        return {
            "execution_id": execution_id,
            "actor": payload["actor"],
            "status": payload["status"],
            "request": payload["request"],
            "events": events,
            "result": payload["result"],
            "error": payload["error"],
            "scheduler": scheduler_state,
        }

    def states(self, executions: Mapping[str, str]) -> dict[str, Any]:
        """Batch control state; never fetch prompts, results or queue ranking."""
        if not executions:
            return {}
        with self.changes.connect() as connection:
            rows = connection.execute(
                "SELECT execution_id,actor,status,cancel_requested,available_at,"
                "NULLIF(checkpoint_json::text,'')::jsonb #>> '{abandoned,status}' AS abandoned_status,"
                "CASE WHEN status='queued' THEN jsonb_build_object("
                "'service_type',request_json::jsonb->'service_type',"
                "'operation',request_json::jsonb->'operation',"
                "'provider',request_json::jsonb->'provider',"
                "'runtime_options',request_json::jsonb->'runtime_options',"
                "'session',request_json::jsonb->'session') END AS scheduling_request "
                "FROM executions WHERE execution_id=ANY(%s)", (list(executions),),
            ).fetchall()
            rows = [row for row in rows if row['actor'] == executions[row['execution_id']]]
            queued = [row for row in rows if row['status'] == 'queued']
            reasons = {}
            if queued:
                requests = {row["execution_id"]: dict(row["scheduling_request"]) for row in queued}
                admission = self.admission.admission_snapshot(connection, list(requests.values()))
                for row in queued:
                    reasons[row["execution_id"]] = self.admission.admission_reason(
                        requests[row["execution_id"]], epoch_seconds(row["available_at"]), bool(row["abandoned_status"]), admission,
                    )
        return {row['execution_id']: {
            'execution_id': row['execution_id'], 'status': row['status'],
            'scheduler': {
                'cancel_requested': bool(row['cancel_requested']) or row['abandoned_status'] == 'cancelled',
                **({'queue_reason': reasons[row['execution_id']]} if row['execution_id'] in reasons else {}),
            },
        } for row in rows}

    def actor_of(self, execution_id: str) -> str:
        """The submitting actor, without reading the whole row."""
        with self.changes.connect() as connection:
            row = connection.execute(
                'SELECT actor FROM executions WHERE execution_id = %s',
                (execution_id,),
            ).fetchone()
        if row is None:
            raise KeyError(execution_id)
        return str(row["actor"])

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
        """Return the snapshot once it changes or the wait ends.

        With ``quiet_reply`` a wait that ends without a change answers
        ``unchanged`` instead of the full snapshot: a running turn's waiter
        polls every 0.25 s to check for Stop, and the full row carries the
        whole request.
        """
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))

        def full_snapshot() -> dict[str, Any]:
            return self.snapshot(
                execution_id,
                after_sequence=after_sequence,
                include_events=include_events,
                include_result=include_result,
            )

        with self.changes.change_notifications():
            while True:
                if not after_updated_at:
                    return full_snapshot()
                seen = self.changes.change_generation("execution")
                # Every append_event wakes every waiter in this process.  Decide
                # from status, updated_at and the in-memory events first; the full
                # row carries the whole request (often hundreds of KB).
                with self.changes.connect() as connection:
                    row = connection.execute(
                        'SELECT status, updated_at FROM executions WHERE execution_id = %s',
                        (execution_id,),
                    ).fetchone()
                if row is None:
                    raise KeyError(execution_id)
                has_events = include_events and any(
                    int(item.get("sequence") or 0) > max(0, int(after_sequence))
                    for item in self.changes.transient_events(execution_id)
                )
                remaining = deadline - time.monotonic()
                if (
                    str(row["status"] or "") in TERMINAL_STATUSES
                    or (iso_time(row["updated_at"]) or "") != after_updated_at
                    or has_events
                ):
                    return full_snapshot()
                if remaining <= 0:
                    if quiet_reply:
                        return {
                            "execution_id": execution_id,
                            "status": str(row["status"] or ""),
                            "unchanged": True,
                        }
                    return full_snapshot()
                # Dev/Release and rolling generations share PostgreSQL, not this
                # process's Condition. Observe peer commits within the same
                # bounded interval as admission, including quiet cancellations.
                self.changes.wait_for_change_signal(
                    "execution", seen, min(remaining, SHARED_SCHEDULER_POLL_SECONDS),
                )
