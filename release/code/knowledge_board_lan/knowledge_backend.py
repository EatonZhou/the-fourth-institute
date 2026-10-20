#!/usr/bin/env python3
"""Knowledge source parsing and item operations for TFI Portal.

List queries are owned by the Portal resource index. This module parses the
filesystem for its Adapter and handles item-level reads and writes.

Dependencies:
  - management_portal.project_registry — Projects and the knowledge tag vocabulary
  - management_common.site_registry — for project-path rewriting
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote

import yaml

from management_common.format_tags import schema_matches
from management_common.knowledge_metadata import document_metaness
from management_common.scan_cancellation import check_scan_cancelled

from management_portal.resources import get_resource_service
from management_portal.resources.locking import resource_mutation_lock
from management_portal import resource_views

from management_portal import project_registry

# ── path rewriter (management_common lives in portal repo root) ────────────────
_PORTAL_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_PORTAL_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_PORTAL_REPO_ROOT))

from management_common.site_registry import rewrite_shared_path  # noqa: E402
from skill_board_lan import skill_backend  # noqa: E402

def _rewrite_project_path(path: str) -> str:
    return rewrite_shared_path(path, include_codes=False)

# ── module-level state ─────────────────────────────────────────────────────────
_MD_CACHE: dict[str, dict] = {}
_FILE_ID_TO_PATH: dict[str, str] = {}
_ITEM_GRAPH_CACHE: dict[str, dict] = {}
_KNOWLEDGE_GRAPH_CACHE: dict[str, object] = {}
_ITEM_GRAPH_CACHE_TTL_SECONDS = 30
_LOCK = threading.RLock()
_MAX_REGISTERED_ITEM_CHARS = 2_000_000
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
_UNIFIED_SKILL_TYPE = "skill"
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
_ARTICLE_DETAIL_MAX_FILES = 2000

# ── helpers ────────────────────────────────────────────────────────────────────

def _strip_quotes(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in {"'", '"'}:
        return v[1:-1]
    return v


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


def _principal_from_actor(actor: str) -> dict[str, str]:
    clean_actor = str(actor or "").strip()
    if clean_actor.startswith(("resident_", "agent_", "Agent_")):
        return {"user": "", "agent": clean_actor}
    return {"user": clean_actor, "agent": ""}


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


def _yaml_scalar(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _replace_frontmatter_block(
    md_text: str,
    key: str,
    replacement: list[str],
) -> str:
    text = str(md_text or "")
    match = re.match(
        r"^(\ufeff?---\r?\n)([\s\S]*?)(\r?\n---(?:\r?\n|$))([\s\S]*)$",
        text,
    )
    if not match:
        return text
    newline = "\r\n" if "\r\n" in match.group(1) else "\n"
    lines = match.group(2).splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(rf"^{re.escape(key)}\s*:", line)
        ),
        None,
    )
    if start is not None:
        end = start + 1
        while end < len(lines) and (
            not lines[end].strip()
            or lines[end].startswith((" ", "\t"))
        ):
            end += 1
        del lines[start:end]
        insert_at = start
    else:
        insert_at = next(
            (
                index + 1
                for index, line in enumerate(lines)
                if re.match(r"^created_at\s*:", line)
            ),
            len(lines),
        )
    lines[insert_at:insert_at] = replacement
    frontmatter = newline.join(lines)
    return (
        match.group(1)
        + frontmatter
        + match.group(3)
        + match.group(4)
    )


def stamp_knowledge_version(
    md_text: str,
    *,
    actor: str = "",
    user: str = "",
    agent: str = "",
    previous_text: str = "",
    creating: bool = False,
    now: str = "",
) -> str:
    """Create or advance the canonical version block in Knowledge Markdown."""

    text = str(md_text or "")
    previous = str(previous_text or "")
    previous_meta = _yaml_front_matter(previous)
    previous_version = (
        previous_meta.get("version")
        if isinstance(previous_meta.get("version"), dict)
        else {}
    )
    current_meta = _yaml_front_matter(text)
    if not current_meta:
        if previous_version:
            raise ValueError(
                "Versioned Knowledge updates require complete Markdown with YAML metadata"
            )
        return text
    current_version = (
        current_meta.get("version")
        if isinstance(current_meta.get("version"), dict)
        else {}
    )
    source_version = previous_version or current_version
    recorded = bool(previous_version)
    try:
        previous_number = max(
            1,
            int(source_version.get("number") or 1),
        )
    except (TypeError, ValueError):
        previous_number = 1
    number = 1 if creating or not recorded else previous_number + 1
    changed_at = str(now or "").strip() or datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    principal = {
        **_principal_from_actor(actor),
        **{
            key: value
            for key, value in {
                "user": str(user or "").strip(),
                "agent": str(agent or "").strip(),
            }.items()
            if value
        },
    }
    created_by = (
        _version_principal(source_version.get("created_by"))
        if source_version
        else (principal if creating else {"user": "", "agent": ""})
    )
    created_at = _version_timestamp(
        source_version.get("created_at")
        or previous_meta.get("created_at")
        or current_meta.get("created_at")
        or changed_at
    )
    block = [
        "version:",
        f"  number: {number}",
        f"  created_at: {_yaml_scalar(created_at)}",
        "  created_by:",
        f"    user: {_yaml_scalar(created_by['user'])}",
        f"    agent: {_yaml_scalar(created_by['agent'])}",
        f"  updated_at: {_yaml_scalar(changed_at)}",
        "  updated_by:",
        f"    user: {_yaml_scalar(principal['user'])}",
        f"    agent: {_yaml_scalar(principal['agent'])}",
    ]
    return _replace_frontmatter_block(text, "version", block)


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


def _id_from_name(name: str, item_type: str) -> str:
    match = re.search(
        rf"(?:^|__){re.escape(item_type)}_([A-Za-z0-9]+)(?=$|__|\.)",
        name,
    )
    return f"{item_type}_{match.group(1)}" if match else ""


def is_canonical_kb_id(value: object, item_type: str = "") -> bool:
    kb_id = str(value or "").strip()
    try:
        parsed = get_resource_service().validate_id(kb_id, item_type or None)
    except ValueError:
        return False
    return not item_type or parsed.resource_type == item_type


def _set_frontmatter_kb_id(text: str, kb_id: str) -> str:
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    clean = text[len(bom):]
    lines = clean.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in clean[:4096] else "\n"
    if lines and lines[0].strip() == "---":
        end = next(
            (index for index, line in enumerate(lines[1:400], start=1) if line.strip() == "---"),
            None,
        )
        if end is not None:
            identity_lines = [
                index
                for index in range(1, end)
                if re.match(r"^\s*(?:id|kb_id)\s*:", lines[index])
            ]
            insert_at = identity_lines[0] if identity_lines else 1
            indent = (
                re.match(r"^(\s*)", lines[insert_at]).group(1)
                if identity_lines
                else ""
            )
            for index in reversed(identity_lines):
                del lines[index]
            lines.insert(insert_at, f"{indent}kb_id: {kb_id}{newline}")
            return bom + "".join(lines)
    return f"{bom}---{newline}kb_id: {kb_id}{newline}---{newline}{newline}{clean.lstrip()}"


def _write_text_atomic(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o7777
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        from management_common.file_permissions import prepare_atomic_permissions
        with temporary.open("x", encoding="utf-8") as stream:
            prepare_atomic_permissions(temporary, path, mode)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_new_text_atomic(path: Path, text: str) -> None:
    """Publish one new text file without replacing an existing fact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        from management_common.file_permissions import set_created_file_permissions
        with temporary.open("x", encoding="utf-8") as stream:
            set_created_file_permissions(temporary, 0o664)
            stream.write(text)
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _raw_knowledge_entries() -> tuple[list[dict], list[Path]]:
    allowed_types, tag_folder_map, _folder_to_type, _config_hash = _get_tag_config()
    records: list[dict] = []
    knowledge_roots: list[Path] = []
    seen_paths: set[Path] = set()
    for project in _parse_projects_yaml():
        project_id = str(project.get("id") or "").strip()
        project_path = str(project.get("path") or "").strip()
        if not project_id or not project_path:
            continue
        knowledge_root = Path(project_path).expanduser() / "knowledge_base"
        if not knowledge_root.is_dir():
            continue
        knowledge_roots.append(knowledge_root.resolve())
        for item_type in allowed_types:
            tag_root = knowledge_root / (tag_folder_map.get(item_type) or item_type)
            if not tag_root.is_dir():
                continue
            for entry_root, primary_path, _entry_files in _entry_specs(tag_root, item_type):
                resolved = primary_path.resolve()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                text = _read_file_text(str(resolved))
                meta, _body = _parse_front_matter(text)
                metadata_id = knowledge_metadata_id(meta)
                inferred_id = (
                    metadata_id
                    or _id_from_name(entry_root.name, item_type)
                    or _id_from_name(primary_path.name, item_type)
                )
                records.append(
                    {
                        "type": item_type,
                        "project": project_id,
                        "path": resolved,
                        "tag_root": tag_root.resolve(),
                        "entry_root": entry_root.resolve(),
                        "metadata_id": metadata_id,
                        "previous_id": inferred_id,
                    }
                )

    return records, knowledge_roots


