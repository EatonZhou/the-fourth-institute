"""Plain values read from loosely typed input."""
from __future__ import annotations


def stripped_text(value: object) -> str:
    """The value as text without surrounding whitespace; None, "" and other false values are ""."""
    return str(value or "").strip()
