"""Knowledge detail records the Resource Index does not hold.

The files an indexed Knowledge, Skill or Experiment item shows in its detail,
and Articles, whose detail is built from the Article record and its root.
"""
from __future__ import annotations

import os
from pathlib import Path

from management_portal.resources import get_resource_service

from knowledge_board_lan.knowledge_source import (
    _ARTICLE_DETAIL_IGNORED_DIRECTORIES,
    _FILE_ID_TO_PATH,
    _candidate_entry_files,
    _file_id_for_path,
    _file_kind,
    _nested_file_address,
)
from skill_board_lan import skill_backend

_ARTICLE_DETAIL_MAX_FILES = 2000


def _indexed_item_files(
    record: dict,
    primary_path: Path,
    content_root: Path,
) -> list[dict]:
    """Build detail-only file records from the indexed source location."""
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    resource_id = str(record.get("resource_id") or "").strip()
    resource_type = str(record.get("resource_type") or "").strip()
    if resource_type == "skill":
        candidates = (
            [primary_path]
            if attrs.get("single_file")
            else skill_backend._candidate_skill_files(content_root)
        )
    elif content_root.parent.name == "knowledge_base":
        companion_root = content_root / resource_id
        candidates = [primary_path, *_candidate_entry_files(companion_root)]
    else:
        candidates = _candidate_entry_files(content_root)

    paths: list[Path] = []
    seen: set[Path] = set()
    for raw_path in [primary_path, *candidates]:
        path = raw_path.resolve(strict=False)
        if path in seen or not path.is_file():
            continue
        try:
            path.relative_to(content_root)
        except ValueError:
            continue
        seen.add(path)
        paths.append(path)

    records = []
    for path in paths:
        relative_path = path.relative_to(content_root).as_posix()
        primary = path == primary_path
        file_id = _file_id_for_path(str(path))
        _FILE_ID_TO_PATH[file_id] = str(path)
        records.append(
            {
                "file_id": file_id,
                "address": resource_id if primary else _nested_file_address(resource_id, relative_path),
                "name": relative_path,
                "relative_path": relative_path,
                "kind": _file_kind(path),
                "primary": primary,
            }
        )
    return records


def _candidate_article_files(root: Path) -> list[Path]:
    resolved_root = root.resolve(strict=False)
    candidates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(resolved_root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _ARTICLE_DETAIL_IGNORED_DIRECTORIES and not name.startswith(".")
        )
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            path = Path(dirpath) / filename
            if path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve(strict=False)
            try:
                resolved.relative_to(resolved_root)
            except ValueError:
                continue
            candidates.append(resolved)
            if len(candidates) >= _ARTICLE_DETAIL_MAX_FILES:
                return candidates
    return candidates


def _article_primary_path(article: dict, article_root: Path) -> Path:
    primary_file = str(article.get("primary_file") or "").strip()
    if not primary_file:
        for raw in article.get("roots") or []:
            if not isinstance(raw, dict):
                continue
            candidate_root = Path(str(raw.get("path") or "")).expanduser().resolve(strict=False)
            if candidate_root == article_root:
                primary_file = str(raw.get("primary_file") or "").strip()
                if primary_file:
                    break
    if not primary_file:
        raise KeyError("article primary file not found")
    primary_path = (article_root / primary_file).resolve(strict=False)
    try:
        primary_path.relative_to(article_root)
    except ValueError as exc:
        raise ValueError("article primary file escapes the article root") from exc
    if not primary_path.is_file():
        raise KeyError("article primary file not found")
    return primary_path


def _article_item_record(
    resource_id: str,
    index_record: dict,
    *,
    active_only: bool = False,
    actor: str = "",
) -> dict:
    attrs = index_record.get("attributes") if isinstance(index_record.get("attributes"), dict) else {}
    try:
        service = get_resource_service()
        resolved = (
            service.resolve(resource_id, actor=actor)
            if actor
            else service.resolve(resource_id)
        )
    except (FileNotFoundError, KeyError) as exc:
        raise KeyError("article not found") from exc
    article = resolved.get("record") if isinstance(resolved.get("record"), dict) else {}
    article_root = Path(str(resolved.get("article_root") or article.get("canonical_root") or "")).expanduser().resolve(strict=False)
    if not article_root.is_dir():
        raise KeyError("article root not found")
    status = str(article.get("status") or attrs.get("status") or "").strip()
    if active_only and status not in {"active", "system"}:
        raise KeyError("article not found")
    primary_path = _article_primary_path(article, article_root)
    paths: list[Path] = []
    seen: set[Path] = set()
    for path in [primary_path, *_candidate_article_files(article_root)]:
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    files = []
    for path in paths:
        relative_path = path.relative_to(article_root).as_posix()
        primary = path == primary_path
        file_id = _file_id_for_path(str(path))
        _FILE_ID_TO_PATH[file_id] = str(path)
        files.append(
            {
                "file_id": file_id,
                "address": resource_id if primary else _nested_file_address(resource_id, relative_path),
                "name": relative_path,
                "relative_path": relative_path,
                "kind": _file_kind(path),
                "primary": primary,
            }
        )
    project_ids = [
        str(value).strip()
        for value in (article.get("project_ids") or attrs.get("project_ids") or [])
        if str(value).strip()
    ]
    try:
        mtime = int(primary_path.stat().st_mtime)
    except OSError:
        mtime = 0
    return {
        "kb_id": resource_id,
        "type": "article",
        "project": project_ids[0] if project_ids else "Articles",
        "project_ids": project_ids,
        "title": str(article.get("title") or index_record.get("title") or resource_id),
        "preview": str(index_record.get("summary") or ""),
        "status": status,
        "created_at": str(article.get("created_at") or ""),
        "importance": 0,
        "mtime": mtime,
        "file_id": _file_id_for_path(str(primary_path)),
        "path": str(primary_path),
        "root_path": str(article_root),
        "files": files,
    }