def scan_knowledge_ids(*, apply: bool = False) -> dict:
    with _LOCK:
        records, knowledge_roots = _raw_knowledge_entries()
        owners: dict[str, list[int]] = {}
        for index, record in enumerate(records):
            current = str(record.get("metadata_id") or "")
            previous = str(record.get("previous_id") or "")
            item_type = str(record.get("type") or "")
            candidate = current or previous
            if is_canonical_kb_id(candidate, item_type):
                record["candidate_id"] = candidate
                owners.setdefault(candidate, []).append(index)

        duplicate_ids = {kb_id for kb_id, indexes in owners.items() if len(indexes) > 1}
        used_ids = {kb_id for kb_id, indexes in owners.items() if len(indexes) == 1}
        assignments: list[dict] = []
        for record in records:
            item_type = str(record.get("type") or "")
            current = str(record.get("metadata_id") or "")
            reason = ""
            if not current:
                reason = "missing"
            elif not is_canonical_kb_id(current, item_type):
                reason = "invalid"
            elif current in duplicate_ids:
                reason = "duplicate"
            elif str(record.get("candidate_id") or "") in duplicate_ids:
                reason = "duplicate"
            if not reason:
                continue
            candidate = str(record.get("candidate_id") or "")
            if not candidate or candidate in duplicate_ids:
                candidate = get_resource_service().new_id(item_type, used_ids)
            used_ids.add(candidate)
            assignments.append(
                {
                    **record,
                    "reason": reason,
                    "new_id": candidate,
                }
            )

        companion_conflicts: list[dict] = []
        companion_moves: list[tuple[Path, Path]] = []
        for assignment in assignments:
            previous_id = str(assignment.get("previous_id") or "")
            if not previous_id:
                continue
            path = Path(assignment["path"])
            tag_root = Path(assignment["tag_root"])
            entry_root = Path(assignment["entry_root"])
            if entry_root != tag_root or path.parent != tag_root:
                continue
            source = tag_root / previous_id
            target = tag_root / str(assignment["new_id"])
            if not source.is_dir():
                continue
            if target.exists():
                companion_conflicts.append(
                    {"source": str(source), "target": str(target)}
                )
            else:
                companion_moves.append((source, target))

        if apply and companion_conflicts:
            raise ValueError(
                f"Knowledge ID migration blocked by {len(companion_conflicts)} companion-directory conflict(s)"
            )

        rewritten_reference_files = 0
        if apply:
            for assignment in assignments:
                path = Path(assignment["path"])
                _write_text_atomic(
                    path,
                    _set_frontmatter_kb_id(
                        _read_file_text(str(path)), str(assignment["new_id"])
                    ),
                )
            for source, target in companion_moves:
                source.rename(target)

            replacements: dict[str, list[dict]] = {}
            for assignment in assignments:
                previous_id = str(assignment.get("previous_id") or "")
                if previous_id and previous_id != assignment["new_id"]:
                    replacements.setdefault(previous_id, []).append(assignment)
            for knowledge_root in knowledge_roots:
                project = next(
                    (
                        str(record.get("project") or "")
                        for record in records
                        if Path(record["path"]).is_relative_to(knowledge_root)
                    ),
                    "",
                )
                for path in knowledge_root.rglob("*"):
                    if not path.is_file() or path.suffix.lower() not in _TEXT_FILE_EXTENSIONS:
                        continue
                    try:
                        original = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    updated = original
                    for previous_id, candidates in replacements.items():
                        selected = candidates[0] if len(candidates) == 1 else next(
                            (
                                candidate
                                for candidate in candidates
                                if str(candidate.get("project") or "") == project
                            ),
                            None,
                        )
                        if selected:
                            updated = updated.replace(previous_id, str(selected["new_id"]))
                    if updated != original:
                        _write_text_atomic(path, updated)
                        rewritten_reference_files += 1

            _MD_CACHE.clear()
            _ITEM_GRAPH_CACHE.clear()
            _KNOWLEDGE_GRAPH_CACHE.clear()
            _FILE_ID_TO_PATH.clear()
            skill_backend._MD_CACHE.clear()

        return {
            "schema": "tfi.knowledge_identity_scan.v1",
            "apply": bool(apply),
            "knowledge_count": len(records),
            "unchanged_count": len(records) - len(assignments),
            "changed_count": len(assignments),
            "missing_count": sum(item["reason"] == "missing" for item in assignments),
            "invalid_count": sum(item["reason"] == "invalid" for item in assignments),
            "duplicate_id_count": len(duplicate_ids),
            "duplicate_entry_count": sum(item["reason"] == "duplicate" for item in assignments),
            "companion_move_count": len(companion_moves),
            "companion_conflict_count": len(companion_conflicts),
            "companion_conflicts": companion_conflicts,
            "rewritten_reference_files": rewritten_reference_files,
            "assignments": [
                {
                    "path": str(item["path"]),
                    "project": item["project"],
                    "type": item["type"],
                    "previous_id": item["previous_id"],
                    "new_id": item["new_id"],
                    "reason": item["reason"],
                }
                for item in assignments
            ],
        }


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
        try:
            mid = name.split(marker, 1)[1]
            title_part = mid.split(suffix, 1)[0].strip()
            if title_part:
                return title_part
        except Exception:
            return name
    return name


