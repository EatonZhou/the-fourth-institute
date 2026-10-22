from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from management_portal.codex_app_server_client import codex_token_usage_delta

from .provider_turn_usage import epoch_ms


MAX_ACTIVE_HISTORY_SCAN_BYTES = 128 * 1024 * 1024
REVERSE_READ_CHUNK_BYTES = 1024 * 1024
MAX_CONTEXT_METADATA_SCAN_BYTES = 16 * 1024 * 1024
MAX_USAGE_SCAN_BYTES = 4 * 1024 * 1024 * 1024


class _ActiveHistoryScanLimit(RuntimeError):
    pass


def read_codex_context_window(path: Path | str, *, model: str) -> int:
    """Read the latest same-model effective window from bounded native records."""

    expected_model = str(model or "").strip()
    if not expected_model:
        return 0
    observed_window = 0
    context_turn_id = ""
    try:
        for raw_line in _reverse_lines(
            Path(path), complete_lines_only=True,
            max_scan_bytes=MAX_CONTEXT_METADATA_SCAN_BYTES,
        ):
            if len(raw_line) > 64 * 1024:
                continue
            record = json.loads(raw_line)
            if not isinstance(record, dict):
                return 0
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            if record.get("type") == "turn_context":
                if context_turn_id or str(payload.get("model") or "").strip() != expected_model:
                    return 0
                if observed_window:
                    return observed_window
                context_turn_id = str(payload.get("turn_id") or "")
                if not context_turn_id:
                    return 0
            elif record.get("type") == "event_msg":
                if _is_rollback_event(payload):
                    return 0
                if payload.get("type") == "thread_settings_applied" and not context_turn_id:
                    settings = payload.get("thread_settings")
                    if not isinstance(settings, dict) or settings.get("model") != expected_model:
                        return 0
                    if observed_window:
                        return observed_window
                if context_turn_id:
                    if payload.get("type") == "task_started" and payload.get("turn_id") == context_turn_id:
                        return _positive_int(payload.get("model_context_window"))
                elif not observed_window:
                    _, observed_window = _token_count_observation(payload)
    except (OSError, ValueError, _ActiveHistoryScanLimit):
        return 0
    return 0


@dataclass(frozen=True)
class CodexCompaction:
    replacement_history: tuple[dict[str, Any], ...]
    fingerprint: str
    created_at: str = ""


@dataclass(frozen=True)
class CodexCompactionRead:
    compaction: CodexCompaction | None
    reason: str = ""


