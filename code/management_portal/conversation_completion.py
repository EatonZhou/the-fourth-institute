from __future__ import annotations

import json
from typing import Any


COMPLETION_SCHEMA_VERSION = 1
READ_SIGNATURE_PREFIX = "actor-completion-v7:"


def _current_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != COMPLETION_SCHEMA_VERSION:
        return None
    completion_id = str(value.get("completion_id") or "").strip()
    if not completion_id:
        return None
    return {
        "schema_version": COMPLETION_SCHEMA_VERSION,
        "request_actor": str(value.get("request_actor") or ""),
        "completion_id": completion_id,
        "input_message_id": str(value.get("input_message_id") or ""),
        "completed_at": str(value.get("completed_at") or ""),
    }


def completion_signature(value: object) -> str:
    payload = _current_payload(value)
    if payload is None:
        return ""
    return READ_SIGNATURE_PREFIX + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def completion_payload(value: object) -> dict[str, Any] | None:
    """The fields of a completion signature, or None when it is not a current one."""
    marker = str(value or "").strip()
    if not marker.startswith(READ_SIGNATURE_PREFIX):
        return None
    try:
        decoded = json.loads(marker[len(READ_SIGNATURE_PREFIX) :])
    except (TypeError, ValueError):
        return None
    return _current_payload(decoded)


def marker_order(value: object) -> tuple[str, str, str]:
    marker = str(value or "").strip()
    payload = completion_payload(marker)
    if payload is None:
        return "", "", ""
    return (
        payload["completed_at"],
        payload["completion_id"] or payload["input_message_id"],
        marker,
    )
