"""Bounded, content-free Codex usage exports owned by ExecutionService."""
from __future__ import annotations

import base64
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens')
SCAN_BYTES = 256 * 1024 * 1024
# Only these record kinds are exported; other lines (tool output, messages) are
# skipped without decoding, which keeps a page of rollout bytes cheap to scan.
WANTED = (b'"session_meta"', b'"turn_context"', b'"token_count"', b'"user_message"')


def _time(raw: str) -> datetime:
    value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('时间必须包含时区')
    return value.astimezone(timezone.utc)


def _text(value: object) -> str:
    return value[:256] if isinstance(value, str) else ''


def _numbers(value: object, fields: tuple[str, ...]) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in fields if type(value.get(key)) in (int, float) and math.isfinite(value[key])}


def public_record(obj: object, start: datetime, end: datetime) -> dict[str, Any] | None:
    if not isinstance(obj, dict) or not isinstance(obj.get('payload'), dict):
        return None
    payload = obj['payload']; kind = obj.get('type')
    timestamp = _text(obj.get('timestamp'))
    try:
        moment = _time(timestamp)
    except (ValueError, TypeError):
        return None
    if moment >= end:
        return None
    if kind == 'session_meta':
        clean = {key: _text(payload.get(key)) for key in ('id', 'model_provider', 'forked_from_id')}
    elif kind == 'turn_context':
        clean = {'model': _text(payload.get('model'))}
    elif kind == 'event_msg' and moment >= start:
        subtype = payload.get('type')
        if subtype == 'user_message':
            clean = {'type': subtype}
        elif subtype == 'token_count':
            info = payload.get('info') if isinstance(payload.get('info'), dict) else {}
            limits = payload.get('rate_limits') if isinstance(payload.get('rate_limits'), dict) else {}
            clean = {'type': subtype, 'info': {'last_token_usage': _numbers(info.get('last_token_usage'), TOKEN_FIELDS)},
                     'rate_limits': {'plan_type': _text(limits.get('plan_type'))}}
            for key in ('primary', 'secondary'):
                if isinstance(limits.get(key), dict):
                    clean['rate_limits'][key] = _numbers(limits[key], ('window_minutes', 'used_percent', 'resets_at'))
        else:
            return None
    else:
        return None
    return {'timestamp': timestamp, 'type': kind, 'payload': clean}


def read_page(root: Path, *, account_id: str, since: str, until: str, cursor: str = '', limit: int = 1000) -> dict[str, Any]:
    start, end = _time(since), _time(until)
    if not start < end or end - start > timedelta(days=60):
        raise ValueError('用量时间范围必须为正且不超过 60 天')
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('limit 必须在 1–1000 之间')
    scope = hashlib.sha256(json.dumps([account_id, since, until]).encode()).hexdigest()
    after, offset = '', 0
    if cursor:
        try:
            position = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if position['scope'] != scope:
                raise ValueError('游标不属于本次查询')
            after, offset = position['path'], position['offset']
            if not isinstance(after, str) or type(offset) is not int or offset < 0:
                raise ValueError('游标无效')
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError('用量游标无效') from exc
    root = root.resolve()
    paths = sorted(path for path in root.rglob('*.jsonl') if path.is_file() and not path.is_symlink()
                   and path.resolve().is_relative_to(root)) if root.is_dir() else []
    records = []; scanned = 0
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if relative < after:
            continue
        # Old, unchanged sessions have no observations in the requested window.
        if path.stat().st_mtime < start.timestamp():
            continue
        with path.open('rb') as stream:
            if relative == after:
                if offset > path.stat().st_size:
                    raise ValueError('用量源在分页期间被截断，请重新查询')
                stream.seek(offset)
            while line := stream.readline():
                scanned += len(line)
                record = None
                if any(marker in line for marker in WANTED):
                    try:
                        record = public_record(json.loads(line), start, end)
                    except (ValueError, UnicodeDecodeError):
                        record = None
                if record is not None:
                    records.append({'source': relative, 'record': record})
                if len(records) >= limit or scanned >= SCAN_BYTES:
                    next_cursor = base64.urlsafe_b64encode(json.dumps({'scope': scope, 'path': relative, 'offset': stream.tell()}).encode()).decode()
                    return {'records': records, 'next_cursor': next_cursor, 'account_id': account_id}
    return {'records': records, 'next_cursor': '', 'account_id': account_id}


def get_provider_usage_events(inv):
    from management_portal.hatg.contracts import invalid, not_found
    from .account_adapters import CodexRoutes
    account_id = str(inv.argument('account_id') or '')
    registry = CodexRoutes()
    try:
        route = registry.get(account_id)
    except (ValueError, KeyError) as exc:
        raise not_found('Codex 账号未登记') from exc
    try:
        return read_page(route.codex_home / 'sessions', account_id=account_id,
                         since=inv.argument('since'), until=inv.argument('until'),
                         cursor=inv.argument('cursor') or '', limit=inv.argument('limit') or 1000)
    except ValueError as exc:
        raise invalid(str(exc)) from exc