def read_codex_turn_messages(
    path: Path | str, turn_id: str, *, thread_id: str = "",
) -> tuple[dict[str, Any], ...] | None:
    """Read native message identities, never reconstructed ThreadItem IDs.

    Ordinary messages require the exact task boundary. Inline reviews instead
    persist outer-turn AgentMessage items between review-mode markers while a
    nested worker owns task_started. Those require the exact thread/turn IDs,
    review exit and terminal receipt, never the nested worker's responses. A
    missing/partial/unbounded history is unknown, not an empty successful read.
    No body matching, display-number parsing, or adjacent-turn imports.
    """
    if not turn_id:
        return None
    pending: list[dict[str, Any]] = []
    invalid = False
    review_messages: dict[str, dict[str, Any]] = {}
    review_terminal = review_exited = review_invalid = False
    try:
        for raw_line in _reverse_lines(Path(path), complete_lines_only=True):
            entry = json.loads(raw_line)
            if not isinstance(entry, dict):
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            if entry.get("type") == "event_msg":
                if _is_rollback_event(payload):
                    return None
                if payload.get("turn_id") == turn_id:
                    if payload.get("type") == "task_complete":
                        review_terminal = True
                    elif payload.get("type") == "turn_aborted":
                        review_invalid = True
                    elif payload.get("type") == "item_completed":
                        item = payload.get("item")
                        if isinstance(item, dict) and item.get("type") in {
                            "EnteredReviewMode", "ExitedReviewMode", "AgentMessage",
                        }:
                            # The rollout can contain nested and neighboring tasks.
                            # Never promote a display item or match by message body.
                            if not thread_id or payload.get("thread_id") != thread_id:
                                review_invalid = True
                            kind = item["type"]
                            if kind == "EnteredReviewMode":
                                if review_invalid or not review_terminal or not review_exited:
                                    return None
                                return tuple(reversed(tuple(review_messages.values())))
                            if kind == "ExitedReviewMode":
                                review_exited = True
                            elif kind == "AgentMessage":
                                identity = item.get("id")
                                content = item.get("content")
                                if (not isinstance(identity, str) or not identity.strip()
                                        or not isinstance(content, list) or review_exited or not review_terminal):
                                    review_invalid = True
                                    continue
                                text = "\n".join(
                                    part["text"] for part in content
                                    if isinstance(part, dict) and part.get("type") == "Text"
                                    and isinstance(part.get("text"), str)
                                ).strip()
                                if text:
                                    message = {"id": identity, "type": "agentMessage", "text": text,
                                               "phase": "final_answer", "questions": []}
                                    if identity in review_messages and review_messages[identity] != message:
                                        review_invalid = True
                                    review_messages[identity] = message
            if entry.get("type") == "event_msg" and payload.get("type") == "task_started":
                if payload.get("turn_id") == turn_id:
                    return None if invalid else tuple(reversed(pending))
                pending.clear()
                invalid = False
            elif entry.get("type") == "response_item" and (
                payload.get("type") == "message" and payload.get("role") == "assistant"
            ):
                identity = str(payload.get("id") or "").strip()
                content = payload.get("content")
                if not identity or not isinstance(content, list):
                    invalid = True
                    continue
                text = "\n".join(
                    part["text"] for part in content
                    if isinstance(part, dict) and part.get("type") in {"output_text", "text"}
                    and isinstance(part.get("text"), str)
                ).strip()
                if text or payload.get("questions"):
                    pending.append({"id": identity, "type": "agentMessage", "text": text,
                                    "phase": str(payload.get("phase") or "commentary"),
                                    "questions": payload.get("questions") or []})
    except (OSError, UnicodeError, json.JSONDecodeError, _ActiveHistoryScanLimit):
        return None
    return None




def read_codex_turn_input_recorded(path: Path | str | None, turn_id: str, *, expected_prompt: str = "") -> bool | None:
    """Whether an exact terminal turn recorded input before it stopped.

    TurnStarted precedes pre-sampling compaction and input recording. Only a
    complete native task boundary proves absence; unreadable, active, torn or
    rolled-back history remains unknown. Compaction copies are not new input.
    """
    if not isinstance(path, (str, Path)) or not turn_id:
        return None
    recorded = terminal = other_input = False
    try:
        for line in _reverse_lines(Path(path), complete_lines_only=True):
            entry = json.loads(line)
            payload = entry.get("payload", {})
            if not isinstance(payload, dict):
                continue
            if entry.get("type") == "event_msg":
                if _is_rollback_event(payload):
                    return None
                kind = payload.get("type")
                if kind == "task_started":
                    if payload.get("turn_id") == turn_id:
                        return (True if recorded else None if other_input else False) if terminal else None
                    recorded = terminal = other_input = False
                elif kind in {"task_complete", "turn_aborted"} and payload.get("turn_id") == turn_id:
                    terminal = True
            elif entry.get("type") == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
                content = payload.get("content") or []
                text = "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
                if not expected_prompt or expected_prompt in text:
                    recorded = True
                else:
                    other_input = True
    except (OSError, ValueError, TypeError, AttributeError, _ActiveHistoryScanLimit):
        return None
    return None


def read_codex_turn_error(path: Path | str, turn_id: str) -> dict[str, Any] | None:
    """Read a terminal provider error bounded by the exact native task."""
    if not turn_id:
        return None
    terminal_error: dict[str, Any] | None = None
    try:
        for raw_line in _reverse_lines(Path(path), complete_lines_only=True):
            entry = json.loads(raw_line)
            if not isinstance(entry, dict) or entry.get("type") != "event_msg":
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "task_started":
                if payload.get("turn_id") == turn_id:
                    return terminal_error
                terminal_error = None
            elif payload.get("type") == "task_complete" and payload.get("turn_id") == turn_id:
                error = payload.get("error")
                if isinstance(error, dict) and error:
                    terminal_error = {
                        key: error[key] for key in
                        ("message", "codexErrorInfo")
                        if key in error
                    }
    except (OSError, UnicodeError, json.JSONDecodeError, _ActiveHistoryScanLimit):
        return None
    return None