def _title_from_experiment_folder(entry_root: Path) -> str:
    try:
        root = entry_root.resolve()
    except Exception:
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
    except Exception:
        return {}

    meta, body = _parse_front_matter(md)
    item_type = str(meta.get("type") or "").strip()
    if not item_type:
        parts = os.path.normpath(path).split(os.sep)
        try:
            kb_index = next(i for i, p in enumerate(parts) if p.lower() == "knowledge_base")
            if kb_index + 1 < len(parts):
                item_type = parts[kb_index + 1].strip()
        except Exception:
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
    except Exception:
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
    except Exception:
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
    except Exception:
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


def resource_index_snapshot(
    *,
    active_only: bool = True,
    semantic_search_id: str = "",
    offset: int = 0,
    limit: int | None = None,
    refresh: bool = False,
    wait_seconds: float = 0,
    actor: str = "",
) -> dict:
    return resource_views.knowledge_index(
        active_only=active_only,
        semantic_search_id=semantic_search_id,
        offset=offset,
        limit=limit,
        refresh=refresh,
        wait_seconds=wait_seconds,
        actor=actor,
        service=get_resource_service(),
    )


_ITEM_LINK_FIELDS = ("source", "related", "supersedes", "derived_from")


def _item_links_from_text(text: str) -> dict[str, list[str]]:
    """Read the standard links block without requiring a YAML dependency."""
    links = {field: [] for field in _ITEM_LINK_FIELDS}
    lines = str(text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return links

    in_links = False
    current_field = ""
    for raw in lines[1:400]:
        if raw.strip() == "---":
            break
        if raw and not raw.startswith((" ", "\t")):
            in_links = raw.strip() == "links:"
            current_field = ""
            continue
        if not in_links:
            continue
        field_match = re.match(r"^\s{2,}([A-Za-z_]+):\s*(.*)$", raw)
        if field_match:
            field = field_match.group(1).strip()
            current_field = field if field in links else ""
            inline = field_match.group(2).strip()
            if current_field and inline.startswith("[") and inline.endswith("]"):
                links[current_field].extend(
                    _strip_quotes(value.strip())
                    for value in inline[1:-1].split(",")
                    if value.strip()
                )
            continue
        value_match = re.match(r"^\s{2,}-\s*(.+?)\s*$", raw)
        if value_match and current_field:
            value = _strip_quotes(value_match.group(1))
            if value:
                links[current_field].append(value)
    return links


def _item_graph_records(actor: str) -> dict[str, dict]:
    """Project the caller-visible graph records with one Resource query."""

    payload = get_resource_service().query(
        facets=["knowledge"],
        actor=str(actor or ""),
        limit=5000,
        include_attributes=True,
    )
    records: dict[str, dict] = {}
    for raw in payload.get("resources") or []:
        if not isinstance(raw, dict):
            continue
        kb_id = str(raw.get("resource_id") or "").strip()
        if not kb_id or "knowledge" not in set(raw.get("facets") or []):
            continue
        attributes = (
            raw.get("attributes")
            if isinstance(raw.get("attributes"), dict)
            else {}
        )
        project_ids = attributes.get("project_ids") or []
        source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        records[kb_id] = {
            "kb_id": kb_id,
            "title": str(raw.get("title") or attributes.get("filename") or kb_id),
            "type": str(attributes.get("type") or raw.get("resource_type") or "other"),
            "project": str(project_ids[0]) if project_ids else "",
            "importance": int(attributes.get("importance") or 0),
            "path": str(source.get("path") or ""),
        }
    return records


def build_item_graph(item_id: str, *, max_depth: int = 2, actor: str = "") -> dict:
    """Return the outbound knowledge-item link graph rooted at ``item_id``.

    Only registered knowledge-item IDs become graph nodes. URLs and file paths
    remain valid frontmatter links but do not form clickable Knowledge cards.
    """
    clean_id = str(item_id or "").strip()
    if not clean_id:
        raise ValueError("kb_id required")
    depth_limit = max(1, min(int(max_depth or 2), 2))
    # ResourceService reads must retain the authenticated caller. Cache entries
    # may only be reused within that caller's view, and root access is rechecked.
    root = resolve_item_record(clean_id, active_only=False, actor=actor)
    cache_key = f"{actor}:{clean_id}:{depth_limit}"
    with _LOCK:
        cached = _ITEM_GRAPH_CACHE.get(cache_key)
        if cached and time.time() - float(cached.get("created_at") or 0) <= _ITEM_GRAPH_CACHE_TTL_SECONDS:
            return dict(cached.get("payload") or {})

    records_by_kb_id = _item_graph_records(actor)

    def public_record(record: dict, depth: int) -> dict:
        kb_id = str(record.get("kb_id") or "")
        return {
            "kb_id": kb_id,
            "title": str(record.get("title") or record.get("filename") or ""),
            "type": str(record.get("type") or "other"),
            "project": str(record.get("project") or ""),
            "importance": int(record.get("importance") or 0),
            "depth": depth,
        }

    root_kb_id = str(root.get("kb_id") or "").strip()
    if not root_kb_id:
        raise KeyError("knowledge item not found")
    root_index_record = records_by_kb_id.get(root_kb_id, root)
    node_depths = {root_kb_id: 0}
    node_records = {root_kb_id: root_index_record}
    edges: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()
    unresolved_links = 0
    unresolved_links_by_source: dict[str, int] = {}
    frontier = [root_kb_id]

    for source_depth in range(depth_limit):
        next_frontier: list[str] = []
        for source_kb_id in frontier:
            source_record = node_records.get(source_kb_id)
            if not source_record:
                continue
            source_path = Path(str(source_record.get("path") or ""))
            if source_kb_id == root_kb_id and not source_path.is_file():
                source_path = Path(str(root.get("path") or ""))
            if not source_path.is_file():
                continue
            item_links = _item_links_from_text(_read_file_text(str(source_path)))
            for relation, values in item_links.items():
                for raw_target in values:
                    target_key = str(raw_target or "").strip()
                    target_record = records_by_kb_id.get(target_key)
                    if not target_record:
                        unresolved_links += 1
                        unresolved_links_by_source[source_kb_id] = (
                            unresolved_links_by_source.get(source_kb_id, 0) + 1
                        )
                        continue
                    target_kb_id = str(target_record.get("kb_id") or "").strip()
                    if not target_kb_id:
                        continue
                    edge_key = (source_kb_id, target_kb_id, relation)
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        edges.append({
                            "source": source_kb_id,
                            "target": target_kb_id,
                            "relation": relation,
                            "depth": source_depth + 1,
                        })
                    if target_kb_id not in node_depths:
                        node_depths[target_kb_id] = source_depth + 1
                        node_records[target_kb_id] = target_record
                        if source_depth + 1 < depth_limit:
                            next_frontier.append(target_kb_id)
        frontier = next_frontier

    nodes = [
        public_record(node_records[kb_id], depth)
        for kb_id, depth in sorted(node_depths.items(), key=lambda pair: (pair[1], str(node_records[pair[0]].get("title") or "")))
    ]
    payload = {
        "root_id": root_kb_id,
        "root_kb_id": root_kb_id,
        "max_depth": depth_limit,
        "nodes": nodes,
        "edges": edges,
        "unresolved_links": unresolved_links,
        "_unresolved_links_by_source": unresolved_links_by_source,
    }
    with _LOCK:
        _ITEM_GRAPH_CACHE[cache_key] = {"created_at": time.time(), "payload": payload}
    return dict(payload)


def build_knowledge_graph(*, actor: str, refresh: bool = False, max_nodes: int = 5000) -> dict:
    """Return the real link graph for the Knowledge surface.

    The Resource Index remains the owner of the public node list. Edges are
    read from the standard ``links`` frontmatter block of each registered item;
    inferred text similarity is intentionally not used here. Both index reads
    retain the caller, and cached graphs belong only to that caller's view.
    """
    actor = str(actor or "").strip()
    if not actor:
        raise ValueError("actor required")
    node_limit = max(1, min(int(max_nodes or 5000), 5000))
    cache_key = f"{actor}:all:{node_limit}"
    with _LOCK:
        cached = _KNOWLEDGE_GRAPH_CACHE.get(cache_key)
        if (
            not refresh
            and isinstance(cached, dict)
            and time.time() - float(cached.get("created_at") or 0) <= _ITEM_GRAPH_CACHE_TTL_SECONDS
        ):
            return dict(cached.get("payload") or {})

    full_index = resource_index_snapshot(
        active_only=False,
        offset=0,
        limit=node_limit,
        refresh=False,
        actor=actor,
    )
    records_by_kb_id: dict[str, dict] = {}
    for raw in full_index.get("items") or []:
        record = dict(raw) if isinstance(raw, dict) else {}
        kb_id = str(record.get("kb_id") or "").strip()
        if kb_id and is_canonical_kb_id(kb_id):
            records_by_kb_id[kb_id] = record

    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    degree = {kb_id: 0 for kb_id in records_by_kb_id}
    relation_counts = {field: 0 for field in _ITEM_LINK_FIELDS}
    unresolved_links = 0
    unresolved_links_by_source: dict[str, int] = {}
    resource_service = get_resource_service()
    resource_payload = resource_service.query(
        facets=["knowledge"],
        actor=actor,
        limit=node_limit,
        include_attributes=False,
    )
    source_paths = {
        str(resource.get("resource_id") or "").strip(): str(
            (resource.get("source") or {}).get("path") or ""
        )
        for resource in resource_payload.get("resources") or []
        if isinstance(resource, dict)
    }

    for source_kb_id in records_by_kb_id:
        source_path = _valid_item_path(source_paths.get(source_kb_id, ""))
        if not source_path:
            continue
        item_links = _item_links_from_text(_read_file_text(str(source_path)))
        for relation, values in item_links.items():
            for raw_target in values:
                target_kb_id = str(raw_target or "").strip()
                if target_kb_id not in records_by_kb_id:
                    unresolved_links += 1
                    unresolved_links_by_source[source_kb_id] = (
                        unresolved_links_by_source.get(source_kb_id, 0) + 1
                    )
                    continue
                edge_key = (source_kb_id, target_kb_id, relation)
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                edges.append(
                    {
                        "source": source_kb_id,
                        "target": target_kb_id,
                        "relation": relation,
                    }
                )
                degree[source_kb_id] += 1
                if target_kb_id != source_kb_id:
                    degree[target_kb_id] += 1
                relation_counts[relation] += 1

    nodes = []
    for kb_id, record in records_by_kb_id.items():
        nodes.append(
            {
                "kb_id": kb_id,
                "title": str(record.get("title") or record.get("filename") or kb_id),
                "type": str(record.get("type") or "other"),
                "project": str(record.get("project") or ""),
                "importance": int(record.get("importance") or 0),
                "status": str(record.get("status") or ""),
                "preview": str(record.get("preview") or ""),
                "mtime": int(record.get("mtime") or 0),
                "degree": degree[kb_id],
            }
        )
    nodes.sort(
        key=lambda node: (
            -int(node.get("degree") or 0),
            -int(node.get("importance") or 0),
            str(node.get("title") or "").lower(),
        )
    )
    edges.sort(
        key=lambda edge: (
            str(edge.get("source") or ""),
            str(edge.get("target") or ""),
            str(edge.get("relation") or ""),
        )
    )
    payload = {
        "schema": "tfi.knowledge_graph.v1",
        "generated_at": int(time.time()),
        "nodes": nodes,
        "edges": edges,
        "relation_counts": relation_counts,
        "unresolved_links": unresolved_links,
        "_unresolved_links_by_source": unresolved_links_by_source,
    }
    with _LOCK:
        _KNOWLEDGE_GRAPH_CACHE[cache_key] = {
            "created_at": time.time(),
            "payload": payload,
        }
    return dict(payload)


def _knowledge_base_root_for_item(path: Path) -> Path:
    resolved_path = path.resolve(strict=True)
    for proj in _parse_projects_yaml():
        project_path = str(proj.get("path") or "").strip()
        if not project_path:
            continue
        kb_root = (Path(project_path).expanduser() / "knowledge_base").resolve(strict=False)
        try:
            resolved_path.relative_to(kb_root)
        except ValueError:
            continue
        return kb_root
    raise ValueError("knowledge item is outside registered project knowledge_base")


def _unique_trash_path(trash_root: Path, source_name: str) -> Path:
    candidate = trash_root / source_name
    if not candidate.exists():
        return candidate
    source = Path(source_name)
    stamp = int(time.time())
    for index in range(1, 1000):
        suffix = f"__deleted_{stamp}" if index == 1 else f"__deleted_{stamp}_{index}"
        candidate = trash_root / f"{source.stem}{suffix}{source.suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("could not allocate trash filename")


def _valid_item_path(abs_path: str) -> Path | None:
    raw = str(abs_path or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve(strict=False)
    if not path.exists() or not path.is_file():
        return None
    if (
        path.suffix.lower() not in _VALID_ITEM_FILE_EXTENSIONS
        and not skill_backend._path_is_allowed(path)
    ):
        return None
    return path


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


def resolve_item_record(
    kb_id: str,
    *,
    active_only: bool = False,
    actor: str = "",
) -> dict:
    clean_id = str(kb_id or "").strip()
    if not is_canonical_kb_id(clean_id):
        raise ValueError("canonical kb_id required")

    try:
        service = get_resource_service()
        record = (
            service.get(clean_id, actor=actor)
            if actor
            else service.get(clean_id)
        )
    except KeyError:
        raise KeyError("knowledge item not found")
    if str(record.get("resource_type") or "") == "article":
        return _article_item_record(
            clean_id,
            record,
            active_only=active_only,
            actor=actor,
        )
    if "knowledge" not in set(record.get("facets") or []):
        raise KeyError("knowledge item not found")
    attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    if active_only and str(attrs.get("project_status") or "") not in {"active", "system"}:
        raise KeyError("knowledge item not found")
    path = _valid_item_path(str((record.get("source") or {}).get("path") or ""))
    if not path:
        raise KeyError("knowledge item not found")
    content_root = Path(str(attrs.get("content_root") or path.parent)).resolve(strict=False)
    if not content_root.is_dir():
        content_root = path.parent
    files = _indexed_item_files(record, path, content_root)
    file_id = _file_id_for_path(str(path))
    _FILE_ID_TO_PATH[file_id] = str(path)
    out = resource_views.knowledge_item(
        clean_id,
        service=get_resource_service(),
        actor=actor,
    )
    out["files"] = files
    out["kb_id"] = clean_id
    out["file_id"] = file_id
    out["path"] = str(path)
    out["root_path"] = str(content_root)
    return out


def resolve_item_address(
    address: str,
    *,
    active_only: bool = False,
    actor: str = "",
) -> dict:
    clean_address = str(address or "").strip().strip("/")
    root_id, separator, suffix = clean_address.partition("/")
    if not is_canonical_kb_id(root_id):
        raise ValueError("canonical kb_id required")
    item = resolve_item_record(
        root_id,
        active_only=active_only,
        actor=actor,
    )
    if not separator:
        out = dict(item)
        out["address"] = root_id
        out["selected_file_id"] = str(item.get("file_id") or "")
        out["selected_path"] = str(item.get("path") or "")
        return out

    parts = suffix.split("/")
    if len(parts) < 2 or parts[0] != "file":
        raise ValueError("Knowledge nested address must use /file/<relative-path>")
    decoded_parts = [unquote(part) for part in parts[1:]]
    if any(not part or part in {".", ".."} or "\x00" in part for part in decoded_parts):
        raise ValueError("invalid Knowledge file address")
    relative_path = "/".join(decoded_parts)
    selected = next(
        (
            raw
            for raw in item.get("files") or []
            if isinstance(raw, dict)
            and str(raw.get("relative_path") or "").replace("\\", "/")
            == relative_path
        ),
        None,
    )
    if not selected:
        raise KeyError("knowledge item file not found")
    content_root = Path(str(item.get("root_path") or "")).resolve(strict=False)
    path = (content_root / relative_path).resolve(strict=False)
    try:
        path.relative_to(content_root)
    except ValueError as exc:
        raise ValueError("invalid Knowledge file address") from exc
    if str(item.get("type") or "") == "article":
        if not path.is_file():
            raise KeyError("article file not found")
    elif not _valid_item_path(str(path)):
        raise KeyError("knowledge item file not found")
    selected_file_id = _file_id_for_path(str(path))
    _FILE_ID_TO_PATH[selected_file_id] = str(path)
    out = dict(item)
    out["address"] = _nested_file_address(root_id, relative_path)
    out["selected_file_id"] = selected_file_id
    out["selected_path"] = str(path)
    return out


def _knowledge_project_ids(item: dict) -> list[str]:
    """Project ownership shared by Knowledge reads and mutation results."""
    raw_project_ids = item.get("project_ids")
    project_ids = [
        str(value).strip()
        for value in (
            raw_project_ids
            if isinstance(raw_project_ids, (list, tuple, set, frozenset))
            else ()
        )
        if str(value).strip()
    ]
    project = str(item.get("project") or "").strip()
    if project and project.casefold() not in {
        value.casefold() for value in project_ids
    }:
        project_ids.append(project)
    return project_ids


def resolve_knowledge_address(address: str, *, actor: str = "") -> dict:
    item = resolve_item_address(
        address,
        active_only=False,
        actor=actor,
    )
    kb_id = str(item.get("kb_id") or "")
    resource_type = str(item.get("type") or "knowledge")
    resource_category = resource_type if resource_type in {"skill", "article", "experiment"} else "knowledge"
    selected_path = Path(str(item.get("selected_path") or ""))
    clean_address = str(item.get("address") or kb_id)
    public_item = public_item_record(item)
    record = {key: value for key, value in public_item.items() if key != "files"}
    record["project_ids"] = _knowledge_project_ids(item)
    return {
        "schema": "tfi.resource_resolution.v2",
        "address": clean_address,
        "root_id": kb_id,
        "resource_type": resource_type,
        "target_kind": "file" if clean_address != kb_id else "knowledge",
        "record": record,
        "detail": public_item,
        "content_kind": _file_kind(selected_path),
        "content_language": _file_language(selected_path),
        "filename": selected_path.name,
        "resolved_path": str(selected_path),
        "portal_url": f"/resource/{resource_category}?item={quote(kb_id, safe='')}",
        "exists": selected_path.is_file(),
    }


def knowledge_resource_id_for_path(abs_path: str) -> str:
    """Return the owning Knowledge Resource ID for a primary or companion file."""
    path = _valid_item_path(abs_path)
    if not path:
        raise KeyError("knowledge item not found")

    def frontmatter_id(candidate: Path, expected_type: str = "") -> str:
        if candidate.suffix.lower() != ".md" or not candidate.is_file():
            return ""
        meta, _body = _parse_front_matter(_read_file_text(str(candidate)))
        metadata = meta.get("metadata")
        skill_metadata_id = ""
        if expected_type == "skill":
            raw_skill_id = (
                metadata.get("id")
                if isinstance(metadata, dict)
                else meta.get("id")
            )
            skill_metadata_id = str(raw_skill_id or "").strip()
        resource_id = knowledge_metadata_id(meta) or skill_metadata_id
        resource_type = str(meta.get("type") or expected_type).strip()
        return (
            resource_id
            if is_canonical_kb_id(resource_id, resource_type)
            else ""
        )

    direct_id = frontmatter_id(path)
    if direct_id:
        return direct_id

    parts = path.parts
    knowledge_index = next(
        (
            index
            for index, part in enumerate(parts)
            if part.lower() == "knowledge_base"
        ),
        -1,
    )
    if knowledge_index < 0 or knowledge_index + 3 > len(parts):
        raise KeyError("knowledge item not found")

    _allowed, _folder_map, folder_to_type, _config_hash = _get_tag_config()
    folder = parts[knowledge_index + 1]
    item_type = str(folder_to_type.get(folder, folder) or "").strip().lower()
    tag_root = Path(*parts[: knowledge_index + 2])
    relative_parts = parts[knowledge_index + 2 :]
    if len(relative_parts) < 2:
        raise KeyError("knowledge item not found")
    entry_root = tag_root / relative_parts[0]

    if is_canonical_kb_id(entry_root.name, item_type):
        return entry_root.name

    if item_type == "skill":
        markdown_files = skill_backend._candidate_markdown_files(entry_root)
        primary = skill_backend._choose_primary_file(entry_root, markdown_files)
    else:
        markdown_files = [
            candidate
            for candidate in _candidate_markdown_files(entry_root)
            if candidate.parent == entry_root
        ]
        primary = _choose_primary_file(entry_root, markdown_files)
    resource_id = frontmatter_id(primary, item_type) if primary else ""
    if resource_id:
        return resource_id
    raise KeyError("knowledge item not found")


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


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
        return _path_is_within(
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
    return _path_is_within(path, content_root)


def _indexed_knowledge_resource_ids_for_path(path: Path) -> list[str]:
    """Return only indexed resources whose declared file scope contains path."""
    service = get_resource_service()
    resource_ids: list[str] = []
    seen: set[str] = set()
    for filters in ({"facets": ["knowledge"]}, {"resource_types": ["article"]}):
        offset = 0
        while True:
            payload = service.query(
                **filters,
                include_attributes=True,
                limit=5000,
                offset=offset,
            )
            records = [
                record
                for record in payload.get("resources") or []
                if isinstance(record, dict)
            ]
            for record in records:
                resource_id = str(record.get("resource_id") or "").strip()
                if (
                    resource_id
                    and resource_id not in seen
                    and _indexed_knowledge_record_owns_path(record, path)
                ):
                    seen.add(resource_id)
                    resource_ids.append(resource_id)
            offset += len(records)
            if not records or offset >= int(payload.get("total") or 0):
                break
    return resource_ids


def item_metadata_for_path(abs_path: str) -> dict:
    path = _valid_item_path(abs_path)
    if not path:
        raise KeyError("knowledge item not found")
    try:
        resource_id = knowledge_resource_id_for_path(str(path))
    except KeyError:
        resource_id = ""
    if resource_id:
        return resolve_item_record(resource_id, active_only=False)
    for resource_id in _indexed_knowledge_resource_ids_for_path(path):
        try:
            return resolve_item_record(resource_id, active_only=False)
        except (KeyError, ValueError):
            continue
    raise KeyError("knowledge item not found")


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
    except Exception:
        size = 0
    if size and size > max_bytes:
        try:
            with open(abs_path, "rb") as f:
                raw = f.read(max_bytes)
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return ""
    try:
        with open(abs_path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _registered_project(project_id: str) -> dict:
    matches = [
        project
        for project in _parse_projects_yaml()
        if str(project.get("id") or "").strip() == project_id
    ]
    if not matches:
        raise KeyError(f"Project not found: {project_id}")
    if len(matches) != 1:
        raise ValueError(f"Project id is ambiguous: {project_id}")
    project = dict(matches[0])
    root = Path(str(project.get("path") or "")).expanduser().resolve(
        strict=False
    )
    if not root.is_dir():
        raise FileNotFoundError(f"Project directory not found: {root}")
    project["path"] = str(root)
    return project


def _knowledge_filename_title(title: str) -> str:
    normalized = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]+',
        "_",
        str(title or "").strip(),
    )
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("._")
    return normalized[:120] or "Untitled"


def _knowledge_links(value: object) -> dict[str, list[str]]:
    raw = value if isinstance(value, dict) else {}
    unknown = set(raw) - {"source", "related", "supersedes", "derived_from"}
    if unknown:
        raise ValueError(
            "unsupported Knowledge link fields: "
            + ", ".join(sorted(unknown))
        )
    normalized: dict[str, list[str]] = {}
    for key in ("source", "related", "supersedes", "derived_from"):
        values = raw.get(key) or []
        if not isinstance(values, list):
            raise ValueError(f"Knowledge links.{key} must be an array")
        normalized[key] = list(dict.fromkeys(
            str(item).strip()
            for item in values
            if str(item).strip()
        ))
    return normalized


def register_item(
    item_type: str,
    kb_id: str,
    payload: dict,
    *,
    actor: str = "",
) -> dict:
    """Create one canonical Markdown-backed Knowledge resource."""

    clean_type = str(item_type or "").strip()
    clean_id = str(kb_id or "").strip()
    unknown = set(payload) - {
        "project_id",
        "title",
        "body",
        "importance",
        "metaness",
        "ai_generated",
        "links",
        "status",
    }
    if unknown:
        raise ValueError(
            "unsupported Knowledge registration fields: "
            + ", ".join(sorted(unknown))
        )
    if not re.fullmatch(
        rf"{re.escape(clean_type)}_[0-9a-f]{{12}}",
        clean_id,
    ):
        raise ValueError("canonical kb_id required")
    project_id = str(payload.get("project_id") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not project_id:
        raise ValueError("Knowledge project_id required")
    if not title:
        raise ValueError("Knowledge title required")
    if len(project_id) > 120:
        raise ValueError("Knowledge project_id is too long")
    if len(title) > 240:
        raise ValueError("Knowledge title is too long")
    try:
        importance = int(payload.get("importance", 5))
    except (TypeError, ValueError) as exc:
        raise ValueError("Knowledge importance must be an integer") from exc
    if not 1 <= importance <= 10:
        raise ValueError("Knowledge importance must be between 1 and 10")
    ai_generated = payload.get("ai_generated", False)
    metaness = document_metaness(payload)
    if not isinstance(ai_generated, bool):
        raise ValueError("Knowledge ai_generated must be a boolean")

    allowed_types, tag_folder_map, _folder_to_type, _config_hash = (
        _get_tag_config()
    )
    if clean_type not in allowed_types:
        raise ValueError(
            f"Knowledge type does not support Markdown registration: {clean_type}"
        )
    project = _registered_project(project_id)
    knowledge_root = Path(project["path"]) / "knowledge_base"
    tag_root = knowledge_root / (tag_folder_map.get(clean_type) or clean_type)
    created_at = date.today().isoformat()
    filename = (
        f"{created_at.replace('-', '')}__{clean_type}__"
        f"{_knowledge_filename_title(title)}__{clean_id}.md"
    )
    path = tag_root / filename
    raw_body = str(payload.get("body") or "").strip()
    if raw_body.startswith("---"):
        raise ValueError(
            "Knowledge registration body must not include YAML front matter"
        )
    if raw_body.startswith("# "):
        first_title = raw_body.splitlines()[0][2:].strip()
        if first_title != title:
            raise ValueError(
                "Knowledge body H1 must match the registration title"
            )
        body = raw_body
    else:
        body = f"# {title}\n\n{raw_body}".rstrip()
    if len(body) > _MAX_REGISTERED_ITEM_CHARS:
        raise ValueError("Knowledge body is too large")

    metadata: dict[str, object] = {
        "kb_id": clean_id,
        "type": clean_type,
        "project": project_id,
        "created_at": created_at,
        "importance": importance,
        "ai_generated": ai_generated,
        "links": _knowledge_links(payload.get("links")),
    }
    status = str(payload.get("status") or "").strip()
    if metaness is not None:
        metadata["metaness"] = metaness
    if status:
        metadata["status"] = status
    front_matter = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    document = stamp_knowledge_version(
        f"---\n{front_matter}\n---\n\n{body}\n",
        actor=actor,
        creating=True,
    )

    with resource_mutation_lock(tag_root, clean_id):
        if path.exists():
            existing = _yaml_front_matter(_read_file_text(str(path)))
            if str(existing.get("kb_id") or "").strip() != clean_id:
                raise FileExistsError(f"Knowledge file already exists: {path}")
            created = False
        else:
            _write_new_text_atomic(path, document)
            created = True
        with _LOCK:
            _MD_CACHE.pop(str(path), None)
            _MD_CACHE.pop(os.path.abspath(str(path)), None)
            _ITEM_GRAPH_CACHE.clear()
            _KNOWLEDGE_GRAPH_CACHE.clear()
    return {
        "kb_id": clean_id,
        "type": clean_type,
        "project": project_id,
        "title": title,
        "path": str(path),
        "created": created,
        "project_ids": _knowledge_project_ids({"project": project_id}),
    }


class KnowledgeVersionConflict(RuntimeError):
    def __init__(self, expected_version: object, current_version: object) -> None:
        super().__init__(
            "Knowledge Resource 已被其他修改更新；请重新加载后再保存"
        )
        self.expected_version = expected_version
        self.current_version = current_version


def _knowledge_version_recovery_source(
    resource_id: str, path: Path, current_text: str, change_ids: object,
) -> str:
    """Use an exact server-retained change chain, never a supplied version floor."""
    from management_portal.resources.content_changes import ContentChangeStore

    if (
        not isinstance(change_ids, list)
        or not 1 <= len(change_ids) <= 8
        or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value)
               for value in change_ids)
        or len(set(change_ids)) != len(change_ids)
    ):
        raise ValueError("version_recovery_change_ids requires 1 to 8 distinct change IDs")
    changes = ContentChangeStore().for_changes(change_ids)
    if len(changes) != len(change_ids):
        raise ValueError("Knowledge version recovery evidence is missing")
    previous_after = None
    high_water = 0
    source = ""
    previous_ordinal = -1
    for row in changes:
        change = row["change"]
        if (
            row["ordinal"] <= previous_ordinal
            or not schema_matches(change.get("schema"), expected="tfi.resource-content-change.v1")
            or change.get("change_id") != row["change_id"]
            or change.get("actor") != row["actor"]
            or change.get("execution_id") != row["execution_id"]
            or change.get("resource_id") != resource_id
            or change.get("address") != resource_id
            or str(change.get("path") or "") != str(path)
        ):
            raise ValueError("Knowledge version recovery evidence has a different scope or order")
        previous_ordinal = row["ordinal"]
        for side in ("before", "after"):
            snapshot = change.get(side)
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("text"), str):
                raise ValueError("Knowledge version recovery snapshot is invalid")
            text = snapshot["text"]
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != snapshot.get("sha256"):
                raise ValueError("Knowledge version recovery snapshot hash is invalid")
            meta = _yaml_front_matter(text)
            version = knowledge_version_record(text)
            retained_version = snapshot.get("version") or {}
            if (version["recorded"] != retained_version.get("recorded")
                    or version["number"] != retained_version.get("number")):
                raise ValueError("Knowledge version recovery snapshot version is invalid")
            if meta and str(meta.get("kb_id") or "") != resource_id:
                raise ValueError("Knowledge version recovery snapshot identity is invalid")
            if version["recorded"] and version["number"] > high_water:
                high_water, source = version["number"], text
        if previous_after is not None and change["before"]["sha256"] != previous_after:
            raise ValueError("Knowledge version recovery evidence is not a continuous content chain")
        previous_after = change["after"]["sha256"]
    if previous_after != hashlib.sha256(current_text.encode("utf-8")).hexdigest():
        raise ValueError("Knowledge version recovery evidence no longer matches current content")
    if high_water <= knowledge_version_record(current_text)["number"]:
        raise ValueError("Knowledge version recovery requires a proven version regression")
    return source


