"""Content-free evidence for a Resident's canonical context and Fork delivery."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Any


def _codex_response_items():
    """Codex's translation of segments (the history its Fork delivery sends); None without the Codex Provider."""
    try:
        from management_portal.execution.codex_history import response_items_from_segments
    except ModuleNotFoundError as exc:
        if exc.name != "management_portal.execution.codex_history":
            raise
        return None
    return response_items_from_segments


def _native_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Use the delivery serializer's digest without returning prompts or images."""
    encoded = json.dumps(items, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    types = Counter(str(item.get("type") or "") for item in items)
    parts: Counter[str] = Counter()
    text_bytes = image_url_bytes = 0
    calls: Counter[str] = Counter()
    outputs: Counter[str] = Counter()
    for item in items:
        kind = str(item.get("type") or "")
        call_id = str(item.get("call_id") or "")
        if call_id:
            if kind in {"function_call", "custom_tool_call"}:
                calls[call_id] += 1
            elif kind in {"function_call_output", "custom_tool_call_output"}:
                outputs[call_id] += 1
        for field in ("content", "output"):
            content = item.get(field)
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "")
                parts[part_type] += 1
                if field == "content" and part_type in {"input_text", "output_text", "text"}:
                    text_bytes += len(str(part.get("text") or "").encode())
                elif part_type in {"input_image", "image"}:
                    image_url_bytes += len(str(part.get("image_url") or "").encode())
    pairs = [{"call_id": key, "calls": calls[key], "outputs": outputs[key]}
             for key in sorted(calls.keys() | outputs.keys())]
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "serialized_bytes": len(encoded),
            "item_count": len(items), "item_types": dict(types), "content_part_types": dict(parts),
            "message_text_utf8_bytes": text_bytes, "image_url_utf8_bytes": image_url_bytes,
            "tool_mapping": pairs[:200], "tool_mapping_truncated": len(pairs) > 200,
            "unpaired_call_ids": sum(calls[key] != outputs[key] for key in calls.keys() | outputs.keys())}


def _references(segments):
    return [{key: item.get(key, "") for key in ("segment_id", "segment_type", "content_sha256", "provider_turn_id")}
            for item in segments[:1000]]


def _tool_observations(segments):
    """Bounded native identities and outcomes, without arguments or output text."""
    tools = []
    for segment in segments:
        content = segment.get("content")
        if segment.get("segment_type") != "tool" or not isinstance(content, dict) or not content.get("call_id"):
            continue
        row = {key: segment.get(key, "") for key in (
            "segment_id", "execution_id", "status", "started_at", "finished_at", "content_sha256")}
        row.update({key: content.get(key, "") for key in ("call_id", "tool_name", "tool_namespace")})
        observation = (segment.get("metadata") or {}).get("provider_observation") or {}
        if isinstance(observation.get("item_id"), str) and observation["item_id"]:
            row["provider_item_id"] = observation["item_id"]
        if isinstance(observation.get("command_sha256"), str):
            row["command_sha256"] = observation["command_sha256"]
        if type(observation.get("exit_code")) is int:
            row["observed_exit_code"] = observation["exit_code"]
        if content.get("tool_name") == "clock.sleep":
            arguments = (content.get("input") or {}).get("arguments")
            duration = arguments.get("duration_ms") if isinstance(arguments, dict) else None
            if type(duration) is int and duration >= 0:
                row["observed_duration_ms"] = duration
        tools.append(row)
    return {"entries": tools[:200], "truncated": len(tools) > 200}


def _trace_positions(snapshot):
    positions = snapshot.get("trace_positions")
    if positions is None:
        # Rolling upgrades must not present an old server's absent observation
        # as an observed empty trace.
        return None
    return {**{key: positions[key] for key in (
                "schema", "sampled_at", "consistency", "trace_id", "trace_updated_at", "truncated")},
            "entries": [{**{key: entry[key] for key in (
                "ordinal", "segment_id", "segment_type", "content_sha256", "message_id", "execution_id",
                "segment_started_at", "segment_finished_at", "provider_session_ref", "provider_turn_id",
                "provider", "backend_route_id", "backend_session_id")},
                **({"provider_item_id": entry["provider_item_id"]} if entry.get("provider_item_id") else {})}
                for entry in positions["entries"]]}


