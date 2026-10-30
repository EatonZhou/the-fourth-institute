"""Browsing and editing a Repo's files: directory listings, file previews, search and edits."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from management_common.clock import utc_now_iso
from management_common.text_values import stripped_text
from management_portal.repo_workspace_checkout import (
    RepoWorkspaceConflict, _checkout, _repo_lock, _safe_relative_path, _source_capabilities, _source_root,
    _target,
)
from management_portal.repo_workspace_git import _git, _git_bytes
from management_portal.repo_workspace_state import _commit_record, _head_commit, _status_payload


MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_TREE_ENTRIES = 1000
MAX_SEARCH_RESULTS = 120


def _latest_commits_by_child(checkout: Path, directory: str) -> dict[str, dict[str, Any]]:
    pathspec = directory or "."
    raw = _git(
        checkout,
        "log",
        "-n",
        "240",
        "--date=iso-strict",
        "--format=%x1e%H%x1f%h%x1f%s%x1f%an%x1f%aI",
        "--name-only",
        "--",
        pathspec,
        allowed_returncodes=(0, 128),
    )
    commits: dict[str, dict[str, Any]] = {}
    prefix = f"{directory.rstrip('/')}/" if directory else ""
    for block in raw.split("\x1e"):
        clean_block = block.strip("\n")
        if not clean_block:
            continue
        lines = clean_block.splitlines()
        fields = lines[0].split("\x1f")
        fields.extend([""] * (5 - len(fields)))
        commit = {
            "sha": fields[0],
            "short_sha": fields[1],
            "subject": fields[2],
            "author": fields[3],
            "authored_at": fields[4],
        }
        for changed in lines[1:]:
            relative = changed.strip()
            if not relative or (prefix and not relative.startswith(prefix)):
                continue
            remainder = relative[len(prefix) :] if prefix else relative
            child = remainder.split("/", 1)[0]
            if child and child not in commits:
                commits[child] = commit
    return commits


def _read_text_file(path: Path, *, max_bytes: int = MAX_TEXT_BYTES) -> tuple[str, bool]:
    size = path.stat().st_size
    if size > max_bytes:
        return "", True
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        return "", True
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return "", True


def _readme_payload(checkout: Path, directory: str, entries: list[Path]) -> dict[str, Any] | None:
    preferred = (
        "readme.md",
        "readme.markdown",
        "readme.mdown",
        "readme.txt",
        "readme",
    )
    by_name = {
        path.name.casefold(): path
        for path in entries
        if not path.is_symlink() and path.is_file()
    }
    for name in preferred:
        path = by_name.get(name)
        if path is None:
            continue
        content, binary = _read_text_file(path, max_bytes=512 * 1024)
        if binary:
            return None
        relative = f"{directory.rstrip('/')}/{path.name}".strip("/")
        return {"path": relative, "name": path.name, "content": content}
    return None


def _visible_child_names(checkout: Path, directory: str) -> set[str]:
    """Return tracked and non-ignored worktree children for a directory."""
    raw = _git_bytes(
        checkout,
        "ls-files",
        "-co",
        "--exclude-standard",
        "-z",
        "--",
        directory or ".",
    )
    prefix = f"{directory.rstrip('/')}/" if directory else ""
    names: set[str] = set()
    for encoded_path in raw.split(b"\x00"):
        if not encoded_path:
            continue
        relative = encoded_path.decode("utf-8", errors="surrogateescape")
        if prefix:
            if not relative.startswith(prefix):
                continue
            relative = relative[len(prefix) :]
        if relative:
            names.add(relative.split("/", 1)[0])
    return names


def list_repo_tree(
    repo_id: str,
    path: str = "",
) -> dict[str, Any]:
    record, checkout = _source_root(repo_id)
    git = _source_capabilities(record, checkout)["git"]
    relative = _safe_relative_path(path, allow_empty=True)
    directory = _target(checkout, relative, must_exist=bool(relative))
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"not a directory: {relative}")
    visible_names = _visible_child_names(checkout, relative) if git else None
    children = sorted(
        [
            value
            for value in directory.iterdir()
            if value.name != ".git" and (visible_names is None or value.name in visible_names)
        ],
        key=lambda value: (
            not (not value.is_symlink() and value.is_dir()),
            value.name.casefold(),
        ),
    )
    truncated = len(children) > MAX_TREE_ENTRIES
    children = children[:MAX_TREE_ENTRIES]
    commit_map = _latest_commits_by_child(checkout, relative) if git else {}
    status = _status_payload(checkout) if git else None
    changed = status["changes"] if status else []
    entries: list[dict[str, Any]] = []
    for child in children:
        child_relative = f"{relative.rstrip('/')}/{child.name}".strip("/")
        try:
            stat = child.lstat()
        except FileNotFoundError:
            continue
        if child.is_symlink():
            kind = "symlink"
        elif child.is_dir():
            kind = "directory"
        else:
            kind = "file"
        child_changes = [
            item
            for item in changed
            if item["path"] == child_relative
            or item["path"].startswith(f"{child_relative}/")
            or item.get("original_path") == child_relative
        ]
        entries.append(
            {
                "name": child.name,
                "path": child_relative,
                "kind": kind,
                "size": stat.st_size if kind == "file" else None,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
                "status": child_changes[0]["label"] if child_changes else "",
                "change_count": len(child_changes),
                "last_commit": commit_map.get(child.name),
            }
        )
    return {
        "id": repo_id,
        "path": relative,
        "entries": entries,
        "truncated": truncated,
        "readme": _readme_payload(checkout, relative, children),
        "head": _head_commit(checkout) if git else None,
        "status": status,
    }


def read_repo_file(
    repo_id: str,
    path: str,
) -> dict[str, Any]:
    record, checkout = _source_root(repo_id)
    git = _source_capabilities(record, checkout)["git"]
    relative = _safe_relative_path(path)
    target = _target(checkout, relative, must_exist=True)
    if target.is_dir():
        raise ValueError(f"not a file: {relative}")
    if target.is_symlink():
        return {
            "id": repo_id,
            "path": relative,
            "name": target.name,
            "kind": "symlink",
            "symlink_target": os.readlink(target),
            "binary": False,
            "editable": False,
            "content": "",
            "size": target.lstat().st_size,
            "hash": "",
            "mime_type": "inode/symlink",
            "line_count": 0,
            "last_commit": _last_commit_for_path(checkout, relative) if git else None,
        }
    if not target.is_file():
        raise ValueError(f"not a regular file: {relative}")
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        data = stream.read(MAX_TEXT_BYTES + 1)
        digest.update(data)
        size = len(data)
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    truncated = size > MAX_TEXT_BYTES
    binary = truncated or b"\x00" in data[:8192]
    content = ""
    if not binary:
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            binary = True
    stat = target.stat()
    return {
        "id": repo_id,
        "path": relative,
        "name": target.name,
        "kind": "file",
        "content": content,
        "binary": binary,
        "truncated": truncated,
        "preview_unavailable_reason": "文件超过 2 MiB 的文本预览上限，请下载后查看。" if truncated else "",
        "editable": git and not binary and os.access(target, os.W_OK),
        "size": size,
        "hash": digest.hexdigest(),
        "mime_type": mimetypes.guess_type(relative)[0] or "application/octet-stream",
        "line_count": content.count("\n") + (1 if content else 0),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
        "last_commit": _last_commit_for_path(checkout, relative) if git else None,
    }


def _last_commit_for_path(checkout: Path, relative: str) -> dict[str, Any] | None:
    raw = _git(
        checkout,
        "log",
        "-1",
        "--follow",
        "--date=iso-strict",
        "--format=%H%x1f%h%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%s%x1f%D%x1f%b",
        "--",
        relative,
        allowed_returncodes=(0, 128),
    )
    return _commit_record(raw) if raw else None


def search_repo_files(
    repo_id: str,
    query: str,
    *,
    limit: int = MAX_SEARCH_RESULTS,
) -> dict[str, Any]:
    _record, checkout = _checkout(repo_id)
    clean_query = stripped_text(query)
    if not clean_query:
        return {"id": repo_id, "query": "", "results": [], "truncated": False}
    bounded_limit = min(MAX_SEARCH_RESULTS, max(1, int(limit)))
    paths = _git_bytes(
        checkout,
        "ls-files",
        "-co",
        "--exclude-standard",
        "-z",
    ).decode("utf-8", errors="replace").split("\x00")
    terms = [value.casefold() for value in clean_query.split() if value]
    matches = [
        path
        for path in paths
        if path
        and all(term in path.casefold() for term in terms)
        and ".git/" not in f"{path}/"
    ]
    matches.sort(
        key=lambda value: (
            0 if Path(value).name.casefold().startswith(terms[0]) else 1,
            len(value),
            value.casefold(),
        )
    )
    return {
        "id": repo_id,
        "query": clean_query,
        "results": [
            {
                "path": value,
                "name": Path(value).name,
                "directory": ("" if PurePosixPath(value).parent == PurePosixPath(".") else PurePosixPath(value).parent.as_posix()),
            }
            for value in matches[:bounded_limit]
        ],
        "truncated": len(matches) > bounded_limit,
    }


def write_repo_file(
    repo_id: str,
    path: str,
    content: str,
    *,
    expected_hash: str = "",
) -> dict[str, Any]:
    relative = _safe_relative_path(path)
    encoded = str(content).encode("utf-8")
    if len(encoded) > MAX_TEXT_BYTES:
        raise ValueError(f"file exceeds {MAX_TEXT_BYTES} byte editor limit")
    with _repo_lock(repo_id):
        _record, checkout = _checkout(repo_id)
        target = _target(checkout, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise ValueError("symbolic links cannot be edited in the web editor")
        if target.exists() and target.is_dir():
            raise ValueError(f"path is a directory: {relative}")
        current = target.read_bytes() if target.is_file() else b""
        clean_expected = stripped_text(expected_hash)
        if clean_expected and hashlib.sha256(current).hexdigest() != clean_expected:
            raise RepoWorkspaceConflict(
                "文件已被其他操作修改，请刷新后重新编辑。"
            )
        mode = target.stat().st_mode if target.exists() else None
        fd, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".portal-save",
            dir=target.parent,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                from management_common.file_permissions import prepare_atomic_permissions
                prepare_atomic_permissions(Path(temporary), target, mode if mode is not None else 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        result = read_repo_file(repo_id, relative)
        result["saved_at"] = utc_now_iso()
        result["status"] = _status_payload(checkout)
        return result


def create_repo_entry(
    repo_id: str,
    path: str,
    *,
    kind: str = "file",
    content: str = "",
) -> dict[str, Any]:
    relative = _safe_relative_path(path)
    clean_kind = stripped_text(kind).casefold()
    if clean_kind not in {"file", "directory"}:
        raise ValueError("entry kind must be file or directory")
    with _repo_lock(repo_id):
        _record, checkout = _checkout(repo_id)
        target = _target(checkout, relative)
        if target.exists() or target.is_symlink():
            raise FileExistsError(relative)
        if clean_kind == "directory":
            target.mkdir(parents=True)
            return {
                "id": repo_id,
                "path": relative,
                "kind": "directory",
                "created_at": utc_now_iso(),
                "status": _status_payload(checkout),
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        return write_repo_file(
            repo_id,
            relative,
            content,
        )


def move_repo_entry(
    repo_id: str,
    path: str,
    destination: str,
) -> dict[str, Any]:
    source_relative = _safe_relative_path(path)
    destination_relative = _safe_relative_path(destination)
    if source_relative == destination_relative:
        raise ValueError("source and destination are identical")
    with _repo_lock(repo_id):
        _record, checkout = _checkout(repo_id)
        source = _target(checkout, source_relative, must_exist=True)
        target = _target(checkout, destination_relative)
        if target.exists() or target.is_symlink():
            raise FileExistsError(destination_relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        return {
            "id": repo_id,
            "path": source_relative,
            "destination": destination_relative,
            "moved_at": utc_now_iso(),
            "status": _status_payload(checkout),
        }


def delete_repo_file(
    repo_id: str,
    path: str,
) -> dict[str, Any]:
    relative = _safe_relative_path(path)
    with _repo_lock(repo_id):
        _record, checkout = _checkout(repo_id)
        target = _target(checkout, relative, must_exist=True)
        if target.is_dir() and not target.is_symlink():
            raise ValueError("directories are removed by deleting their files")
        target.unlink()
        return {
            "id": repo_id,
            "path": relative,
            "deleted": True,
            "deleted_at": utc_now_iso(),
            "status": _status_payload(checkout),
        }
