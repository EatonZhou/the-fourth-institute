"""Value coercion, status vocabularies and labels shared by the Resource Backup projections.

Every value the backup service reports is untrusted display data: these turn it into
bounded strings, non-negative numbers, booleans, UTC timestamps and known states."""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


BACKUP_LABELS = {
    "critical": "关键状态",
    "content": "完整内容",
}
VERIFICATION_LABELS = {
    "check": "仓库校验",
    "deep-check": "深度校验",
}
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,159}$")


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: object, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _number(value: object, default: int = 0) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return default
    return max(0, number)


def _boolean(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _safe_identifier(value: object) -> str:
    candidate = _string(value, limit=160)
    return candidate if _SAFE_ID_RE.fullmatch(candidate) else ""


def _timestamp(value: object) -> str:
    text = _string(value, limit=80)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_status(value: object) -> str:
    normalized = _string(value, limit=80).casefold().replace(" ", "_").replace("-", "_")
    aliases = {
        "": "unknown",
        "accepted": "queued",
        "created": "queued",
        "pending": "queued",
        "scheduled": "queued",
        "in_progress": "running",
        "planning": "running",
        "preparing": "running",
        "backing_up": "running",
        "snapshotting": "running",
        "verifying": "running",
        "replicating": "running",
        "finalizing": "running",
        "ok": "completed",
        "success": "completed",
        "succeeded": "completed",
        "done": "completed",
        "passed": "completed",
        "no_op": "completed",
        "completed_with_warning": "degraded",
        "completed_with_warnings": "degraded",
        "partial": "incomplete",
        "blocked": "failed",
        "error": "failed",
        "canceled": "cancelled",
        "skipped_locked": "skipped",
    }
    return aliases.get(normalized, normalized if normalized in {
        "queued",
        "running",
        "completed",
        "degraded",
        "incomplete",
        "failed",
        "cancelled",
        "skipped",
    } else "unknown")


def _health_status(value: object) -> str:
    normalized = _string(value, limit=80).casefold().replace(" ", "_").replace("-", "_")
    aliases = {
        "": "unknown",
        "ok": "healthy",
        "ready": "healthy",
        "success": "healthy",
        "completed": "healthy",
        "protected": "healthy",
        "warning": "degraded",
        "stale": "degraded",
        "partial": "degraded",
        "incomplete": "degraded",
        "error": "failed",
        "offline": "unavailable",
        "not_configured": "setup_required",
        "missing": "setup_required",
    }
    allowed = {
        "healthy",
        "degraded",
        "failed",
        "running",
        "setup_required",
        "unavailable",
        "unknown",
    }
    result = aliases.get(normalized, normalized)
    return result if result in allowed else "unknown"


def _coverage_status(value: object) -> str:
    normalized = _string(value, limit=80).casefold().replace(" ", "_").replace("-", "_")
    aliases = {
        "": "unknown",
        "ok": "protected",
        "healthy": "protected",
        "ready": "protected",
        "complete": "protected",
        "completed": "protected",
        "missing": "unprotected",
        "uncovered": "unprotected",
        "not_configured": "setup_required",
        "warning": "stale",
        "degraded": "stale",
        "error": "failed",
        "rebuildable": "excluded",
    }
    allowed = {
        "protected",
        "stale",
        "unprotected",
        "failed",
        "excluded",
        "setup_required",
        "unknown",
    }
    result = aliases.get(normalized, normalized)
    return result if result in allowed else "unknown"


def _named_record(raw: object, record_id: str) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return _mapping(raw.get(record_id))
    if isinstance(raw, list):
        for value in raw:
            item = _mapping(value)
            if _string(
                item.get("id") or item.get("name") or item.get("profile"),
                limit=60,
            ).casefold() == record_id:
                return item
    return {}
