"""The canonical durable Seed marker."""
from __future__ import annotations

import re
import json

MARKER_NAME = "tfi-hatg-seed-state"


def marker_name_pattern() -> str:
    return re.escape(MARKER_NAME)


def read_marker(text: str) -> dict | None:
    """Read only the canonical leading marker; never inspect historical body text."""
    match = re.match(r"\A(?:\ufeff)?\s*<!--\s*" + marker_name_pattern()
                     + r"\s+(\{[^\r\n]*\})\s*-->", text, re.IGNORECASE)
    if not match:
        return None
    try:
        value = json.loads(match[1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
