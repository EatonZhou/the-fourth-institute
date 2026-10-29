"""The vocabulary of scheduler rows: statuses, JSON columns, ownership and handover checkpoints."""
from __future__ import annotations

import json
from typing import Any

from management_common.database_time import epoch_seconds, iso_time
from management_common.json_columns import json_text

from .provider_catalog import AGENT_PROVIDERS


ACTIVE_STATUSES = ("claimed", "running")


def compact_json(value: object) -> str:
    return json_text(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def turn_kept_running(checkpoint: dict[str, Any]) -> bool:
    """Whether an unfinished Provider turn may outlive its Worker."""
    from .provider_catalog import AGENT_PROVIDERS
    provider = AGENT_PROVIDERS.get(str(checkpoint.get("provider") or ""))
    return bool(provider and provider.handover_keeps_turn and checkpoint.get("turn_id")
                and not checkpoint.get("provider_completed")
                and not isinstance(checkpoint.get("abandoned"), dict))


def row_payload(
    row: Any,
    *,
    include_result: bool = True,
) -> dict[str, Any]:
    return {
        "execution_id": str(row["execution_id"]),
        "capability": str(row["capability"]),
        "actor": str(row["actor"]),
        "priority": int(row["priority"]),
        "correlation_id": str(row["correlation_id"]),
        "request": json_object(row["request_json"]),
        "status": str(row["status"]),
        "owner_worker_id": str(row["owner_worker_id"]),
        "lease_expires_at": (epoch_seconds(row["lease_expires_at"]) or 0.0),
        "available_at": (epoch_seconds(row["available_at"]) or 0.0),
        # A cancelled turn whose lost Worker's Provider work still waits to
        # be interrupted is stopping too.
        "cancel_requested": bool(row["cancel_requested"]) or (
            (json_object(row["checkpoint_json"]).get("abandoned") or {}).get("status") == "cancelled"
        ),
        "attempts": int(row["attempts"]),
        "checkpoint": json_object(row["checkpoint_json"]),
        "result": json_object(row["result_json"]) if include_result else {},
        "error": str(row["error"]),
        "acknowledged_at": (iso_time(row["acknowledged_at"]) or ""),
        "created_at": (iso_time(row["created_at"]) or ""),
        "updated_at": (iso_time(row["updated_at"]) or ""),
    }


def assert_owner(
    row: Any | None,
    *,
    worker_id: str,
    claim_token: str,
) -> Any:
    if row is None:
        raise KeyError("execution not found")
    if (
        row["owner_worker_id"] != worker_id
        or row["claim_token"] != claim_token
    ):
        raise PermissionError("execution claim ownership changed")
    return row


def handover_checkpoint(
    checkpoint: dict[str, Any], worker_id: str,
) -> dict[str, Any]:
    payload = dict(checkpoint)
    payload["turn_start_reason"] = "backend_service_update"
    if payload.get("turn_id") and not payload.get("provider_completed"):
        payload["worker_handover"] = {
            "worker_id": worker_id,
            "turn_id": str(payload["turn_id"]),
            "backend_session_id": str(payload.get("backend_session_id") or ""),
        }
        provider = AGENT_PROVIDERS.get(str(payload.get("provider") or ""))
        if provider is not None and provider.handover_keeps_turn and payload.get("operation") == "turn":
            # The turn goes on in the Provider's daemon; the next Worker
            # attaches to it instead of asking it again.
            payload["worker_handover"]["turn_kept_running"] = True
    return payload
