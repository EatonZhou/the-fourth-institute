"""Read the newest context compaction that Kimi Code or DSH committed.

Like Claude Code, both Providers compact a session inside their own process
and record it in their own session files.  AgentData must record the same
replacement, otherwise the Provider context it hands to a Fork is the whole
uncompacted history.

* Kimi Code appends a ``context.apply_compaction`` record to the session's
  ``agents/main/wire.jsonl``.  Its context summary replaces the history;
  whatever the agent does afterwards follows it.
* DSH appends a ``user/message`` checkpoint (``source.kind`` is
  ``compact-checkpoint``) that replaces the surface range ``startSeq`` to
  ``endSeq`` with the summary; the recent messages after that range stay.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DSH_SURFACE_TYPES = frozenset({
    "system/message",
    "developer/message",
    "user/message",
    "assistant/message",
    "tool/result",
})


@dataclass(frozen=True)
class NativeCompaction:
    """One committed compaction of a Provider-managed session."""

    ref: str
    created_at: str
    retain_from: str
    replacement_history: tuple[dict[str, Any], ...]


def _session_name(session_id: str) -> str:
    clean = str(session_id or "").strip()
    if not clean or Path(clean).name != clean or clean in {".", ".."}:
        return ""
    return clean


def _iso_from_epoch_ms(value: object) -> str:
    try:
        milliseconds = int(str(value).strip())
    except (TypeError, ValueError):
        return ""
    moment = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def summary_history(text: str) -> tuple[dict[str, Any], ...]:
    """The history a compaction leaves: its summary, as one user message."""

    return ({
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text}],
    },)


def _text_parts(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _lines(path: str | Path) -> list[str]:
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []


def kimi_wire_path(kimi_home: str | Path, session_id: str) -> Path | None:
    """Locate the main agent wire of one Kimi Code session."""

    name = _session_name(session_id)
    sessions = Path(str(kimi_home or "")).expanduser() / "sessions"
    if not name or not sessions.is_dir():
        return None
    candidates = [
        path for path in sessions.glob(f"*/{name}/agents/main/wire.jsonl") if path.is_file()
    ]
    return candidates[0] if len(candidates) == 1 else None


def read_kimi_latest_compaction(path: str | Path) -> NativeCompaction | None:
    """Return the newest compaction applied to the main Kimi agent, or ``None``."""

    latest: tuple[str, dict[str, Any]] | None = None
    for line in _lines(path):
        if '"context.apply_compaction"' not in line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(record, dict)
            and record.get("type") == "context.apply_compaction"
            and str(record.get("agentId") or "main") == "main"
        ):
            latest = (line, record)
    if latest is None:
        return None
    line, record = latest
    summary = record.get("summary")
    text = str(record.get("contextSummary") or "") or (
        summary if isinstance(summary, str)
        else _text_parts(summary.get("content")) if isinstance(summary, dict)
        else ""
    )
    created_at = _iso_from_epoch_ms(record.get("time"))
    if not text.strip() or not created_at:
        return None
    return NativeCompaction(
        ref="kimi:" + hashlib.sha256(line.encode("utf-8")).hexdigest()[:32],
        created_at=created_at,
        retain_from=created_at,
        replacement_history=summary_history(text),
    )


def dsh_session_path(state_root: str | Path, session_id: str) -> Path | None:
    """Locate the newest-format session log of one DSH session."""

    name = _session_name(session_id)
    sessions = Path(str(state_root or "")).expanduser() / name / "sessions"
    if not name or not sessions.is_dir():
        return None

    def version(path: Path) -> int:
        match = re.fullmatch(r"session\.v(\d+)\.jsonl", path.name)
        return int(match.group(1)) if match else -1

    candidates = [
        path for path in sessions.glob(f"*/{name}/session.v*.jsonl")
        if path.is_file() and version(path) >= 0
    ]
    return max(candidates, key=version) if candidates else None


def read_dsh_latest_compaction(path: str | Path) -> NativeCompaction | None:
    """Return the newest compaction checkpoint of a DSH session, or ``None``."""

    events: list[dict[str, Any]] = []
    for line in _lines(path):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    found: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None = None
    for event in events:
        operation = event.get("surfaceOp")
        data = event.get("data")
        source = data.get("source") if isinstance(data, dict) else None
        if (
            event.get("type") == "user/message"
            and isinstance(operation, dict)
            and operation.get("op") == "replace"
            and isinstance(data, dict)
            and isinstance(source, dict)
            and source.get("kind") == "compact-checkpoint"
        ):
            found = (event, operation, data, source)
    if found is None:
        return None
    checkpoint, operation, data, source = found
    text = _text_parts(data.get("content"))
    created_at = _iso_from_epoch_ms(checkpoint.get("time"))
    if not text.strip() or not created_at:
        return None
    end_seq = operation.get("endSeq")
    seq = checkpoint.get("seq")
    kept = [
        event for event in events
        if event.get("type") in _DSH_SURFACE_TYPES
        and event.get("surfaceOp") is not None
        and isinstance(event.get("seq"), int)
        and isinstance(end_seq, int)
        and isinstance(seq, int)
        and end_seq < event["seq"] < seq
    ]
    retain_from = _iso_from_epoch_ms(kept[0].get("time")) if kept else ""
    compaction_id = str(source.get("compactionId") or "")
    return NativeCompaction(
        ref="dsh:" + (compaction_id or hashlib.sha256(json.dumps(checkpoint, sort_keys=True).encode()).hexdigest()[:32]),
        created_at=created_at,
        retain_from=retain_from or created_at,
        replacement_history=summary_history(text),
    )


def read_dsh_request_config(path: str | Path) -> dict[str, Any]:
    """The request configuration of the newest DSH request header, or ``{}``.

    After its first request a DSH agent derives every request from this
    logged header, so it shows which model the session really uses.
    """

    config: dict[str, Any] = {}
    for line in _lines(path):
        if '"request/header"' not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        data = event.get("data") if isinstance(event, dict) else None
        header = data.get("header") if isinstance(data, dict) else None
        if isinstance(header, dict) and isinstance(header.get("config"), dict):
            config = {**header["config"], "_adapter_defaults": header.get("adapterDefaults") or {}}
    return config
