from __future__ import annotations

import copy
import difflib
import hashlib
from typing import Any
from urllib.parse import unquote

from . import identity_contract, resident_protocol, resource_client
from .resident_conversation_stream import DEFAULT_HISTORY_LIMIT


DEFAULT_CONTEXT_MAX_MESSAGES = 160
DEFAULT_CONTEXT_MAX_CHARS = 120_000
MAX_CONTEXT_MESSAGES = 500
MAX_CONTEXT_CHARS = 1_000_000
MAX_CONTEXT_MESSAGE_CHARS = 100_000


def _text(value: object) -> str:
    return str(value or "").strip()


def _message_role(item: dict[str, Any]) -> str:
    role = _text(item.get("role")).casefold()
    if role == "agent":
        return "agent"
    if role == "user":
        return "human"
    return ""


def _message_content(item: dict[str, Any]) -> str:
    raw = item.get("content_parts")
    if not isinstance(raw, list):
        raise ValueError("Resident Message content_parts must be a list")
    parts: list[str] = []
    for block in raw:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        value = _text(block.get("text"))
        if value:
            parts.append(value)
    return "\n".join(parts).strip()[:MAX_CONTEXT_MESSAGE_CHARS]


def _normalize_messages(raw_messages: object) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for raw in raw_messages if isinstance(raw_messages, list) else []:
        if not isinstance(raw, dict):
            continue
        role = _message_role(raw)
        content = _message_content(raw)
        if not role or not content:
            continue
        identity = _text(raw.get("message_id"))[:240]
        if not identity:
            raise ValueError("MessageIdentity missing from conversation projection")
        messages.append(
            {
                "id": identity,
                "role": role,
                "speaker": "用户" if role == "human" else "Agent",
                "content": content,
                "created_at": _text(raw.get("created_at"))[:80],
                "_digest": hashlib.sha256(
                    f"{role}\0{content}".encode("utf-8")
                ).hexdigest(),
            }
        )
    return messages


def _lineage_relation(record: dict[str, Any]) -> str:
    attributes = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    for relation in attributes.get("relations") or []:
        if (
            isinstance(relation, dict)
            and _text(relation.get("kind")) == "lineage"
            and _text(relation.get("target_id"))
        ):
            return _text(relation.get("target_id"))
    return ""


def _snapshot(resource_id: str, actor: str, *, max_messages: int) -> dict[str, Any]:
    identity_contract.validate_id(resource_id, "resident")
    descriptor = resource_client.get(resource_id, actor=actor)
    snapshot = resident_protocol.get_resident_conversation_snapshot(
        resource_id,
        actor,
        history_limit=DEFAULT_HISTORY_LIMIT,
    )
    messages = _normalize_messages(snapshot.get("messages"))
    token = str(snapshot.get("next_page_token") or "")
    while token:
        page = resident_protocol.get_conversation_messages(
            resource_id, actor, token, limit=DEFAULT_HISTORY_LIMIT,
        )
        older = _normalize_messages(page.get("messages"))
        # Older pages contribute their count and comparison digest. Keep text
        # only for the newest requested messages, bounding retained content.
        for item in older[:max(0, len(older) + len(messages) - max_messages)]:
            item.pop("content", None)
        messages = older + messages
        next_token = str(page.get("next_page_token") or "")
        if next_token == token:
            raise RuntimeError("Conversation pagination did not advance")
        token = next_token
    state = _text((snapshot.get("state") or {}).get("work_state")) or "idle"
    return {
        "id": resource_id,
        "resource_type": "resident",
        "title": _text(descriptor.get("title")) or resource_id,
        "lineage_id": _lineage_relation(descriptor),
        "state": state,
        "messages": messages,
    }


def _common_window(
    source: list[dict[str, Any]],
    relative: list[dict[str, Any]],
) -> tuple[int, int, int]:
    if not source or not relative:
        return 0, 0, 0
    matcher = difflib.SequenceMatcher(
        None,
        [_text(message.get("_digest")) for message in source],
        [_text(message.get("_digest")) for message in relative],
        autojunk=False,
    )
    match = matcher.find_longest_match(0, len(source), 0, len(relative))
    return match.a, match.b, match.size


