#!/usr/bin/env python3
"""Knowledge source parsing for the Resource Index adapter.

Reads the Project knowledge bases on disk: the tag vocabulary and Project list,
front matter and the version block, which files form an entry, and the item
record the index projects from them. The Resource Index fingerprints this
file's source, so a change here re-projects unchanged entries.

Dependencies:
  - management_portal.project_registry — Projects and the knowledge tag vocabulary
  - management_common.site_registry — for project-path rewriting
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

import yaml

from management_common.knowledge_metadata import document_metaness
from management_common.path_containment import path_is_within
from management_common.scan_cancellation import check_scan_cancelled

from management_portal.resources import get_resource_service

from management_portal import project_registry

# ── path rewriter (management_common lives in portal repo root) ────────────────
_PORTAL_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_PORTAL_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PORTAL_REPO_ROOT))

from management_common.site_registry import rewrite_shared_path  # noqa: E402

def _rewrite_project_path(path: str) -> str:
    return rewrite_shared_path(path, include_codes=False)

# ── module-level state ─────────────────────────────────────────────────────────
_MD_CACHE: dict[str, dict] = {}
_FILE_ID_TO_PATH: dict[str, str] = {}
_LOCK = threading.RLock()
_DEFAULT_TYPES = [
    "task",
    "todo",
    "review",
    "question",
    "idea",
    "insight",
    "hypothesis",
    "decision",
    "experiment",
    "thinking",
    "risk",
    "concept",
    "information",
    "patent",
]
_EXCLUDED_KNOWLEDGE_TYPES = {"skill"}
_TEXT_FILE_EXTENSIONS = {
    ".bbx",
    ".bib",
    ".bst",
    ".cbx",
    ".cfg",
    ".cls",
    ".csv",
    ".def",
    ".json",
    ".jsonl",
    ".ltx",
    ".log",
    ".md",
    ".py",
    ".sh",
    ".sty",
    ".tex",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_SKILL_TEXT_FILE_EXTENSIONS = {
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".xml",
}
_CODE_LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".sh": "shell",
}
_IMAGE_FILE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}
_VIDEO_FILE_EXTENSIONS = {".m4v", ".mov", ".mp4", ".ogg", ".ogv", ".webm"}
_AUDIO_FILE_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".oga", ".wav"}
_ENTRY_FILE_EXTENSIONS = _TEXT_FILE_EXTENSIONS | _IMAGE_FILE_EXTENSIONS
_EDITABLE_TEXT_FILE_EXTENSIONS = _TEXT_FILE_EXTENSIONS | _SKILL_TEXT_FILE_EXTENSIONS
_VALID_ITEM_FILE_EXTENSIONS = _ENTRY_FILE_EXTENSIONS | _SKILL_TEXT_FILE_EXTENSIONS
_ARTICLE_DETAIL_IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
_INDEX_ENTRY_FILE_LIMIT = 100

# ── helpers ────────────────────────────────────────────────────────────────────



def _parse_projects_yaml() -> list[dict]:
    out: list[dict] = []
    for p in project_registry.projects():
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        tags = p.get("tags")
        if not isinstance(tags, list):
            tags = []
        out.append(
            {
                "id": pid,
                "status": str(p.get("status") or ""),
                "path": _rewrite_project_path(str(p.get("path") or "")),
                "description": str(p.get("description") or ""),
                "ddl": str(p.get("ddl") or ""),
                "tags": [str(x) for x in tags if str(x).strip()],
            }
        )
    return out


def _parse_tag_vocab() -> list[dict]:
    return project_registry.tags()


def _get_tag_config() -> tuple[list[str], dict[str, str], dict[str, str], str]:
    tags = _parse_tag_vocab()
    if not tags:
        allowed_types = list(_DEFAULT_TYPES)
        tag_folder_map: dict[str, str] = {t: t for t in allowed_types}
    else:
        tag_folder_map = project_registry.tag_folder_map()
        allowed_types = [t for t in tag_folder_map.keys() if t not in _EXCLUDED_KNOWLEDGE_TYPES]

    folder_to_type = {folder: tid for tid, folder in tag_folder_map.items() if folder}
    config_hash = hashlib.sha1(
        ("\n".join([f"{tid}:{tag_folder_map.get(tid,'')}" for tid in allowed_types])).encode()
    ).hexdigest()[:12]
    return allowed_types, tag_folder_map, folder_to_type, config_hash


_FRONT_MATTER_PATTERN = re.compile(r"^\ufeff?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)")


def _parse_front_matter(md_text: str) -> tuple[dict, str]:
    match = _FRONT_MATTER_PATTERN.match(md_text)
    if not match:
        return {}, md_text

    meta = _yaml_front_matter(md_text)
    rest = "\n".join(md_text[match.end():].splitlines())
    return meta, rest


def _yaml_front_matter(md_text: str) -> dict[str, object]:
    """Parse typed YAML metadata for both indexing and resource writes."""

    match = _FRONT_MATTER_PATTERN.match(str(md_text or ""))
    if not match:
        return {}
    try:
        payload = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _version_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        normalized = value
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "").strip()


def _file_timestamp(mtime: int | float) -> str:
    if not mtime:
        return ""
    try:
        return datetime.fromtimestamp(
            float(mtime),
            tz=timezone.utc,
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return ""


def _version_principal(value: object) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    return {
        "user": str(raw.get("user") or "").strip(),
        "agent": str(raw.get("agent") or "").strip(),
    }


def knowledge_version_record(
    md_text: str,
    *,
    created_at: str = "",
    mtime: int | float = 0,
) -> dict[str, object]:
    """Return the stable public version/audit contract for one Knowledge item."""

    meta = _yaml_front_matter(md_text)
    raw_version = (
        meta.get("version")
        if isinstance(meta.get("version"), dict)
        else {}
    )
    try:
        number = max(
            1,
            int(raw_version.get("number") or 1),
        )
    except (TypeError, ValueError):
        number = 1
    recorded = bool(raw_version)
    return {
        "number": number,
        "created_at": _version_timestamp(
            raw_version.get("created_at")
            or meta.get("created_at")
            or created_at
        ),
        "updated_at": _version_timestamp(raw_version.get("updated_at"))
        or _file_timestamp(mtime),
        "created_by": _version_principal(raw_version.get("created_by")),
        "updated_by": _version_principal(raw_version.get("updated_by")),
        "recorded": recorded,
    }


def _is_index_file(path: Path) -> bool:
    return path.name.lower().endswith(".index.md")


def _is_entry_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _ENTRY_FILE_EXTENSIONS


def _file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "markdown"
    if suffix in _IMAGE_FILE_EXTENSIONS:
        return "image"
    if suffix in _VIDEO_FILE_EXTENSIONS:
        return "video"
    if suffix in _AUDIO_FILE_EXTENSIONS:
        return "audio"
    if suffix == ".pdf":
        return "pdf"
    if suffix in _CODE_LANGUAGE_BY_EXTENSION:
        return "code"
    if suffix in _EDITABLE_TEXT_FILE_EXTENSIONS:
        return "text"
    return "binary"


def _file_language(path: Path) -> str:
    return _CODE_LANGUAGE_BY_EXTENSION.get(path.suffix.lower(), "")


def _candidate_entry_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        [
            path for path in root.rglob("*")
            if _is_entry_file(path)
            and not path.name.startswith(".")
            and not any(
                part.startswith(".")
                for part in path.relative_to(root).parts
            )
            and not _is_index_file(path)
            and "__pycache__" not in path.parts
        ],
        key=lambda path: str(path.relative_to(root)).lower(),
    )


def _candidate_markdown_files(root: Path) -> list[Path]:
    return [path for path in _candidate_entry_files(root) if path.suffix.lower() == ".md"]


def _choose_primary_file(root: Path, markdown_files: list[Path]) -> Path | None:
    if not markdown_files:
        return None
    preferred_names = ["experiment.md", "README.md", "readme.md", "index.md"]
    for name in preferred_names:
        candidate = root / name
        if candidate in markdown_files:
            return candidate
    top_level = [path for path in markdown_files if path.parent == root]
    return sorted(top_level or markdown_files, key=lambda path: str(path.relative_to(root)).lower())[0]


def is_canonical_kb_id(value: object, item_type: str = "") -> bool:
    kb_id = str(value or "").strip()
    try:
        parsed = get_resource_service().validate_id(kb_id, item_type or None)
    except ValueError:
        return False
    return not item_type or parsed.resource_type == item_type


def _file_records(root: Path, primary_path: Path, files: list[Path]) -> list[dict]:
    records = []
    for path in files:
        records.append(
            {
                "file_id": _file_id_for_path(str(path)),
                "name": path.relative_to(root).as_posix(),
                "relative_path": path.relative_to(root).as_posix(),
                "kind": _file_kind(path),
                "primary": path == primary_path,
            }
        )
    return records


def _nested_file_address(kb_id: str, relative_path: str) -> str:
    segments = [
        quote(segment, safe="-._~:@+")
        for segment in str(relative_path or "").replace("\\", "/").split("/")
        if segment
    ]
    if not segments:
        raise ValueError("knowledge file relative path required")
    return f"{kb_id}/file/{'/'.join(segments)}"


def public_item_record(item: dict, *, include_files: bool = True) -> dict:
    kb_id = str(item.get("kb_id") or "").strip()
    if not is_canonical_kb_id(kb_id, str(item.get("type") or "")):
        raise ValueError("knowledge item has no canonical kb_id")
    out = {
        key: value
        for key, value in item.items()
        if key not in {
            "file_id",
            "path",
            "root_path",
            "files",
            "address",
            "selected_file_id",
            "selected_path",
            "id",
            "resource_id",
        }
    }
    out["kb_id"] = kb_id
    if include_files:
        files = []
        for raw in item.get("files") or []:
            if not isinstance(raw, dict):
                continue
            relative_path = str(raw.get("relative_path") or "").strip()
            primary = bool(raw.get("primary"))
            address = kb_id if primary else _nested_file_address(kb_id, relative_path)
            files.append(
                {
                    "address": address,
                    "name": str(raw.get("name") or relative_path),
                    "relative_path": relative_path,
                    "kind": str(raw.get("kind") or "binary"),
                    "primary": primary,
                }
            )
        out["files"] = files
    return out


def _first_non_empty_non_heading_line(text: str) -> str:
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if s.startswith("-"):
            s = s[1:].strip()
        if s:
            return s
    return ""


def _first_heading(text: str) -> str:
    """Return the document title only when it is an H1 (single #).

    The original implementation returned the first heading of any level,
    causing section headers like ``## 问题`` to be used as the card title.
    Here we stop at the first heading found: if it is H1, return it;
    if it is H2+, return "" so the caller falls back to filename extraction.
    """
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("# ") and not s.startswith("##"):
            return s[2:].strip()
        if s.startswith("#"):
            return ""   # first heading is H2+ — not a document title
        # non-heading lines are skipped (continue scanning for H1)
    return ""


def _title_from_filename(filename: str, item_type: str) -> str:
    name = os.path.basename(filename)
    marker = f"__{item_type}__"
    suffix = f"__{item_type}_"
    if marker in name and suffix in name:
        mid = name.split(marker, 1)[1]
        title_part = mid.split(suffix, 1)[0].strip()
        if title_part:
            return title_part
    return name


def _title_from_experiment_folder(entry_root: Path) -> str:
    try:
        root = entry_root.resolve()
    except (OSError, RuntimeError, ValueError):
        root = entry_root
    if not root.name or root.parent.name.lower() == "knowledge_base":
        return ""
    prefix = root.name.split("__experiment_", 1)[0].strip()
    return prefix or root.name


def _file_id_for_path(path: str) -> str:
    h = hashlib.sha1(os.path.abspath(path).encode())
    return h.hexdigest()[:16]


def knowledge_metadata_id(meta: dict) -> str:
    """TODO files own an id field; other knowledge files own kb_id."""
    field = "id" if str(meta.get("type") or "").strip() == "todo" else "kb_id"
    return str(meta.get(field) or "").strip()


def _read_kb_file(
    path: str,
    project_id: str,
    allowed_type_set: set[str],
    folder_to_type: dict[str, str],
    *,
    entry_root: Path,
    entry_files: list[Path],
) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            md = f.read()
    except (OSError, ValueError):
        return {}

    meta, body = _parse_front_matter(md)
    item_type = str(meta.get("type") or "").strip()
    if not item_type:
        parts = os.path.normpath(path).split(os.sep)
        try:
            kb_index = next(i for i, p in enumerate(parts) if p.lower() == "knowledge_base")
            if kb_index + 1 < len(parts):
                item_type = parts[kb_index + 1].strip()
        except StopIteration:
            item_type = ""
    item_type = item_type.lower()
    if item_type not in allowed_type_set:
        mapped = folder_to_type.get(item_type)
        if mapped and mapped in allowed_type_set:
            item_type = mapped
        else:
            return {}

    kb_id = knowledge_metadata_id(meta)
    if not is_canonical_kb_id(kb_id, item_type):
        return {}

    created_at = str(meta.get("created_at") or "")
    status = str(meta.get("status") or "")
    links = meta.get("links") if isinstance(meta.get("links"), dict) else {}
    # What the entry names as related: the index records it, so a research
    # direction finds the entries that name it (decision_46c37026e8ea).
    related = [str(value).strip() for value in links.get("related") or [] if isinstance(value, str) and value.strip()] \
        if isinstance(links.get("related"), list) else []
    task_fields = {}
    if item_type == "todo":
        status = str(meta.get("task_status") or ("finished" if status == "done" else "active"))
        task_fields = {
            "assignees": list(meta.get("assignees") or []),
            "task_kind": str(meta.get("task_kind") or "default"),
            "finished_at": meta.get("finished_at"),
            "archived_at": meta.get("archived_at"),
        }
    importance_raw = meta.get("importance")
    try:
        metaness = document_metaness(meta)
    except ValueError:
        metaness = None
    try:
        importance = int(str(importance_raw)) if importance_raw is not None else 0
    except ValueError:
        importance = 0

    filename = os.path.basename(path)
    title = _first_heading(body) or (
        _title_from_experiment_folder(entry_root)
        if item_type == "experiment"
        else ""
    ) or _title_from_filename(filename, item_type) or filename
    preview = _first_non_empty_non_heading_line(body)
    if len(preview) > 220:
        preview = preview[:220].rstrip() + "…"

    abs_path = os.path.abspath(path)
    file_id = _file_id_for_path(abs_path)
    try:
        mtime = int(os.path.getmtime(abs_path))
    except OSError:
        mtime = 0
    version = knowledge_version_record(
        md,
        created_at=created_at,
        mtime=mtime,
    )
    root_path = entry_root.resolve()
    primary_path = Path(abs_path).resolve()
    return {
        **task_fields,
        "file_id": file_id,
        "kb_id": kb_id,
        "type": item_type,
        "project": project_id,
        "created_at": created_at,
        "status": status,
        "importance": importance,
        "metaness": metaness,
        "title": title,
        "preview": preview,
        "filename": filename,
        "path": abs_path,
        "mtime": mtime,
        "version": version,
        "related": list(dict.fromkeys(related)),
        "root_path": str(root_path),
        "relative_path": primary_path.relative_to(root_path).as_posix() if root_path.is_dir() else primary_path.name,
        "file_count": len(entry_files),
        "files": _file_records(root_path if root_path.is_dir() else primary_path.parent, primary_path, entry_files),
    }


def _get_md_item_cached(
    primary_path: Path,
    entry_root: Path,
    entry_files: list[Path],
    project_id: str,
    config_hash: str,
    allowed_type_set: set[str],
    folder_to_type: dict[str, str],
) -> dict:
    abs_path = os.path.abspath(str(primary_path))
    try:
        mtime = os.path.getmtime(abs_path)
    except (OSError, ValueError):
        _MD_CACHE.pop(abs_path, None)
        return {}
    cached = _MD_CACHE.get(abs_path)
    files_signature = [
        (str(path.resolve()), int(path.stat().st_mtime_ns) if path.exists() else 0)
        for path in entry_files
    ]
    if (
        cached
        and cached.get("mtime") == mtime
        and cached.get("project_id") == project_id
        and cached.get("config_hash") == config_hash
        and cached.get("entry_root") == str(entry_root.resolve())
        and cached.get("files_signature") == files_signature
        and isinstance(cached.get("item"), dict)
    ):
        return dict(cached["item"])
    item = _read_kb_file(
        abs_path,
        project_id,
        allowed_type_set,
        folder_to_type,
        entry_root=entry_root,
        entry_files=entry_files,
    )
    if not item:
        _MD_CACHE.pop(abs_path, None)
        return {}
    _MD_CACHE[abs_path] = {
        "mtime": mtime,
        "project_id": project_id,
        "config_hash": config_hash,
        "entry_root": str(entry_root.resolve()),
        "files_signature": files_signature,
        "item": dict(item),
    }
    return dict(item)


def _top_level_markdown_files(tag_root: Path) -> list[Path]:
    if not tag_root.is_dir():
        return []
    return sorted(
        [
            path for path in tag_root.iterdir()
            if path.is_file() and path.suffix.lower() == ".md" and not path.name.startswith(".") and not _is_index_file(path)
        ],
        key=lambda path: path.name.lower(),
    )


def _entry_specs(tag_root: Path, item_type: str) -> list[tuple[Path, Path, list[Path]]]:
    """Return one index entry per direct Markdown file or direct entry directory.

    A normal entry is a top-level Markdown file. Its optional sibling directory
    named after the knowledge ID contains companions. A directory entry keeps a
    primary Markdown file plus its readable companion files together.
    """
    top_level_files = _top_level_markdown_files(tag_root)
    attachment_dirs: set[Path] = set()
    specs: list[tuple[Path, Path, list[Path]]] = []

    for primary_path in top_level_files:
        text = _read_file_text(str(primary_path))
        meta, _ = _parse_front_matter(text)
        kb_id = knowledge_metadata_id(meta)
        companion_root = tag_root / kb_id if kb_id else None
        files = [primary_path]
        if companion_root and companion_root.is_dir():
            attachment_dirs.add(companion_root.resolve())
            files.extend(_candidate_entry_files(companion_root))
        specs.append((tag_root, primary_path, sorted(files, key=lambda path: str(path.relative_to(tag_root)).lower())))

    for entry_root in sorted((path for path in tag_root.iterdir() if path.is_dir() and not path.name.startswith(".")), key=lambda path: path.name.lower()):
        if entry_root.resolve() in attachment_dirs:
            continue
        markdown_files = _candidate_markdown_files(entry_root)
        top_level_markdown = [
            path for path in markdown_files if path.parent == entry_root
        ]
        primary_path = _choose_primary_file(entry_root, top_level_markdown)
        if primary_path:
            specs.append((entry_root, primary_path, _candidate_entry_files(entry_root)))
    return specs


def _bounded_index_files(root: Path) -> tuple[list[Path], bool]:
    if not root.is_dir():
        return [], False
    files: list[Path] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        check_scan_cancelled()
        names[:] = sorted(
            name
            for name in names
            if name not in _ARTICLE_DETAIL_IGNORED_DIRECTORIES
            and not name.startswith(".")
        )
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            path = Path(directory) / filename
            if not _is_entry_file(path) or _is_index_file(path):
                continue
            files.append(path)
            if len(files) > _INDEX_ENTRY_FILE_LIMIT:
                return files[:_INDEX_ENTRY_FILE_LIMIT], True
    return files, False


def _bounded_entry_files(primary_path: Path, files_root: Path | None) -> tuple[list[Path], bool]:
    """An entry's primary file and the files under ``files_root``, cut at
    _INDEX_ENTRY_FILE_LIMIT, and whether they were cut."""
    candidates, truncated = _bounded_index_files(files_root) if files_root else ([], False)
    files = [primary_path, *[path for path in candidates if path != primary_path]]
    if len(files) > _INDEX_ENTRY_FILE_LIMIT:
        files, truncated = files[:_INDEX_ENTRY_FILE_LIMIT], True
    return files, truncated


def _index_entry_specs(
    tag_root: Path,
) -> list[tuple[Path, Path, list[Path], bool]]:
    top_level_files = _top_level_markdown_files(tag_root)
    attachment_dirs: set[Path] = set()
    specs: list[tuple[Path, Path, list[Path], bool]] = []
    for primary_path in top_level_files:
        check_scan_cancelled()
        text = _read_file_text(str(primary_path))
        meta, _body = _parse_front_matter(text)
        kb_id = knowledge_metadata_id(meta)
        companion_root = tag_root / kb_id if kb_id else None
        if companion_root and companion_root.is_dir():
            attachment_dirs.add(companion_root.resolve())
        files, truncated = _bounded_entry_files(primary_path, companion_root)
        specs.append((tag_root, primary_path, files, truncated))

    for entry_root in sorted(
        (
            path
            for path in tag_root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ),
        key=lambda path: path.name.lower(),
    ):
        check_scan_cancelled()
        if entry_root.resolve() in attachment_dirs:
            continue
        top_level_markdown = sorted(
            (
                path
                for path in entry_root.iterdir()
                if path.is_file()
                and path.suffix.lower() == ".md"
                and not path.name.startswith(".")
                and not _is_index_file(path)
            ),
            key=lambda path: path.name.lower(),
        )
        primary_path = _choose_primary_file(entry_root, top_level_markdown)
        if not primary_path:
            continue
        files, truncated = _bounded_entry_files(primary_path, entry_root)
        specs.append((entry_root, primary_path, files, truncated))
    return specs


def _indexed_knowledge_record_owns_path(record: dict, path: Path) -> bool:
    """Cheaply test an indexed Knowledge/Article record's declared path scope."""
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    source_text = str(source.get("path") or "").strip()
    if source_text:
        source_path = Path(source_text).expanduser().resolve(strict=False)
        if path == source_path:
            return True

    resource_type = str(record.get("resource_type") or "").strip()
    if resource_type == "article":
        root_text = str(attrs.get("canonical_root") or "").strip()
        if not root_text:
            return False
        return path_is_within(
            path,
            Path(root_text).expanduser().resolve(strict=False),
        )

    if attrs.get("single_file"):
        return False
    root_text = str(attrs.get("content_root") or "").strip()
    if not root_text:
        return False
    content_root = Path(root_text).expanduser().resolve(strict=False)
    if content_root.parent.name == "knowledge_base":
        resource_id = str(record.get("resource_id") or "").strip()
        if not resource_id:
            return False
        content_root = content_root / resource_id
    return path_is_within(path, content_root)


def scan_index_source(active_only: bool = False) -> dict:
    """Parse the Knowledge filesystem for the Resource Index Adapter.

    The per-file Markdown cache avoids reparsing unchanged entries. The complete
    list is not cached here; the Resource Index is the only list snapshot.
    """
    projects = _parse_projects_yaml()
    if active_only:
        projects = [p for p in projects if str(p.get("status") or "") == "active"]

    allowed_types, tag_folder_map, folder_to_type, config_hash = _get_tag_config()
    allowed_type_set = set(allowed_types)
    items: list[dict] = []
    resource_errors: list[dict[str, str]] = []
    for proj in projects:
        check_scan_cancelled()
        project_id = str(proj.get("id") or "").strip()
        project_path = str(proj.get("path") or "").strip()
        if not project_id or not project_path:
            continue
        kb_root = os.path.join(os.path.normpath(project_path), "knowledge_base")
        if not os.path.isdir(kb_root):
            continue
        for item_type in allowed_types:
            check_scan_cancelled()
            folder = tag_folder_map.get(item_type) or item_type
            root = Path(kb_root) / folder
            if not root.is_dir():
                continue
            for entry_root, primary_path, entry_files, files_truncated in _index_entry_specs(root):
                check_scan_cancelled()
                try:
                    item = _get_md_item_cached(
                        primary_path,
                        entry_root,
                        entry_files,
                        project_id,
                        config_hash,
                        allowed_type_set,
                        folder_to_type,
                    )
                except Exception as exc:
                    resource_errors.append(
                        {
                            "path": str(primary_path),
                            "error": str(exc)[:1000],
                        }
                    )
                    continue
                if not item:
                    continue
                item.pop("files", None)
                item["file_count"] = len(entry_files)
                item["file_count_truncated"] = files_truncated
                item.pop("path", None)
                items.append(item)

    items.sort(
        key=lambda item: (
            int(item.get("importance") or 0),
            str(item.get("created_at") or ""),
        ),
        reverse=True,
    )
    with _LOCK:
        _FILE_ID_TO_PATH.clear()
    return {
        "generated_at": int(time.time()),
        "allowed_types": allowed_types,
        "projects": [
            {
                "id": str(project.get("id") or ""),
                "status": str(project.get("status") or ""),
                "path": str(project.get("path") or ""),
            }
            for project in projects
            if project.get("id")
        ],
        "items": items,
        "resource_errors": resource_errors,
    }


def _read_file_text(abs_path: str, max_bytes: int = 2_000_000) -> str:
    try:
        size = os.path.getsize(abs_path)
    except (OSError, ValueError):
        size = 0
    if size and size > max_bytes:
        try:
            with open(abs_path, "rb") as f:
                raw = f.read(max_bytes)
            return raw.decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return ""
    try:
        with open(abs_path, encoding="utf-8") as f:
            return f.read()
    except (OSError, ValueError):
        return ""
