"""Column encodings shared by the parts of the Lineage data store."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from management_common.json_columns import json_text, json_value


def json_column(value: object) -> str:
    """Canonical JSON text: sorted keys, no spaces, Unicode kept."""

    return json_text(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stored_object(raw: object, identity: str) -> dict[str, Any]:
    value = json_value(raw or "{}")
    if not isinstance(value, dict):
        raise RuntimeError(f"Stored {identity} is not an object")
    return value


def now_iso_millis() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
