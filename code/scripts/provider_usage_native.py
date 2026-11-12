"""Read native usage metadata only; no native processes or service calls."""
import json
from pathlib import Path
import sqlite3
from collections import deque
from management_common import portal_paths


def record_path(reference):
    """All external paths/references use the validated deployment projection."""
    return portal_paths.resolve_directory(str(reference))


def expand_sources(sources, gaps, cache=None):
    """Expand explicitly authorized trees and native child logs, never credential files."""
    pending, seen = deque(sources), set()
    while pending:
        source = pending.popleft()
        path = record_path(source['path'])
        if Path(source['path']).is_absolute() and Path(source['path']).is_symlink():
            gaps.add('symlink_source_excluded')
            continue
        source = {**source, 'path': str(path)}
        if path.is_symlink():
            gaps.add('symlink_source_excluded')
            continue
        key = (source['format'], str(path.resolve()))
        if key in seen:
            continue
        seen.add(key)
        fmt = source['format']
        if path.is_dir():
            areas = [path / p for p in ('sessions', 'archived_sessions') if (path / p).is_dir()]
            roots = areas if fmt == 'codex' and areas else [path]
            pattern = '*.db' if fmt in ('opencode_sqlite', 'antigravity') else 'wire.jsonl' if fmt == 'kimi' else '*.jsonl'
            for root in roots:
                pending.extend({**source, 'path': str(p)} for p in sorted(root.rglob(pattern)) if not p.is_symlink())
            continue
        if not source.get('session_id'):
            source = {**source, 'session_id': path.parents[2].name if fmt == 'kimi' and path.name == 'wire.jsonl' else path.stem}
        source = {**source, 'execution_session_id': source.get('execution_session_id') or source['session_id']}
        if source.get('children', True):
            if fmt == 'claude':
                pending.extend({**source, 'path': str(p), 'session_id': p.stem}
                               for p in sorted(path.with_suffix('').glob('subagents/*.jsonl')))
            elif fmt == 'kimi' and path.parent.name == 'main':
                pending.extend({**source, 'path': str(p)} for p in sorted(path.parent.parent.glob('*/wire.jsonl')) if p != path)
            elif fmt == 'dsh' and path.is_file():
                def children():
                    with path.open(encoding='utf-8') as stream:
                        for line in stream:
                            if '"subagent/catalog"' in line:
                                try:
                                    yield (json.loads(line).get('data') or {}).get('childId', '')
                                except ValueError:
                                    continue
                for child in cache.child_ids(path) if cache else children():
                    if child and '/' not in child and '\\' not in child and child not in ('.', '..'):
                        candidate = path.parent.parent / child / path.name
                        if candidate.is_file():
                            pending.append({**source, 'path': str(candidate), 'session_id': child})
                        else:
                            gaps.add('referenced_child_log_missing')
        yield source


def sqlite_read(path):
    c = sqlite3.connect(record_path(path).as_uri() + '?mode=ro', uri=True, timeout=1)
    c.execute('PRAGMA query_only=ON')
    c.row_factory = sqlite3.Row
    return c


def opencode_rows(source, gaps):
    from management_portal.opencode_bridge import canonical_usage
    with sqlite_read(source['path']) as c:
        session_ids = None
        if source.get('native_session_id'):
            session_ids, pending = set(), [source['native_session_id']]
            while pending:
                sid = pending.pop()
                if sid in session_ids:
                    continue
                session_ids.add(sid)
                pending.extend(r[0] for r in c.execute('SELECT id FROM session WHERE parent_id=?', (sid,)))
        # One native step-finish is a request; message totals are fallback only.
        for row in c.execute('SELECT id,session_id,time_created,data FROM message ORDER BY time_created,id'):
            if session_ids is not None and row['session_id'] not in session_ids:
                continue
            msg = json.loads(row['data'])
            if msg.get('role') != 'assistant':
                continue
            found = False
            for part in c.execute('SELECT id,data FROM part WHERE message_id=? ORDER BY time_created,id', (row['id'],)):
                p = json.loads(part['data'])
                if p.get('type') != 'step-finish' or not p.get('tokens'):
                    continue
                found = True
                yield {'id': part['id'], 'session': row['session_id'],
                       'at': ((msg.get('time') or {}).get('created') or row['time_created']) / 1000,
                       'model': msg.get('modelID'), 'usage': canonical_usage(p['tokens'])}
            if not found and msg.get('tokens'):
                gaps.add('opencode_message_total_fallback_no_step_finish')
                yield {'id': 'message:' + row['id'], 'session': row['session_id'],
                       'at': ((msg.get('time') or {}).get('created') or row['time_created']) / 1000,
                       'model': msg.get('modelID'), 'usage': canonical_usage(msg['tokens'])}


