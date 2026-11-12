"""Entity-tag matching for existing GET/HEAD representations (RFC 9110)."""
from __future__ import annotations

import re


# Commas belong to opaque tags too; splitting the header on commas is unsafe.
_TAG = r'(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"'
_TAGS = re.compile(_TAG)


def if_none_match_matches(value: str, etag: str) -> bool:
    """Weak comparison against an existing representation; malformed lists miss.

    Call only after resolving/authorizing the requested representation. This
    retrieval validator does not implement If-Match or If-Range's strong test.
    """
    value = str(value or "").strip()
    if value == "*":
        return True
    target = etag.removeprefix("W/")
    matched = False
    position = 0
    while position < len(value):
        # Empty list members and optional whitespace are permitted. Consume
        # them once, rather than backtracking across overlapping whitespace.
        if value[position] in " \t,":
            position += 1
            continue
        tag = _TAGS.match(value, position)
        if tag is None:
            return False
        matched |= tag.group().removeprefix("W/") == target
        position = tag.end()
        while position < len(value) and value[position] in " \t":
            position += 1
        if position < len(value) and value[position] != ",":
            return False
    return matched