def _positive_limit(value: object, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if parsed < 1:
        raise ValueError(f"{label} must be positive")
    return min(parsed, maximum)


def _bounded(
    messages: list[dict[str, Any]],
    *,
    max_messages: int,
    max_chars: int,
) -> tuple[list[dict[str, Any]], int, int, bool]:
    selected: list[dict[str, Any]] = []
    char_count = 0
    content_truncated = False
    for message in reversed(messages[-max_messages:]):
        content = str(message.get("content") or "")
        remaining = max_chars - char_count
        if remaining <= 0:
            break
        item = message
        if len(content) > remaining:
            if selected:
                break
            item = copy.deepcopy(message)
            item["content"] = content[:remaining]
            item["content_truncated"] = True
            content = item["content"]
            content_truncated = True
        selected.append(item)
        char_count += len(content)
    selected.reverse()
    return selected, max(0, len(messages) - len(selected)), char_count, content_truncated


def read_context(
    address: str,
    *,
    actor: str,
    relative_to_address: str = "",
    max_messages: int = DEFAULT_CONTEXT_MAX_MESSAGES,
    max_chars: int = DEFAULT_CONTEXT_MAX_CHARS,
) -> dict[str, Any]:
    actor_id = _text(actor)
    if not actor_id:
        raise PermissionError("context lookup requires a Portal actor")
    message_limit = _positive_limit(max_messages, MAX_CONTEXT_MESSAGES, "max_messages")
    char_limit = _positive_limit(max_chars, MAX_CONTEXT_CHARS, "max_chars")
    source_id = unquote(_text(address)).strip("/")
    source = _snapshot(source_id, actor_id, max_messages=message_limit)
    relative_id = unquote(_text(relative_to_address)).strip("/")
    relative = _snapshot(relative_id, actor_id, max_messages=message_limit) if relative_id else None

    source_messages = list(source["messages"])
    relative_messages = list(relative["messages"]) if relative else []
    lineage_verified = bool(
        relative
        and source["lineage_id"]
        and source["lineage_id"] == relative["lineage_id"]
    )
    source_start, relative_start, common_count = (
        _common_window(source_messages, relative_messages)
        if lineage_verified
        else (0, 0, 0)
    )
    context_mode = (
        "lineage_delta" if lineage_verified and common_count > 0 else "full_context"
    )
    candidates = (
        source_messages[source_start + common_count :]
        if context_mode == "lineage_delta"
        else source_messages
    )
    bounded, truncated, returned_chars, content_truncated = _bounded(
        candidates,
        max_messages=message_limit,
        max_chars=char_limit,
    )
    public_messages = [
        {key: value for key, value in message.items() if key != "_digest"}
        for message in bounded
    ]

    def summary(item: dict[str, Any] | None) -> dict[str, Any]:
        if item is None:
            return {}
        return {
            "id": item["id"],
            "type": item["resource_type"],
            "title": item["title"],
            "state": item["state"],
        }

    return {
        "schema": "tfi.context_lookup.v1",
        "access_mode": "agent_pull",
        "source": summary(source),
        "relative_to": summary(relative),
        "context_mode": context_mode,
        "lineage_verified": lineage_verified,
        "scope": "post_divergence" if context_mode == "lineage_delta" else "full_source",
        "source_message_count": len(source_messages),
        "relative_message_count": len(relative_messages),
        "common_message_count": common_count if context_mode == "lineage_delta" else 0,
        "common_source_start": source_start if context_mode == "lineage_delta" else 0,
        "common_relative_start": relative_start if context_mode == "lineage_delta" else 0,
        "returned_message_count": len(public_messages),
        "truncated_message_count": truncated,
        "returned_char_count": returned_chars,
        "content_truncated": content_truncated,
        "messages": public_messages,
    }
