"""The current time in the forms Portal records it."""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """Now in UTC to the second, as ISO 8601 ending in Z (2026-10-29T12:00:00Z)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