def antigravity_rows(source, gaps):
    """Schema verified against Google's installed 1.4.5 protobuf descriptors.

    CortexStepGeneratorMetadata.chat_model=1 -> ChatModelMetadata:
    usage=4, chat_start_metadata=9 (created_at=4), response_model=19,
    model_display_name=21, response_model_full=22.
    """
    from management_portal.execution.antigravity_backend import _pb_fields, _step_usage
    from scripts.provider_usage_attribution import antigravity_evidence
    with sqlite_read(source['path']) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():
            gaps.add('antigravity_empty_database')
            return
        session = source.get('session_id') or Path(source['path']).stem
        for row in c.execute('SELECT idx,data FROM gen_metadata ORDER BY idx'):
            blob = row['data']
            usage = _step_usage(blob)
            if not usage:
                continue
            root = {n: v for n, w, v in _pb_fields(blob)}
            chat = {n: v for n, w, v in _pb_fields(root.get(1, b''))}
            start = {n: v for n, w, v in _pb_fields(chat.get(9, b''))}
            timestamp = {n: v for n, w, v in _pb_fields(start.get(4, b''))}
            if not timestamp.get(1):
                gaps.add('antigravity_missing_request_created_at')
                continue
            model = (chat.get(22) or chat.get(19) or b'').decode('utf-8')
            if not model:
                gaps.add('antigravity_missing_response_model')
            raw_usage = {n: v for n, w, v in _pb_fields(chat.get(4, b''))}
            response_id = raw_usage.get(11) or raw_usage.get(12) or raw_usage.get(7)
            request_id = response_id.decode() if isinstance(response_id, bytes) and response_id else 'gen:' + str(row['idx'])
            at = timestamp[1] + timestamp.get(2, 0) / 1e9
            yield {'id': request_id,
                   'session': session, 'at': timestamp[1] + timestamp.get(2, 0) / 1e9,
                   'model': model, 'usage': usage,
                   'quota_evidence': antigravity_evidence(chat, at, request_id)}


def quota_rows(payload):
    """Normalize existing monitor-history exports and old daily snapshots."""
    records = payload if isinstance(payload, list) else payload.get('observations', [payload])
    for record in records:
        sample = record.get('sample') or record
        accounts = sample.get('accounts') or [sample]
        for account in accounts:
            windows = account.get('rate_limits') or []
            if isinstance(windows, dict):
                limits = windows.get('by_limit_id') or {}
                current = [(windows.get('limit_id') or '', windows)]
                current.extend(limits.items())
                windows = [{**w, 'quota_group': group} for group, limit in current if isinstance(limit, dict)
                           for key in ('primary', 'secondary') if isinstance(w := limit.get(key), dict) and w]
            for window in windows:
                if not isinstance(window, dict):
                    continue
                at = window.get('observed_at') or account.get('updated_at') or record.get('finished_at') or sample.get('observed_at')
                yield {'account_id': account.get('account_id') or record.get('account_id'),
                       'at': at, **window,
                       'remaining_percent': window.get('remaining_percent', 100 - window['used_percent'] if isinstance(window.get('used_percent'), (int, float)) else None),
                       'stale': bool(window.get('stale') or account.get('stale') or sample.get('status') == 'unavailable')}
