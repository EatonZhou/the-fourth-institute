"""Files inside a folder resource, addressed as ``<root-id>/file[/<relative-path>]``.

A Workspace, a Resident (its Resident Folder) and a Lineage (its Lineage
Folder) expose the files under their folder through this one address.  Each
component is opened without following symlinks, and bytes are read from the
inode that was opened, so a path cannot be swapped between the permission
check and the read.  ``<root-id>/file`` and any directory inside it read as
the directory's direct children.
"""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import urlencode

from management_common import secure_paths
from management_common.secure_paths import open_target

DIRECTORY_MEDIA_TYPE = "application/vnd.tfi.directory+json"
MAX_DIRECTORY_ENTRIES = 200


def file_parts(suffix: str) -> tuple[str, ...] | None:
    """Relative path parts of a ``file`` address suffix: ``()`` for the folder itself, None for another suffix."""

    namespace, separator, relative = suffix.partition("/")
    if namespace != "file":
        return None
    if not separator or not relative:
        return ()
    return secure_paths.relative_parts(relative)


def lineage_folder(lineage_id: str) -> Path:
    from management_portal.society_service.lineage_storage import canonical_lineage_folder

    # The Lineage entry may link to its Project folder; files are read under the real directory.
    return Path(canonical_lineage_folder(lineage_id)).resolve(strict=False)


def resident_folder(resident_id: str, record: dict[str, Any]) -> Path:
    relations = (record.get("attributes") or {}).get("relations") or []
    lineage_id = next((str(item.get("target_id") or "") for item in relations
                       if isinstance(item, dict) and item.get("kind") == "lineage"), "")
    if not lineage_id:
        raise KeyError(f"{resident_id} belongs to no Lineage")
    return lineage_folder(lineage_id) / "residents" / resident_id


def hidden(parts: tuple[str, ...]) -> bool:
    from ..workspace_files import _hidden_workspace_path

    return bool(parts) and _hidden_workspace_path("/".join(parts))


def describe(result: dict[str, Any], root: PurePath, parts: tuple[str, ...], *, hide: bool) -> dict[str, Any]:
    """Add the file or directory an address names to a resolution; ``hide`` refuses the hidden paths."""

    if hide and hidden(parts):
        raise KeyError(result["address"])
    relative = "/".join(parts)
    name = parts[-1] if parts else (root.name or "/")
    result.update(target_kind="file", relative_path=relative, filename=name, title=name,
                  portal_url="/api/resources/content?" + urlencode({"address": result["address"]}))
    if result.get("exists"):
        try:
            with open_target(root, parts) as (_fd, target):
                if stat.S_ISDIR(target.st_mode):
                    result.update(target_kind="directory", media_type=DIRECTORY_MEDIA_TYPE)
                else:
                    result["content_size"] = target.st_size
                result["content_mtime_ns"] = target.st_mtime_ns
        except FileNotFoundError:
            result["exists"] = False
    # No resolved_path: HTTP must read these bytes through the Provider, not
    # reopen a filesystem path after ResourceService's boundary checks.
    return result


def _modified_at(info: os.stat_result) -> str:
    return datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def entry_kind(name: str, info: os.stat_result) -> str:
    from ..workspace_files import workspace_file_kind

    if stat.S_ISDIR(info.st_mode):
        return "directory"
    if stat.S_ISLNK(info.st_mode):
        return "symlink"
    return workspace_file_kind(name) if stat.S_ISREG(info.st_mode) else "other"


def metadata(info: os.stat_result, name: str) -> dict[str, Any]:
    """Name, preview kind, size and modification time of a file or directory."""

    directory = stat.S_ISDIR(info.st_mode)
    return {"name": name, "kind": entry_kind(name, info), "size": 0 if directory else int(info.st_size),
            "modified_at": _modified_at(info),
            # Nanosecond timestamps exceed JavaScript's exact integer range.
            "mtime_ns": str(info.st_mtime_ns)}


def _listing(fd: int, root_id: str, parts: tuple[str, ...]) -> dict[str, Any]:
    infos = {}
    with secure_paths.scandir(fd) as entries:
        for entry in entries:
            try:
                infos[entry.name] = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
    ordered = sorted(infos, key=lambda name: (not stat.S_ISDIR(infos[name].st_mode), name.casefold()))
    visible = [name for name in ordered if not hidden((*parts, name))]
    entries = []
    for name in visible[:MAX_DIRECTORY_ENTRIES]:
        relative = "/".join((*parts, name))
        entries.append({**metadata(infos[name], name), "path": relative, "address": f"{root_id}/file/{relative}"})
    return {"entries": entries, "truncated": len(visible) > MAX_DIRECTORY_ENTRIES,
            "hidden_count": len(ordered) - len(visible)}


def read_content(resolved: dict[str, Any], root: PurePath, *, max_bytes: int = 4 * 1024 * 1024):
    if not resolved["exists"]:
        raise KeyError(resolved["address"])
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    parts = tuple(part for part in resolved["relative_path"].split("/") if part)
    with open_target(root, parts) as (fd, info):
        if stat.S_ISDIR(info.st_mode):
            data = json.dumps(_listing(fd, resolved["root_id"], parts), ensure_ascii=False).encode("utf-8")
            if len(data) > max_bytes:
                raise ValueError(f"resource exceeds {max_bytes} byte content limit")
            return data, DIRECTORY_MEDIA_TYPE, resolved
        if info.st_size > max_bytes:
            raise ValueError(f"resource exceeds {max_bytes} byte content limit")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"resource exceeds {max_bytes} byte content limit")
    from ..content_payload import resolved_media_type

    return data, resolved_media_type(resolved, fallback="application/octet-stream"), resolved


def read_content_range(resolved: dict[str, Any], root: PurePath, *, start: int, end: int):
    if not resolved["exists"]:
        raise KeyError(resolved["address"])
    if resolved["target_kind"] != "file":
        raise ValueError("Only a file address has a byte range")
    if type(start) is not int or type(end) is not int or start < 0 or end < start:
        raise ValueError("content range requires non-negative inclusive start and end")
    with open_target(root, tuple(resolved["relative_path"].split("/"))) as (fd, info):
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Only a file address has a byte range")
        if start >= info.st_size:
            raise ValueError("content range starts beyond the resource")
        remaining = min(end, info.st_size - 1) - start + 1
        os.lseek(fd, start, os.SEEK_SET)
        chunks = []
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    from ..content_payload import resolved_media_type

    current = {**resolved, "content_size": info.st_size, "content_mtime_ns": info.st_mtime_ns}
    return b"".join(chunks), resolved_media_type(resolved, fallback="application/octet-stream"), current
