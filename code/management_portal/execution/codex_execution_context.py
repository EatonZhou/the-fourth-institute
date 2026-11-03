"""Keep a Codex thread's shell context locator stable across binding moves.

The native thread keeps its creation environment until it can be reloaded.
These adapter artifacts record that locator, not a second Provider binding.
Only the current execution ID is written into the context file itself.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from management_common.file_lock import lock_stream
from management_portal.shared_state import atomic_replace_json
from .config import execution_state_root


def _root() -> Path:
    return execution_state_root() / 'codex_context'


def _receipt(route: str, thread: str) -> Path:
    identity = json.dumps([route, thread], separators=(',', ':')).encode()
    return _root() / 'threads' / (hashlib.sha256(identity).hexdigest() + '.json')


def _read(path: Path, route: str, thread: str) -> Path | None:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if (not isinstance(value, dict) or value.get('schema') != 'tfi.codex-thread-context.v1'
            or value.get('route') != route or value.get('thread') != thread
            or not re.fullmatch(r'[0-9a-f]{64}\.json', str(value.get('filename') or ''))):
        raise ValueError('Invalid Codex thread execution context locator')
    return _root() / value['filename']


def context_path(request, *, backend_route_id: str = '', backend_session_id: str = '') -> Path:
    if backend_route_id and backend_session_id:
        saved = _read(_receipt(backend_route_id, backend_session_id), backend_route_id, backend_session_id)
        if saved is not None:
            return saved
    identity = request.session.binding_id or request.execution_id
    return _root() / (hashlib.sha256(identity.encode()).hexdigest() + '.json')


def remember_thread_context(request, *, backend_route_id: str, backend_session_id: str) -> None:
    """Record only the environment actually supplied when this thread was created."""
    if not backend_route_id or not backend_session_id:
        raise ValueError('Codex thread context requires an exact route and thread')
    target = context_path(request)
    path = _receipt(backend_route_id, backend_session_id)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with path.with_suffix('.lock').open('a', encoding='utf-8') as stream, lock_stream(stream):
        existing = _read(path, backend_route_id, backend_session_id)
        if existing is not None:
            if existing != target:
                raise ValueError('Codex thread execution context locator changed')
            return
        atomic_replace_json(path, {'schema':'tfi.codex-thread-context.v1',
                                  'route':backend_route_id, 'thread':backend_session_id,
                                  'filename':target.name})
