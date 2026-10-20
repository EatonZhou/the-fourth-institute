"""What a Fork's Chat says about the context it inherited (decision_5bf5bfbd52cb).

A Fork's trace begins with the source's effective window: from its latest
compaction, or the selected turns. The Chat projects only that trace, so the
source's earlier conversation is not in it. This view names the source, the
way and time of the Fork, and what the trace took; it reads the opening
compaction's text only on request.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import resident_access
from .domain_ports import execution_port

_TEXT_PARTS = frozenset({"input_text", "output_text", "text"})
_IMAGE_PARTS = frozenset({"input_image", "image"})


def _compaction_items(content: object) -> tuple[list[dict[str, Any]], bool]:
    """The readable messages of a compaction's replacement history.

    Session instructions (developer and system messages) belong to the session
    that wrote them. A Provider's encrypted summary cannot be shown.
    """
    history = content.get("replacement_history") if isinstance(content, Mapping) else None
    items: list[dict[str, Any]] = []
    encrypted = False
    for item in history if isinstance(history, list) else []:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "message":
            encrypted = encrypted or bool(item.get("encrypted_content"))
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        parts = item.get("content")
        texts: list[str] = [parts] if isinstance(parts, str) else []
        images = 0
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, Mapping):
                continue
            if part.get("type") in _TEXT_PARTS and str(part.get("text") or "").strip():
                texts.append(str(part["text"]))
            elif part.get("type") in _IMAGE_PARTS:
                images += 1
        text = "\n\n".join(texts).strip()
        if text or images:
            items.append({"role": "user" if role == "user" else "agent", "text": text, "image_count": images})
    return items, encrypted


def _source_view(prefix: str, resident_id: str, visible: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """A source by ID; its title and Lineage only for a caller who can see it."""

    record = visible.get(resident_id) or {}
    return {
        f"{prefix}_resident_id": resident_id,
        f"{prefix}_visible": bool(record),
        f"{prefix}_title": str(record.get("title") or ""),
        f"{prefix}_lineage_id": str(record.get("lineage_id") or ""),
    }


def inheritance_view(
    resident_id: str,
    record: Mapping[str, Any],
    actor: str,
    *,
    include_context: bool = False,
) -> dict[str, Any] | None:
    """The Fork card of a derived Resident's Chat; ``None`` for any other Resident."""

    source_id = str(record.get("source_resident_id") or "").strip()
    if not source_id:
        return None
    context_source_id = str(record.get("context_source_resident_id") or "").strip() or source_id
    visible = {
        str(item.get("resident_id") or ""): item
        for item in resident_access.visible_resident_records(
            [source_id, context_source_id], actor, "portal",
        )
    }
    facts = execution_port.fork_inheritance(resident_id, include_compaction=include_context)
    view: dict[str, Any] = {
        **_source_view("source", source_id, visible),
        **_source_view("context_source", context_source_id, visible),
        "fork_mode": str(record.get("fork_mode") or ""),
        "forked_from_at": str(record.get("forked_from_at") or ""),
        "source_message_id": str(record.get("source_message_id") or ""),
        "segment_count": int(facts.get("segment_count") or 0),
        "message_count": int(facts.get("message_count") or 0),
        "last_message_id": str(facts.get("last_message_id") or ""),
        "context_cleared": bool(facts.get("context_cleared")),
        "recorded": bool(facts),
        "opening_compaction": None,
    }
    compaction = facts.get("opening_compaction")
    if isinstance(compaction, Mapping):
        view["opening_compaction"] = {"created_at": str(compaction.get("created_at") or "")}
        if include_context:
            items, encrypted = _compaction_items(compaction.get("content"))
            view["opening_compaction"].update(items=items, encrypted=encrypted)
    return view
