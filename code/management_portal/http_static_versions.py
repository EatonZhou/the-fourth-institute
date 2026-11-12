"""Short-lived copies of the exact static bytes referenced by issued documents.

The cache survives Web replacement, but is not an archive or a permission owner.
Callers must resolve the original static path and apply its normal access policy.
"""
from __future__ import annotations

import hashlib
from contextlib import suppress
from pathlib import Path
import re
import tempfile
import time

from management_common.instance_manifest import load


RETENTION_SECONDS = 3600
MAX_BYTES = 128 * 1024 * 1024
MAX_FILES = 2048
_DIGEST = re.compile(r"sha256-([0-9a-f]{64})\Z")


def _directory() -> Path:
    return load().directory("cache") / "portal-static-versions"


def _entry(path: Path, version: str) -> Path | None:
    match = _DIGEST.fullmatch(version)
    if match is None:
        return None
    # Bind bytes to both their original path and their digest; Dev and Release
    # paths therefore cannot supply each other's scripts.
    owner = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
    return _directory() / (owner + "-" + match[1] + ".asset")


def _prune(directory: Path, now: float) -> None:
    entries = []
    for file in directory.glob("*.asset"):
        try:
            stat = file.stat()
            if now - stat.st_mtime > RETENTION_SECONDS:
                file.unlink(missing_ok=True)
            else:
                entries.append((stat.st_mtime, stat.st_size, file))
        except OSError:
            continue
    total = sum(size for _, size, _ in entries)
    count = len(entries)
    for _, size, file in sorted(entries):
        if total <= MAX_BYTES and count <= MAX_FILES:
            break
        try:
            file.unlink(missing_ok=True)
        except OSError:
            continue
        total -= size
        count -= 1


def remember(path: Path, version: str, data: bytes) -> None:
    """Capture before issuing the URL. Cache trouble must not break a page."""
    temporary = None
    try:
        entry = _entry(path, version)
        if entry is None or len(data) > MAX_BYTES:
            return
        now = time.time()
        if entry.is_file():
            # Refresh retention for recently issued documents, without writing
            # or scanning the entire cache on every normal page request.
            if now - entry.stat().st_mtime > 60:
                entry.touch()
                # Renewal adds no bytes or files. Admissions prune capacity;
                # recall rejects expired entries without a directory scan.
            return
        entry.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=entry.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.replace(entry)
        _prune(entry.parent, now)
    except OSError:
        pass
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def recall(path: Path, version: str) -> bytes | None:
    """Return only matching, retained bytes; never substitute a newer version."""
    try:
        entry = _entry(path, version)
        if entry is None or time.time() - entry.stat().st_mtime > RETENTION_SECONDS:
            return None
        with entry.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return None
        if "sha256-" + hashlib.sha256(data).hexdigest() == version:
            return data
    except OSError:
        pass
    return None
