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
