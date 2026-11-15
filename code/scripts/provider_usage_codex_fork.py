"""Resolve inherited Codex turn identities from native parent history."""
import json
from functools import lru_cache
from pathlib import Path
from scripts.provider_usage_native import record_path

_homes = {}


def parent_path(source, parent_id):
    explicit = source.get('fork_parent_path')
    if explicit:
        return record_path(explicit)
    path = record_path(source['path'])
    home = next((p.parent for p in path.parents if p.name in ('sessions', 'archived_sessions')), None)
    if home is None:
        raise ValueError('Codex fork parent history path required')
    index = _homes.get(str(home), {})
    if parent_id not in index or not index[parent_id].is_file():
        index = {p.stem[-36:]: p for area in ('sessions', 'archived_sessions')
                 for p in (home / area).rglob('*.jsonl') if not p.is_symlink()}
        _homes[str(home)] = index
    if parent_id not in index:
        raise FileNotFoundError('Codex fork parent history missing')
    return index[parent_id]


def inherited_turns(source, parent_id):
    """Includes the parent's copied ancestors. Never consult clocks or prompts."""
    path = parent_path(source, parent_id)
    if path.is_symlink():
        raise ValueError('Codex fork parent symlink excluded')
    s = path.stat()
    signature = (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    return _read_parent_turns(str(path), signature, parent_id)


@lru_cache(maxsize=128)
def _read_parent_turns(path_string, signature, parent_id):
    path = Path(path_string)
    turns, found, current = set(), False, ''
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if not any(marker in line for marker in ('"session_meta"', '"turn_context"', '"task_started"', '"token_count"')):
                continue
            row = json.loads(line)
            payload = row.get('payload') or {}
            if row.get('type') == 'session_meta' and not found:
                if payload.get('id') != parent_id:
                    raise ValueError('Codex fork parent identity mismatch')
                found = True
            if row.get('type') == 'turn_context' or (
                    row.get('type') == 'event_msg' and payload.get('type') == 'task_started'):
                current = str(payload.get('turn_id') or '')
                if current:
                    turns.add(current)
            if row.get('type') == 'event_msg' and payload.get('type') == 'token_count' and not current:
                raise ValueError('Codex fork parent usage lacks turn identity')
    if not found:
        raise ValueError('Codex fork parent metadata missing')
    s = path.stat()
    if signature != (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns):
        raise OSError('Codex fork parent changed during read')
    return tuple(sorted(turns))
