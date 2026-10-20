"""Read-only host directory navigation, using existing path permissions."""
from __future__ import annotations

import heapq
from pathlib import Path

from management_common import secure_paths

PAGE_SIZE = 200


def authorize(path: Path, actor: str) -> None:
    from management_portal.society_service.lineage_access_policy import file_reaches, path_resource

    found = path_resource(str(path))
    resource, owner = (found[1], found[2]) if found else (str(path), "")
    if not file_reaches(actor, resource, write=False, owner=owner):
        raise PermissionError("没有读取此目录的权限")


def browse(arguments: dict, actor: str) -> dict:
    if not isinstance(arguments, dict) or set(arguments) - {"path", "after"}:
        raise ValueError("目录浏览只接受 path 和 after")
    raw, after = arguments.get("path", Path.home().anchor), arguments.get("after", "")
    if not isinstance(raw, str) or not Path(raw).is_absolute() or "\x00" in raw or len(raw) > 4096:
        raise ValueError("请输入主机上的绝对目录路径")
    if not isinstance(after, str) or len(after) > 255 or "/" in after or "\x00" in after:
        raise ValueError("目录翻页位置无效")
    path = Path(raw).resolve(strict=True)
    authorize(path, actor)
    # Pin the resolved directory; a link swapped in after authorization cannot
    # redirect the enumeration. No file contents or write operation are exposed.
    with secure_paths.open_directory(path) as fd:
        if not secure_paths.directory_unchanged(fd, path):
            raise PermissionError("目录位置已变化，请重新打开")
        def candidates():
            with secure_paths.scandir(fd) as entries:
                for entry in entries:
                    if entry.name <= after:
                        continue
                    try:
                        if not entry.is_dir(follow_symlinks=True):
                            continue
                        child = (path / entry.name).resolve(strict=True)
                        authorize(child, actor)
                        yield {"name": entry.name, "path": str(child)}
                    except (PermissionError, FileNotFoundError, NotADirectoryError, RuntimeError):
                        continue
        rows = heapq.nsmallest(PAGE_SIZE + 1, candidates(), key=lambda entry: entry["name"])
        authorize(path, actor)
        if not secure_paths.directory_unchanged(fd, path):
            raise PermissionError("目录位置已变化，请重新打开")
        return {
            "path": str(path), "parent": str(path.parent) if path != path.parent else None,
            "directories": rows[:PAGE_SIZE],
            "next_after": rows[PAGE_SIZE - 1]["name"] if len(rows) > PAGE_SIZE else "",
        }
