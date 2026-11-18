"""Bounded retained-fact pages for an already-authorized Agent identity.

No session, Chat reset, filesystem or Provider access participates. The caller
owns authorization; this owner derives every Sequence/Execution from agent_id.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from management_common.database_time import iso_time
from management_common.json_columns import json_value

from .agent_data_segments import _decode_content
from .agent_data_traces import trace_id
from .message_projection import project_message


MAX_BODY_BYTES = 8192
MAX_METADATA_BYTES = 4096
MAX_PAGE_BYTES = 256 * 1024
MAX_TOOL_CONTAINER_BYTES = 1024 * 1024
MAX_TOOL_TEXT_BYTES = 16 * 1024


def _tool_text(value: Any) -> Any:
    """Do not materialize inline media or attachments contained in a result."""
    if isinstance(value, list):
        return [_tool_text(item) for item in value]
    if isinstance(value, dict):
        if value.get("type") in {"image", "image_url", "input_image", "file", "input_file", "audio", "input_audio"}:
            return {"type": value["type"], "omitted": "attachment_requires_separate_read"}
        return {key: _tool_text(item) for key, item in value.items()
                if key not in {"imageUrl", "image_url", "image_data", "file_data", "base64"}}
    return value


def read_tool_evidence(store: Any, agent_id: str, *, ordinal: int | None, tool_index: int | None,
                       expected_hash: str, tool_item_id: str, after: Any, limit: int) -> dict[str, Any]:
    if type(ordinal) is not int or not 1 <= ordinal < 2**63:
        raise ValueError("Tool evidence requires an exact positive ordinal")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid tool evidence page limit")
    if tool_index is not None and (type(tool_index) is not int or not 0 <= tool_index < 2**31):
        raise ValueError("Invalid tool index")
    if tool_index is not None and (not isinstance(expected_hash, str) or len(expected_hash) != 64
                                  or not isinstance(tool_item_id, str) or not 1 <= len(tool_item_id) <= 256):
        raise ValueError("Tool body requires the indexed content hash and item ID")
    if tool_index is None and (expected_hash or tool_item_id):
        raise ValueError("Body selectors require tool_index")
    if tool_index is not None and after is not None:
        raise ValueError("Tool body has no continuation cursor")
    with store.database.snapshot() as connection:
        trace = trace_id(connection, agent_id)
        row = connection.execute(
            "SELECT s.segment_id,s.segment_type,s.status,s.content_sha256,s.execution_id,p.byte_length,"
            "s.created_by_agent_id, (s.execution_id='' OR EXISTS (SELECT 1 FROM executions e "
            "WHERE e.execution_id=s.execution_id AND e.agent_id=%s)) AS execution_matches "
            "FROM sequence_segments m JOIN segments s USING(segment_id) "
            "LEFT JOIN payloads p ON p.payload_ref=s.content_ref "
            "WHERE m.sequence_id=%s AND m.ordinal=%s", (agent_id, trace, ordinal),
        ).fetchone()
        if row is None:
            raise KeyError("Tool ordinal is not a member of this Resident trace")
        availability = {"ordinal": ordinal, "segment_id": row["segment_id"],
                        "segment_type": row["segment_type"], "status": row["status"],
                        "content_sha256": row["content_sha256"], "body_limit_bytes": MAX_TOOL_TEXT_BYTES,
                        "container_limit_bytes": MAX_TOOL_CONTAINER_BYTES, "attachment_bytes_read": False,
                        "body_continuation_supported": False}
        result = {"items": [], "next_after": None, "availability": availability}
        digest = str(row["content_sha256"])
        offset = 0
        if after is not None:
            if (not isinstance(after, dict) or set(after) != {"ordinal", "hash", "offset"}
                    or after["ordinal"] != ordinal or after["hash"] != digest
                    or type(after["offset"]) is not int or not 0 <= after["offset"] < 2**31):
                raise ValueError("Tool index cursor no longer matches ordinal/content")
            offset = after["offset"]
        if tool_index is not None and expected_hash != digest:
            raise ValueError("Tool content hash changed")
        if row["created_by_agent_id"] != agent_id or not row["execution_matches"]:
            availability["gap"] = "external_or_unproven_source_requires_separate_authorization"
            return result
        if row["segment_type"] != "tool":
            availability["gap"] = "unsupported_segment_type"
            return result
        if row["byte_length"] is None or row["byte_length"] > MAX_TOOL_CONTAINER_BYTES:
            availability["gap"] = "tool_container_missing_or_exceeds_parse_limit"
            return result
        # JSON parsing stays inside PostgreSQL, guarded by actual payload size.
        # Only the canonical tool writer's Segment type/shape is supported.
        # Native-turn cards have no proven tool-container writer contract.
        document = (
            "SELECT CASE WHEN p.byte_length<=%s AND s.content_type='application/json' "
            "THEN convert_from(p.body,'UTF8')::jsonb ELSE '{}'::jsonb END AS doc "
            "FROM segments s JOIN payloads p ON p.payload_ref=s.content_ref WHERE s.segment_id=%s"
        )
        candidates = (
            "WITH source AS (" + document + "), items AS (SELECT CASE "
            "WHEN jsonb_typeof(doc)='object' AND jsonb_typeof(doc->'tool_name')='string' "
            "AND jsonb_typeof(doc->'call_id')='string' AND doc->>'tool_name'<>'' AND doc->>'call_id'<>'' "
            "AND NOT doc ? 'type' AND NOT doc ? 'tools' "
            "THEN jsonb_build_array(doc) "
            "ELSE '[]'::jsonb END AS tool_items FROM source) "
        )
        # An index page selects only identifiers, not arbitrary metadata/input/output.
        index_rows = connection.execute(
            candidates + "SELECT ordinality-1 AS item_index,left(item->>'call_id',256) AS item_id,"
            "left(item->>'tool_name',256) AS tool_name FROM items, "
            "jsonb_array_elements(tool_items) WITH ORDINALITY AS tool(item,ordinality) "
            "WHERE ordinality>%s AND jsonb_typeof(item)='object' AND item ? 'call_id' AND item ? 'tool_name' "
            "ORDER BY ordinality LIMIT %s",
            (MAX_TOOL_CONTAINER_BYTES, row["segment_id"], tool_index if tool_index is not None else offset,
             1 if tool_index is not None else limit + 1),
        ).fetchall()
        if tool_index is not None:
            if (not index_rows or index_rows[0]["item_index"] != tool_index
                    or index_rows[0]["item_id"] != tool_item_id):
                raise ValueError("Tool item ID/index mismatch")
            # Select one exact item and only bounded input/result JSON text.
            selected = connection.execute(
                candidates + "SELECT "
                "CASE WHEN octet_length((tool_items->%s->'input')::text)<=%s THEN (tool_items->%s->'input')::text END AS input_text,"
                "CASE WHEN octet_length((tool_items->%s->'result')::text)<=%s THEN (tool_items->%s->'result')::text END AS output_text,"
                "(tool_items->%s ? 'input') AS input_recorded,(tool_items->%s ? 'result') AS output_recorded "
                "FROM items",
                (MAX_TOOL_CONTAINER_BYTES, row["segment_id"], tool_index, MAX_TOOL_TEXT_BYTES, tool_index,
                 tool_index, MAX_TOOL_TEXT_BYTES, tool_index, tool_index, tool_index),
            ).fetchone()
            item = {"tool_index": tool_index, "tool_item_id": tool_item_id,
                    "segment_type": row["segment_type"], "status": row["status"], "format": "canonical_tool",
                    "tool_name": index_rows[0]["tool_name"], "ordinal": ordinal,
                    "segment_id": row["segment_id"], "content_sha256": digest,
                    "execution_id": row["execution_id"]}
            for field, recorded in (("input", "input_recorded"), ("output", "output_recorded")):
                text = selected[field + "_text"]
                item[field + "_text"] = json.dumps(_tool_text(json.loads(text)), ensure_ascii=False) if text is not None else ""
                item[field + "_omitted"] = text is None
                if text is None:
                    item[field + "_gap"] = "exceeds_text_limit" if selected[recorded] else "not_recorded"
            result["items"] = [item]
        else:
            result["items"] = [{"tool_index": int(item["item_index"]), "tool_item_id": item["item_id"],
                                "segment_type": row["segment_type"], "status": row["status"], "format": "canonical_tool",
                                "tool_name": item["tool_name"], "ordinal": ordinal,
                                "content_sha256": digest, "segment_id": row["segment_id"]}
                               for item in index_rows[:limit]]
            if len(index_rows) > limit:
                result["next_after"] = {"ordinal": ordinal, "hash": digest,
                                        "offset": result["items"][-1]["tool_index"] + 1}
            if not index_rows:
                availability["gap"] = "no_supported_retained_tool_items"
        return result


def _execution_position(after: Any) -> tuple[str, str]:
    if after is None:
        return "0001-01-01T00:00:00+00:00", ""
    if (not isinstance(after, list) or len(after) != 2
            or not all(isinstance(value, str) for value in after)
            or len(after[0]) > 64 or not 1 <= len(after[1]) <= 128):
        raise ValueError("Invalid execution history position")
    try:
        parsed = datetime.fromisoformat(after[0].replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Invalid execution history position") from exc
    return after[0], after[1]


def _ordinal(after: Any) -> int:
    if after is None:
        return 0
    if type(after) is not int or not 0 <= after < 2**63:
        raise ValueError("Invalid trace history position")
    return after


def _references(part: dict[str, Any]) -> dict[str, Any]:
    # References do not authorize their bytes. Never return inline image data,
    # local paths, signed URLs or arbitrary nested metadata as an attachment.
    result = {"type": str(part.get("type") or "reference")[:64]}
    for key in ("resource_id", "address", "artifact_id", "upload_id", "name"):
        if isinstance(part.get(key), str):
            result[key] = part[key][:2048]
    reference = part.get("file_reference")
    if isinstance(reference, dict):
        result["file_reference"] = {
            key: value[:2048] for key, value in reference.items()
            if key in {"resource_id", "repo_id", "workspace_id", "article_id", "upload_id"}
            and isinstance(value, str)
        }
        relative = reference.get("relative_path")
        if (isinstance(relative, str) and relative and len(relative) <= 2048
                and not PurePosixPath(relative).is_absolute()
                and not PureWindowsPath(relative).drive and "\\" not in relative
                and not any(part in {"..", ""} for part in relative.split("/"))
                and not any(char in relative for char in ("\x00", "\n", "\r"))):
            result["file_reference"]["relative_path"] = relative
    result["bytes_not_read"] = True
    return result


def _segment_item(row: Any, *, messages: bool) -> dict[str, Any]:
    item = {key: str(row[key] or "") for key in (
        "segment_id", "segment_type", "status", "content_ref", "content_sha256", "execution_id",
    )}
    item.update(ordinal=int(row["ordinal"]),
                started_at=iso_time(row["started_at"]) or "",
                finished_at=iso_time(row["finished_at"]) or "",
                content_bytes=int(row["byte_length"] or 0))
    if not row["execution_matches"]:
        item["execution_id"] = ""
    if not messages:
        return item
    if row["foreign_source"]:
        item.update(content_omitted=True, omission_reason="external_source_requires_separate_authorization")
        return item
    metadata = json_value(row["message_metadata"] or {})
    raw = row["payload_body"]
    omitted = raw is None or row["metadata_omitted"]
    content = _decode_content(bytes(raw), str(row["content_type"])) if raw is not None else ""
    message = project_message(str(row["segment_id"]), metadata.get("message") or {}, content)
    item.update({key: message.get(key, "") for key in ("message_id", "role", "author_id", "created_at")})
    parts = []
    for part in message.get("content_parts") or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            parts.append({"type": "text", "text": str(part.get("text") or "")})
        else:
            parts.append(_references(part))
    item.update(content_parts=parts, content_omitted=bool(omitted))
    if omitted:
        item["omission_reason"] = "body_or_metadata_exceeds_history_read_limit_or_is_missing"
    return item


def read_page(store: Any, agent_id: str, *, section: str, after: Any, limit: int) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("History page limit must be between 1 and 100")
    if section not in {"messages", "executions", "segments"}:
        raise ValueError("Unknown retained history section")
    boundary = _execution_position(after) if section == "executions" else _ordinal(after)
    with store.database.snapshot() as connection:
        trace = trace_id(connection, agent_id)
        availability = {
            "agent": "retained" if trace else "not_recorded",
            "trace_sequence_id": trace, "scope": "retained_trace_segments_and_agent_executions",
            "body_limit_bytes": MAX_BODY_BYTES, "metadata_limit_bytes": MAX_METADATA_BYTES,
            "items_limit_bytes": MAX_PAGE_BYTES, "attachment_bytes_read": False,
            "native_session_and_files": "not_checked",
            "omitted_body_read_supported": False,
        }
        if section == "executions":
            rows = connection.execute(
                "SELECT execution_id,agent_id,provider,created_at,started_at,finished_at,status "
                "FROM executions WHERE agent_id=%s AND (created_at,execution_id)>(%s::timestamptz,%s) "
                "ORDER BY created_at,execution_id LIMIT %s",
                (agent_id, *boundary, limit + 1),
            ).fetchall()
            fields = ("execution_id", "agent_id", "provider", "created_at", "started_at", "finished_at", "status")
            items = [{key: (iso_time(row[key]) or "") if key.endswith("_at") else str(row[key] or "")
                      for key in fields} for row in rows[:limit]]
            next_after = [items[-1]["created_at"], items[-1]["execution_id"]] if len(rows) > limit else None
            return {"items": items, "next_after": next_after, "availability": availability}
        if not trace:
            return {"items": [], "next_after": None, "availability": availability}
        # Bound rows and bytes in SQL, before payload/metadata reach Python.
        # Segment pages never select payload.body or metadata_json at all.
        message_columns = (
            ", s.content_type, "
            "(s.created_by_agent_id<>'' AND s.created_by_agent_id<>%s) AS foreign_source, "
            "octet_length(s.metadata_json::text)>%s AS metadata_omitted, "
            "CASE WHEN octet_length(s.metadata_json::text)<=%s "
            "AND (s.created_by_agent_id='' OR s.created_by_agent_id=%s) "
            "THEN s.metadata_json::jsonb ELSE '{}'::jsonb END AS message_metadata, "
            "CASE WHEN p.byte_length<=%s AND (s.created_by_agent_id='' OR s.created_by_agent_id=%s) "
            "THEN p.body ELSE NULL END AS payload_body"
        ) if section == "messages" else ""
        parameters: tuple[Any, ...] = (
            (agent_id, MAX_METADATA_BYTES, MAX_METADATA_BYTES, agent_id, MAX_BODY_BYTES, agent_id)
            if section == "messages" else ()
        )
        predicate = "AND s.metadata_json::jsonb ? 'message'" if section == "messages" else ""
        rows = connection.execute(
            "SELECT member.ordinal,s.segment_id,s.segment_type,s.status,s.started_at,s.finished_at,"
            "s.content_ref,s.content_sha256,p.byte_length,s.execution_id,"
            "(s.execution_id='' OR EXISTS (SELECT 1 FROM executions e "
            "WHERE e.execution_id=s.execution_id AND e.agent_id=%s)) AS execution_matches"
            + message_columns + " FROM sequence_segments member JOIN segments s USING(segment_id) "
            "LEFT JOIN payloads p ON p.payload_ref=s.content_ref "
            "WHERE member.sequence_id=%s AND member.ordinal>%s " + predicate
            + " ORDER BY member.ordinal LIMIT %s",
            (agent_id, *parameters, trace, boundary, limit + 1),
        ).fetchall()
        items, consumed_bytes = [], 2  # JSON array delimiters, plus separators below.
        for row in rows[:limit]:
            item = _segment_item(row, messages=section == "messages")
            size = len(json.dumps(item, ensure_ascii=False).encode()) + (2 if items else 0)
            if items and consumed_bytes + size > MAX_PAGE_BYTES:
                break
            if size > MAX_PAGE_BYTES:
                raise ValueError("History item exceeds bounded projection")
            items.append(item)
            consumed_bytes += size
        next_after = items[-1]["ordinal"] if len(rows) > len(items) else None
        return {"items": items, "next_after": next_after, "availability": availability}