def read_codex_latest_compaction(path: Path | str) -> CodexCompactionRead:
    """Read the newest exact replacement context committed by Codex."""

    rollout_path = Path(path)
    if not rollout_path.is_file():
        return CodexCompactionRead(None, "rollout_unavailable")
    try:
        for raw_line in _reverse_lines(rollout_path, complete_lines_only=True):
            item = json.loads(raw_line)
            if not isinstance(item, dict) or str(item.get("type") or "") != "compacted":
                continue
            payload = item.get("payload")
            replacement = (
                payload.get("replacement_history")
                if isinstance(payload, dict)
                else None
            )
            if not isinstance(replacement, list) or not all(
                isinstance(value, dict) for value in replacement
            ):
                return CodexCompactionRead(None, "invalid_replacement_history")
            return CodexCompactionRead(CodexCompaction(
                replacement_history=tuple(dict(value) for value in replacement),
                fingerprint=hashlib.sha256(raw_line).hexdigest(),
                created_at=str(item.get("timestamp") or item.get("created_at") or ""),
            ))
    except _ActiveHistoryScanLimit:
        return CodexCompactionRead(None, "active_history_scan_limit")
    except (OSError, UnicodeError, json.JSONDecodeError):
        return CodexCompactionRead(None, "rollout_read_failed")
    return CodexCompactionRead(None, "compaction_unavailable")


_ROLLOUT_USAGE_KEYS = {
    "input_tokens": "inputTokens",
    "cached_input_tokens": "cachedInputTokens",
    "output_tokens": "outputTokens",
    "reasoning_output_tokens": "reasoningOutputTokens",
    "total_tokens": "totalTokens",
}


def read_codex_request_usage(
    path: Path | str, *, since: str, history_start: int = 0,
) -> list[tuple[str, dict[str, Any], str]] | None:
    """The model requests of the turns a thread started at or after ``since``.

    Codex writes each ``thread/tokenUsage/updated`` notification to the
    rollout as a ``token_count`` event.  Each request is returned as
    ``(event id, usage delta, written at)``, the identity being the one the
    live notification gets, so a request reported both ways counts once.
    A turn started before ``since`` belongs to an earlier Execution.  A
    subagent's rollout begins with a copy of its parent's history; its own
    records start at ``history_start``.
    """

    since_ms = epoch_ms(since)
    if not since_ms:
        return None
    requests: list[tuple[str, dict[str, Any], str]] = []
    # The requests of the turn being read backwards, until its start is seen.
    turn: list[tuple[str, dict[str, Any], str]] = []
    try:
        # An Execution of several hours writes more than other readers scan.
        for raw_line in _reverse_lines(Path(path), complete_lines_only=True, max_scan_bytes=MAX_USAGE_SCAN_BYTES):
            if b'"token_count"' not in raw_line and b'"task_started"' not in raw_line:
                continue
            entry = json.loads(raw_line)
            if not isinstance(entry, dict):
                continue
            ordinal = entry.get("ordinal")
            if isinstance(ordinal, int) and ordinal < history_start:
                break
            payload = entry.get("payload")
            if entry.get("type") != "event_msg" or not isinstance(payload, dict):
                continue
            kind, written_at = payload.get("type"), str(entry.get("timestamp") or "")
            if kind in ("token_count", "task_started") and epoch_ms(written_at) < since_ms:
                # Written before ``since``: this turn and every one before it started earlier.
                turn = []
                break
            if kind == "token_count" and isinstance(payload.get("info"), dict):
                info = payload["info"]
                event_id, delta = codex_token_usage_delta({
                    "last": _rollout_usage(info.get("last_token_usage")),
                    "total": _rollout_usage(info.get("total_token_usage")),
                    "modelContextWindow": info.get("model_context_window"),
                })
                if event_id:
                    turn.append((event_id, delta, written_at))
            elif kind == "task_started":
                requests.extend(turn)
                turn = []
    except (OSError, UnicodeError, json.JSONDecodeError, _ActiveHistoryScanLimit):
        return None
    requests.extend(turn)
    return list(reversed(requests))


