"""Composer recall across a full Fork's compaction boundary.

The child's trace stays independent. Earlier personal inputs are read from
authorized sources, strictly before the first immutable reference inherited
at each Fork. Source work after that boundary never enters this history.
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Mapping

from . import resident_access

_PREFIX = 'fork:'
_MAX_DEPTH = 32


def _cursor(target: str, depth: int, before: str) -> str:
    if not depth:
        return before
    value = json.dumps([target, depth, before], separators=(',', ':')).encode()
    return _PREFIX + base64.urlsafe_b64encode(value).decode()


def _position(target: str, token: str) -> tuple[int, str]:
    if not token.startswith(_PREFIX):
        return 0, token
    try:
        if len(token) > 1024:
            raise ValueError
        owner, depth, before = json.loads(base64.b64decode(token[len(_PREFIX):], altchars=b'-_', validate=True))
        if owner != target or type(depth) is not int or not 1 <= depth <= _MAX_DEPTH:
            raise ValueError
        if not isinstance(before, str) or not before.isdecimal() or int(before) <= 0:
            raise ValueError
        return depth, before
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError('Invalid input page token') from exc


def input_history_page(repository: Any, record: Mapping[str, Any], actor: str,
                       page_token: str = '', *, limit: int = 50) -> dict[str, Any]:
    target = str(record['resident_id'])
    requested_depth, before = _position(target, str(page_token or '').strip())
    safe_limit = max(1, min(int(limit or 50), 200))
    current = record
    depth = 0
    ceiling = None
    seen = {target}
    result: dict[str, Any] = {'resident_id': target, 'messages': [], 'next_page_token': ''}

    def predecessor() -> tuple[Mapping[str, Any], int] | None:
        if current.get('fork_mode') not in {'full', 'compacted'} or depth >= _MAX_DEPTH:
            return None
        source = str(current.get('context_source_resident_id') or current.get('source_resident_id') or '')
        if not source or source in seen:
            return None
        visible = resident_access.visible_resident_records([source], actor, 'portal')
        source_record = next((item for item in visible if item.get('resident_id') == source and not item.get('deleted_at')), None)
        if source_record is None:
            return None
        boundary = repository.agent_data.input_history_predecessor(str(current['resident_id']))
        if boundary.get('source_agent_id') != source:
            return None
        return source_record, int(boundary['before_ordinal'])

    while True:
        if depth >= requested_depth:
            boundary = str(min(int(before), ceiling)) if before and ceiling is not None else before or str(ceiling or '')
            page = repository.input_history_page(current['resident_id'], actor, boundary,
                                                 limit=safe_limit - len(result['messages']))
            result['messages'].extend(page['messages'])
            if page['next_page_token']:
                result['next_page_token'] = _cursor(target, depth, page['next_page_token'])
                return result
        previous = predecessor()
        if previous is None:
            return result
        current, ceiling = previous
        seen.add(str(current['resident_id']))
        depth += 1
        if depth > requested_depth:
            before = ''
        if len(result['messages']) >= safe_limit:
            result['next_page_token'] = _cursor(target, depth, str(ceiling))
            return result
