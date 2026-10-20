"""Shared status cache storage and version retention (not usage history)."""
from __future__ import annotations
from management_common.portal_paths import path as portal_path

from management_common.file_lock import lock_stream
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from .shared_state import atomic_replace_json


def default_path() -> Path:
    return Path(os.getenv("AGENT_STATUS_CACHE_PATH", str(portal_path("staging_root") / "shared" / "portal_cache" / "agent_status" / "status_cache.json")))


def identity(key: str) -> tuple[str, int]:
    match = re.fullmatch(r"([^:]+):v(\d+)(:.*)?", key)
    return (match[1] + (match[3] or ""), int(match[2])) if match else (key, 0)


def current_entries(entries: dict) -> dict:
    return {key: value for key, value in entries.items()
            if key.startswith("disk_status:") or re.fullmatch(r"agent_status_summary:v10:[^:]+", key)}


@contextmanager
def locked(path: Path):
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as handle, lock_stream(handle):
        yield


def read(path: Path) -> dict:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(entries, dict):
        raise ValueError("Invalid status cache")
    return entries


def write(path: Path, entries: dict) -> None:
    atomic_replace_json(path, current_entries(entries), indent=None, sort_keys=False)
