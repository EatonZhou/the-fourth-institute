"""Pure bounded keyset selection shared by resource directory readers.

The owner filters candidates and provides a trusted scope (identity, relative
directory and binding identity). A cursor is a position, never authorization.
Each request sees the current directory, not a retained filesystem snapshot.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import heapq
import json
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

T = TypeVar('T')
SortKey = tuple[int, str, str]


def directory_key(name: str, is_directory: bool) -> SortKey:
    return (0 if is_directory else 1, name.casefold(), name)


def directory_scope(identity: str, relative: str, info: Any, *, view: str = 'children') -> str:
    """Owner-supplied inode identity detects a replaced/rebound directory."""
    return json.dumps([view, identity, relative, info.st_dev, info.st_ino], ensure_ascii=True)


def _decode(cursor: str | None, scope: str) -> SortKey | None:
    if cursor is None or cursor == '':
        return None
    try:
        if not isinstance(cursor, str) or len(cursor) > 16384:
            raise ValueError()
        value = json.loads(base64.b64decode(cursor.encode('ascii'), altchars=b'-_', validate=True))
        if not isinstance(value, dict) or set(value) != {'v', 'scope', 'key'}:
            raise ValueError()
        key = value['key']
        if (type(value['v']) is not int or value['v'] != 1 or value['scope'] != scope
                or not isinstance(key, list) or len(key) != 3 or type(key[0]) is not int
                or key[0] not in (0, 1) or not all(isinstance(k, str) for k in key[1:])
                or key[1] != key[2].casefold()):
            raise ValueError()
        return tuple(key)
    except (ValueError, TypeError, UnicodeError, binascii.Error) as exc:
        raise ValueError('invalid directory cursor') from exc


def paginate(candidates: Iterable[T], *, scope: str, key: Callable[[T], SortKey],
             cursor: str | None = None, limit: int = 200) -> dict[str, Any]:
    if type(limit) is not int or limit < 1:
        raise ValueError('limit must be a positive integer')
    fingerprint = hashlib.sha256(scope.encode('utf-8')).hexdigest()
    after = _decode(cursor, fingerprint)
    rows = heapq.nsmallest(limit + 1,
                          (row for row in candidates if after is None or key(row) > after), key=key)
    more = len(rows) > limit
    entries = rows[:limit]
    next_cursor = ''
    if more:
        payload = {'v': 1, 'scope': fingerprint, 'key': key(entries[-1])}
        next_cursor = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=True,
                                                        separators=(',', ':')).encode('ascii')).decode('ascii')
    return {'entries': entries, 'next_cursor': next_cursor, 'truncated': more}