def _transformations(snapshot):
    return [{**{key: item[key] for key in (
                 "schema", "receipt_id", "trace_id", "operation_id", "operation_type", "recorded_at",
                 "execution_id", "target_segment_id", "target_ordinal")},
             "sources": [{key: source[key] for key in (
                 "ordinal", "segment_id", "message_id", "content_sha256")} for source in item["sources"]]}
            for item in snapshot.get("transformations", [])]


def project_context_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    segments = snapshot["segments"]
    effective = {"segment_count": len(segments),
                 "segment_types": dict(Counter(item["segment_type"] for item in segments)),
                 "first_segment_id": segments[0]["segment_id"] if segments else "",
                 "last_segment_id": segments[-1]["segment_id"] if segments else "",
                 "references": _references(segments), "references_truncated": len(segments) > 1000,
                 "tool_observations": _tool_observations(segments),
                 "compactions": []}
    for segment in segments:
        if segment["segment_type"] == "compaction":
            history = segment["content"].get("replacement_history")
            effective["compactions"].append({"segment_id": segment["segment_id"],
                "content_sha256": segment.get("content_sha256", ""),
                "compressed_from": [row["source_segment_id"] for row in snapshot.get("compressed_from", [])
                                    if row["target_segment_id"] == segment["segment_id"]],
                "compressed_from_truncated": snapshot.get("compressed_from_truncated", False),
                "replacement_history": _native_summary(history) if isinstance(history, list) else None})
    # The canonical evidence works for every provider. Codex conversion is an
    # additional diagnostic; an unsupported shape is reported, never coerced.
    response_items = _codex_response_items()
    if response_items is None:
        effective["codex_history"] = {"available": False, "reason": "provider_not_installed"}
    else:
        try:
            effective["codex_history"] = _native_summary(response_items(segments))
        except ValueError:
            effective["codex_history"] = {"available": False, "reason": "unsupported_context_shape"}
    imports = []
    summaries = {}
    for item in snapshot["imports"]:
        content = item["content"]
        key = tuple((segment["segment_id"], segment.get("provider_turn_id", ""))
                    for segment in item["segments"])
        if key not in summaries:
            summaries[key] = _native_summary(response_items(item["segments"])) if response_items else None
        summary = summaries[key]
        imports.append({"operation_id": item["operation_id"], "state": content["state"],
            "backend_session_id": content["backend_session_id"],
            "history_sha256": content["history_sha256"], "segment_count": len(item["segments"]),
            "references": _references(item["segments"]), "references_truncated": len(item["segments"]) > 1000,
            "matches_recorded_digest": summary is not None and summary["sha256"] == content["history_sha256"],
            "history": summary})
    return {"schema": "tfi.resident-context-evidence.v1",
            "trace": {"segment_count": sum(snapshot["trace_counts"].values()),
                      "segment_types": snapshot["trace_counts"], "positions": _trace_positions(snapshot)},
            "transformations": _transformations(snapshot),
            "transformations_available": "transformations" in snapshot,
            "transformations_truncated": snapshot.get("transformations_truncated", False),
            "effective_context": effective, "fork_deliveries": imports,
            "fork_deliveries_truncated": snapshot["imports_truncated"]}


def read_context_evidence(resident_id: str, actor: str) -> dict[str, Any]:
    from .resident_workspace_domain import resolve_resident_runtime_target
    from .resident_conversation_projection_domain import _can_view_actual_sent_prompts
    from . import execution_client

    if not _can_view_actual_sent_prompts(actor, resident_id):
        raise PermissionError("无权读取模型上下文证据")
    target = resolve_resident_runtime_target(resident_id, actor, require_visible=True)
    return {"resident_id": target.resident_id, **project_context_evidence(
        execution_client.context_evidence_snapshot(target.resident_id))}
