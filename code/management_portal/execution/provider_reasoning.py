from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _blocks(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for block in value:
        text = block if isinstance(block, str) else (
            block.get("text") if isinstance(block, Mapping) else None
        )
        if isinstance(text, str) and text.strip():
            result.append(text)
    return result


@dataclass(frozen=True)
class ProviderReasoning:
    item_id: str
    content: dict[str, Any]
    formats: tuple[str, ...]


def _item_reasoning_tokens(item: Mapping[str, Any]) -> int | None:
    """Accept explicit item-level counts only, never turn/response totals."""
    usage = item.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    details = usage.get("output_tokens_details")
    details = details if isinstance(details, Mapping) else {}
    for value in (item.get("reasoning_tokens"), usage.get("reasoning_tokens"), details.get("reasoning_tokens")):
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def provider_reasoning(event: Mapping[str, Any]) -> ProviderReasoning | None:
    method = str(event.get("method") or "")
    event_type = str(event.get("type") or "")
    params = event.get("params")
    params = params if isinstance(params, Mapping) else {}
    if method == "item/completed":
        item = params.get("item")
    elif event_type == "response_item":
        item = event.get("payload")
    else:
        return None
    if not isinstance(item, Mapping) or item.get("type") not in {
        "reasoning", "thinking", "redacted_thinking",
    }:
        return None
    if item.get("status") in {"in_progress", "incomplete", "failed", "cancelled"}:
        return None
    content: dict[str, Any] = {}
    text = _blocks(item.get("content")) or _blocks(
        item.get("thinking") or item.get("text") or item.get("message")
    )
    summary = _blocks(item.get("summary"))
    encrypted = item.get("encrypted_content")
    if item.get("type") == "redacted_thinking":
        encrypted = item.get("data")
    formats: list[str] = []
    if text:
        content["text"] = text
        formats.append("full")
    if summary:
        content["summary"] = summary
        formats.append("summary")
    has_encrypted = (isinstance(encrypted, str) and bool(encrypted)) or item.get("encrypted") is True
    if has_encrypted and not text:
        # Ciphertext is not a useful record of the model's thinking. Retain
        # only its presence; readable summaries are still kept as summaries.
        content["encrypted"] = True
        formats.append("encrypted")
    if not formats:
        return None
    tokens = _item_reasoning_tokens(item)
    if tokens is not None:
        content["reasoning_tokens"] = tokens
    if text and isinstance(item.get("signature"), str) and item["signature"]:
        content["signature"] = item["signature"]
    item_id = str(item.get("id") or item.get("update_id") or "")
    return ProviderReasoning(
        item_id=item_id,
        content=content,
        formats=tuple(formats),
    )
