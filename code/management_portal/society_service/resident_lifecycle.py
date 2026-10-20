"""Durable, resumable effects following a Resident lifecycle state commit."""
from __future__ import annotations

import secrets
import time
from management_common.database_time import instant, epoch_seconds
from management_common.json_columns import json_text, json_value
from contextlib import contextmanager
from typing import Any, Callable

from . import invariant_locks
from .state_store import LineageDataStore


class LifecycleAttemptFailed(RuntimeError):
    """A replay-safe effect has a confirmed terminal failure, not an unknown outcome."""


class LifecycleStepPending(Exception):
    """A durable execution is still running; retain the step without an error."""


class LifecyclePermanentFailure(RuntimeError):
    """Retain a failed operation for inspection without scheduling retries."""


class ResidentLifecycleOperations:
    def __init__(self, store: LineageDataStore) -> None:
        self.store = store

    def enqueue(self, resident_id: str, steps: list[dict[str, Any]]) -> str:
        """Commit with the owning state transaction; one operation per Resident."""

        operation_id = f"resident-operation_{secrets.token_hex(12)}"
        self._insert(operation_id, resident_id, steps)
        return operation_id

    def _insert(self, operation_id: str, resident_id: str, steps: list[dict[str, Any]]) -> None:
        payload = json_text({"steps": steps, "next_step": 0}, ensure_ascii=False)
        self.store.database.write(lambda connection: connection.execute(
            'INSERT INTO resident_lifecycle_operations(operation_id,resident_id,payload_json) VALUES (%s,%s,%s)',
            (operation_id, resident_id, payload),
        ))

    def enqueue_cleanup(self, resident_id: str, steps: list[dict[str, Any]]) -> str:
        """Join deletion effects and receipt acknowledgements in their state transaction."""
        def append(connection):
            row = connection.execute(
                'SELECT operation_id,payload_json FROM resident_lifecycle_operations '
                'WHERE resident_id=%s FOR UPDATE', (resident_id,),
            ).fetchone()
            if row is None:
                return self.enqueue(resident_id, steps)
            payload = json_value(row['payload_json'])
            for step in steps:
                if step not in payload['steps']:
                    payload['steps'].append(step)
            connection.execute(
                'UPDATE resident_lifecycle_operations SET payload_json=%s WHERE operation_id=%s',
                (json_text(payload, ensure_ascii=False), row['operation_id']),
            )
            return str(row['operation_id'])
        return self.store.database.write(append)

    @contextmanager
    def prepare(self, resident_id: str, rollback_steps: Callable[[str], list[dict[str, Any]]]):
        """Reserve compensation before effects, without publishing an identity.

        The caller commits the reservation in its final identity transaction.
        Any other exit leaves recovery to the same lifecycle effect executor.
        """
        if invariant_locks.lock_held():
            raise RuntimeError("Preparation must run outside state scopes")
        operation_id = f"resident-operation_{secrets.token_hex(12)}"
        with invariant_locks.resident_lifecycle_lock(operation_id) as owned:
            if not owned:
                raise RuntimeError("Preparation operation is already owned")
            self._insert(operation_id, resident_id, rollback_steps(operation_id))
            yield operation_id

    def stage_preparation_execution(self, operation_id: str, request: dict[str, Any]) -> None:
        """Write the immutable request before dispatch, retaining its receipt."""
        def stage(connection):
            row = connection.execute(
                'SELECT payload_json FROM resident_lifecycle_operations WHERE operation_id=%s',
                (operation_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Registration preparation no longer exists")
            payload = json_value(row["payload_json"])
            step = payload["steps"][0]
            if step["kind"] != "registration_abort":
                raise RuntimeError("Registration preparation already committed")
            previous = step.get("execution_request")
            if previous and previous != request:
                raise ValueError("Registration execution request cannot change")
            step["execution_request"] = request
            payload["steps"] = [step, {
                "kind": "execution_ack", "execution_id": request["execution_id"],
                "actor": request["actor"],
            }]
            connection.execute(
                'UPDATE resident_lifecycle_operations SET payload_json=%s WHERE operation_id=%s',
                (json_text(payload, ensure_ascii=False), operation_id),
            )
        self.store.database.write(stage)

    def commit_preparation(self, operation_id: str) -> None:
        """Atomically replace compensation with receipt cleanup on publication."""
        def commit(connection):
            row = connection.execute(
                'SELECT payload_json FROM resident_lifecycle_operations WHERE operation_id=%s',
                (operation_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Registration preparation no longer exists")
            payload = json_value(row["payload_json"])
            payload["steps"] = [step for step in payload["steps"] if step["kind"] == "execution_ack"]
            if not payload["steps"]:
                self.complete(operation_id)
            else:
                connection.execute(
                    'UPDATE resident_lifecycle_operations SET payload_json=%s WHERE operation_id=%s',
                    (json_text(payload, ensure_ascii=False), operation_id),
                )
        self.store.database.write(commit)

    def complete(self, operation_id: str) -> None:
        self.store.database.write(lambda connection: connection.execute(
            'DELETE FROM resident_lifecycle_operations WHERE operation_id=%s', (operation_id,),
        ))

    def pending(self, resident_id: str) -> bool:
        with self.store.database.read() as connection:
            return connection.execute(
                'SELECT 1 FROM resident_lifecycle_operations WHERE resident_id=%s', (resident_id,),
            ).fetchone() is not None

    def enqueue_transfer(self, resident_id: str, steps: list[dict[str, Any]], source_id: str, target_id: str) -> str:
        from management_portal.hatg.kernel import current_instruction_id

        def commit():
            operation_id = self.enqueue(resident_id, steps)
            self.store.database.write(lambda connection: connection.execute(
                'INSERT INTO resident_workspace_transfers VALUES (%s,%s,%s,%s,%s) ON CONFLICT(resident_id) DO UPDATE SET operation_id=excluded.operation_id,source_lineage_id=excluded.source_lineage_id,target_lineage_id=excluded.target_lineage_id,instruction_id=excluded.instruction_id',
                (resident_id, operation_id, source_id, target_id, current_instruction_id()),
            ))
            return operation_id

        return self.store.transaction(commit)

    def transfer(self, operation_id: str) -> dict[str, Any]:
        with self.store.database.read() as connection:
            row = connection.execute('SELECT * FROM resident_workspace_transfers WHERE operation_id=%s', (operation_id,)).fetchone()
        if row is None:
            raise KeyError("Resident workspace transfer not found")
        return dict(row)

    def observe_committed_transfer(self, operation_id: str, resident_id: str) -> dict[str, Any]:
        """Observe the current transfer, committed atomically with its outbox."""
        transfer = self.transfer(operation_id)
        if transfer["resident_id"] != resident_id:
            raise ValueError("Resident workspace transfer identity mismatch")
        with self.store.database.read() as connection:
            row = connection.execute(
                'SELECT resident_id,payload_json,available_at,last_error FROM resident_lifecycle_operations WHERE operation_id=%s', (operation_id,),
            ).fetchone()
        result: dict[str, Any] = {
            "operation_id": operation_id,
            "resident_id": resident_id,
            "operation_type": "workspace_transfer",
            "status": "completed",
            "phase": "completed",
            "terminal": True,
            "error": None,
            "retry_at": None,
        }
        if row is None:
            return result
        payload = json_value(row["payload_json"])
        steps = payload["steps"]
        if row["resident_id"] != resident_id or not any(step.get("kind") == "workspace_move" for step in steps):
            raise ValueError("Resident operation does not match its workspace transfer")
        index = int(payload["next_step"])
        error = str(row["last_error"] or "")
        result.update({
            "status": "retrying" if error else "pending",
            "phase": str(steps[index]["kind"]) if index < len(steps) else "finalizing",
            "terminal": False,
            "completed_steps": index,
            "total_steps": len(steps),
            "error": {"message": error, "retryable": True} if error else None,
            "retry_at": epoch_seconds(row["available_at"]) or None,
        })
        return result

    def pending_lineage_transfer(self, lineage_id: str) -> bool:
        """Both Lineage folders must survive until their transfer outbox settles."""
        with self.store.database.read() as connection:
            query = (
                "SELECT 1 FROM resident_lifecycle_operations AS operation "
                "WHERE EXISTS (SELECT 1 FROM jsonb_array_elements("
                "COALESCE(operation.payload_json::jsonb -> 'steps', '[]'::jsonb)) AS step(value) "
                "WHERE step.value ->> 'kind'='workspace_move') "
                "AND EXISTS (SELECT 1 FROM jsonb_array_elements("
                "COALESCE(operation.payload_json::jsonb -> 'steps', '[]'::jsonb)) AS step(value) "
                "WHERE step.value ->> 'kind'='resident_detach' "
                "AND step.value ->> 'lineage_id'=%s) LIMIT 1"
            )
            return connection.execute(
                query,
                (lineage_id,),
            ).fetchone() is not None

    def require_lineage_transfer_settled(self, lineage_id: str) -> None:
        if self.pending_lineage_transfer(lineage_id):
            raise ValueError("Lineage has a pending workspace transfer; wait for completion before deleting")

    def ready(self) -> list[str]:
        with self.store.database.read() as connection:
            rows = connection.execute(
                "SELECT operation_id FROM resident_lifecycle_operations WHERE available_at<=%s "
                "AND COALESCE(payload_json::jsonb ->> 'status','') != 'failed' ORDER BY available_at,operation_id",
                (instant(time.time()),),
            ).fetchall()
        ready = []
        for row in rows:
            operation_id = str(row["operation_id"])
            with invariant_locks.resident_lifecycle_lock(operation_id) as owned:
                if owned:
                    ready.append(operation_id)
            if len(ready) == 32:
                break
        return ready

    def run(self, operation_id: str, apply: Callable[[str, dict[str, Any]], None]) -> str:
        """Effects are idempotent; persist the step cursor after each success."""

        if invariant_locks.lock_held():
            raise RuntimeError("Lifecycle effects must run outside state scopes")
        with invariant_locks.resident_lifecycle_lock(operation_id) as owned:
            if not owned:
                return ""
            with self.store.database.read() as connection:
                row = connection.execute(
                    "SELECT resident_id,payload_json FROM resident_lifecycle_operations WHERE operation_id=%s AND available_at<=%s "
                    "AND COALESCE(payload_json::jsonb ->> 'status','') != 'failed'",
                    (operation_id, instant(time.time())),
                ).fetchone()
            if row is None:
                return ""
            payload = json_value(row["payload_json"])
            try:
                for index in range(int(payload["next_step"]), len(payload["steps"])):
                    attempt = int(payload.get("step_attempt", 0))
                    effect_id = f"{operation_id}:{index}"
                    if attempt:
                        effect_id += f":retry-{attempt}"
                    apply(effect_id, payload["steps"][index])
                    payload["next_step"] = index + 1
                    payload.pop("step_attempt", None)
                    self.store.database.write(lambda connection: connection.execute(
                        "UPDATE resident_lifecycle_operations SET payload_json=%s,last_error='' WHERE operation_id=%s",
                        (json_text(payload, ensure_ascii=False), operation_id),
                    ))
                self.complete(operation_id)
                return str(row["resident_id"])
            except Exception as exc:
                if isinstance(exc, LifecyclePermanentFailure):
                    payload["status"] = "failed"
                if isinstance(exc, LifecycleAttemptFailed):
                    payload["step_attempt"] = int(payload.get("step_attempt", 0)) + 1
                last_error = "" if isinstance(exc, LifecycleStepPending) else f"{type(exc).__name__}: {exc}"[:2000]
                self.store.database.write(lambda connection: connection.execute(
                    'UPDATE resident_lifecycle_operations SET payload_json=%s,available_at=%s,last_error=%s WHERE operation_id=%s',
                    (json_text(payload, ensure_ascii=False), instant(time.time() + 5), last_error, operation_id),
                ))
                return ""

    def health(self) -> dict[str, Any]:
        with self.store.database.read() as connection:
            row = connection.execute(
                "SELECT count(*) FILTER (WHERE COALESCE(payload_json::jsonb ->> 'status','') != 'failed') AS pending,"
                "count(*) FILTER (WHERE payload_json::jsonb ->> 'status' = 'failed') AS failed,"
                "coalesce(max(last_error) FILTER (WHERE COALESCE(payload_json::jsonb ->> 'status','') != 'failed'),'') AS last_error "
                "FROM resident_lifecycle_operations",
            ).fetchone()
        return dict(row)
