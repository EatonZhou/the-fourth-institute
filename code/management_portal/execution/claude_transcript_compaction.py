"""Read the newest context compaction committed by Claude Code.

Claude Code compacts a session inside its own process.  The session transcript
(``<config_dir>/projects/<project>/<session_id>.jsonl``) records a
``compact_boundary`` system entry, the summary message that replaced the older
history, and the recent messages that the compaction kept verbatim.  Portal's
AgentData must record the same replacement, otherwise the Provider context it
hands to a Fork is the whole uncompacted history.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BOUNDARY_MARKER = '"compact_boundary"'
_SUMMARY_MARKER = '"isCompactSummary"'


@dataclass(frozen=True)
class ClaudeCompaction:
    """One committed compaction of a Claude Code session."""

    boundary_uuid: str
    created_at: str
    retain_from: str
    replacement_history: tuple[dict[str, Any], ...]
    fingerprint: str


def claude_transcript_path(config_dir: str | Path, session_id: str) -> Path | None:
    """Locate a session transcript under one Claude configuration directory."""

    clean_id = str(session_id or "").strip()
    if not clean_id or Path(f"{clean_id}.jsonl").name != f"{clean_id}.jsonl":
        return None
    projects_dir = Path(str(config_dir or "")).expanduser() / "projects"
    if not projects_dir.is_dir():
        return None
    candidates = [
        path
        for path in projects_dir.glob(f"*/{clean_id}.jsonl")
        if path.is_file()
    ]
    if len(candidates) != 1:
        return None
    return candidates[0]


def _message_text(entry: dict[str, Any]) -> str:
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
            and str(part.get("text") or "").strip()
        )
    return ""


def _timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def latest_compaction_boundary(lines: list[str] | tuple[str, ...]) -> tuple[int, dict[str, Any]]:
    """Find the newest complete compaction boundary in a transcript."""
    for index in range(len(lines) - 1, -1, -1):
        if _BOUNDARY_MARKER not in lines[index]:
            continue
        try:
            entry = json.loads(lines[index])
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("subtype") == "compact_boundary":
            return index, entry
    return -1, {}


def read_claude_latest_compaction(path: str | Path) -> ClaudeCompaction | None:
    """Return the newest compaction in a transcript, or ``None``.

    The replacement history is the compaction summary as one user message, in
    the typed Responses item shape shared with other Providers.  Messages that
    Claude Code preserved verbatim are not copied into it: they are the Portal
    Segments recorded at or after ``retain_from``, which the caller keeps after
    the compaction.
    """

    transcript = Path(path)
    try:
        lines = transcript.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    boundary_index, boundary = latest_compaction_boundary(lines)
    if boundary_index < 0:
        return None
    metadata = boundary.get("compactMetadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    preserved = metadata.get("preservedMessages")
    preserved = preserved if isinstance(preserved, dict) else {}
    segment = metadata.get("preservedSegment")
    segment = segment if isinstance(segment, dict) else {}
    anchor_uuid = str(preserved.get("anchorUuid") or segment.get("anchorUuid") or "")
    preserved_uuids = {
        str(item)
        for item in (preserved.get("allUuids") or preserved.get("uuids") or [])
        if str(item or "").strip()
    }
    wanted = preserved_uuids | ({anchor_uuid} if anchor_uuid else set())
    summary: dict[str, Any] | None = None
    preserved_times: list[datetime] = []
    for index, line in enumerate(lines):
        is_summary_candidate = index > boundary_index and _SUMMARY_MARKER in line
        if not is_summary_candidate and not any(uuid in line for uuid in wanted):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        entry_uuid = str(entry.get("uuid") or "")
        if entry_uuid in preserved_uuids:
            moment = _timestamp(entry.get("timestamp"))
            if moment is not None:
                preserved_times.append(moment)
        if summary is None and entry.get("isCompactSummary") and (
            entry_uuid == anchor_uuid or (not anchor_uuid and index > boundary_index)
        ):
            summary = entry
    summary_text = _message_text(summary) if summary is not None else ""
    if not summary_text.strip():
        return None
    boundary_time = _timestamp(boundary.get("timestamp"))
    if boundary_time is None:
        return None
    retain_from = min(preserved_times) if preserved_times else boundary_time
    return ClaudeCompaction(
        boundary_uuid=str(boundary.get("uuid") or ""),
        created_at=_iso(boundary_time),
        retain_from=_iso(retain_from),
        replacement_history=(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": summary_text}],
            },
        ),
        fingerprint=hashlib.sha256(lines[boundary_index].encode("utf-8")).hexdigest(),
    )
