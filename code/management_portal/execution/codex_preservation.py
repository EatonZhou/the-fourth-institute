"""Freeze a registered Codex session's actual effective native history.

Display transcripts are not replay logs. Immutable native items are captured
outside Chat, retaining ciphertext and the Provider's compaction boundary.
"""
import hashlib
import json
from pathlib import Path

from .session_history_import import validate_codex_preservation
from .codex_rollout_history import _is_rollback_event


def read_native_window(path, thread_id):
    path = Path(path)
    before = path.stat()
    items, identity = [], ''
    with path.open('rb') as source:
        for line in source:
            if not line.endswith(b'\n'):
                raise ValueError('Codex native history has an incomplete record')
            record = json.loads(line)
            payload = record.get('payload') or {}
            kind = record.get('type')
            if kind == 'session_meta':
                identity = payload.get('id') or payload.get('session_id')
                if identity != thread_id:
                    raise ValueError('Codex native history belongs to another session')
            elif kind == 'compacted':
                replacement = payload.get('replacement_history')
                if not isinstance(replacement, list):
                    raise ValueError('Codex native compaction history is unavailable')
                items = list(replacement)
            elif kind == 'response_item':
                items.append(payload)
            elif kind == 'event_msg' and _is_rollback_event(payload):
                raise ValueError('Codex native rollback requires effective-history reconstruction')
    after = path.stat()
    if identity != thread_id or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Codex native history changed while preserving context')
    if any(not isinstance(item, dict) or not isinstance(item.get('type'), str) for item in items):
        raise ValueError('Codex native history contains an invalid item')
    segments = [{'segment_type':'model', 'content':item, 'metadata':{}} for item in items]
    validate_codex_preservation(segments)
    return items


def native_seed(agent_id, binding, *, store=None, routes=None):
    """Return a frozen seed, or None when this binding has no native archive.

    Never edit old Segments or infer correspondence from readable message text.
    The selected route and exact native session identity establish provenance.
    """
    if not binding.get('backend_session_id') or binding.get('provider') != 'codex':
        return None
    from .agent_data_store import get_agent_data_store, stable_segment_id
    from .account_adapters import CodexRoutes
    store = store or get_agent_data_store()
    routes = routes or CodexRoutes()
    thread_id = binding['backend_session_id']
    path = routes.find_thread_rollout(binding['backend_route_id'], thread_id, include_archived=True)
    if path is None:
        return None
    items = read_native_window(path, thread_id)
    refs = []
    for index, item in enumerate(items):
        digest = hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        sid = stable_segment_id('native-preservation', agent_id, binding['provider_session_ref'], str(index), digest)
        # Standalone immutable replay facts, never appended to visible Chat.
        kind = ('input' if item.get('type') == 'message' and item.get('role') in {'user','system','developer'}
                else 'tool' if item.get('type') in {'function_call','function_call_output','custom_tool_call','custom_tool_call_output'} else 'model')
        store.put_segment(segment_id=sid, segment_type=kind, content=item,
            content_type='application/json', created_by_agent_id=agent_id,
            created_by_actor_ref=agent_id, metadata={'kind':'native_preservation',
                'provider_session_ref':binding['provider_session_ref'], 'native_index':index})
        refs.append({'segment_id':sid})
    return {'schema':'tfi.provider-context-seed.v1', 'segments':refs,
            'empty_context':not refs, 'source_agent_id':agent_id}
