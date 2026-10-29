"""The canonical form of a Chat message and the text it carries."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def message_segment_type(role: str) -> str:
    clean = str(role or "").strip().casefold()
    if clean in {"user", "human"}:
        return "input"
    if clean in {"assistant", "agent", "solver", "error"}:
        return "model"
    return "harness"


def chat_role(value: object) -> str:
    role = str(value or "").strip().casefold()
    if role in {"assistant", "agent"}:
        return "agent"
    if role in {"user", "human"}:
        return "user"
    if role in {"system", "portal", "error"}:
        return "system"
    raise ValueError(f"invalid Resident Message role: {role or '(missing)'}")


def chat_message_text(message: Mapping[str, Any]) -> str:
    return "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, Mapping) and str(part.get("type") or "") == "text"
    )


def answer_text(agent_id: str, message: Mapping[str, Any]) -> str:
    """The text of an Agent reply once its ``portal-question`` fences became its question card.

    The delivery decodes the fences; the captured Segment keeps them.
    """
    from management_common.async_questions import decode_question_content

    parts = [dict(part) for part in message.get("content_parts") or [] if isinstance(part, Mapping)]
    return chat_message_text({
        "content_parts": decode_question_content(parts, agent_id, str(message.get("message_id") or "")),
    })


def canonical_chat_message(message: Mapping[str, Any]) -> dict[str, Any]:
    from management_common.principal import message_attribution, message_quote, message_recipients

    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        raise ValueError("Resident Message identity is required")
    role = chat_role(message.get("role"))
    raw_parts = message.get("content_parts")
    if not isinstance(raw_parts, list):
        raise ValueError("Resident Message content_parts are required")
    result: dict[str, Any] = {
        "message_id": message_id,
        "role": role,
        "content_parts": [
            dict(part) for part in raw_parts if isinstance(part, Mapping)
        ],
        "created_at": str(message.get("created_at") or ""),
    }
    activated_at = str(message.get("activated_at") or "").strip()
    if activated_at:
        result["activated_at"] = activated_at
    result.update(message_attribution(message))
    reply_to = str(message.get("reply_to_message_id") or "").strip()
    if role != "user" and reply_to:
        result["reply_to_message_id"] = reply_to
    recipients = message_recipients(message)
    if recipients:
        result["recipients"] = recipients
    quote = message_quote(message) if role == "user" else {}
    if quote:
        result["quote"] = quote
    usage = message.get("usage")
    if isinstance(usage, Mapping) and usage:
        result["usage"] = dict(usage)
    for key in (
        "provider_response",
        "execution_id",
        "provider_turn_id",
        "business_ref",
        # A held info is delivered with a message; a group member as part of its group.
        "released_with",
        "grouped_into_message_id",
        # An input a Harness gave its Provider on its own (a recovery's re-ask or continue).
        "harness_input",
        "edited_at",
        "retracted_at",
    ):
        value = message.get(key)
        if value not in (None, "", {}):
            result[key] = value
    return result
