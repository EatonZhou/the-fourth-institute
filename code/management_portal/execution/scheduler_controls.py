"""Turn controls: input and settings sent to a running Provider turn through its Worker."""
from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from management_common.database_time import iso_time
from management_common.format_tags import schema_matches

from .contracts import ExecutionInputControl, ExecutionTurnSettingsControl, execution_timestamp
from .scheduler_changes import SHARED_SCHEDULER_POLL_SECONDS, SchedulerChanges
from .scheduler_rows import ACTIVE_STATUSES, assert_owner, compact_json, json_object


INPUT_CONTROL_ACCEPTING_STATUSES = ("queued", *ACTIVE_STATUSES)


def _supports_active_turn_input(request: dict[str, Any]) -> bool:
    from .provider_catalog import AGENT_PROVIDERS
    adapter = AGENT_PROVIDERS.get(str(request.get("provider") or ""))
    return bool(adapter and adapter.active_turn_input)


def _input_control_payload(row: Any) -> dict[str, Any]:
    payload = json_object(row["input_json"])
    return {
        **payload,
        "status": str(row["status"]),
        "error": str(row["error"]),
        "created_at": (iso_time(row["created_at"]) or ""),
        "updated_at": (iso_time(row["updated_at"]) or ""),
    }


class TurnControls:
    """Submission, delivery and completion of turn controls."""

    def __init__(self, changes: SchedulerChanges) -> None:
        self.changes = changes

    def submit_input_control(self, control: ExecutionInputControl) -> dict[str, Any]:
        return self._submit_turn_control(control)

    def submit_turn_settings_control(self, control: ExecutionTurnSettingsControl) -> dict[str, Any]:
        return self._submit_turn_control(control)

    def _submit_turn_control(
        self,
        control: ExecutionInputControl | ExecutionTurnSettingsControl,
    ) -> dict[str, Any]:
        control.validate()
        is_input = isinstance(control, ExecutionInputControl)
        description = "input" if is_input else "turn settings"
        with self.changes.connect() as connection:
            # The execution row orders this control against its turn's changes.
            execution = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                (control.execution_id,),
            ).fetchone()
            existing_control = connection.execute(
                'SELECT * FROM execution_input_controls WHERE control_id = %s',
                (control.control_id,),
            ).fetchone()
            if existing_control is not None:
                stored = type(control).from_payload(
                    json_object(existing_control["input_json"])
                )
                same_request = (
                    stored.execution_id == control.execution_id
                    and stored.actor == control.actor
                    and (stored.input == control.input if is_input else stored.settings == control.settings)
                    and (not is_input or not stored.source_segment_id or not control.source_segment_id
                         or stored.source_segment_id == control.source_segment_id)
                    and (
                        not control.expected_turn_id
                        or stored.expected_turn_id == control.expected_turn_id
                    )
                )
                connection.rollback()
                if not same_request:
                    raise ValueError(f"execution {description} control id was reused")
                return _input_control_payload(existing_control)
            if execution is None:
                connection.rollback()
                raise KeyError(control.execution_id)
            if str(execution["status"]) not in INPUT_CONTROL_ACCEPTING_STATUSES:
                connection.rollback()
                raise ValueError("execution has no active turn")
            request = json_object(execution["request_json"])
            if (
                str(request.get("service_type") or "") != "agent_session"
                or not _supports_active_turn_input(request)
                or str(request.get("operation") or "") != "turn"
            ):
                connection.rollback()
                raise ValueError(
                    f"active execution does not support {description} updates"
                )
            checkpoint = json_object(execution["checkpoint_json"])
            turn_id = str(checkpoint.get("turn_id") or "").strip()
            if control.expected_turn_id and control.expected_turn_id != turn_id:
                connection.rollback()
                raise ValueError("active provider turn changed")
            bound_control = replace(control, expected_turn_id=turn_id).validate()
            now = execution_timestamp()
            connection.execute(
                "\n                INSERT INTO execution_input_controls (\n                    control_id, execution_id, actor, input_json,\n                    expected_turn_id, status, error, created_at, updated_at\n                ) VALUES (%s, %s, %s, %s, %s, 'pending', '', %s, %s)\n                ",
                (
                    bound_control.control_id,
                    bound_control.execution_id,
                    bound_control.actor,
                    compact_json(bound_control.to_payload()),
                    bound_control.expected_turn_id,
                    now,
                    now,
                ),
            )
            self.changes.event(
                connection,
                control.execution_id,
                "input_control_requested" if is_input else "turn_settings_control_requested",
                {
                    "control_id": control.control_id,
                    "actor": control.actor,
                    "turn_id": turn_id,
                    **({} if is_input else {"fields": sorted(control.settings)}),
                },
            )
            connection.commit()
            self.changes.notify("control")
            row = connection.execute(
                'SELECT * FROM execution_input_controls WHERE control_id = %s',
                (control.control_id,),
            ).fetchone()
        return _input_control_payload(row)

    def input_control(self, control_id: str) -> dict[str, Any] | None:
        with self.changes.connect() as connection:
            row = connection.execute(
                'SELECT * FROM execution_input_controls WHERE control_id=%s', (control_id,),
            ).fetchone()
            if row is None:
                return None
            return _input_control_payload(row)

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
        target_control_id = str(control_id or "").strip()
        with self.changes.connect() as connection:
            assert_owner(
                connection.execute(
                    'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                    (execution_id,),
                ).fetchone(),
                worker_id=worker_id,
                claim_token=claim_token,
            )
            row = connection.execute(
                'SELECT * FROM execution_input_controls WHERE control_id = %s AND execution_id = %s',
                (target_control_id, execution_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(target_control_id)
            if str(row["status"]) != "pending":
                connection.commit()
                return _input_control_payload(row)
            status = "delivered" if delivered else "failed"
            normalized_error = "" if delivered else str(error or "delivery_failed")
            updated_at = execution_timestamp()
            connection.execute(
                '\n                UPDATE execution_input_controls\n                SET status = %s, error = %s, updated_at = %s\n                WHERE control_id = %s\n                ',
                (status, normalized_error, updated_at, target_control_id),
            )
            # Receipts are shared across Scheduler instances; their transient
            # events are not. Wake peer snapshot observers via the same durable
            # execution change marker used by other runtime transitions.
            connection.execute(
                'UPDATE executions SET updated_at = %s WHERE execution_id = %s',
                (updated_at, execution_id),
            )
            payload = json_object(row["input_json"])
            is_settings = (
                schema_matches(payload.get("schema"), "tfi.execution-turn-settings-control.v1")
            )
            self.changes.event(
                connection,
                execution_id,
                (
                    "turn_settings_control_delivered"
                    if delivered
                    else "turn_settings_control_failed"
                ) if is_settings else (
                    "input_control_delivered"
                    if delivered
                    else "input_control_failed"
                ),
                {
                    "control_id": target_control_id,
                    "error": normalized_error,
                },
            )
            connection.commit()
            self.changes.notify("control")
            row = connection.execute(
                'SELECT * FROM execution_input_controls WHERE control_id = %s',
                (target_control_id,),
            ).fetchone()
        return _input_control_payload(row)

    def wait_for_worker_control(
        self,
        execution_id: str,
        *,
        worker_id: str,
        claim_token: str,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Block until worker control changes or its heartbeat is due."""
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        with self.changes.change_notifications():
            while True:
                seen = self.changes.change_generation("control")
                with self.changes.connect() as connection:
                    row = assert_owner(
                        connection.execute(
                            '\n                            SELECT executions.*, execution_workers.state\n                                   AS owner_worker_state\n                            FROM executions\n                            LEFT JOIN execution_workers\n                              ON execution_workers.worker_id =\n                                 executions.owner_worker_id\n                            WHERE executions.execution_id = %s\n                            ',
                            (execution_id,),
                        ).fetchone(),
                        worker_id=worker_id,
                        claim_token=claim_token,
                    )
                worker_state = str(row["owner_worker_state"] or "")
                handover_requested = worker_state not in {
                    "active",
                }
                payload = {
                    "cancel_requested": bool(row["cancel_requested"]),
                    "status": str(row["status"]),
                    "handover_requested": handover_requested,
                }
                controls: list[Any] = []
                if not handover_requested:
                    with self.changes.connect() as connection:
                        controls = connection.execute(
                            "\n                            SELECT * FROM execution_input_controls\n                            WHERE execution_id = %s AND status = 'pending'\n                            ORDER BY created_at ASC, control_id ASC\n                            ",
                            (execution_id,),
                        ).fetchall()
                turn_controls = [
                    _input_control_payload(control)
                    for control in controls
                ] if not handover_requested else []
                if turn_controls:
                    payload["turn_controls"] = turn_controls
                if (
                    payload["cancel_requested"]
                    or handover_requested
                    or turn_controls
                    or payload["status"] not in ACTIVE_STATUSES
                ):
                    return payload
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return payload
                # Peer Dev/Release commits cannot signal this local Condition.
                # Bound cancel, handover and input delivery latency independently
                # of the Worker's much longer heartbeat interval.
                self.changes.wait_for_change_signal(
                    "control", seen, min(remaining, SHARED_SCHEDULER_POLL_SECONDS),
                )
