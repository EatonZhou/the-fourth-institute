"""Exact validation of canonical product format tags."""
from __future__ import annotations

def schema_matches(actual: object, expected: str) -> bool:
    """Accept only the complete canonical tag, including its version.

    This predicate does not mutate a payload, stored record, or fingerprint.
    Callers keep their existing validation of every other field.
    """
    if not isinstance(expected, str) or not expected.startswith("tfi.") or len(expected) <= 4:
        raise ValueError("expected must be a complete tfi format tag")
    return isinstance(actual, str) and actual == expected
