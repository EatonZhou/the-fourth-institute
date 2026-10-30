"""Plain values read from loosely typed input."""
from __future__ import annotations


def stripped_text(value: object) -> str:
    """The value as text without surrounding whitespace; None, "" and other false values are ""."""
    return str(value or "").strip()


def strip_quotes(value: str) -> str:
    """The value without surrounding whitespace and one pair of matching single or double quotes."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in {"'", '"'}:
        return v[1:-1]
    return v
