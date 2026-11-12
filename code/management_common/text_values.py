"""Plain values read from loosely typed input."""
from __future__ import annotations

from collections.abc import Iterable


def stripped_text(value: object) -> str:
    """The value as text without surrounding whitespace; None, "" and other false values are ""."""
    return str(value or "").strip()


def strip_quotes(value: str) -> str:
    """The value without surrounding whitespace and one pair of matching single or double quotes."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in {"'", '"'}:
        return v[1:-1]
    return v


def unique_texts(values: Iterable[object]) -> list[str]:
    """Keep the first nonempty spelling of each stripped, casefolded value."""
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = stripped_text(value)
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output