def update_item_text(
    address: str,
    text: str,
    *,
    actor: str = "",
    expected_version: object = None,
    version_recovery_change_ids: object = None,
) -> dict:
    clean_address = str(address or "").strip()
    if not clean_address:
        raise ValueError("knowledge address required")
    record = resolve_item_address(
        clean_address,
        active_only=False,
        actor=actor,
    )
    path = Path(str(record.get("selected_path") or ""))
    selected_file_id = str(record.get("selected_file_id") or "")
    if path.suffix.lower() not in _EDITABLE_TEXT_FILE_EXTENSIONS:
        raise ValueError("text file required")
    content = str(text or "")
    if version_recovery_change_ids is not None and expected_version is None:
        raise ValueError("Knowledge version recovery requires expected_version")
    if str(record.get("type") or "") == _UNIFIED_SKILL_TYPE:
        if version_recovery_change_ids is not None:
            raise ValueError("Knowledge version recovery requires a Knowledge Markdown primary")
        status, result = skill_backend.save_address_text(
            str(record.get("address") or clean_address),
            content,
            actor=actor,
        )
        if status != 200:
            raise ValueError(str(result.get("error") or "skill update failed"))
        with _LOCK:
            _MD_CACHE.pop(str(path), None)
            _MD_CACHE.pop(os.path.abspath(str(path)), None)
            _ITEM_GRAPH_CACHE.clear()
            _KNOWLEDGE_GRAPH_CACHE.clear()
        return {
            "address": str(record.get("address") or clean_address),
            "kb_id": str(record.get("kb_id") or ""),
            "path": str(path),
            "text": content,
            "mtime": int(result.get("mtime") or 0),
            "project_ids": _knowledge_project_ids(record),
        }
    root_id = str(record.get("kb_id") or "").strip()
    primary_path = Path(str(record.get("path") or ""))
    with resource_mutation_lock(primary_path.parent, root_id):
        record = resolve_item_address(
            clean_address,
            active_only=False,
            actor=actor,
        )
        path = Path(str(record.get("selected_path") or ""))
        selected_file_id = str(record.get("selected_file_id") or "")
        if path.suffix.lower() not in _EDITABLE_TEXT_FILE_EXTENSIONS:
            raise ValueError("text file required")
        primary_path = Path(str(record.get("path") or ""))
        version_path = (
            primary_path
            if primary_path.suffix.lower() == ".md"
            and str(record.get("type") or "") != "article"
            else None
        )
        previous_primary = (
            _read_file_text(str(version_path))
            if version_path is not None
            else ""
        )
        if expected_version is not None:
            if version_path is None:
                raise ValueError("Knowledge Resource does not expose a version token")
            current_version = knowledge_version_record(
                previous_primary,
                mtime=(
                    int(version_path.stat().st_mtime)
                    if version_path.exists()
                    else 0
                ),
            )
            expected_number = (
                expected_version.get("number")
                if isinstance(expected_version, dict)
                else expected_version
            )
            try:
                expected_number = int(expected_number)
            except (TypeError, ValueError) as exc:
                raise ValueError("valid Knowledge version token required") from exc
            if expected_number != int(current_version.get("number") or 0):
                raise KnowledgeVersionConflict(
                    expected_version,
                    current_version,
                )
        if version_path is not None and path.resolve() == version_path.resolve():
            document_metaness(_yaml_front_matter(content))
        version_source = previous_primary
        if version_recovery_change_ids is not None:
            if version_path is None or path.resolve() != version_path.resolve():
                raise ValueError("Knowledge version recovery requires a Knowledge Markdown primary")
            if content != previous_primary:
                raise ValueError("Knowledge version recovery must preserve current complete text")
            version_source = _knowledge_version_recovery_source(
                root_id, version_path, previous_primary, version_recovery_change_ids,
            )
        stored_content = (
            stamp_knowledge_version(
                content,
                actor=actor,
                previous_text=version_source,
            )
            if version_path is not None and path.resolve() == version_path.resolve()
            else content
        )
        previous_content = _read_file_text(str(path))
        before_version = knowledge_version_record(previous_primary) if version_path is not None else {}
        _write_text_atomic(path, stored_content)
        if version_path is not None and path.resolve() != version_path.resolve():
            stamped_primary = stamp_knowledge_version(
                previous_primary,
                actor=actor,
                previous_text=previous_primary,
            )
            if stamped_primary != previous_primary:
                _write_text_atomic(version_path, stamped_primary)
        try:
            mtime = int(path.stat().st_mtime)
        except Exception:
            mtime = 0
        version = (
            knowledge_version_record(
                _read_file_text(str(version_path)),
                mtime=(
                    int(version_path.stat().st_mtime)
                    if version_path is not None and version_path.exists()
                    else 0
                ),
            )
            if version_path is not None
            else {}
        )
        from management_portal.resources.content_changes import record_text_change

        record_text_change(
            clean_address, path, previous_content, stored_content,
            before_version=before_version, after_version=version,
            version_recovery_change_ids=version_recovery_change_ids,
        )
        if version_path is not None and path.resolve() != version_path.resolve():
            record_text_change(
                root_id, version_path, previous_primary, stamped_primary,
                before_version=before_version, after_version=version,
            )
        with _LOCK:
            _MD_CACHE.pop(str(path), None)
            _MD_CACHE.pop(os.path.abspath(str(path)), None)
            if version_path is not None:
                _MD_CACHE.pop(str(version_path), None)
                _MD_CACHE.pop(os.path.abspath(str(version_path)), None)
            _ITEM_GRAPH_CACHE.clear()
            _KNOWLEDGE_GRAPH_CACHE.clear()
            _FILE_ID_TO_PATH[selected_file_id] = str(path)
    return {
        "address": str(record.get("address") or clean_address),
        "kb_id": str(record.get("kb_id") or ""),
        "path": str(path),
        "text": stored_content,
        "mtime": mtime,
        "version": version,
        "project_ids": _knowledge_project_ids(record),
    }


