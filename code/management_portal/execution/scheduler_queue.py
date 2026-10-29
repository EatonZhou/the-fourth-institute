"""The execution queue: submission, queued changes, cancellation and terminal retention."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from management_common.database_time import iso_time
from management_common.principal import OperationIdentity, actor_principal

from .contracts import TERMINAL_STATUSES, ExecutionRequest, execution_timestamp
from .scheduler_capacity import SchedulerCapacity
from .scheduler_changes import SchedulerChanges
from .scheduler_recovery import LeaseRecovery
from .scheduler_rows import compact_json, json_object, turn_kept_running
from .scheduler_views import SchedulerViews


# A terminal result nobody acknowledged within a week has no caller left waiting for it.
UNACKNOWLEDGED_TERMINAL_RETENTION_SECONDS = 7 * 24 * 60 * 60


class ExecutionQueue:
    """Changes of executions on behalf of their callers."""

    def __init__(self, accounts: Any, *, changes: SchedulerChanges, capacity: SchedulerCapacity,
                 recovery: LeaseRecovery, views: SchedulerViews) -> None:
        self.accounts = accounts
        self.changes = changes
        self.capacity = capacity
        self.recovery = recovery
        self.views = views

    def submit(self, request: ExecutionRequest, *, prepare=None) -> dict[str, Any]:
        request.validate()
        from .agent_data_store import get_agent_data_store
        from .execution_lifecycle import check_new_submission
        from .submission_identity import SubmissionIdentity, submission_digest
        get_agent_data_store()
        identity = SubmissionIdentity.from_request(request)
        original_digest = identity.digest
        now = execution_timestamp()
        digest_key = f"request_digest:{request.execution_id}"
        with self.changes.connect() as connection:
            existing = connection.execute(
                'SELECT request_json FROM executions WHERE execution_id = %s',
                (request.execution_id,),
            ).fetchone()
            if existing is None and not connection.execute(
                # The digest row reserves the id: a concurrent submit of the
                # same id waits here and then answers as a duplicate.
                'INSERT INTO execution_store_meta(key,value) VALUES (%s,%s) ON CONFLICT(key) DO NOTHING',
                (digest_key, original_digest),
            ).rowcount:
                existing = connection.execute(
                    'SELECT request_json FROM executions WHERE execution_id = %s',
                    (request.execution_id,),
                ).fetchone()
                if existing is None:
                    # A digest left without its execution.
                    connection.execute(
                        'UPDATE execution_store_meta SET value=%s WHERE key=%s',
                        (original_digest, digest_key),
                    )
            if existing is not None:
                saved = connection.execute("SELECT value FROM execution_store_meta WHERE key=%s", (digest_key,)).fetchone()
                stored_digest = str(saved["value"]) if saved else submission_digest(json_object(existing["request_json"]))
                if not identity.matches(stored_digest):
                    connection.rollback()
                    raise ValueError(
                        "execution id already exists with a different request"
                    )
                connection.commit()
                return self.views.snapshot(request.execution_id)
            check_new_submission(connection, request.execution_id, identity)
            if prepare is not None:
                request = prepare(request)
            capacity_key = self.capacity.capacity_key(request.to_payload())
            nonterminal = connection.execute(
                """
                SELECT 1
                FROM executions
                WHERE status IN ('queued', 'claimed', 'running')
                LIMIT 1
                """
            ).fetchone()
            if nonterminal is None:
                connection.execute("DELETE FROM execution_actor_dispatch_state")
            connection.execute(
                "\n                INSERT INTO executions (\n                    execution_id, capability, actor, priority,\n                    correlation_id, request_json, status, created_at, updated_at\n                ) VALUES (%s, %s, %s, %s, %s, %s, 'queued', %s, %s)\n                ",
                (
                    request.execution_id,
                    request.capability,
                    request.actor,
                    request.priority,
                    request.correlation_id,
                    compact_json(request.to_payload()),
                    now,
                    now,
                ),
            )
            self.changes.event(
                connection,
                request.execution_id,
                "queued",
                {
                    "service_type": request.service_type,
                    "provider": request.provider,
                    "operation": request.operation,
                    "capability": request.capability,
                    "priority": request.priority,
                    "capacity_key": capacity_key,
                },
            )
            connection.commit()
        # Workers wait on this condition between claims.  Persisting a new
        # request without waking them makes every idle submission pay the
        # full long-poll timeout before it can be claimed.
        self.changes.notify("claim")
        return self.views.snapshot(request.execution_id)

    def withdraw_unstarted(
        self, execution_id: str, *, actor: str, binding_id: str,
        operation_id: str, replacement_execution_id: str, performed_by: str,
    ) -> dict[str, Any]:
        """Retire an unclaimed request for its owner's durable input handoff.

        This does not cancel the business input. A claim, a previous attempt,
        a checkpoint or a Stop wins over the handoff. The terminal receipt is
        retained so a lost response can be read back without another action.
        """
        if not all((binding_id, operation_id, replacement_execution_id, performed_by)):
            raise ValueError("unstarted handoff identity is incomplete")
        if replacement_execution_id == execution_id:
            raise ValueError("handoff needs a distinct prepared execution")
        receipt = dict(execution_id=execution_id, binding_id=binding_id,
                       operation_id=operation_id, replacement_execution_id=replacement_execution_id)
        from . import execution_lifecycle
        with self.changes.connect() as connection:
            row = connection.execute(
                'SELECT * FROM executions WHERE execution_id=%s FOR UPDATE', (execution_id,),
            ).fetchone()
            if row is not None and row['actor'] != actor:
                raise PermissionError("execution belongs to another actor")
            previous = execution_lifecycle.unstarted_handoff(connection, execution_id, actor=actor)
            if previous:
                if previous != receipt:
                    raise ValueError("execution already handed off by another operation")
                return {"withdrawn": True, **receipt}
            if row is None:
                raise KeyError(execution_id)
            request = json_object(row['request_json'])
            session = request.get('session') or {}
            if session.get('mode') == 'stateless' and not session.get('binding_id'):
                # Timer/isolated Resident inputs deliberately have no session.
                # Their immutable request still names the Resident and input;
                # use that ownership for the same never-claimed input handoff.
                resident_id = request.get('agent_id') or ''
                metadata = request.get('metadata') or {}
                input_id = (request.get('runtime_options') or {}).get('client_user_message_id')
                if (not resident_id.startswith('resident_')
                        or metadata.get('business_type') != 'resident'
                        or metadata.get('resident_id') != resident_id or not input_id):
                    return {"withdrawn": False, "execution_id": execution_id}
                expected_binding = 'resident:' + resident_id
            elif session.get('mode') == 'persistent' and session.get('binding_id'):
                expected_binding = session['binding_id']
            else:
                return {"withdrawn": False, "execution_id": execution_id}
            if expected_binding != binding_id:
                raise ValueError("execution binding changed")
            if (row['status'] != 'queued' or row['attempts'] or row['owner_worker_id']
                    or row['cancel_requested'] or json_object(row['checkpoint_json'])
                    or request.get('service_type') != 'agent_session' or request.get('operation') != 'turn'):
                return {"withdrawn": False, "execution_id": execution_id}
            self.recovery.finalize_released_cancellation(
                connection, row, error="unstarted execution handed off: " + operation_id,
                details={"actor": actor_principal(performed_by).id, "request_actor": actor,
                         "unstarted_handoff": receipt},
            )
            result = json_object(connection.execute(
                'SELECT result_json FROM executions WHERE execution_id=%s', (execution_id,),
            ).fetchone()['result_json'])
            connection.execute('UPDATE executions SET result_json=%s WHERE execution_id=%s',
                               (compact_json({**result, "unstarted_handoff": receipt}), execution_id))
            execution_lifecycle.record_unstarted_handoff(
                connection, agent_id=request['agent_id'], receipt=receipt,
                performed_by=performed_by, at=execution_timestamp(),
            )
            connection.commit()
        self.changes.notify("control")
        return {"withdrawn": True, **receipt}

    def set_priority(
        self,
        execution_id: str,
        *,
        actor: str,
        priority: int,
        performed_by: str = "",
    ) -> dict[str, Any]:
        performer = actor_principal(performed_by or actor).id
        next_priority = int(priority)
        if not -1_000_000 <= next_priority <= 1_000_000:
            raise ValueError("execution priority is outside the supported range")
        with self.changes.connect() as connection:
            row = connection.execute(
                '\n                SELECT actor, status, request_json\n                FROM executions\n                WHERE execution_id = %s FOR UPDATE\n                ',
                (execution_id,),
            ).fetchone()
            if row is None or str(row["actor"]) != actor:
                connection.rollback()
                raise KeyError(execution_id)
            if str(row["status"]) != "queued":
                connection.rollback()
                raise ValueError("only queued executions can change priority")
            request = json_object(row["request_json"])
            request["priority"] = next_priority
            connection.execute(
                '\n                UPDATE executions\n                SET priority = %s, request_json = %s, updated_at = %s\n                WHERE execution_id = %s\n                ',
                (next_priority, compact_json(request), execution_timestamp(), execution_id),
            )
            self.changes.event(
                connection,
                execution_id,
                "priority_changed",
                {"actor": performer, "request_actor": actor, "priority": next_priority, "identity": OperationIdentity.for_actor(performer).to_dict()},
            )
            connection.commit()
        return self.views.snapshot(execution_id)

    def reassign_queued_route(
        self,
        execution_id: str,
        *,
        actor: str,
        backend_route_id: str,
        performed_by: str = "",
    ) -> dict[str, Any]:
        """Move a queued execution; the caller guarantees its binding is unused."""

        performer = actor_principal(performed_by or actor).id
        route_id = str(backend_route_id or "").strip()
        if not route_id:
            raise ValueError("backend route id is required")
        with self.changes.connect() as connection:
            row = connection.execute(
                'SELECT actor, status, request_json FROM executions WHERE execution_id = %s FOR UPDATE',
                (execution_id,),
            ).fetchone()
            if row is None or str(row["actor"]) != actor:
                connection.rollback()
                raise KeyError(execution_id)
            if str(row["status"]) != "queued":
                connection.rollback()
                raise ValueError("only queued executions can change account")
            request = json_object(row["request_json"])
            session = request.get("session") if isinstance(request.get("session"), dict) else {}
            if (
                str(request.get("service_type") or "") != "agent_session"
            ):
                connection.rollback()
                raise ValueError("only queued agent executions can change account")
            previous_route_id = str(session.get("backend_route_id") or "")
            request = self.accounts.reroute(request, route_id)
            normalized = ExecutionRequest.from_payload(request).to_payload()
            connection.execute(
                'UPDATE executions SET request_json = %s, updated_at = %s WHERE execution_id = %s',
                (compact_json(normalized), execution_timestamp(), execution_id),
            )
            self.changes.event(connection, execution_id, "route_changed", {
                "actor": performer,
                "request_actor": actor,
                "source_backend_route_id": previous_route_id,
                "backend_route_id": route_id,
                "identity": OperationIdentity.for_actor(performer).to_dict(),
            })
            connection.commit()
        return self.views.snapshot(execution_id)

    def cancel(
        self,
        execution_id: str,
        *,
        actor: str,
        reason: str = "",
        performed_by: str = "",
        queued_only: bool = False,
    ) -> dict[str, Any]:
        performer = actor_principal(performed_by or actor).id
        with self.changes.connect() as connection:
            row = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                (execution_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(execution_id)
            if row["actor"] != actor:
                connection.rollback()
                raise PermissionError("execution belongs to another actor")
            status = str(row["status"])
            if queued_only and status != "queued":
                connection.commit()
                return self.views.snapshot(execution_id)
            if status in TERMINAL_STATUSES:
                connection.commit()
                return self.views.snapshot(execution_id)
            details = {"actor": performer, "request_actor": actor, "reason": str(reason or ""), "identity": OperationIdentity.for_actor(performer).to_dict()}
            checkpoint = json_object(row["checkpoint_json"])
            if status == "queued" and isinstance(checkpoint.get("abandoned"), dict):
                # A lost Worker's Provider work waits to be interrupted; the
                # execution still ends only after that, now as cancelled.
                connection.execute(
                    'UPDATE executions SET checkpoint_json = %s, updated_at = %s WHERE execution_id = %s',
                    (compact_json({**checkpoint, "abandoned": {**checkpoint["abandoned"], "status": "cancelled"}}),
                     execution_timestamp(), execution_id),
                )
                self.changes.event(connection, execution_id, "cancellation_requested", details)
            elif status == "queued" and turn_kept_running(checkpoint):
                # A Worker handover left this turn running in the Provider; a
                # Worker interrupts it before the execution ends as cancelled.
                abandoned = {
                    "status": "cancelled",
                    "error": str(reason or "execution cancelled during worker handover"),
                    "previous_worker_id": str((checkpoint.get("worker_handover") or {}).get("worker_id") or ""),
                }
                connection.execute(
                    'UPDATE executions SET checkpoint_json = %s, updated_at = %s WHERE execution_id = %s',
                    (compact_json({**checkpoint, "resumable": False, "abandoned": abandoned}), execution_timestamp(), execution_id),
                )
                self.changes.event(connection, execution_id, "cancellation_requested", details)
            elif status == "queued":
                self.recovery.finalize_released_cancellation(
                    connection, row,
                    error=str(reason or "cancelled before execution"),
                    details=details,
                )
            else:
                connection.execute(
                    "\n                    UPDATE executions\n                    SET cancel_requested = '1', updated_at = %s\n                    WHERE execution_id = %s\n                    ",
                    (execution_timestamp(), execution_id),
                )
                self.changes.event(
                    connection,
                    execution_id,
                    "cancellation_requested",
                    details,
                )
            connection.commit()
        self.changes.notify("control")
        return self.views.snapshot(execution_id)

    def acknowledge(self, execution_id: str, *, actor: str) -> dict[str, Any]:
        with self.changes.connect() as connection:
            row = connection.execute(
                'SELECT actor, status, acknowledged_at FROM executions WHERE execution_id = %s FOR UPDATE',
                (execution_id,),
            ).fetchone()
            if row is None or str(row["actor"]) != str(actor or ""):
                connection.rollback()
                raise KeyError(execution_id)
            if str(row["status"]) not in TERMINAL_STATUSES:
                connection.rollback()
                raise RuntimeError("only terminal executions can be acknowledged")
            acknowledged_at = (iso_time(row["acknowledged_at"]) or "") or execution_timestamp()
            connection.execute(
                "\n                UPDATE executions\n                SET request_json = '{}', checkpoint_json = '{}',\n                    acknowledged_at = %s, updated_at = %s\n                WHERE execution_id = %s\n                ",
                (acknowledged_at, acknowledged_at, execution_id),
            )
            self.changes.event(
                connection,
                execution_id,
                "acknowledged",
                {},
            )
            connection.commit()
        return self.views.snapshot(execution_id, include_result=False)

    def prune_terminal(
        self,
        *,
        older_than_epoch: float,
        unacknowledged_older_than_epoch: float | None = None,
    ) -> int:
        """Delete terminal work items: acknowledged ones past ``older_than_epoch``,
        unacknowledged ones past a week. The trajectory lives in AgentData."""

        def stamp(epoch: float) -> str:
            return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z")

        threshold = stamp(older_than_epoch)
        unacknowledged_threshold = stamp(
            time.time() - UNACKNOWLEDGED_TERMINAL_RETENTION_SECONDS
            if unacknowledged_older_than_epoch is None
            else unacknowledged_older_than_epoch
        )
        with self.changes.connect() as connection:
            # Dev and Release prune side by side; each deletes the rows it locked.
            rows = connection.execute(
                "\n                SELECT execution_id FROM executions\n                WHERE status IN ('completed', 'failed', 'cancelled')\n                  AND ((acknowledged_at IS NOT NULL AND updated_at < %s) OR updated_at < %s)\n                FOR UPDATE SKIP LOCKED\n                ",
                (threshold, unacknowledged_threshold),
            ).fetchall()
            ids = [str(row["execution_id"]) for row in rows]
            if ids:
                connection.executemany(
                    'DELETE FROM executions WHERE execution_id = %s',
                    [(execution_id,) for execution_id in ids],
                )
                connection.executemany(
                    'DELETE FROM execution_store_meta WHERE key IN (%s, %s)',
                    [(f"started:{execution_id}", f"request_digest:{execution_id}") for execution_id in ids],
                )
            connection.commit()
        self.changes.forget_transient_events(ids)
        if ids:
            self.changes.notify("execution")
        return len(ids)
