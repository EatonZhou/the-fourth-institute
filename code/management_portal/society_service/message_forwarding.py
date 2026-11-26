"""Read references for a forwarded message with the forwarding caller's access."""

from __future__ import annotations

from typing import Any

from management_portal import portal_features
from management_portal.hatg.contracts import invalid, not_found


def read_sources(caller: str, references: list[Any]) -> list[dict[str, Any]]:
    return [_read_source(caller, reference) for reference in references]


def _read_source(caller: str, reference: Any) -> dict[str, Any]:
    if not isinstance(reference, dict):
        raise invalid("messages 的每一项必须包含来源和 message_id")

    for _feature, source in portal_features.contributions("conversation.forwarding"):
        if reference.get(source.FORWARD_FIELD):
            return source.forward_source(caller, reference[source.FORWARD_FIELD], reference.get("message_id"))

    from . import resident_protocol

    resident_id = str(reference.get("resident_id") or "").strip().lower()
    message_id = str(reference.get("message_id") or "").strip()
    if not resident_id or not message_id:
        raise invalid("Resident 消息引用必须包含 resident_id 与 message_id")
    message = resident_protocol.get_resident_message(resident_id, caller, message_id)
    if not message:
        raise not_found(f"消息不存在：{message_id}")
    return {
        "role": str(message.get("role") or ""),
        "author": str(message.get("author_id") or ""),
        "title": str(message.get("title") or ""),
        "content": "\n\n".join(
            str(part["text"])
            for part in message.get("content_parts") or []
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
        ),
    }
