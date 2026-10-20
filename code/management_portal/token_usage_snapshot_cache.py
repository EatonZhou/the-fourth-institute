"""One deduplicated usage snapshot per environment/user, separate from status."""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from pathlib import Path

from . import status_cache_store as shared, usage_snapshot_codec as codec
from .shared_state import atomic_replace_json

_entries: OrderedDict = OrderedDict()
_lock = threading.Lock()


def _path(source: Path, key: str) -> Path:
    name = shared.identity(key)[0]
    return source.with_suffix(".usage") / (hashlib.sha256(name.encode()).hexdigest() + ".json")


def _load(path: Path) -> tuple[str, dict]:
    stat = path.stat()
    signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
    with _lock:
        cached = _entries.get(path)
        if cached and cached[0] == signature:
            _entries.move_to_end(path)
            return cached[1]
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Invalid usage snapshot")
    result = (document["key"], codec.decode(document))
    with _lock:
        _entries[path] = (signature, result)
        _entries.move_to_end(path)
        while len(_entries) > 16:
            _entries.popitem(last=False)
    return result


def read(source: Path, key: str) -> dict:
    try:
        stored_key, entry = _load(_path(source, key))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return {}
    return entry if stored_key == key else {}


def _rank(key: str, entry: dict) -> tuple:
    payload = entry.get("payload") or {}
    return (shared.identity(key)[1], float(payload.get("snapshot_version") or 0),
            float(entry.get("stored_at") or 0), float(entry.get("next_refresh_at") or 0))


def write(source: Path, key: str, entry: dict) -> None:
    path = _path(source, key)
    document = codec.encode(key, entry)
    with shared.locked(path):
        try:
            previous_key, previous = _load(path)
        except (FileNotFoundError, ValueError, KeyError, TypeError, IndexError):
            previous_key, previous = "", {}
        # Another worker may have completed a full refresh since the caller read.
        # A newer partial summary (one account's native usage unreadable) is
        # current data and may replace it; the version check below orders them.
        if (previous_key == key and (previous.get("payload") or {}).get("status") == "ok"
                and (entry.get("payload") or {}).get("status") in {"loading", "error", "unavailable"}):
            return
        # Retaining the same data version may shorten the retry deadline after a
        # failure. Scheduling metadata must not prevent that state from saving.
        if previous_key and _rank(previous_key, previous)[:2] > _rank(key, entry)[:2]:
            return
        atomic_replace_json(path, document, indent=None, sort_keys=False)
