from __future__ import annotations
from management_common.portal_paths import path as portal_path

import os
import threading
from pathlib import Path

from .notifications import publish_local_projection_change
from .notifications.file_watch import RecursiveFileChangeWatcher


ARCHIVE_ROOT = Path(
    os.getenv(
        "KNOWLEDGE_ARCHIVE_ROOT",
        str(portal_path("staging_root") / "shared" / "knowledge_archive_jobs"),
    )
).expanduser()

_WATCHER_LOCK = threading.Lock()
_WATCHER: RecursiveFileChangeWatcher | None = None


def _archive_change(path_text: str) -> None:
    path = Path(path_text).expanduser().resolve(strict=False)
    try:
        relative = path.relative_to(ARCHIVE_ROOT.resolve(strict=False))
    except ValueError:
        return
    if len(relative.parts) < 2:
        return
    actor, archive_id = relative.parts[:2]
    if not actor or not archive_id:
        return
    field = "/".join(relative.parts[2:]) or "job"
    if field.endswith(".tmp") or "/." in f"/{field}":
        return
    publish_local_projection_change(
        "knowledge-archive",
        archive_id,
        actor=actor,
        fields=[field],
    )


def _archive_overflow() -> None:
    publish_local_projection_change(
        "knowledge-archive",
        "snapshot",
        fields=["filesystem-overflow"],
    )


def ensure_knowledge_archive_projection_watcher() -> RecursiveFileChangeWatcher | None:
    global _WATCHER
    if not ARCHIVE_ROOT.is_dir():
        return None
    with _WATCHER_LOCK:
        if _WATCHER is not None:
            return _WATCHER
        _WATCHER = RecursiveFileChangeWatcher(
            [ARCHIVE_ROOT],
            _archive_change,
            _archive_overflow,
            thread_name="portal-knowledge-archive-files",
        )
        _WATCHER.start()
        return _WATCHER


