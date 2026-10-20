from __future__ import annotations

import secrets
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Iterator

from .state_store import get_lineage_data_store
from .lineage_values import _text
from .lineage_primitives import now_iso as _now_iso


NOTIFICATION_SCHEMA = "tfi.hatg-human-notifications.v1"
MAX_NOTIFICATIONS = 200


def _recipients(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        name = _text(item, 120)
        if name and name.casefold() not in {existing.casefold() for existing in result}:
            result.append(name)
    return result[:200]


def _normalize_notification(raw: object) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    notification_id = _text(item.get("id"), 120)
    if not notification_id:
        raise ValueError("Human Notification id 缺失")
    title = _text(item.get("title"), 160)
    message = _text(item.get("message"), 4000)
    if not title or not message:
        raise ValueError("Human Notification 需要 title 和 message")
    severity = _text(item.get("severity"), 24).lower() or "info"
    if severity not in {"info", "success", "warning", "error"}:
        raise ValueError("Human Notification severity 无效")
    created_at = _text(item.get("created_at"), 80) or _now_iso()
    return {
        "id": notification_id,
        "title": title,
        "message": message,
        "severity": severity,
        "lineage_id": _text(item.get("lineage_id"), 80),
        "trigger_path_id": _text(item.get("trigger_path_id"), 120),
        "recipients": _recipients(item.get("recipients")),
        "created_by": _text(item.get("created_by"), 120),
        "created_at": created_at,
    }


def _normalize(raw: object) -> dict[str, Any]:
    items = raw.get("notifications") if isinstance(raw, dict) else []
    return {
        "schema": NOTIFICATION_SCHEMA,
        "notifications": [
            _normalize_notification(item)
            for item in items or []
            if isinstance(item, dict)
        ][-MAX_NOTIFICATIONS:],
    }


@contextmanager
def _notification_transaction(lineage_id: str) -> Iterator[dict[str, Any]]:
    with get_lineage_data_store().lineage_human_notifications.transaction(lineage_id) as stored:
        runtime = _normalize(stored)
        stored.clear()
        stored.update(runtime)
        yield stored
        normalized = _normalize(stored)
        stored.clear()
        stored.update(normalized)


@contextmanager
def _portal_transaction() -> Iterator[dict[str, Any]]:
    """Notifications without a Lineage live in one Portal record."""

    with get_lineage_data_store().portal_human_notifications() as stored:
        runtime = _normalize(stored)
        stored.clear()
        stored.update(runtime)
        yield stored
        normalized = _normalize(stored)
        stored.clear()
        stored.update(normalized)


def create_notification(
    lineage_id: str,
    *,
    title: object,
    message: object,
    recipients: list[str],
    severity: object = "info",
    trigger_path_id: object = "",
    created_by: object = "",
    notification_id: object = "",
) -> dict[str, Any]:
    item = _normalize_notification(
        {
            "id": _text(notification_id, 120)
            or f"human_notice_{secrets.token_hex(8)}",
            "title": title,
            "message": message,
            "severity": severity,
            "lineage_id": lineage_id,
            "trigger_path_id": trigger_path_id,
            "recipients": recipients,
            "created_by": created_by,
            "created_at": _now_iso(),
        }
    )
    if not item["recipients"]:
        raise ValueError("通知至少需要一位接收者")
    transaction = _notification_transaction(lineage_id) if lineage_id else _portal_transaction()
    with transaction as runtime:
        existing = next(
            (
                value
                for value in runtime["notifications"]
                if value["id"] == item["id"]
            ),
            None,
        )
        if existing is not None:
            return {"notification": deepcopy(existing), "created": False}
        runtime["notifications"].append(item)
        runtime["notifications"] = runtime["notifications"][-MAX_NOTIFICATIONS:]
    # Each recipient's Mailbox row is created with the notification.
    from management_portal import conversation_read_state

    conversation_read_state.alert(
        item["recipients"], conversation_read_state.notification_key(lineage_id, item["id"]), item["id"],
    )
    return {"notification": deepcopy(item), "created": True}


def _addressed_to(notification: dict[str, Any], actor: str) -> bool:
    return any(str(name).casefold() == actor.casefold() for name in notification.get("recipients") or [])


def list_actor_notifications(actor: str, lineage_ids=None) -> list[dict[str, Any]]:
    """Notifications addressed to ``actor``, grouped by Lineage ("" for none, listed only without ``lineage_ids``)."""

    records = get_lineage_data_store().human_notification_records(lineage_ids)
    result = []
    for record in records:
        notifications = [
            notification for notification in _normalize(record["runtime"])["notifications"]
            if _addressed_to(notification, actor)
        ]
        if notifications:
            result.append({
                "lineage_id": record["lineage_id"], "title": record["title"],
                "notifications": notifications,
            })
    return result
