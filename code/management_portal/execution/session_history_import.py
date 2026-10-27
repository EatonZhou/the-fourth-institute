"""Strict validation for session replacement, separate from lossy fork prompts."""
from collections.abc import Mapping

from .codex_history import response_items_from_segments


def validate_codex_preservation(segments):
    for segment in segments:
        content = segment.get("content")
        metadata = segment.get("metadata") or {}
        items = content.get("replacement_history", []) if (
            segment.get("segment_type") == "compaction" and isinstance(content, Mapping)
        ) else [content]
        if metadata.get("kind") == "reasoning" and (
            not isinstance(content, Mapping) or content.get("type") != "reasoning"
        ):
            raise ValueError("Cannot preserve reasoning without complete native history")
        for item in items:
            if isinstance(item, Mapping) and item.get("type") == "reasoning" and not item.get("encrypted_content"):
                raise ValueError("Cannot preserve native reasoning by converting it to a transcript")
    return response_items_from_segments(segments)