def delete_item(kb_id: str, *, actor: str = "") -> dict:
    clean_id = str(kb_id or "").strip()
    if not is_canonical_kb_id(clean_id):
        raise ValueError("canonical kb_id required")
    record = resolve_item_record(
        clean_id,
        active_only=False,
        actor=actor,
    )
    resource_type = str(record.get("type") or "")
    if resource_type == _UNIFIED_SKILL_TYPE:
        status, result = skill_backend.delete_skill(clean_id, actor=actor)
        if status == 400:
            raise ValueError(str(result.get("error") or "skill delete failed"))
        if status == 403:
            raise PermissionError(str(result.get("error") or "skill delete failed"))
        if status == 404:
            raise KeyError(str(result.get("error") or "skill not found"))
        if status != 200:
            raise RuntimeError(str(result.get("error") or "skill delete failed"))
        with _LOCK:
            _MD_CACHE.clear()
            _FILE_ID_TO_PATH.clear()
            _ITEM_GRAPH_CACHE.clear()
            _KNOWLEDGE_GRAPH_CACHE.clear()
        return {**result, "project_ids": _knowledge_project_ids(record)}
    if resource_type == "article":
        raise PermissionError("This resource cannot be deleted from Knowledge detail")
    path = Path(str(record.get("path") or ""))
    if path.suffix.lower() not in _EDITABLE_TEXT_FILE_EXTENSIONS:
        raise ValueError("text file required")
    with resource_mutation_lock(path.parent, clean_id):
        record = resolve_item_record(
            clean_id,
            active_only=False,
            actor=actor,
        )
        path = Path(str(record.get("path") or ""))
        if path.suffix.lower() not in _EDITABLE_TEXT_FILE_EXTENSIONS:
            raise ValueError("text file required")
        kb_root = _knowledge_base_root_for_item(path)
        trash_root = kb_root / ".trash"
        trash_root.mkdir(parents=True, exist_ok=True)
        target = _unique_trash_path(trash_root, path.name)
        from management_portal.workbench_projections.resource_lifecycle import release_resource
        release_resource(clean_id, actor=actor)
        shutil.move(str(path), str(target))
        with _LOCK:
            _MD_CACHE.pop(str(path), None)
            _MD_CACHE.pop(os.path.abspath(str(path)), None)
            _FILE_ID_TO_PATH.pop(str(record.get("file_id") or ""), None)
            _ITEM_GRAPH_CACHE.clear()
            _KNOWLEDGE_GRAPH_CACHE.clear()
    return {
        "kb_id": clean_id,
        "deleted": True,
        "trash_path": str(target),
        "project_ids": _knowledge_project_ids(record),
    }


def _now() -> int:
    return int(time.time())


def _compact_text(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    return compact[:limit].rstrip()


