"""Select and serialize frozen model context without consulting Chat history."""
from __future__ import annotations

import json
from typing import Any, Mapping

from ..execution.context_seed import context_turn_starts, is_context_user_input as _is_user_input
from .resident_message_contract import derived_message_identity


def requires_initial_context_compaction(state: Mapping[str, Any]) -> bool:
    return (
        str(state.get("context_compaction_policy") or "").strip() == "before_first_turn"
    )


def initial_context_compaction_completed(state: Mapping[str, Any]) -> bool:
    return (
        requires_initial_context_compaction(state)
        and str(state.get("context_compaction_state") or "").strip() == "completed"
        and bool(str(state.get("context_compacted_at") or "").strip())
    )


_MEDIA_TYPE_KEYS = ("media_type", "mime_type", "mimeType")


def _omitted_media(media_type: str, encoded_chars: int) -> str:
    size = max(1, encoded_chars * 3 // 4 // 1024)
    return f"[{media_type or '媒体'}，约 {size} KB，继承的历史只带文字，此处省略]"


def without_inline_media(value: Any) -> Any:
    """Inherited context with base64 media replaced by a short note.

    The inherited history reaches the new session as text, where base64 is
    read as text: a 200 KB screenshot costs about as many tokens as a long
    conversation, and a few of them pushed a Fork's first turn past the
    model's context window.  The note keeps the media type and size; a tool
    call's own input (such as the image file it read) stays beside it.
    """

    if isinstance(value, str):
        if value.startswith("data:") and ";base64," in value[:200]:
            return _omitted_media(value[5:value.index(";")], len(value) - value.index(",") - 1)
        return value
    if isinstance(value, list):
        return [without_inline_media(item) for item in value]
    if isinstance(value, dict):
        media_type = next((str(value[key]) for key in _MEDIA_TYPE_KEYS if isinstance(value.get(key), str)), "")
        return {
            key: _omitted_media(media_type, len(item))
            if key == "data" and media_type and isinstance(item, str) and len(item) > 256
            else without_inline_media(item)
            for key, item in value.items()
        }
    return value


# The inherited history shares the new session's window with the system
# prompt, the tools and the replies.  Measured on a Claude session's usage, a
# Chinese character is about 1.23 tokens and other text about 2.46 characters
# a token; the estimate rounds both up (hundredths of a token per character).
SEED_WINDOW_SHARE = 0.6
DEFAULT_CONTEXT_WINDOW = 200_000
_NON_ASCII_TOKENS_X100 = 125
_ASCII_TOKENS_X100 = 42


def estimated_tokens(text: str) -> int:
    non_ascii = len(text) - len(text.encode("ascii", "ignore"))
    return (non_ascii * _NON_ASCII_TOKENS_X100 + (len(text) - non_ascii) * _ASCII_TOKENS_X100 + 99) // 100


def _message_tokens(item: Mapping[str, Any]) -> int:
    return estimated_tokens("".join(str(part.get("text") or "") for part in item.get("content_parts") or []))


def fit_to_window(
    messages: list[dict[str, Any]], context_window: int,
) -> tuple[list[dict[str, Any]], int]:
    """The most recent whole turns that fit the new session, and how many messages were left out.

    A Fork's history fits its source's window, so this rarely drops anything;
    it keeps a first turn from failing as "Prompt is too long" when the
    history still does not fit (another model, or text the source never held
    at once).  The compaction summary of older history always stays.
    """

    budget = int((context_window or DEFAULT_CONTEXT_WINDOW) * SEED_WINDOW_SHARE)
    if sum(map(_message_tokens, messages)) <= budget:
        return messages, 0
    prelude = [item for item in messages if item.get("source_segment_type") == "compaction"]
    rest = [item for item in messages if item.get("source_segment_type") != "compaction"]
    used = sum(map(_message_tokens, prelude))
    keep_from = len(rest)
    for index in range(len(rest) - 1, -1, -1):
        used += _message_tokens(rest[index])
        if used > budget:
            break
        if rest[index].get("context_turn_start", rest[index].get("role") == "user"):
            keep_from = index
    return prelude + rest[keep_from:], keep_from


def select_segments(
    snapshot: dict[str, Any], *, recent_turns: int = 0,
) -> list[dict[str, Any]]:
    """Select contiguous trace history; Provider IDs only group known inputs."""
    segments = snapshot["segments"]
    if not recent_turns:
        return segments
    starts = context_turn_starts(segments)
    return segments[starts[-recent_turns]:] if len(starts) > recent_turns else segments


def seed_messages(
    snapshot: dict[str, Any], *, recent_turns: int = 0,
) -> list[dict[str, Any]]:
    segments = select_segments(snapshot, recent_turns=recent_turns)
    turn_starts = set(context_turn_starts(segments))
    messages: list[dict[str, Any]] = []
    for index, item in enumerate(segments):
        # A session's one-time startup instructions name that session's Resident;
        # the Fork target starts its own session with its own instructions. An
        # inherited copy (e.g. through an unstarted full Fork) made the model treat
        # the inherited facts as another Resident's.  Besides Portal's record of
        # them, a Provider may record the system and developer messages of its
        # own session (DSH): they are that session's instructions as well.
        metadata = item.get("metadata") or {}
        if metadata.get("kind") == "system_instructions" or (
            item["segment_type"] == "input" and metadata.get("role") in {"system", "developer"}
        ):
            continue
        content = without_inline_media(item.get("content"))
        kind = item["segment_type"]
        if kind == "compaction" and (
            not content or isinstance(content, dict)
            and not content.get("replacement_history")
        ):
            raise ValueError("Fork 来源的 PostgreSQL 压缩记录缺少可恢复的上下文正文")
        # Encrypted-only reasoning is an audit placeholder, not model-readable
        # context. Preserve its trace membership without pretending to decode it.
        if (item.get("metadata") or {}).get("kind") == "reasoning" and isinstance(content, dict):
            if content.get("encrypted") and not (
                content.get("encrypted_content") or content.get("text") or content.get("summary")
            ):
                continue
        text = content if isinstance(content, str) else json.dumps(
            content, ensure_ascii=False, separators=(",", ":"),
        )
        if not text.strip():
            continue
        role = {"input": "user", "model": "agent"}.get(kind, "system")
        if _is_user_input(item):
            role = "user"
        if (item.get("metadata") or {}).get("role") in {"system", "developer"}:
            role = "system"
        messages.append({
            "message_id": derived_message_identity(
                "fork-provider-context", item["segment_id"],
            ),
            "role": role,
            "content_parts": [{"type": "text", "text": text}],
            "created_at": item.get("created_at") or "",
            "source_segment_id": item["segment_id"],
            "source_segment_type": kind,
            "provider_turn_id": item.get("provider_turn_id") or "",
            "context_turn_start": index in turn_starts,
        })
    if not messages and not snapshot.get("empty_context"):
        raise ValueError("Fork 来源没有可验证的有效上下文")
    return messages
