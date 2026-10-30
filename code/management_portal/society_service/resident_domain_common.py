from __future__ import annotations
from management_common.portal_paths import path as portal_path

import copy
import os
from dataclasses import dataclass
from typing import Any, Mapping
from datetime import datetime, timezone
import json
import re

from management_common.operation_errors import OperationError
from . import identity_contract

MAX_MESSAGE_CHARS = 12000
EARLIEST = datetime.min.replace(tzinfo=timezone.utc)
TOKEN_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
RESIDENT_TEXT_SUFFIXES = {
    ".txt", ".md", ".mdx", ".markdown", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".csv", ".log", ".py", ".js", ".ts", ".tsx",
    ".jsx", ".html", ".css", ".sh", ".tex", ".ini", ".cfg", ".rst",
}
RESIDENT_VIDEO_SUFFIXES = {".mp4", ".webm", ".ogv", ".ogg", ".mov", ".m4v"}


def new_resident_id(existing_ids: set[str] | None = None) -> str:
    occupied = {str(value or "").strip() for value in existing_ids or set()}
    return identity_contract.new_id("resident", occupied)


def new_lineage_id(existing_ids: set[str] | None = None) -> str:
    occupied = {str(value or "").strip() for value in existing_ids or set()}
    return identity_contract.new_id("lineage", occupied)


AGENTS_ROOT = (portal_path("share_root") / "Agents")
PROJECTS_ROOT = (portal_path("share_root") / "Projects")
STAGING_ROOT = portal_path("staging_root")
MAX_TIMELINE_ITEMS = 120


class ResidentSessionConflictError(OperationError):
    http_status = 409
    commit_state = "not_committed"

    def __init__(self, message: str, *, code: str, restart: Mapping[str, Any]) -> None:
        super().__init__(message, code=code, details={"restart": copy.deepcopy(dict(restart))})

    def conflict_details(self) -> dict[str, Any]:
        return dict(self.details)


class ResidentTurnCancelled(RuntimeError):
    """The canonical Resident lifecycle accepted cancellation of the turn."""

    def __init__(self, message: str, *, usage: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        # What the stopped turn's execution counted, when it had run.
        self.usage = dict(usage or {})


class ResidentExecutionFailed(RuntimeError):
    """Carry an Execution failure intact to the single completion owner."""

    def __init__(
        self, snapshot: Mapping[str, Any], fallback: str, *, usage: Mapping[str, Any] | None = None,
    ) -> None:
        result = snapshot.get("result")
        result = result if isinstance(result, dict) else {}
        super().__init__(str(result.get("error") or snapshot.get("error") or fallback))
        self.error_code = str(result.get("error_code") or snapshot.get("error_code") or "")
        details = result.get("error_details", snapshot.get("error_details"))
        self.error_details = dict(details) if isinstance(details, dict) else {}
        # What the failed turn's execution counted for its Provider requests.
        self.usage = dict(usage or {})

CODEX_COMPACT_TIMEOUT_SECONDS = int(os.getenv("RESIDENT_AGENT_CODEX_COMPACT_TIMEOUT_SECONDS", "1800"))
RESIDENT_RESOURCE_ACTIVITY_TOTAL_CHARS = 48_000
LINEAGE_FOLDER_SCHEMA_VERSION = "lineage-folder-v3"
RESIDENT_INSTRUCTIONS_FILE = "resident.md"
RESIDENT_DIARY_FILE = "diary.md"
RESIDENT_MAILBOX_FILE = "mailbox.md"
RESIDENT_WORKSPACES_FILE = "workspaces.md"
RESIDENT_WORKSPACE_DIR = "workspace"
RESIDENT_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif"}
RESIDENT_MEDIA_KINDS = {"image", "video", "pdf"}


@dataclass(frozen=True)
class ResidentRuntimeTarget:
    """Resolved runtime state for one exact Resident identity."""

    resident_id: str
    record: dict[str, Any]


def parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):  # not ISO 8601, or out of range once moved to UTC
        return None


def date_part(value: str) -> str:
    parsed = parse_iso(value)
    if parsed:
        return parsed.date().isoformat()
    return str(value or "").split("T", 1)[0] or "unknown date"


def truncate_text(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n\n[content truncated for portal preview]"


def resident_status_signature(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class _AppServerTokenUsage:
    turn: dict[str, int]
    cumulative: dict[str, int]
    context: dict[str, int]
    model_context_window: int


def app_server_token_usage(value: object) -> _AppServerTokenUsage:
    payload = value if isinstance(value, dict) else {}

    def normalize(raw: object) -> dict[str, int]:
        data = raw if isinstance(raw, dict) else {}
        aliases = {
            "input_tokens": "inputTokens",
            "cached_input_tokens": "cachedInputTokens",
            "output_tokens": "outputTokens",
            "reasoning_output_tokens": "reasoningOutputTokens",
            "total_tokens": "totalTokens",
        }
        return {
            target: nonnegative_int(data.get(source), 0)
            for target, source in aliases.items()
            if nonnegative_int(data.get(source), 0) > 0
        }

    turn = normalize(payload.get("last"))
    return _AppServerTokenUsage(
        turn=turn,
        cumulative=normalize(payload.get("total")),
        context=normalize(payload.get("context")) or turn,
        model_context_window=nonnegative_int(
            payload.get("modelContextWindow"),
            0,
        ),
    )


def execution_token_usage_contract(
    value: object,
) -> dict[str, Any]:
    """Separate turn accounting from cumulative Provider usage."""

    payload = value if isinstance(value, dict) else {}
    flat_usage = clean_token_usage(payload)
    app_server_usage = app_server_token_usage(payload)
    current_usage = flat_usage or app_server_usage.turn
    return {
        "turn_usage": current_usage,
        "cumulative_usage": app_server_usage.cumulative,
        "context_usage": app_server_usage.context or current_usage,
        "model_context_window": app_server_usage.model_context_window,
        "context_updated_at": "",
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _slugify(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return text[:48] or "resident_agent"


def nonnegative_int(value: object, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if number >= 0 else default


def safe_id(value: str) -> str:
    raw = str(value or "").strip()
    safe = re.sub(r"[^A-Za-z0-9_.@+-]+", "_", raw).strip("._")
    return safe[:96] or "unknown"


def clean_token_usage(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key in TOKEN_USAGE_KEYS:
        number = nonnegative_int(value.get(key), 0)
        if number > 0:
            out[key] = number
    return out


def record_resident_id(record: dict[str, Any]) -> str:
    return str(record.get("resident_id") or "").strip()


def dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def protocol_message_text(message: Mapping[str, Any]) -> str:
    return "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, Mapping) and part.get("type") == "text"
    ).strip()
