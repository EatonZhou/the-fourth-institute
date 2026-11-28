"""Translate PostgreSQL-owned effective history to typed Codex response items.

Selection, cutoffs and compaction ownership remain with AgentData/Lineage.
This adapter never reads a Provider rollout or treats media bytes as text.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _has_media(value: Any) -> bool:
    if isinstance(value, Mapping):
        kind = value.get("type")
        if (kind in {"input_image", "inputImage"} and any(key in value for key in ("image_url", "imageUrl"))) or (
            kind == "input_file" and any(key in value for key in ("file_data", "file_id", "file_url"))
        ) or (
            kind == "image" and any(key in value for key in ("source", "data", "mimeType"))
        ) or (kind == "image_url" and "image_url" in value):
            return True
        return any(_has_media(item) for item in value.values())
    return isinstance(value, list) and any(_has_media(item) for item in value)


def _output_part(part: Mapping[str, Any]) -> dict[str, Any]:
    kind = part.get("type")
    if kind in {"inputText", "inputImage"}:
        part = {**part, "type": {"inputText": "input_text", "inputImage": "input_image"}[kind]}
        if "imageUrl" in part:
            part["image_url"] = part.pop("imageUrl")
        kind = part["type"]
    if (kind == "input_image" and isinstance(part.get("image_url"), str) and part["image_url"]) or (
        kind == "input_file" and any(isinstance(part.get(key), str) and part[key]
                                      for key in ("file_data", "file_id", "file_url"))
    ):
        return copy.deepcopy(dict(part))
    if kind in {"text", "input_text", "output_text"} and isinstance(part.get("text"), str) and set(part) <= {"type", "text"}:
        return {"type": "input_text", "text": part["text"]}
    if kind == "image" and set(part) <= {"type", "mimeType", "data"} and part.get("mimeType") and part.get("data"):
        return {"type": "input_image", "image_url": f"data:{part['mimeType']};base64,{part['data']}"}
    if kind == "image" and set(part) == {"type", "source"} and isinstance(part["source"], Mapping):
        source = part["source"]
        if set(source) == {"type", "media_type", "data"} and source["type"] == "base64" and source.get("media_type") and source.get("data"):
            return {"type": "input_image", "image_url": f"data:{source['media_type']};base64,{source['data']}"}
        if set(source) == {"type", "url"} and source["type"] == "url" and isinstance(source.get("url"), str) and source["url"]:
            return {"type": "input_image", "image_url": source["url"]}
    if kind == "image_url" and set(part) == {"type", "image_url"}:
        image = part["image_url"]
        if isinstance(image, Mapping) and set(image) - {"url", "detail"}:
            raise ValueError("unsupported historical image metadata")
        url = image.get("url") if isinstance(image, Mapping) else image
        if isinstance(url, str) and url:
            result = {"type": "input_image", "image_url": url}
            if isinstance(image, Mapping) and image.get("detail"):
                result["detail"] = image["detail"]
            return result
    raise ValueError(f"unsupported Codex historical output part: {kind}")


def _tool_output(value: Any) -> str | list[dict[str, Any]]:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(part, Mapping) and "type" in part for part in value):
        return [_output_part(part) for part in value]
    if isinstance(value, Mapping) and "result" in value:
        inner = _tool_output(value["result"])
        extra = {key: item for key, item in value.items() if key != "result"}
        if not extra:
            return inner
        if _has_media(value):
            raise ValueError("unsupported metadata alongside historical tool media")
        return _json(value)
    # MCP uses content, Claude uses output, and common native tool events
    # use contentItems. Only
    # typed block lists are media envelopes, not arbitrary business JSON.
    field = next((key for key in ("content", "output", "contentItems")
                  if isinstance(value, Mapping) and isinstance(value.get(key), list)
                  and all(isinstance(part, Mapping) and part.get("type") in {
                      "text", "input_text", "output_text", "image", "image_url", "input_image", "input_file",
                      "inputText", "inputImage",
                  } for part in value[key])), None)
    if field is not None:
        parts = [_output_part(part) for part in value[field]]
        extra = {key: item for key, item in value.items() if key != field}
        if extra:
            if _has_media(value):
                raise ValueError("unsupported metadata alongside historical tool media")
            return _json(value)
        return parts
    if _has_media(value):
        raise ValueError("unsupported historical tool media envelope")
    return _json(value)


def is_display_only_reasoning(segment: Mapping[str, Any]) -> bool:
    """A display projection stays in the trace but cannot be appended to a live Codex thread."""
    if segment.get("segment_type") != "model":
        return False
    content = segment.get("content")
    if isinstance(content, Mapping) and content.get("type"):
        return content["type"] == "reasoning" and not content.get("encrypted_content")
    return (segment.get("metadata") or {}).get("kind") == "reasoning"


def response_items_from_segments(segments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Preserve a selected window's order, media and native compaction items."""
    items: list[dict[str, Any]] = []
    for segment in segments:
        kind = segment.get("segment_type")
        content = segment.get("content")
        metadata = segment.get("metadata") or {}
        if kind == "compaction":
            replacement = content.get("replacement_history") if isinstance(content, Mapping) else None
            if not isinstance(replacement, list) or not replacement or any(
                not isinstance(item, Mapping) or not isinstance(item.get("type"), str)
                for item in replacement
            ):
                raise ValueError("Codex Fork requires PostgreSQL compaction replacement_history items")
            # replacement_history already IS the effective sequence. In
            # particular its image URLs and encrypted compaction are not prose.
            items.extend(copy.deepcopy(replacement))
            continue
        if kind not in {"input", "model", "tool", "harness"}:
            raise ValueError(f"unsupported Codex history segment: {kind}")
        if isinstance(content, Mapping) and isinstance(content.get("type"), str):
            items.append(copy.deepcopy(dict(content)))
            continue
        if kind == "tool":
            if not isinstance(content, Mapping):
                raise ValueError("unsupported non-structured historical tool")
            name = content.get("tool_name")
            call_id = content.get("call_id")
            if not isinstance(name, str) or not name.strip() or not isinstance(call_id, str) or not call_id.strip():
                raise ValueError("historical tool requires name and call_id")
            if "input" not in content or not ("result" in content or content.get("error") is not None):
                raise ValueError("historical tool requires recorded arguments and outcome")
            if content.get("external_effect_refs") or set(content) - {
                "tool_name", "tool_namespace", "call_id", "input", "result", "error", "external_effect_refs",
            }:
                raise ValueError("unsupported metadata on historical tool call")
            namespace = content.get("tool_namespace") or ""
            arguments = content.get("input")
            if isinstance(arguments, Mapping) and set(arguments) == {"arguments"}:
                arguments = arguments["arguments"]
            output = content.get("result")
            outcome = {}
            if content.get("error") is not None:
                outcome["error"] = content["error"]
            if segment.get("status") in {"failed", "cancelled", "canceled"}:
                outcome["status"] = segment["status"]
            if outcome:
                output = {"result": output, **outcome}
            items.extend([
                {"type": "function_call", "call_id": call_id,
                 "name": name, **({"namespace": namespace} if namespace else {}),
                 "arguments": arguments if isinstance(arguments, str) else _json(arguments)},
                {"type": "function_call_output", "call_id": call_id, "output": _tool_output(output)},
            ])
            continue
        if metadata.get("kind") == "reasoning":
            raise ValueError("Cannot preserve reasoning without complete native history")
        parts = (metadata.get("message") or {}).get("parts") or []
        if any(part.get("type") != "text" for part in parts):
            raise ValueError("Historical attachment lacks a frozen content block")
        role = metadata.get("role") or {"input": "user", "model": "assistant"}.get(kind)
        if role == "agent":
            role = "assistant"
        if role not in {"system", "developer", "user", "assistant"}:
            raise ValueError(f"unsupported historical {kind} message role: {role}")
        if not isinstance(content, str):
            raise ValueError(f"unsupported historical {kind} content")
        item: dict[str, Any] = {
            # Segment identities belong to Portal provenance. Invented
            # Responses item IDs are rejected by the model on replay.
            "type": "message", "role": role,
            "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": content}],
        }
        phase = metadata.get("phase") or metadata.get("kind")
        if role == "assistant" and phase in {"commentary", "final_answer"}:
            item["phase"] = phase
        items.append(item)
    for item in items:
        if item.get("type") == "reasoning" and not item.get("encrypted_content"):
            raise ValueError("Cannot preserve native reasoning without encrypted content")
    return items
