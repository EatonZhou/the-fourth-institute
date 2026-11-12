"""Freeze a registered Codex session's actual effective native history.

Display transcripts are not replay logs. Immutable native items are captured
outside Chat, retaining ciphertext and the Provider's compaction boundary.
"""
import hashlib
import json
from pathlib import Path

from .session_history_import import validate_codex_preservation
from .codex_rollout_history import _is_rollback_event


def read_native_window(path, thread_id, *, evidence=None):
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
    if evidence is not None:
        evidence.update(path=str(path), fingerprint=[after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns])
    return items


def native_seed(agent_id, binding, *, store=None, routes=None, actor_ref=None, evidence=None):
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
    items = read_native_window(path, thread_id, evidence=evidence)
    if not items:
        return {'schema':'tfi.provider-context-seed.v1', 'segments':[],
                'empty_context':True, 'source_agent_id':agent_id}
    # Materialize the Provider's existing effective window as one immutable
    # context boundary. No model compaction runs and no old Segment changes.
    # Admission attaches this reference through the Execution preservation port;
    # preparing it must not change the live Agent's effective window.
    digest = hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    sid = stable_segment_id('native-preservation', agent_id, binding['provider_session_ref'], digest)
    store.put_segment(segment_id=sid, segment_type='compaction',
        content={'replacement_history':items}, content_type='application/json',
        created_by_agent_id=agent_id, created_by_actor_ref=actor_ref or agent_id,
        metadata={'kind':'native_preservation', 'provider_session_ref':binding['provider_session_ref'],
                  'source':'native_effective_window'})
    return {'schema':'tfi.provider-context-seed.v1', 'segments':[{'segment_id':sid}],
            'empty_context':not items, 'source_agent_id':agent_id}