def codex_subagent_rollouts(
    codex_home: Path | str, thread_id: str, *, since: str,
) -> list[tuple[str, Path, int]]:
    """The subagents a thread spawned, at any depth, that worked since ``since``.

    Returns ``(thread id, rollout, history start)``.  Codex files a rollout
    under the local date it was created; a subagent's header names its
    parent thread.
    """

    since_ms = epoch_ms(since)
    if not since_ms or not thread_id:
        return []
    sessions = Path(codex_home) / "sessions"
    day = datetime.fromtimestamp(since_ms / 1000).date() - timedelta(days=1)
    last_day = datetime.now().date() + timedelta(days=1)
    headers: dict[str, tuple[str, Path, int]] = {}
    while day <= last_day:
        directory = sessions / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        for path in sorted(directory.glob("rollout-*.jsonl")) if directory.is_dir() else ():
            try:
                if path.stat().st_mtime * 1000 < since_ms:
                    continue
                with path.open("rb") as stream:
                    meta = json.loads(stream.readline()).get("payload")
            except (OSError, UnicodeError, ValueError, AttributeError):
                continue
            source = meta.get("source") if isinstance(meta, dict) else None
            subagent = source.get("subagent") if isinstance(source, dict) else None
            spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
            parent = str(spawn.get("parent_thread_id") or "") if isinstance(spawn, dict) else ""
            child = str(meta.get("id") or "") if parent else ""
            if parent and child:
                headers[child] = (parent, path, _positive_int(meta.get("subagent_history_start_ordinal")))
        day += timedelta(days=1)
    found: list[tuple[str, Path, int]] = []
    parents = {thread_id}
    while True:
        spawned = [(child, path, start) for child, (parent, path, start) in headers.items()
                   if parent in parents and child not in parents]
        if not spawned:
            return found
        found.extend(spawned)
        parents.update(child for child, _, _ in spawned)


def _rollout_usage(value: object) -> dict[str, int]:
    usage = value if isinstance(value, dict) else {}
    return {camel: usage[key] for key, camel in _ROLLOUT_USAGE_KEYS.items() if key in usage}


def _reverse_lines(
    path: Path, *, complete_lines_only: bool = False,
    max_scan_bytes: int | None = None,
):
    scan_limit = MAX_ACTIVE_HISTORY_SCAN_BYTES if max_scan_bytes is None else max_scan_bytes
    with path.open("rb") as stream:
        stream.seek(0, 2)
        position = stream.tell()
        scanned = 0
        buffer = b""
        at_tail = True
        while position > 0:
            if scanned >= scan_limit:
                raise _ActiveHistoryScanLimit
            read_size = min(
                REVERSE_READ_CHUNK_BYTES,
                position,
                scan_limit - scanned,
            )
            position -= read_size
            stream.seek(position)
            buffer = stream.read(read_size) + buffer
            scanned += read_size
            lines = buffer.split(b"\n")
            buffer = lines[0]
            complete = lines[1:]
            if at_tail and len(lines) > 1:
                # Only newline-terminated records are committed. This also
                # handles a torn UTF-8 character or a tail spanning chunks.
                if complete_lines_only:
                    complete = complete[:-1]
                at_tail = False
            for raw_line in reversed(complete):
                if raw_line:
                    yield raw_line
        if buffer and not (complete_lines_only and at_tail):
            yield buffer


def _is_rollback_event(payload: object) -> bool:
    data = payload if isinstance(payload, dict) else {}
    marker = " ".join(
        str(data.get(key) or "")
        for key in ("type", "kind", "event", "name")
    ).lower()
    return "rollback" in marker or "rolled_back" in marker


def _token_count_observation(payload: object) -> tuple[int, int]:
    data = payload if isinstance(payload, dict) else {}
    if str(data.get("type") or "") != "token_count":
        return 0, 0
    info = data.get("info") if isinstance(data.get("info"), dict) else {}
    usage = (
        info.get("last_token_usage")
        if isinstance(info.get("last_token_usage"), dict)
        else {}
    )
    return (
        _positive_int(usage.get("input_tokens")),
        _positive_int(info.get("model_context_window")),
    )


def _positive_int(value: object) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0
