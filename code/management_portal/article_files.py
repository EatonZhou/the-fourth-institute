"""An article's workspace files: listing, reading, describing, writing, creating,
uploading and searching files of its manuscript root.
"""
from __future__ import annotations

import codecs
import hashlib
import mimetypes
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from management_common.text_values import stripped_text
from management_portal import article_discovery, article_registry, article_settings


ARTICLE_UPLOAD_MAX_BYTES = 25 * 1024 * 1024

ARTICLE_TEXT_SUFFIXES = {
    ".bib",
    ".cfg",
    ".cls",
    ".csv",
    ".json",
    ".latexmkrc",
    ".md",
    ".markdown",
    ".py",
    ".sty",
    ".tex",
    ".txt",
    ".yaml",
    ".yml",
}

ARTICLE_TREE_IGNORED_SUFFIXES = {
    ".aux",
    ".bbl",
    ".bcf",
    ".blg",
    ".fdb_latexmk",
    ".fls",
    ".log",
    ".out",
    ".run.xml",
    ".synctex.gz",
    ".toc",
}


def _is_generated_latex_file(path: Path) -> bool:
    lowered = path.name.casefold()
    return any(lowered.endswith(suffix) for suffix in ARTICLE_TREE_IGNORED_SUFFIXES)


def list_article_files(
    article_id: str,
    *,
    build_root: str | Path | None = None,
    limit: int = 2500,
) -> dict[str, Any]:
    record, root_item, article_root = article_registry._article_source_context(
        article_id
    )
    settings = article_settings.load_article_settings(
        article_id, build_root=build_root
    )
    entries: list[dict[str, Any]] = []
    truncated = False
    for folder, directory_names, file_names in os.walk(article_root, followlinks=False):
        current = Path(folder)
        directory_names[:] = sorted(
            [
                name
                for name in directory_names
                if name not in article_discovery.ARTICLE_TREE_IGNORED_DIRECTORIES
                and not (current / name).is_symlink()
            ],
            key=str.casefold,
        )
        for name in directory_names:
            path = current / name
            relative = path.relative_to(article_root).as_posix()
            entries.append({"path": relative, "name": name, "type": "directory"})
            if len(entries) >= limit:
                truncated = True
                break
        if truncated:
            break
        for name in sorted(file_names, key=str.casefold):
            path = current / name
            if path.is_symlink() or _is_generated_latex_file(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            relative = path.relative_to(article_root).as_posix()
            entries.append(
                {
                    "path": relative,
                    "name": name,
                    "type": "file",
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime
                    ).astimezone().isoformat(timespec="seconds"),
                    "editable": (
                        path.suffix.casefold() in ARTICLE_TEXT_SUFFIXES
                        or path.name.casefold() == ".latexmkrc"
                    ),
                    "primary": relative == settings["primary_file"],
                }
            )
            if len(entries) >= limit:
                truncated = True
                break
        if truncated:
            break
    return {
        "id": article_id,
        "root": str(article_root),
        "primary_file": settings["primary_file"],
        "record_primary_file": stripped_text(
            root_item.get("primary_file") or record.get("primary_file")
        ),
        "entries": entries,
        "truncated": truncated,
    }


def read_article_file(
    article_id: str,
    relative_path: str,
    *,
    max_bytes: int = 8 * 1024 * 1024,
) -> dict[str, Any]:
    article_root, target = article_registry._article_target_path(
        article_id, relative_path
    )
    if not target.is_file():
        raise FileNotFoundError(target)
    data = target.read_bytes()
    if len(data) > max_bytes:
        raise ValueError(f"article file is too large to edit ({len(data)} bytes)")
    if b"\x00" in data:
        raise ValueError("binary article files cannot be opened in the text editor")
    stat = target.stat()
    revision = hashlib.sha256(data).hexdigest()
    return {
        "id": article_id,
        "path": target.relative_to(article_root).as_posix(),
        "name": target.name,
        "content": data.decode("utf-8", errors="replace"),
        "size": len(data),
        "revision": revision,
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime
        ).astimezone().isoformat(timespec="seconds"),
    }


def resolve_article_raw_file(
    article_id: str,
    relative_path: str,
) -> Path:
    _article_root, target = article_registry._article_target_path(
        article_id, relative_path
    )
    if not target.is_file():
        raise FileNotFoundError(target)
    return target


