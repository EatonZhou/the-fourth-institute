"""Shared nullable-limit contract for Portal configuration and APIs.

An omitted value belongs to the caller's defaulting policy.  Once a limit is
present, ``None`` is the only canonical representation of an unbounded limit;
numeric zero remains the real bounded value zero.
"""

from __future__ import annotations

from typing import Final


UNSET: Final = object()
_UNBOUNDED_TEXT = frozenset({
    "inf",
    "infinite",
    "infinity",
    "unlimited",
    "none",
    "no_limit",
    "no-limit",
    "∞",
})


def normalize_optional_limit(
    value: object = UNSET,
    *,
    default: int | None = None,
    maximum: int | None = None,
) -> int | None:
    """Normalize one non-negative count limit.

    ``UNSET`` uses the caller-provided default, ``None`` and explicit unbounded
    words become the canonical unbounded value, and zero stays zero.
    Invalid or negative values fall back to ``default``.
    """

    if value is UNSET:
        return _validated_default(default, maximum=maximum)
    if value is None:
        return None
    if isinstance(value, bool):
        return _validated_default(default, maximum=maximum)
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in _UNBOUNDED_TEXT:
            return None
        if not text:
            return _validated_default(default, maximum=maximum)
        value = text
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return _validated_default(default, maximum=maximum)
    if normalized < 0:
        return _validated_default(default, maximum=maximum)
    if maximum is not None:
        normalized = min(normalized, max(0, int(maximum)))
    return normalized


def limit_reached(limit: int | None, count: object) -> bool:
    """Return whether a bounded limit has been reached."""

    if limit is None:
        return False
    try:
        used = max(0, int(count))
    except (TypeError, ValueError, OverflowError):
        used = 0
    return used >= limit


def limit_remaining(limit: int | None, count: object) -> int | None:
    """Return remaining capacity, or ``None`` for an unbounded limit."""

    if limit is None:
        return None
    try:
        used = max(0, int(count))
    except (TypeError, ValueError, OverflowError):
        used = 0
    return max(0, limit - used)


def _validated_default(
    default: int | None,
    *,
    maximum: int | None,
) -> int | None:
    if default is None:
        return None
    normalized = max(0, int(default))
    if maximum is not None:
        normalized = min(normalized, max(0, int(maximum)))
    return normalized
