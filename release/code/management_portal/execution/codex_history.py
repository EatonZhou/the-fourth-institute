"""Translate PostgreSQL-owned effective history to typed Codex response items.

Selection, cutoffs and compaction ownership remain with AgentData/Lineage.
This adapter never reads a Provider rollout or treats media bytes as text.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _item_id(prefix: str, segment_id: str) -> str:
    # PostgreSQL identities remain in provenance; Provider item IDs have typed
    # prefixes (fc/msg/rs), independently of the original tool call_id.
    return prefix + "_" + hashlib.sha256(segment_id.encode()).hexdigest()[:32]


def _has_media(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("type") in {"image", "image_url", "input_image", "input_file"}:
            return True
        return any(_has_media(item) for item in value.values())
    return isinstance(value, list) and any(_has_media(item) for item in value)


def _output_part(part: Mapping[str, Any]) -> dict[str, Any]:
    kind = part.get("type")
    if kind in {"input_image", "input_file"}:
        return copy.deepcopy(dict(part))
    if kind in {"text", "input_text", "output_text"} and isinstance(part.get("text"), str):
        return {"type": "input_text", "text": part["text"]}
    if kind == "image" and part.get("mimeType") and part.get("data"):
        return {"type": "input_image", "image_url": f"data:{part['mimeType']};base64,{part['data']}"}
    if kind == "image_url":
        image = part.get("image_url")
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
    if isinstance(value, Mapping) and set(value) == {"result"}:
        return _tool_output(value["result"])
    if isinstance(value, Mapping) and isinstance(value.get("content"), list):
        parts = [_output_part(part) for part in value["content"]]
        extra = {key: item for key, item in value.items() if key != "content"}
        if extra:
            if _has_media(extra):
                raise ValueError("unsupported media outside historical tool content")
            parts.append({"type": "input_text", "text": _json(extra)})
        return parts
    if _has_media(value):
        raise ValueError("unsupported historical tool media envelope")
    return _json(value)


def _reasoning_parts(value: Any, kind: str) -> list[dict[str, str]]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("unsupported historical reasoning content")
    parts = []
    for entry in value:
        text = entry.get("text") if isinstance(entry, Mapping) else entry
        if not isinstance(text, str):
            raise ValueError("unsupported historical reasoning part")
        parts.append({"type": kind, "text": text})
    return parts


def _portable_reasoning(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("type") != "reasoning" or item.get("encrypted_content"):
        return item
    summary = _reasoning_parts(item.get("summary"), "summary_text")
    reasoning = _reasoning_parts(item.get("content"), "reasoning_text")
    if not summary and not reasoning:
        raise ValueError("Codex history reasoning requires encrypted content or readable text")
    return {
        "type": "message", "role": "assistant", "phase": "commentary",
        "content": [{"type": "output_text", "text": "[Historical reasoning]\n" + "\n".join(
            part["text"] for part in [*summary, *reasoning]
        )}],
    }


def response_items_from_segments(segments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Preserve a selected window's order, media and native compaction items."""
    items: list[dict[str, Any]] = []
    for segment in segments:
        kind = segment.get("segment_type")
        content = segment.get("content")
        metadata = segment.get("metadata") or {}
        identity = str(segment["segment_id"])
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
            name = str(content.get("tool_name") or "").strip()
            call_id = str(content.get("call_id") or "").strip()
            if not name or not call_id:
                raise ValueError("historical tool requires name and call_id")
            namespace = str(content.get("tool_namespace") or "").strip()
            arguments = content.get("input")
            if isinstance(arguments, Mapping) and set(arguments) == {"arguments"}:
                arguments = arguments["arguments"]
            output = content.get("result")
            if content.get("error") is not None:
                output = {"result": output, "error": content["error"]}
            items.extend([
                {"type": "function_call", "id": _item_id("fc", identity), "call_id": call_id,
                 "name": name, **({"namespace": namespace} if namespace else {}),
                 "arguments": arguments if isinstance(arguments, str) else _json(arguments)},
                {"type": "function_call_output", "call_id": call_id, "output": _tool_output(output)},
            ])
            continue
        if metadata.get("kind") == "reasoning" and isinstance(content, Mapping):
            summary = _reasoning_parts(content.get("summary"), "summary_text")
            reasoning = _reasoning_parts(content.get("text"), "reasoning_text")
            if not summary and not reasoning and content.get("encrypted"):
                continue  # Existing audit-only placeholder, not recoverable model content.
            if not summary and not reasoning:
                raise ValueError("unsupported historical reasoning content")
            item = {"type": "reasoning", "id": _item_id("rs", identity), "summary": summary}
            if reasoning:
                item["content"] = reasoning
            items.append(item)
            continue
        role = "assistant" if kind == "model" else "user"
        if metadata.get("role") in {"system", "developer"}:
            role = "developer"
        if not isinstance(content, str):
            raise ValueError(f"unsupported historical {kind} content")
        item: dict[str, Any] = {
            "type": "message", "id": _item_id("msg", identity), "role": role,
            "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": content}],
        }
        phase = metadata.get("phase") or metadata.get("kind")
        if role == "assistant" and phase in {"commentary", "final_answer"}:
            item["phase"] = phase
        items.append(item)
    return [_portable_reasoning(item) for item in items]