def describe_article_file(
    article_id: str,
    relative_path: str,
) -> dict[str, Any]:
    target = resolve_article_raw_file(
        article_id, relative_path
    )
    stat = target.stat()
    with target.open("rb") as stream:
        sample = stream.read(4096)
    binary = b"\x00" in sample
    try:
        codecs.getincrementaldecoder("utf-8")().decode(sample, final=False)
    except UnicodeDecodeError:
        binary = True
    raw_url = f"/api/articles/{article_id}/raw?path={quote(relative_path, safe='')}"
    return {
        "id": article_id,
        "path": relative_path,
        "name": target.name,
        "mediaType": mimetypes.guess_type(target.name)[0] or "application/octet-stream",
        "binary": binary,
        "size": stat.st_size,
        "version": str(stat.st_mtime_ns),
        "rawUrl": raw_url,
        "downloadUrl": f"{raw_url}&download=1",
    }


def write_article_file(
    article_id: str,
    relative_path: str,
    content: str,
    *,
    max_bytes: int = 8 * 1024 * 1024,
) -> dict[str, Any]:
    article_root, target = article_registry._article_target_path(
        article_id, relative_path
    )
    if not target.is_file():
        raise FileNotFoundError(target)
    encoded = str(content).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"article file is too large to save ({len(encoded)} bytes)")
    mode = target.stat().st_mode
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            from management_common.file_permissions import prepare_atomic_permissions
            prepare_atomic_permissions(Path(temporary_name), target, mode)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    stat = target.stat()
    return {
        "id": article_id,
        "path": target.relative_to(article_root).as_posix(),
        "size": stat.st_size,
        "revision": hashlib.sha256(encoded).hexdigest(),
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime
        ).astimezone().isoformat(timespec="seconds"),
    }


def create_article_entry(
    article_id: str,
    relative_path: str,
    kind: str,
) -> dict[str, Any]:
    article_root, target = article_registry._article_target_path(
        article_id, relative_path
    )
    entry_kind = stripped_text(kind).casefold()
    if entry_kind not in {"file", "directory"}:
        raise ValueError("article entry kind must be file or directory")
    if target.exists():
        raise FileExistsError(target)
    if not target.parent.is_dir():
        raise FileNotFoundError(target.parent)
    if entry_kind == "directory":
        target.mkdir()
    else:
        target.touch(exist_ok=False)
    return {
        "id": article_id,
        "path": target.relative_to(article_root).as_posix(),
        "name": target.name,
        "type": entry_kind,
    }


def upload_article_file(
    article_id: str,
    *,
    filename: str,
    data: bytes,
    directory: str = "",
    max_bytes: int = ARTICLE_UPLOAD_MAX_BYTES,
) -> dict[str, Any]:
    clean_name = Path(stripped_text(filename)).name
    if not clean_name or clean_name in {".", ".."} or "\x00" in clean_name:
        raise ValueError("uploaded article filename is invalid")
    if len(data) > max_bytes:
        raise ValueError(f"uploaded article file is too large ({len(data)} bytes)")
    article_root, target_directory = article_registry._article_target_path(
        article_id,
        directory,
        allow_root=True,
    )
    if not target_directory.is_dir():
        raise FileNotFoundError(target_directory)
    destination = (target_directory / clean_name).resolve(strict=False)
    try:
        destination.relative_to(article_root)
    except ValueError as exc:
        raise ValueError("uploaded article path escapes the article root") from exc
    if destination.exists():
        raise FileExistsError(destination)
    with destination.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return {
        "id": article_id,
        "path": destination.relative_to(article_root).as_posix(),
        "name": destination.name,
        "type": "file",
        "size": destination.stat().st_size,
    }


def search_article_files(
    article_id: str,
    query: str,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    needle = stripped_text(query)
    if not needle:
        return {"id": article_id, "query": "", "results": [], "truncated": False}
    if len(needle) > 200:
        raise ValueError("article search query is too long")
    tree = list_article_files(article_id)
    lowered = needle.casefold()
    results: list[dict[str, Any]] = []
    truncated = False
    for entry in tree["entries"]:
        if entry.get("type") != "file" or not entry.get("editable"):
            continue
        try:
            payload = read_article_file(
                article_id,
                str(entry.get("path") or ""),
                max_bytes=2 * 1024 * 1024,
            )
        except (OSError, ValueError):
            continue
        for line_number, line in enumerate(
            str(payload.get("content") or "").splitlines(), start=1
        ):
            column = line.casefold().find(lowered)
            if column < 0:
                continue
            results.append(
                {
                    "path": entry["path"],
                    "line": line_number,
                    "column": column + 1,
                    "preview": line.strip()[:320],
                }
            )
            if len(results) >= max(1, min(int(limit), 500)):
                truncated = True
                break
        if truncated:
            break
    return {
        "id": article_id,
        "query": needle,
        "results": results,
        "truncated": truncated,
    }
